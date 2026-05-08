"""
Backward-compatible shim — delegates to sector_engine for live data.

Any existing code importing get_sector_status_map or quadrant_summary
from this module continues to work unchanged.
"""
from __future__ import annotations

from typing import Optional

from sector_engine import (  # noqa: F401 (re-exported)
    SectorStatus,
    get_sector_status_map,
    get_sector_statuses,
    quadrant_summary,
)


def get_sector_status(sector_etf: str) -> Optional[str]:
    """Return the quadrant label for a single sector ETF ticker."""
    ss = get_sector_statuses().get((sector_etf or "").strip().upper())
    return ss.quadrant if ss else None
