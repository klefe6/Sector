"""Futures dashboard data models — Phase 1."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class FutureProduct:
    symbol: str
    name: str
    asset_class: str
    point_value: Optional[float] = None
    tick_size: Optional[float] = None
    micro_parent: Optional[str] = None
    notes: str = ""
