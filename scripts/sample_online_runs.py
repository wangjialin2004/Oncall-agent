#!/usr/bin/env python3
"""Sample online / offline runs into a scoring skeleton (M3 W11).

Reads compact harness traces (volumes/traces/*.json) and/or eval result JSON
files, then writes CSV + Markdown tables for human online scoring.

Never writes back to production memory / distill APIs.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from pathlib import Path
from typing import Any


COLUMNS = [
    "run_id",
    "session",
    "route",
    "latency_s",
    "pass",
    "correctness",
    "evidence",
    "safety",
    "latency_ux",
    "hallucinate_or_overclaim",
    "hitl_false_executed",
    "notes",
    "scorer",
    "date",
    "source_path",
]


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _row_from_trace(path: Path, data: dict[str, Any]) -> dict[str, str]:
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    verify = data.get("verify") if isinstance(data.get("verify"), dict) else {}
    session = str(data.get("session_id") or path.stem)
    notes_bits = []
    if usage:
        notes_bits.append(
            "tokens="
            + ",".join(f"{k}:{usage.get(k)}" for k in ("prompt_tokens", "completion_tokens", "total_tokens") if k in usage)
        )
    if verify.get("status"):
        notes_bits.append(f"verify={verify.get('status')}")
    if data.get("re_evidence_rounds"):
        notes_bits.append(f"re_evidence={data.get('re_evidence_rounds')}")
    return {
        "run_id": str(data.get("trace_id") or session),
        "session": session,
        "route": str(data.get("route") or ""),
        "latency_s": "",  # not always in trace; scorer may fill from eval
        "pass": "",
        "correctness": "",
        "evidence": "",
        "safety": "",
        "latency_ux": "",
        "hallucinate_or_overclaim": "",
        "hitl_false_executed": "",
        "notes": "; ".join(notes_bits),
        "scorer": "",
        "date": "",
        "source_path": str(path),
    }


def _row_from_eval(path: Path, data: dict[str, Any]) -> dict[str, str]:
    case_id = str(data.get("case_id") or data.get("id") or path.stem)
    latency = data.get("latency_seconds") or data.get("latency_s") or data.get("duration_seconds")
    return {
        "run_id": case_id,
        "session": str(data.get("session_id") or ""),
        "route": str(data.get("route") or data.get("focus_route") or ""),
        "latency_s": "" if latency is None else str(latency),
        "pass": str(data.get("passed") if data.get("passed") is not None else data.get("pass") or ""),
        "correctness": "",
        "evidence": "",
        "safety": "",
        "latency_ux": "",
        "hallucinate_or_overclaim": "",
        "hitl_false_executed": "",
        "notes": str(data.get("summary") or data.get("error") or "")[:200],
        "scorer": "",
        "date": "",
        "source_path": str(path),
    }


def collect_rows(
    *,
    traces_dir: Path | None,
    eval_dir: Path | None,
    n: int,
    seed: int,
) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    if traces_dir and traces_dir.is_dir():
        for path in sorted(traces_dir.glob("*.json")):
            data = _load_json(path)
            if data is None:
                continue
            candidates.append(_row_from_trace(path, data))
    if eval_dir and eval_dir.is_dir():
        for path in sorted(eval_dir.rglob("*.json")):
            data = _load_json(path)
            if data is None:
                continue
            # Skip huge aggregated files without case identity when possible.
            if "case_id" in data or "session_id" in data or "route" in data:
                candidates.append(_row_from_eval(path, data))
    if not candidates:
        return []
    rng = random.Random(seed)
    if n > 0 and len(candidates) > n:
        return rng.sample(candidates, n)
    rng.shuffle(candidates)
    return candidates[:n] if n > 0 else candidates


def blank_rows(n: int) -> list[dict[str, str]]:
    rows = []
    for i in range(max(n, 1)):
        rows.append({col: "" for col in COLUMNS})
        rows[-1]["run_id"] = f"sample-{i + 1}"
        rows[-1]["notes"] = "dry-run skeleton — fill after live sample"
    return rows


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in COLUMNS})


def write_md(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = "| " + " | ".join(COLUMNS) + " |"
    sep = "| " + " | ".join("---" for _ in COLUMNS) + " |"
    lines = [
        "# Online sample skeleton",
        "",
        "Score with [online-eval-template.md](../online-eval-template.md).",
        "",
        header,
        sep,
    ]
    for row in rows:
        cells = [str(row.get(c, "")).replace("|", "/") for c in COLUMNS]
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sample runs for online human scoring")
    parser.add_argument("--from-traces", default="volumes/traces", help="Trace JSON directory")
    parser.add_argument("--from-eval", default="", help="Optional eval results directory")
    parser.add_argument("--n", type=int, default=5, help="Sample size")
    parser.add_argument("--seed", type=int, default=42, help="RNG seed")
    parser.add_argument(
        "--out",
        default="docs/pilot/samples",
        help="Output directory for CSV/MD",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Emit blank skeleton rows (no filesystem sample required)",
    )
    args = parser.parse_args(argv)

    if args.dry_run:
        rows = blank_rows(args.n)
    else:
        traces = Path(args.from_traces) if args.from_traces else None
        eval_dir = Path(args.from_eval) if args.from_eval else None
        rows = collect_rows(traces_dir=traces, eval_dir=eval_dir, n=args.n, seed=args.seed)
        if not rows:
            print(
                "No traces/eval rows found; writing dry-run skeleton instead.",
                file=sys.stderr,
            )
            rows = blank_rows(args.n)

    out_dir = Path(args.out)
    csv_path = out_dir / "online_sample.csv"
    md_path = out_dir / "online_sample.md"
    write_csv(csv_path, rows)
    write_md(md_path, rows)
    print(f"Wrote {len(rows)} rows → {csv_path}")
    print(f"Wrote markdown → {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
