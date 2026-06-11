#!/usr/bin/env python3
"""Print recent crypto signal log and outcome rows (read-only).

Usage:
    python3 inspect_signal_log.py
    python3 inspect_signal_log.py --limit 10
    python3 inspect_signal_log.py --outcomes
    python3 inspect_signal_log.py --outcomes --outcome-limit 3
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_LOG_PATH = _SCRIPT_DIR.parent / "output" / "signal_log" / "signal_log.jsonl"
_OUTCOMES_PATH = _SCRIPT_DIR.parent / "output" / "signal_log" / "signal_outcomes.jsonl"


def _read_last_lines(path: Path, limit: int) -> list[str]:
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        print(f"Could not read {path}: {exc}", file=sys.stderr)
        return []
    non_empty = [ln for ln in lines if ln.strip()]
    return non_empty[-limit:]


def _print_rows(path: Path, rows: list[str], label: str) -> None:
    if not rows:
        print(f"{label}: (empty)\n")
        return
    print(f"{label} — last {len(rows)} row(s) from {path}\n")
    for i, line in enumerate(rows, 1):
        try:
            obj = json.loads(line)
            print(f"--- {label} row {i} ---")
            print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))
            print()
        except json.JSONDecodeError as exc:
            print(f"--- {label} row {i} (invalid JSON) ---")
            print(line[:500])
            print(f"parse error: {exc}\n")


def main() -> int:
    ap = argparse.ArgumentParser(description="Inspect crypto signal feedback logs")
    ap.add_argument(
        "--limit", "-n", type=int, default=5,
        help="Number of recent signal log rows (default: 5)",
    )
    ap.add_argument(
        "--path", type=Path, default=_LOG_PATH,
        help="Path to signal_log.jsonl",
    )
    ap.add_argument(
        "--outcomes", action="store_true",
        help="Also print recent outcome rows",
    )
    ap.add_argument(
        "--outcome-limit", type=int, default=5,
        help="Number of recent outcome rows when --outcomes is set",
    )
    ap.add_argument(
        "--outcomes-path", type=Path, default=_OUTCOMES_PATH,
        help="Path to signal_outcomes.jsonl",
    )
    args = ap.parse_args()
    limit = max(1, int(args.limit))

    if not args.path.is_file():
        print(f"No signal log yet: {args.path}")
    else:
        rows = _read_last_lines(args.path, limit)
        if not rows:
            print(f"Signal log exists but is empty: {args.path}")
        else:
            _print_rows(args.path, rows, "signal_log")

    if args.outcomes:
        if not args.outcomes_path.is_file():
            print(f"No outcomes log yet: {args.outcomes_path}")
        else:
            o_rows = _read_last_lines(args.outcomes_path, max(1, int(args.outcome_limit)))
            _print_rows(args.outcomes_path, o_rows, "outcomes")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
