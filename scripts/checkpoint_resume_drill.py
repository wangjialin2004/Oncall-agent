"""Checkpoint kill/resume drill for L1 pilot ops gate (H6 after-tool).

Flow:
1. Login and start a diagnosis-style SSE request.
2. Wait until at least one completed ``tool_event`` (not just route/agent).
3. Settle briefly so fire-and-forget ``save_step`` can flush to Redis.
4. Kill the backend process (simulate crash).
5. Confirm checkpoint API: prefer ``resumable=true`` and ``step>0``.
6. Restart backend.
7. Re-POST /api/assistant with the same session_id (conservative resume).
8. Require a complete event and write drill result JSON.

Usage (from repo root, venv active):
  python scripts/checkpoint_resume_drill.py
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

BASE = os.environ.get("ONCALL_BASE", "http://127.0.0.1:9900")
LOG_DIR = Path("logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)
OUT = LOG_DIR / "checkpoint_resume_drill_20260712.log"


def log(msg: str) -> None:
    line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {msg}"
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        print(line.encode("ascii", "backslashreplace").decode("ascii"), flush=True)
    with OUT.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def password() -> str:
    env = os.environ.get("ONCALL_EVAL_PASSWORD") or os.environ.get("AUTH_PASSWORD")
    if env:
        return env
    p = Path("logs/.pilot_pass")
    if p.exists():
        return p.read_text(encoding="utf-8").strip()
    return "admin"


def login() -> str:
    req = urllib.request.Request(
        f"{BASE}/api/auth/login",
        data=json.dumps({"username": "admin", "password": password()}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode())["data"]["token"]


def parse_sse(raw: str) -> list[dict]:
    events = []
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    for frame in raw.split("\n\n"):
        data_lines = []
        for line in frame.splitlines():
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
            elif line == "data":
                data_lines.append("")
        if not data_lines:
            continue
        blob = "\n".join(data_lines).strip()
        if not blob:
            continue
        try:
            events.append(json.loads(blob))
        except json.JSONDecodeError:
            continue
    return events


def stream_until_progress(
    token: str,
    session_id: str,
    question: str,
    min_events: int = 3,
    max_wait: float = 120.0,
    require_tool: bool = True,
    tool_settle_seconds: float = 2.5,
):
    """Stream until enough progress is observed, then return events for kill phase.

    H6 / step-level resume needs a completed tool step so ``save_step`` has run.
    When ``require_tool=True`` (default for H6), do **not** kill on route/agent
    alone — wait for at least one ``tool_event``, prefer a completed tool status,
    then settle briefly so the fire-and-forget Redis checkpoint can flush.
    """
    body = json.dumps(
        {
            "Id": session_id,
            "Question": question,
            "AttachmentIds": [],
            "CheckpointReplay": False,
        }
    ).encode()
    req = urllib.request.Request(
        f"{BASE}/api/assistant",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    started = time.perf_counter()
    chunks: list[bytes] = []
    saw_tool = False
    saw_tool_done = False
    tool_names: list[str] = []
    events: list[dict] = []
    try:
        with urllib.request.urlopen(req, timeout=max_wait) as resp:
            while True:
                chunk = resp.read(2048)
                if not chunk:
                    break
                chunks.append(chunk)
                raw = b"".join(chunks).decode("utf-8", "replace")
                events = parse_sse(raw)
                types = [e.get("type") for e in events]
                for e in events:
                    if e.get("type") != "tool_event":
                        continue
                    saw_tool = True
                    tool = str(e.get("tool") or "")
                    if tool and tool not in tool_names:
                        tool_names.append(tool)
                    status = str(e.get("status") or "").lower()
                    if status in {
                        "completed",
                        "success",
                        "ok",
                        "failed",
                        "error",
                        "degraded",
                        "timeout",
                        "timed_out",
                    }:
                        saw_tool_done = True
                ready = False
                if require_tool:
                    # Prefer a finished tool call (step save runs after tools execute).
                    ready = saw_tool_done or (
                        saw_tool and (time.perf_counter() - started) >= 25.0
                    )
                else:
                    ready = len(events) >= min_events and (
                        saw_tool
                        or any(
                            t in {"route_selected", "route_event", "agent_event"}
                            for t in types
                        )
                    )
                if ready:
                    log(
                        f"progress observed: events={len(events)} types={types[:12]} "
                        f"saw_tool={saw_tool} saw_tool_done={saw_tool_done} tools={tool_names[:8]}"
                    )
                    # Give fire-and-forget save_step a moment to hit Redis.
                    if require_tool and tool_settle_seconds > 0:
                        time.sleep(tool_settle_seconds)
                    break
                if time.perf_counter() - started > max_wait:
                    log(
                        f"max_wait reached before kill "
                        f"(saw_tool={saw_tool} saw_tool_done={saw_tool_done})"
                    )
                    break
    except Exception as exc:  # noqa: BLE001
        log(f"stream interrupted (expected around kill): {type(exc).__name__}:{exc}")
        events = parse_sse(b"".join(chunks).decode("utf-8", "replace"))
        for e in events:
            if e.get("type") == "tool_event":
                saw_tool = True
                tool = str(e.get("tool") or "")
                if tool and tool not in tool_names:
                    tool_names.append(tool)
                status = str(e.get("status") or "").lower()
                if status in {
                    "completed",
                    "success",
                    "ok",
                    "failed",
                    "error",
                    "degraded",
                    "timeout",
                    "timed_out",
                }:
                    saw_tool_done = True
    return {
        "events": events,
        "latency_s": time.perf_counter() - started,
        "saw_tool": saw_tool,
        "saw_tool_done": saw_tool_done,
        "tool_names": tool_names,
    }


def get_checkpoint(token: str, session_id: str) -> dict:
    req = urllib.request.Request(
        f"{BASE}/api/checkpoint/{session_id}",
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        return {"code": exc.code, "error": body}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}:{exc}"}


def kill_backend() -> list[str]:
    killed: list[str] = []
    out = subprocess.getoutput("netstat -ano | findstr :9900 | findstr LISTENING")
    for pid in set(re.findall(r"\s(\d+)\s*$", out, re.M)):
        if pid and pid != "0":
            subprocess.call(["taskkill", "/PID", pid, "/F"])
            killed.append(pid)
            log(f"killed backend pid {pid}")
    # also kill pid file if present
    pid_file = Path("logs/backend_run.pid")
    if pid_file.exists():
        try:
            p = pid_file.read_text(encoding="utf-8").strip()
            if p and p not in killed:
                subprocess.call(["taskkill", "/PID", p, "/F"])
                killed.append(p)
                log(f"killed pidfile backend {p}")
        except Exception as exc:  # noqa: BLE001
            log(f"pidfile kill failed: {exc}")
    time.sleep(2)
    return killed


def start_backend() -> None:
    # ensure venv python
    env = os.environ.copy()
    log("starting backend...")
    # use subprocess Popen detached
    with open("logs/backend_run.log", "ab") as logf:
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9900"],
            stdout=logf,
            stderr=logf,
            cwd=str(Path.cwd()),
            env=env,
        )
    Path("logs/backend_run.pid").write_text(str(proc.pid), encoding="utf-8")
    log(f"backend started pid={proc.pid}")
    for i in range(30):
        try:
            with urllib.request.urlopen(f"{BASE}/health", timeout=3) as resp:
                data = json.loads(resp.read().decode())
                status = (data.get("data") or {}).get("status")
                if status == "healthy":
                    log(f"backend healthy after {i + 1} probes")
                    return
                log(f"backend status={status}")
        except Exception:
            pass
        time.sleep(2)
    raise RuntimeError("backend failed to become healthy")


def stream_resume(token: str, session_id: str, question: str, timeout: float = 180.0):
    body = json.dumps(
        {
            "Id": session_id,
            "Question": question,
            "AttachmentIds": [],
            "CheckpointReplay": False,
        }
    ).encode()
    req = urllib.request.Request(
        f"{BASE}/api/assistant",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    started = time.perf_counter()
    chunks: list[bytes] = []
    err = None
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            while True:
                chunk = resp.read(4096)
                if not chunk:
                    break
                chunks.append(chunk)
                if time.perf_counter() - started > timeout:
                    err = "client_timeout"
                    break
    except Exception as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}:{exc}"
    events = parse_sse(b"".join(chunks).decode("utf-8", "replace"))
    latency = time.perf_counter() - started
    has_complete = any(e.get("type") == "complete" for e in events)
    answer = ""
    for e in events:
        if e.get("type") == "complete":
            answer = str(e.get("answer") or "")
    return events, latency, err, has_complete, answer


def inspect_redis_ckpt(session_id: str) -> int:
    try:
        import redis

        r = redis.Redis.from_url(
            "redis://:123456@localhost:6379/0", protocol=2, socket_timeout=3
        )
        # namespace default super_biz_agent
        keys = list(r.scan_iter(match=f"*{session_id}*", count=100))
        log(f"redis keys matching session: {len(keys)}")
        for k in keys[:20]:
            kk = k.decode() if isinstance(k, bytes) else str(k)
            log(f"  key={kk}")
        return len(keys)
    except Exception as exc:  # noqa: BLE001
        log(f"redis inspect failed: {exc}")
        return -1


def main() -> int:
    log("=== checkpoint kill/resume drill start (H6 after-tool) ===")
    session_id = f"ckpt-drill-tool-{int(time.time())}"
    question = "checkout-api 最近10分钟CPU使用率过高告警，请排查（checkpoint tool-after kill 演练）"
    token = login()
    log(f"session={session_id}")

    # Phase 1: stream until a tool step is observed (required for step-level ckpt)
    pre = stream_until_progress(
        token,
        session_id,
        question,
        min_events=2,
        max_wait=120.0,
        require_tool=True,
        tool_settle_seconds=3.0,
    )
    events = pre["events"]
    lat = float(pre["latency_s"])
    saw_tool = bool(pre["saw_tool"])
    saw_tool_done = bool(pre["saw_tool_done"])
    tool_names = list(pre.get("tool_names") or [])
    log(
        f"pre-kill events={len(events)} lat={lat:.1f}s "
        f"saw_tool={saw_tool} saw_tool_done={saw_tool_done} tools={tool_names}"
    )
    types = [e.get("type") for e in events]
    log(f"pre-kill types={types[:24]}")

    # extra grace so store can flush meta/steps
    time.sleep(2.0)
    key_count_before = inspect_redis_ckpt(session_id)

    # Phase 2: kill backend
    killed = kill_backend()
    log(f"killed={killed}")
    time.sleep(1)
    key_count_after_kill = inspect_redis_ckpt(session_id)

    # Phase 3: restart
    start_backend()
    token = login()  # new process, same secret/token may still work if not rotated
    ckpt = get_checkpoint(token, session_id)
    log(f"checkpoint api after restart: {json.dumps(ckpt, ensure_ascii=False)[:500]}")

    # Phase 4: resume
    events2, lat2, err2, complete2, answer2 = stream_resume(
        token, session_id, question, timeout=200
    )
    types2 = [e.get("type") for e in events2]
    log(f"resume events={len(events2)} complete={complete2} lat={lat2:.1f}s err={err2}")
    log(f"resume types={types2[:20]}")
    # Avoid Windows GBK console crash on CJK answers
    preview = (answer2 or "")[:200].replace("\n", " ")
    try:
        log(f"answer_preview={preview}")
    except Exception:
        log(f"answer_preview={preview.encode('ascii', 'backslashreplace').decode('ascii')}")

    ckpt2 = get_checkpoint(token, session_id)
    log(f"checkpoint api after resume: {json.dumps(ckpt2, ensure_ascii=False)[:500]}")

    data = (ckpt.get("data") or {}) if isinstance(ckpt, dict) else {}
    data2 = (ckpt2.get("data") or {}) if isinstance(ckpt2, dict) else {}
    enabled = bool(data.get("enabled"))
    resumable = bool(data.get("resumable"))
    step_after_restart = int(data.get("step") or 0)
    # H6 success:
    # - hard: resume complete after kill/restart
    # - soft evidence: saw tool before kill AND resumable=true with step>0
    hard_pass = bool(complete2) and not err2
    step_resume_evidence = bool(saw_tool and resumable and step_after_restart > 0)
    passed = hard_pass
    result = {
        "passed": passed,
        "drill_mode": "after_tool",
        "session_id": session_id,
        "pre_kill_events": len(events),
        "pre_kill_latency_s": round(lat, 2),
        "saw_tool_before_kill": saw_tool,
        "saw_tool_done_before_kill": saw_tool_done,
        "tools_before_kill": tool_names,
        "redis_keys_before_kill": key_count_before,
        "redis_keys_after_kill": key_count_after_kill,
        "checkpoint_enabled": enabled,
        "checkpoint_resumable_after_restart": resumable,
        "checkpoint_step_after_restart": step_after_restart,
        "checkpoint_route_after_restart": str(data.get("route") or ""),
        "step_resume_evidence": step_resume_evidence,
        "resume_complete": complete2,
        "resume_latency_s": round(lat2, 2),
        "resume_error": err2 or "",
        "checkpoint_after_resume": {
            "resumable": bool(data2.get("resumable")),
            "completed": bool(data2.get("completed")),
            "step": int(data2.get("step") or 0),
        },
        "note": (
            "H6: kill only after tool_event (prefer completed tool) + settle; "
            "conservative re-POST same session_id; "
            "step_resume_evidence=true means meta was resumable with step>0 after restart"
        ),
        "backend_recovered": True,
    }
    log(f"RESULT {json.dumps(result, ensure_ascii=False)}")
    out = Path("logs/checkpoint_resume_drill_result.json")
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    Path(f"logs/checkpoint_resume_drill_result_{stamp}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log("=== checkpoint kill/resume drill end ===")
    if not saw_tool:
        log("WARNING: killed without seeing tool_event; step-level resume not verified")
    if hard_pass and not step_resume_evidence:
        log(
            "WARNING: resume completed but checkpoint was not resumable/step>0 "
            "(may still be fresh-run style close)"
        )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
