#!/usr/bin/env python3
"""
Export Decision Cockpit positions (in_position + trimming_zone) to
warren_ai_daily/config/current_portfolio.json for Warren AI portfolio prompts.

Run from anywhere:
  python3 warren_ai_daily/scripts/export_portfolio_from_db.py

Or via npm:
  npm run export-portfolio
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date

# Repo root (rrg/) — two levels up from this file: scripts -> warren_ai_daily -> rrg
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_RRG_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", ".."))
sys.path.insert(0, _RRG_ROOT)

import data as db  # noqa: E402

_OUT_PATH = os.path.join(_SCRIPT_DIR, "..", "config", "current_portfolio.json")


def main() -> int:
    db.init_db()
    rows = db.get_positions(db.ACTIVE_STATUSES)
    positions = []
    for p in rows:
        positions.append(
            {
                "ticker": (p.get("ticker") or "").strip().upper(),
                "company": "",
                "side": "Long",
                "quantity": p.get("shares"),
                "avg_cost": p.get("cost_basis"),
                "sector": (p.get("sector") or "").strip() or None,
                "thesis": (p.get("manual_fundamental_note") or "").strip()[:2000],
                "notes": (p.get("notes") or "").strip()[:2000],
            }
        )
    payload = {
        "updated_at": date.today().isoformat(),
        "source": "positions.db",
        "positions": positions,
    }
    os.makedirs(os.path.dirname(_OUT_PATH), exist_ok=True)
    with open(_OUT_PATH, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")
    print(f"[export-portfolio] Wrote {len(positions)} position(s) to {_OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
