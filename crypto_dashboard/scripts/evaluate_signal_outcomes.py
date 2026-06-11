#!/usr/bin/env python3
"""Manually evaluate logged crypto alert outcomes (offline, not scheduled).

Reads ``output/signal_log/signal_log.jsonl`` and appends results to
``output/signal_log/signal_outcomes.jsonl``.

Usage:
    python3 evaluate_signal_outcomes.py
    python3 evaluate_signal_outcomes.py --force
    python3 evaluate_signal_outcomes.py --dry-run
    python3 evaluate_signal_outcomes.py --swing-hours 24 --scalp-hours 4
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_CRYPTO_DIR = _SCRIPT_DIR.parent
sys.path.insert(0, str(_CRYPTO_DIR))

import signal_outcome_eval as soe  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Evaluate logged crypto alert outcomes (manual, offline)",
    )
    ap.add_argument(
        "--signal-log",
        type=Path,
        default=soe.DEFAULT_SIGNAL_LOG_PATH,
        help="Path to signal_log.jsonl",
    )
    ap.add_argument(
        "--outcomes",
        type=Path,
        default=soe.DEFAULT_OUTCOMES_PATH,
        help="Path to signal_outcomes.jsonl",
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="Re-evaluate signals even if an outcome row already exists",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be evaluated without fetching candles or writing",
    )
    ap.add_argument(
        "--swing-hours",
        type=float,
        default=soe.DEFAULT_MIN_AGE_HOURS["swing"],
        help="Minimum age (hours) before evaluating swing alerts",
    )
    ap.add_argument(
        "--scalp-hours",
        type=float,
        default=soe.DEFAULT_MIN_AGE_HOURS["scalp"],
        help="Minimum age (hours) before evaluating scalp alerts",
    )
    args = ap.parse_args()

    soe.DEFAULT_MIN_AGE_HOURS["swing"] = max(0.0, float(args.swing_hours))
    soe.DEFAULT_MIN_AGE_HOURS["scalp"] = max(0.0, float(args.scalp_hours))

    if not args.signal_log.is_file():
        print(f"No signal log: {args.signal_log}")
        return 0

    now = datetime.now(timezone.utc)
    evaluated = set() if args.force else soe.load_evaluated_signal_ids(args.outcomes)

    pending: list[dict] = []
    for row in soe.load_jsonl_rows(args.signal_log):
        if row.get("record_type") != "alert_sent" or row.get("is_preview"):
            continue
        sid = str(row.get("signal_id") or "")
        if not sid or (sid in evaluated and not args.force):
            continue
        if soe.is_old_enough(row, now=now):
            pending.append(row)

    print(f"Signal log: {args.signal_log}")
    print(f"Outcomes:   {args.outcomes}")
    print(f"Pending eligible alerts: {len(pending)}")
    if args.dry_run:
        for r in pending[:20]:
            print(
                f"  - {r.get('signal_id')}  {r.get('symbol')}  "
                f"{r.get('section')}  logged={r.get('logged_at_utc')}"
            )
        if len(pending) > 20:
            print(f"  ... and {len(pending) - 20} more")
        return 0

    stats = soe.evaluate_pending_signals(
        signal_log_path=args.signal_log,
        outcomes_path=args.outcomes,
        force=args.force,
        fetch_candles=True,
        now=now,
    )
    print(json.dumps(stats, indent=2))
    return 0 if stats.get("errors", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
