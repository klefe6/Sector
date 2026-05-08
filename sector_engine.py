"""
sector_engine.py — live sector RRG status with directional acceleration.

Computes RS-Ratio and RS-Momentum using the same WMA formula as app_rrg.py,
but runs independently (no shared imports) so the Decision Cockpit can use it
without loading the RRG Dash app.

Direction is computed by comparing the current bar to the average of the
prior two bars, giving a sense of whether a sector is accelerating or fading
inside its current quadrant.

Results are cached in-process for CACHE_TTL seconds (default 1 hour) to
avoid repeated yfinance calls during a session.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf

# ---------------------------------------------------------------------------
# Config — mirrors app_rrg.py defaults
# ---------------------------------------------------------------------------

BENCHMARK  = "SPY"
SECTORS    = ["XLV", "XLRE", "XLB", "XLU", "XLP", "XLF", "XLY", "XLE", "XLI", "XLC", "XLK"]
FREQ       = "W"
SHORT_WMA  = 10
LONG_WMA   = 30
START      = "2015-01-01"

# Delta threshold: changes smaller than this magnitude in both dimensions
# are classified as "stalling" rather than strengthening/deteriorating.
DELTA_THRESHOLD: float = 0.15

# Acceleration score scale: a combined delta of ±ACCEL_SCALE maps to ±100.
# With weekly data, typical deltas are ±0.2–1.5, so 1.5 is a reasonable scale.
ACCEL_SCALE: float = 1.5

CACHE_TTL: int = 3600  # seconds (1 hour)

_cache: dict[str, "SectorStatus"] = {}
_cache_ts: float = 0.0


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------

@dataclass
class SectorStatus:
    ticker:          str
    quadrant:        str    # Leading | Improving | Weakening | Lagging | Unknown
    direction:       str    # "↑ strengthening" | "↓ deteriorating" | etc.
    decision_label:  str    # Combined human-readable label
    current_rsr:     float  # current RS-Ratio
    current_rsm:     float  # current RS-Momentum
    prev2_avg_rsr:   float  # average of the 2 bars before the current bar
    prev2_avg_rsm:   float
    delta_rsr:       float  # current_rsr − prev2_avg_rsr
    delta_rsm:       float  # current_rsm − prev2_avg_rsm
    accel_score:     float  # −100 to +100
    sufficient_data: bool   # False when fewer than 3 bars are available


# ---------------------------------------------------------------------------
# RRG math (mirrors app_rrg.py — kept private to avoid shared state)
# ---------------------------------------------------------------------------

def _wma(series: pd.Series, n: int) -> pd.Series:
    w = np.arange(1, n + 1, dtype=float)
    return series.rolling(n).apply(lambda x: float(np.dot(x, w) / w.sum()), raw=True)


def _load_prices() -> pd.DataFrame:
    tickers = SECTORS + [BENCHMARK]
    raw = yf.download(tickers, start=START, progress=False,
                      auto_adjust=False, actions=False)
    df = raw["Adj Close"] if isinstance(raw.columns, pd.MultiIndex) else raw
    df = df.resample("W-FRI").last()
    return df.dropna(how="all")


def _calc_rrg(px: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    rs    = px.div(px[BENCHMARK], axis=0)
    rs_s  = rs.apply(lambda s: _wma(s, SHORT_WMA))
    rs_l  = rs.apply(lambda s: _wma(s, LONG_WMA))
    rsr   = 100 + 100 * (rs_s / rs_l - 1)
    mom_w = rsr.apply(lambda s: _wma(s, SHORT_WMA))
    rsm   = 100 + 100 * (rsr / mom_w - 1)
    rsr   = rsr.drop(columns=[BENCHMARK], errors="ignore")
    rsm   = rsm.drop(columns=[BENCHMARK], errors="ignore")
    return rsr, rsm


# ---------------------------------------------------------------------------
# Classification helpers (all public so app_rrg.py can import them)
# ---------------------------------------------------------------------------

def classify_quadrant(rsr: float, rsm: float) -> str:
    if rsr >= 100 and rsm >= 100: return "Leading"
    if rsr >= 100 and rsm < 100:  return "Weakening"
    if rsr < 100  and rsm < 100:  return "Lagging"
    return "Improving"


def classify_direction(d_rsr: float, d_rsm: float,
                       thresh: float = DELTA_THRESHOLD) -> str:
    """
    Map (delta_rsr, delta_rsm) to a direction string.
    Both deltas must exceed the threshold in the same direction for a clear
    classification; mixed signals get an arrow description.
    """
    if math.isnan(d_rsr) or math.isnan(d_rsm):
        return "insufficient history"
    up_r = d_rsr >  thresh
    dn_r = d_rsr < -thresh
    up_m = d_rsm >  thresh
    dn_m = d_rsm < -thresh
    if up_r and up_m: return "↑ strengthening"
    if dn_r and dn_m: return "↓ deteriorating"
    if up_r and dn_m: return "↗ RS↑ mom↓"
    if dn_r and up_m: return "↘ RS↓ mom↑"
    return "→ stalling"


# Lookup table: (quadrant, direction) → human decision label
_DECISION_LABELS: dict[tuple[str, str], str] = {
    ("Leading",   "↑ strengthening"): "Leading, strengthening",
    ("Leading",   "↓ deteriorating"): "Leading, losing momentum",
    ("Leading",   "↗ RS↑ mom↓"):      "Leading, momentum fading",
    ("Leading",   "↘ RS↓ mom↑"):      "Leading, RS slipping",
    ("Leading",   "→ stalling"):       "Leading, stalling",
    ("Weakening", "↑ strengthening"): "Weakening but recovering",
    ("Weakening", "↓ deteriorating"): "Weakening, deteriorating",
    ("Weakening", "↗ RS↑ mom↓"):      "Weakening, momentum fading",
    ("Weakening", "↘ RS↓ mom↑"):      "Weakening, momentum recovering",
    ("Weakening", "→ stalling"):       "Weakening, stalling",
    ("Lagging",   "↑ strengthening"): "Lagging but strengthening",
    ("Lagging",   "↓ deteriorating"): "Lagging, deteriorating",
    ("Lagging",   "↗ RS↑ mom↓"):      "Lagging, slight RS recovery",
    ("Lagging",   "↘ RS↓ mom↑"):      "Lagging, momentum building",
    ("Lagging",   "→ stalling"):       "Lagging, stalling",
    ("Improving", "↑ strengthening"): "Improving, accelerating",
    ("Improving", "↓ deteriorating"): "Improving but fading",
    ("Improving", "↗ RS↑ mom↓"):      "Improving, stalling",
    ("Improving", "↘ RS↓ mom↑"):      "Improving, momentum building",
    ("Improving", "→ stalling"):       "Improving, stalling",
}


def build_decision_label(quadrant_str: str, direction_str: str) -> str:
    return _DECISION_LABELS.get(
        (quadrant_str, direction_str),
        f"{quadrant_str}, {direction_str}",
    )


def compute_accel_score(d_rsr: float, d_rsm: float,
                        scale: float = ACCEL_SCALE) -> float:
    """
    Acceleration score in [−100, +100].

    Formula: average(delta_rsr, delta_rsm) / scale × 100, clipped to ±100.

    A ``scale`` of 1.5 means a combined weekly delta of ±1.5 maps to ±100.
    Positive = sector improving relative to its recent RRG path.
    Negative = sector deteriorating.
    """
    if math.isnan(d_rsr) or math.isnan(d_rsm):
        return 0.0
    raw = (d_rsr + d_rsm) / 2.0
    return round(float(np.clip(raw / scale * 100, -100, 100)), 1)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_sector_statuses(force_refresh: bool = False) -> dict[str, SectorStatus]:
    """
    Return ``{ticker: SectorStatus}`` for all sector ETFs.
    Results are cached in-process for ``CACHE_TTL`` seconds.
    """
    global _cache, _cache_ts
    now = time.monotonic()
    if not force_refresh and _cache and (now - _cache_ts) < CACHE_TTL:
        return _cache

    try:
        px = _load_prices()
        rsr_full, rsm_full = _calc_rrg(px)
    except Exception as exc:
        print(f"[sector_engine] yfinance error: {exc!r} — using placeholder data.")
        return _placeholder_statuses()

    result: dict[str, SectorStatus] = {}
    for t in SECTORS:
        if t not in rsr_full.columns or t not in rsm_full.columns:
            continue
        rsr_col = rsr_full[t].dropna()
        rsm_col = rsm_full[t].dropna()

        if len(rsr_col) < 3:
            result[t] = SectorStatus(
                ticker=t, quadrant="Unknown",
                direction="insufficient history",
                decision_label="Insufficient history",
                current_rsr=float("nan"), current_rsm=float("nan"),
                prev2_avg_rsr=float("nan"), prev2_avg_rsm=float("nan"),
                delta_rsr=float("nan"), delta_rsm=float("nan"),
                accel_score=0.0, sufficient_data=False,
            )
            continue

        cur_rsr  = float(rsr_col.iloc[-1])
        cur_rsm  = float(rsm_col.iloc[-1])
        p2_rsr   = float(rsr_col.iloc[-3:-1].mean())
        p2_rsm   = float(rsm_col.iloc[-3:-1].mean())
        d_rsr    = round(cur_rsr - p2_rsr, 3)
        d_rsm    = round(cur_rsm - p2_rsm, 3)

        quad  = classify_quadrant(cur_rsr, cur_rsm)
        dirn  = classify_direction(d_rsr, d_rsm)
        label = build_decision_label(quad, dirn)
        score = compute_accel_score(d_rsr, d_rsm)

        result[t] = SectorStatus(
            ticker=t,
            quadrant=quad,
            direction=dirn,
            decision_label=label,
            current_rsr=round(cur_rsr, 2),
            current_rsm=round(cur_rsm, 2),
            prev2_avg_rsr=round(p2_rsr, 2),
            prev2_avg_rsm=round(p2_rsm, 2),
            delta_rsr=d_rsr,
            delta_rsm=d_rsm,
            accel_score=score,
            sufficient_data=True,
        )

    _cache    = result
    _cache_ts = now
    return result


def get_sector_status_map() -> dict[str, str]:
    """Backward-compatible: ``{ticker: quadrant_string}``."""
    return {t: s.quadrant for t, s in get_sector_statuses().items()}


def quadrant_summary() -> dict[str, list[str]]:
    """Backward-compatible: groups sectors by quadrant."""
    summary: dict[str, list[str]] = {
        "Leading": [], "Improving": [], "Weakening": [], "Lagging": [],
    }
    for t, s in get_sector_statuses().items():
        if s.quadrant in summary:
            summary[s.quadrant].append(t)
    return summary


def _placeholder_statuses() -> dict[str, SectorStatus]:
    """Fallback when yfinance is unavailable."""
    FALLBACK = {
        "XLK": "Leading",  "XLF": "Leading",  "XLC": "Leading",
        "XLI": "Improving", "XLY": "Improving",
        "XLV": "Weakening", "XLP": "Weakening", "XLB": "Weakening",
        "XLRE": "Lagging",  "XLU": "Lagging",   "XLE": "Lagging",
    }
    return {
        t: SectorStatus(
            ticker=t, quadrant=q, direction="→ stalling",
            decision_label=f"{q}, stalling (fallback)",
            current_rsr=float("nan"), current_rsm=float("nan"),
            prev2_avg_rsr=float("nan"), prev2_avg_rsm=float("nan"),
            delta_rsr=0.0, delta_rsm=0.0, accel_score=0.0,
            sufficient_data=False,
        )
        for t, q in FALLBACK.items()
    }
