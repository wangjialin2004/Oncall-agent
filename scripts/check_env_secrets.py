#!/usr/bin/env python3
"""Best-effort env/secret checklist for pilot machines (M3 W9).

Exit codes:
  0 — no hard failures (warnings allowed)
  1 — hard failures (missing LLM key, etc.)
  2 — strict mode: also fail on default/dev secrets
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _load_dotenv(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    if not path.exists():
        return data
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            data[key] = val
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description="Check pilot env/secrets")
    parser.add_argument("--strict", action="store_true", help="fail on default secrets")
    parser.add_argument("--env-file", default=".env")
    args = parser.parse_args()

    file_vals = _load_dotenv(Path(args.env_file))

    def get(name: str) -> str:
        return (os.environ.get(name) or file_vals.get(name) or "").strip()

    hard: list[str] = []
    warn: list[str] = []

    llm = get("LLM_API_KEY") or get("DASHSCOPE_API_KEY")
    if not llm or llm.startswith("get.env"):
        hard.append("LLM_API_KEY / DASHSCOPE_API_KEY missing or placeholder")

    secret = get("AUTH_TOKEN_SECRET")
    if not secret:
        hard.append("AUTH_TOKEN_SECRET empty")
    elif secret in {"dev-auth-token-secret", "replace-with-a-local-random-secret"}:
        msg = f"AUTH_TOKEN_SECRET looks like a default: {secret!r}"
        (hard if args.strict else warn).append(msg)

    users = get("AUTH_USERS")
    if not users:
        warn.append("AUTH_USERS empty — login will fail")
    elif "admin:admin" in users or users == "admin:admin":
        msg = "AUTH_USERS still contains admin:admin"
        (hard if args.strict else warn).append(msg)

    if not get("PROMETHEUS_BASE_URL"):
        warn.append("PROMETHEUS_BASE_URL unset (metric path may be weak)")

    print("check_env_secrets")
    for w in warn:
        print(f"  WARN  {w}")
    for h in hard:
        print(f"  FAIL  {h}")
    if not warn and not hard:
        print("  OK    no issues detected")
    if hard:
        return 1 if not args.strict else 2
    if args.strict and warn:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
