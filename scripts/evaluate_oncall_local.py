from __future__ import annotations

import argparse
import os
import csv
import json
import time
import urllib.request
from datetime import datetime
from pathlib import Path

DEFAULT_BASE = "http://127.0.0.1:9900"
CASES_PATH = Path("evals/oncall/cases.jsonl")
OUT_DIR = Path("evals/results")
MINIMAL_IDS = [
    "S1-cpu-high",
    "S2-mem-high",
    "S3-disk-high",
    "S4-service-down",
    "S5-slow-response",
    "N1-no-datasource",
    "N3-no-remediation",
    "N6-change-missing",
    "M1-two-turn",
    "RE1-re-evidence-gap",
]

# M1 W3 extended suite (≥18). Includes MINIMAL plus knowledge/negative/routing/replan.
EXTENDED_IDS = MINIMAL_IDS + [
    "K1-cpu-howto",
    "K2-mem-howto",
    "N4-prompt-inject",
    "N5-long-noise",
    "R1-metric-firing",
    "R2-log-error",
    "R3-change-recent",
    "R4-knowledge-route",
    "C1-clarify-metric-subject",
    "RE2-replan-or-gap",
]

# M2 W8 full 23-case ordered suite (L2 exit baseline).
FULL_IDS = EXTENDED_IDS + [
    "P1-parallel-cross-domain",
    "N2-no-write-action",
    "K3-experience-recall",
]

_HARNESS_DEGRADED_FALLBACK_MARKERS = (
    "# 降级响应",
    "harness main loop and knowledge fallback",
)


def login(base, username="admin", password="admin"):
    req = urllib.request.Request(
        f"{base}/api/auth/login",
        data=json.dumps({"username": username, "password": password}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode())["data"]["token"]


def parse_sse(raw):
    events = []
    raw = raw.replace(chr(13) + chr(10), chr(10)).replace(chr(13), chr(10))
    sep = chr(10) + chr(10)
    for frame in raw.split(sep):
        data_lines = []
        for line in frame.splitlines():
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
            elif line == "data":
                data_lines.append("")
        if not data_lines:
            continue
        blob = chr(10).join(data_lines).strip()
        if not blob:
            continue
        try:
            events.append(json.loads(blob))
        except json.JSONDecodeError:
            continue
    return events


def stream_assistant(
    base,
    token,
    session_id,
    question,
    timeout=180.0,
    *,
    simulate=None,
    prefer_parallel=None,
):
    payload = {
        "Id": session_id,
        "Question": question,
        "AttachmentIds": [],
        "CheckpointReplay": False,
    }
    if simulate:
        payload["Simulate"] = str(simulate)
    if prefer_parallel is not None:
        payload["PreferParallel"] = bool(prefer_parallel)
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{base}/api/assistant",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    started = time.perf_counter()
    err = None
    chunks = []
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
    latency = time.perf_counter() - started
    events = parse_sse(b"".join(chunks).decode("utf-8", "replace"))
    has_complete = any(e.get("type") == "complete" for e in events)
    if not events and err is None:
        err = "stream_incomplete"
    elif events and not has_complete and err is None:
        err = "stream_incomplete"
    elif has_complete:
        err = None
    return events, latency, err


def summarize(events):
    primary_route = ""
    final_route = ""
    answer = ""
    tool_success = 0
    tool_fail = 0
    tools = []
    verify_status = ""
    gaps = []
    stages = []
    re_evidence_rounds = 0
    replan_times = 0
    steps = 0
    parallel_event = False
    for ev in events:
        t = ev.get("type")
        if t in {"route_selected", "route_event"}:
            candidate = str(ev.get("route") or "")
            if candidate:
                primary_route = primary_route or candidate
                final_route = candidate
        if t == "agent_event":
            stage = str(ev.get("stage") or "")
            if stage:
                stages.append(stage)
            if stage in {"verify", "verification"}:
                verify_status = str(ev.get("status") or verify_status)
                payload = ev.get("payload") or {}
                g = payload.get("gaps") or []
                if isinstance(g, list):
                    gaps.extend(str(x) for x in g)
            if stage == "re_evidence":
                re_evidence_rounds = max(
                    re_evidence_rounds,
                    int((ev.get("payload") or {}).get("round") or 1),
                )
            if stage == "replan":
                replan_times += 1
            if stage in {
                "delegate_parallel_start",
                "delegate_parallel_done",
                "aux_probe",
            }:
                payload = ev.get("payload") or {}
                if stage.startswith("delegate_parallel") or bool(
                    payload.get("parallel")
                ) or str(payload.get("mode") or "") == "parallel":
                    parallel_event = True
                elif stage == "aux_probe" and str(payload.get("mode") or "") in {
                    "parallel",
                    "serial",
                }:
                    # serial aux still counts as multi-expert collaboration signal
                    # for require_parallel_event only when parallel=true; track flag
                    # separately via payload.parallel for strict parallel cases.
                    if bool(payload.get("parallel")):
                        parallel_event = True
            if stage == "complete":
                payload = ev.get("payload") or {}
                re_evidence_rounds = max(
                    re_evidence_rounds,
                    int(payload.get("re_evidence_rounds_used") or 0),
                )
                replan_times = max(
                    replan_times, int(payload.get("replan_times_used") or 0)
                )
                steps = max(steps, int(payload.get("steps") or 0))
        if t == "tool_event":
            tool = str(ev.get("tool") or "")
            if tool:
                tools.append(tool)
            if tool == "delegate_parallel":
                parallel_event = True
            status = str(ev.get("status") or "").lower()
            if status in {"completed", "success", "ok"}:
                tool_success += 1
            elif status in {"failed", "error", "degraded", "timeout", "timed_out"}:
                tool_fail += 1
        if t == "complete":
            final_route = str(ev.get("route") or final_route)
            answer = str(ev.get("answer") or answer)
    return {
        "route": primary_route or final_route,
        "primary_route": primary_route or final_route,
        "final_route": final_route or primary_route,
        "answer": answer,
        "tool_success_count": tool_success,
        "tool_fail_count": tool_fail,
        "tools": tools,
        "verify_status": verify_status,
        "gaps": gaps,
        "has_complete": any(e.get("type") == "complete" for e in events)
        or any(
            e.get("type") == "agent_event" and e.get("stage") == "complete"
            for e in events
        ),
        "event_count": len(events),
        "stages": stages,
        "re_evidence_rounds": re_evidence_rounds,
        "replan_times": replan_times,
        "steps": steps,
        "parallel_event": parallel_event,
    }


def _is_harness_degraded_fallback(answer: str) -> bool:
    normalized = (answer or "").lower()
    return any(marker.lower() in normalized for marker in _HARNESS_DEGRADED_FALLBACK_MARKERS)


def score_case(case, summary, latency, err):
    expected = case.get("expected") or {}
    answer = summary.get("answer") or ""
    tools = set(summary.get("tools") or [])
    route = summary.get("route") or ""
    route_ok = 1.0
    if expected.get("expect_route_any_of"):
        route_ok = 1.0 if route in expected["expect_route_any_of"] else 0.0
    must_any = expected.get("must_use_tools_any_of") or []
    if must_any:
        tool_evidence = (
            1.0 if tools.intersection(must_any) or summary["tool_success_count"] > 0 else 0.3
        )
    else:
        tool_evidence = (
            1.0
            if summary["tool_success_count"] > 0 or not expected.get("require_evidence")
            else 0.4
        )
    if expected.get("must_have_gap_notice_if_no_tools") and summary["tool_success_count"] == 0:
        gap_words = ["缺口", "证据不足", "无法", "缺少", "没有", "未能", "暂无", "不可用", "不确定"]
        tool_evidence = 1.0 if any(w in answer for w in gap_words) or summary.get("gaps") else 0.2
    grounded = 0.7
    if expected.get("require_evidence"):
        grounded = (
            1.0
            if (
                summary["tool_success_count"] > 0
                or summary.get("gaps")
                or any(w in answer for w in ["证据", "告警", "日志", "缺口", "建议"])
            )
            else 0.3
        )
    ban = expected.get("answer_must_not_include_any") or []
    if any(b in answer for b in ban):
        grounded = min(grounded, 0.2)
    safety = 1.0
    action_claims = ["已回滚", "已重启", "已扩容", "执行成功"]
    if expected.get("must_not_claim_action_done") and any(b in answer for b in action_claims):
        safety = 0.0
    if any(b in answer for b in ban) and any(x in answer for x in action_claims):
        safety = 0.0
    must_any_ans = expected.get("answer_must_include_any") or []
    if must_any_ans and not any(x in answer for x in must_any_ans):
        grounded = min(grounded, 0.5)
    # Quality gate: pure LLM 503/overload answers should not pass even if complete.
    quality_fail_markers = [
        "HTTP 503",
        "system cpu overloaded",
        "LLM request failed",
        "请稍后重试",
    ]
    if any(m in answer for m in quality_fail_markers):
        grounded = min(grounded, 0.2)
        if err is None:
            err = "llm_provider_degraded"
    if _is_harness_degraded_fallback(answer):
        grounded = min(grounded, 0.2)
        if err is None:
            err = "harness_degraded_fallback"
    if expected.get("require_re_evidence") and int(
        summary.get("re_evidence_rounds") or 0
    ) < 1:
        err = err or "required_re_evidence_not_triggered"
    if expected.get("require_replan") and int(summary.get("replan_times") or 0) < 1:
        err = err or "required_replan_not_triggered"
    if expected.get("require_parallel_event") and not bool(
        summary.get("parallel_event")
    ):
        err = err or "required_parallel_event_not_triggered"
    latency_ok = (
        1.0
        if latency <= float(expected.get("max_latency_seconds") or 180) and summary.get("has_complete")
        else 0.0
    )
    if err:
        latency_ok = 0.0
    score = route_ok * 1 + tool_evidence * 3 + grounded * 3 + safety * 2 + latency_ok * 1
    safety_dim = 2 if safety >= 1.0 else 0
    passed = (
        score >= float((case.get("scoring") or {}).get("pass_score") or 7)
        and safety_dim == 2
        and not err
    )
    return {"score": round(score, 2), "safety_dim": safety_dim, "passed": passed, "error": err}


# Backwards-compatible name for scripts or local notebooks written before M2 W5.
rule_score = score_case


def _default_password() -> str:
    env_pw = os.environ.get("ONCALL_EVAL_PASSWORD") or os.environ.get("AUTH_PASSWORD")
    if env_pw:
        return env_pw
    once = Path("logs/.pilot_pass")
    if once.exists():
        return once.read_text(encoding="utf-8").strip()
    return "admin"


def load_cases(only=None, suite: str = "minimal"):
    by_id = {}
    for line in CASES_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        c = json.loads(line)
        by_id[c["id"]] = c
    if only is not None:
        ids = [i for i in only if i in by_id]
    elif suite in {"extended"}:
        ids = [i for i in EXTENDED_IDS if i in by_id]
    elif suite in {"full", "all"}:
        # Prefer stable FULL_IDS order; append any extra cases from the file.
        ordered = [i for i in FULL_IDS if i in by_id]
        extras = [i for i in by_id.keys() if i not in set(ordered)]
        ids = ordered + extras
    else:
        ids = [i for i in MINIMAL_IDS if i in by_id]
    return [by_id[i] for i in ids if i in by_id]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", action="append")
    parser.add_argument(
        "--suite",
        choices=["minimal", "extended", "full", "all"],
        default="minimal",
        help="minimal=10, extended>=18, full/all=23 ordered oncall suite",
    )
    parser.add_argument("--base", default=DEFAULT_BASE)
    parser.add_argument("--user", default=os.environ.get("ONCALL_EVAL_USER", "admin"))
    parser.add_argument("--password", default=_default_password())
    parser.add_argument("--timeout-extra", type=float, default=90.0)
    parser.add_argument("--inter-case-sleep", type=float, default=3.0)
    args = parser.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    base = args.base
    token = login(base, args.user, args.password)
    only = set(args.case) if args.case else None
    cases = load_cases(only, suite=args.suite)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    suite_tag = args.suite if only is None else "selected"
    out_csv = OUT_DIR / f"oncall_{suite_tag}_{stamp}.csv"
    rows = []
    print(f"running {len(cases)} cases ({suite_tag}) against {base} ...")
    for idx, case in enumerate(cases):
        if idx and args.inter_case_sleep > 0:
            time.sleep(args.inter_case_sleep)
        cid = case["id"]
        q = case["user_question"]
        sid = f"eval-{cid}-{int(time.time())}"
        err = None
        latency = 0.0
        summary = {}
        score = {}
        try:
            if cid == "M1-two-turn" or case.get("setup", {}).get("multi_turn"):
                text = q.replace("ROUND1:", "")
                chunks = [x.strip() for x in text.split("ROUND2:")]
                q1 = chunks[0].strip()
                q2 = (
                    chunks[1].strip()
                    if len(chunks) > 1
                    else "结合你刚才的结论，还需要看哪些日志？"
                )
                e1, l1, err1 = stream_assistant(base, token, sid, q1, timeout=180)
                e2, l2, err2 = stream_assistant(base, token, sid, q2, timeout=180)
                latency = l1 + l2
                err = err1 or err2
                s1 = summarize(e1)
                s2 = summarize(e2)
                summary = {
                    "route": s2.get("route") or s1.get("route"),
                    "primary_route": s2.get("primary_route")
                    or s1.get("primary_route"),
                    "final_route": s2.get("final_route") or s1.get("final_route"),
                    "answer": (s1.get("answer") or "")
                    + chr(10)
                    + "---"
                    + chr(10)
                    + (s2.get("answer") or ""),
                    "tool_success_count": (s1.get("tool_success_count") or 0)
                    + (s2.get("tool_success_count") or 0),
                    "tool_fail_count": (s1.get("tool_fail_count") or 0)
                    + (s2.get("tool_fail_count") or 0),
                    "tools": list(
                        dict.fromkeys((s1.get("tools") or []) + (s2.get("tools") or []))
                    ),
                    "verify_status": s2.get("verify_status") or s1.get("verify_status"),
                    "gaps": (s1.get("gaps") or []) + (s2.get("gaps") or []),
                    "has_complete": bool(s1.get("has_complete") and s2.get("has_complete")),
                    "event_count": len(e1) + len(e2),
                    "stages": (s1.get("stages") or []) + (s2.get("stages") or []),
                    "re_evidence_rounds": max(
                        int(s1.get("re_evidence_rounds") or 0),
                        int(s2.get("re_evidence_rounds") or 0),
                    ),
                    "replan_times": int(s1.get("replan_times") or 0)
                    + int(s2.get("replan_times") or 0),
                    "steps": max(int(s1.get("steps") or 0), int(s2.get("steps") or 0)),
                }
                a2 = s2.get("answer") or ""
                if not any(
                    w in a2
                    for w in [
                        "刚才",
                        "前述",
                        "之前",
                        "初步",
                        "结合",
                        "如上",
                        "前面",
                        "checkout",
                        "CPU",
                        "cpu",
                        "告警",
                    ]
                ):
                    err = err or "second_turn_may_not_refer_prior"
            else:
                timeout = float((case.get("expected") or {}).get("max_latency_seconds") or 120) + float(args.timeout_extra)
                setup = case.get("setup") or {}
                events, latency, err = stream_assistant(
                    base,
                    token,
                    sid,
                    q,
                    timeout=timeout,
                    simulate=setup.get("simulate"),
                    prefer_parallel=setup.get("prefer_parallel"),
                )
                summary = summarize(events)
            score = rule_score(case, summary, latency, err)
            err = score.get("error") or err
        except Exception as exc:  # noqa: BLE001
            err = f"runner:{type(exc).__name__}:{exc}"
            score = {"score": 0, "safety_dim": 0, "passed": False}
            summary = {
                "route": "",
                "primary_route": "",
                "final_route": "",
                "answer": "",
                "tool_success_count": 0,
                "tool_fail_count": 0,
                "tools": [],
                "has_complete": False,
                "re_evidence_rounds": 0,
                "replan_times": 0,
                "steps": 0,
            }
        row = {
            "case_id": cid,
            "title": case.get("title"),
            "session_id": sid,
            "route": summary.get("route"),
            "primary_route": summary.get("primary_route"),
            "final_route": summary.get("final_route"),
            "latency_s": round(latency, 2),
            "latency_ms": int(round(latency * 1000)),
            "steps": summary.get("steps"),
            "tool_success_count": summary.get("tool_success_count"),
            "tool_fail_count": summary.get("tool_fail_count"),
            "tool_count": len(summary.get("tools") or []),
            "tools": "|".join(summary.get("tools") or []),
            "verify_status": summary.get("verify_status"),
            "re_evidence_rounds": summary.get("re_evidence_rounds"),
            "replan_times": summary.get("replan_times"),
            "answer_chars": len(summary.get("answer") or ""),
            "has_complete": summary.get("has_complete"),
            "score": score.get("score"),
            "safety_dim": score.get("safety_dim"),
            "passed": score.get("passed"),
            "error": err or "",
            "answer_preview": (summary.get("answer") or "")[:240].replace(chr(10), " "),
        }
        rows.append(row)
        print(
            f"[{cid}] pass={row['passed']} score={row['score']} "
            f"route={row['route']} lat={row['latency_s']}s "
            f"re={row['re_evidence_rounds']} rp={row['replan_times']} err={row['error']}"
        )
    with out_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    lats = sorted(r["latency_s"] for r in rows)
    summary_doc = {
        "stamp": stamp,
        "suite": suite_tag,
        "total": len(rows),
        "passed": sum(1 for r in rows if r["passed"]),
        "core_pass": sum(
            1 for r in rows if str(r["case_id"]).startswith("S") and r["passed"]
        ),
        "must_pass": {
            "N1": next(
                (r["passed"] for r in rows if r["case_id"] == "N1-no-datasource"), False
            ),
            "N3": next(
                (r["passed"] for r in rows if r["case_id"] == "N3-no-remediation"), False
            ),
            "M1": next(
                (r["passed"] for r in rows if r["case_id"] == "M1-two-turn"), False
            ),
        },
        "p50": lats[len(lats) // 2] if lats else None,
        "p95": lats[max(0, int(len(lats) * 0.95) - 1)] if lats else None,
        "complete_rate": sum(1 for r in rows if r["has_complete"]) / len(rows)
        if rows
        else 0,
        "re_evidence_trigger_rate": (
            sum(1 for r in rows if int(r.get("re_evidence_rounds") or 0) > 0) / len(rows)
            if rows
            else 0
        ),
        "replan_trigger_rate": (
            sum(1 for r in rows if int(r.get("replan_times") or 0) > 0) / len(rows)
            if rows
            else 0
        ),
        "rows": rows,
    }
    out_json = OUT_DIR / f"oncall_{suite_tag}_{stamp}.json"
    out_json.write_text(
        json.dumps(summary_doc, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("wrote", out_csv)
    print("wrote", out_json)
    print(
        json.dumps(
            {k: summary_doc[k] for k in summary_doc if k != "rows"},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
