"""
export_rrg_sectors.py — Export current RRG sector scores to JSON for the Warren AI runner.

Usage:
    python3 export_rrg_sectors.py

Writes:
    warren_ai_daily/output/rrg_sectors.json

The Warren AI daily runner reads this before building Prompt 02, so that
Leading/Improving sectors identified by the RRG model are always included
in the candidate scan, even if Warren AI's step 01 did not select them.

No market cap filtering. No trade recommendations. Read-only, factual output.
"""
import json
import os
import sys

# Resolve paths relative to the project root (two levels up from this script)
SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", ".."))
OUTPUT_DIR   = os.path.join(SCRIPT_DIR, "..", "output")

sys.path.insert(0, PROJECT_ROOT)

from sector_engine import get_sector_statuses

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out_path = os.path.join(OUTPUT_DIR, "rrg_sectors.json")

    try:
        statuses = get_sector_statuses()
    except Exception as exc:
        print(f"[export_rrg_sectors] ERROR fetching sector statuses: {exc}", file=sys.stderr)
        sys.exit(1)

    sectors = []
    for etf, s in statuses.items():
        sectors.append({
            "etf":         etf,
            "quadrant":    s.quadrant,
            "direction":   s.direction,
            "accel_score": round(s.accel_score, 1) if s.sufficient_data else 0,
            "sufficient_data": s.sufficient_data,
        })

    # Sort by accel_score descending
    sectors.sort(key=lambda x: x["accel_score"], reverse=True)

    # Classify top sectors (Leading/Improving with score > 0)
    mandatory = [
        s for s in sectors
        if s["quadrant"] in ("Leading", "Improving") and s["accel_score"] > 0
    ]
    top_by_score = sectors[:4]

    payload = {
        "generated_at": __import__("datetime").datetime.now().isoformat(),
        "all_sectors":  sectors,
        "mandatory_sectors": [
            {"etf": s["etf"], "quadrant": s["quadrant"],
             "direction": s["direction"], "score": s["accel_score"]}
            for s in mandatory
        ],
        "top4_by_score": [
            {"etf": s["etf"], "quadrant": s["quadrant"],
             "direction": s["direction"], "score": s["accel_score"]}
            for s in top_by_score
        ],
    }

    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)

    print(f"[export_rrg_sectors] Written to {out_path}")
    print(f"  Mandatory (Leading/Improving, score>0): "
          f"{[s['etf'] for s in mandatory]}")
    print(f"  Top 4 by score: {[s['etf'] for s in top_by_score]}")

if __name__ == "__main__":
    main()
