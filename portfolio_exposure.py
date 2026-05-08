"""
portfolio_exposure.py — Pure-function helpers for Dashboard sector/concentration views.

All functions accept ``active`` (list[PositionResult]) and return plain dicts/lists.
No Dash imports. No database writes.

────────────────────────────────────────────────────────────────────────────────
Baseline mode system
────────────────────────────────────────────────────────────────────────────────

A "baseline mode" decides what the *target sector weight* is when the user has
not configured per-position ``target_weight`` fields.

Supported modes
───────────────
  "equal_weight"      (current default)
      100% divided equally across however many sectors are currently held.
      E.g.  4 sectors → each = 25.0%.

  "manual_targets"
      Only use ``target_weight`` values stored on positions; no auto-fill.
      Sectors without a target show target_pct = None and diff_pct = None.

Future modes (not yet built)
─────────────────────────────
  "spy_weight"
      Mirror the current S&P 500 sector weights (fetched from an external
      data source).  Add a `_SPY_WEIGHTS` dict or fetch call here.

  "custom_model"
      Load a user-supplied JSON file / DB table with explicit target %.

The active mode is passed into ``compute_sector_alignment`` as the
``baseline_mode`` parameter (default "equal_weight").  The rest of the app
doesn't need to know about modes — it just reads ``AlignmentRow.target_source``.

────────────────────────────────────────────────────────────────────────────────
Rebalancing workflow scaffold
────────────────────────────────────────────────────────────────────────────────

``AlignmentRow`` already carries everything needed for the NEXT rebalancing step:

    sector              → which sector bucket
    weight_pct          → current allocation (%)
    target_pct          → desired allocation (%)
    diff_pct            → actual - target  (+ve = overweight, -ve = underweight)
    portfolio_value ($) → multiply diff_pct/100 × total_value → dollar gap
    verdict             → Reduce / Overweight (on trend) / Add candidate / Aligned

A future ``compute_rebalancing_plan(align_rows, total_value)`` function would:
    1.  Filter rows where abs(diff_pct) > min_threshold (e.g. 2%)
    2.  Compute implied_dollar_gap = diff_pct / 100 × total_value
    3.  For overweight rows → list trim candidates (from active positions)
    4.  For underweight rows → list add candidates (from watchlist)
    5.  Respect conviction and sector direction before suggesting action
    6.  Return a list of RebalancingAction objects (no trade recommendations —
        just the raw numbers and candidate tickers, decision stays with user)

No trade recommendations are made here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

# Supported baseline modes.
# Extend this type annotation when adding "spy_weight" or "custom_model".
BaselineMode = Literal["equal_weight", "manual_targets"]


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass
class SectorSlice:
    sector:      str
    tickers:     list[str]
    value:       float          # sum of market values ($)
    weight_pct:  float          # % of total portfolio
    n_positions: int


@dataclass
class ConcentrationRow:
    ticker:        str
    sector:        str
    value:         float
    weight_pct:    float
    target_pct:    Optional[float]
    diff_pct:      Optional[float]   # actual − target  (+ve = overweight)
    color:         str               # "green" | "amber" | "red" | "neutral"


@dataclass
class AlignmentRow:
    # ── Identity ────────────────────────────────────────────────────────────
    sector:        str
    quadrant:      str            # from RRG sector_status_map

    # ── Weight accounting (rebalancing scaffold) ──────────────────────────
    weight_pct:    float          # current allocation (%)
    target_pct:    float          # desired allocation (%) — always set
    diff_pct:      float          # weight_pct - target_pct (+ve = overweight)
    # NOTE for rebalancing: implied_dollar_gap = diff_pct / 100 × total_value

    # ── Decision output ──────────────────────────────────────────────────
    verdict:       str            # "Reduce" | "Add candidate" | "Overweight (on trend)" | "Aligned"
    verdict_color: str            # "red" | "blue" | "amber" | "neutral"

    # ── Baseline provenance ───────────────────────────────────────────────
    target_source: str            # "Manual target" | "Auto equal-weight"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pct(v: float, total: float) -> float:
    return round(v / total * 100, 1) if total else 0.0


def _market_value(r) -> Optional[float]:
    """Return market_value from a PositionResult; fall back to weight proxy."""
    mv = getattr(r, "market_value", None)
    if mv is not None:
        return mv
    # Fallback: use stored current_weight as proxy (already a %)
    cw = getattr(r, "current_weight", None)
    return float(cw) if cw else None


def _resolve_target(
    manual_pcts: list[float],
    baseline: float,
    mode: BaselineMode,
) -> tuple[float, str]:
    """
    Return ``(target_pct, source_label)`` for one sector bucket.

    Logic
    ─────
    1.  If any position in this sector has a ``target_weight`` → always use it
        regardless of mode (manual intent always wins).
    2.  Otherwise:
        - "equal_weight"    → use the pre-computed equal-weight baseline.
        - "manual_targets"  → no baseline; caller must handle None.

    When adding "spy_weight":
        Add an ``elif mode == "spy_weight": return spy_targets[sector], "SPY weight"``
        branch in ``compute_sector_alignment`` before calling ``_resolve_target``.
    """
    if manual_pcts:
        return round(sum(manual_pcts), 1), "Manual target"
    if mode == "equal_weight":
        return round(baseline, 1), "Auto equal-weight"
    # "manual_targets" and any future mode without auto-fill → signal missing
    return 0.0, "No target"


# ---------------------------------------------------------------------------
# 1. Sector Allocation
# ---------------------------------------------------------------------------

def compute_sector_exposure(active: list) -> tuple[list[SectorSlice], float]:
    """
    Return (sector_slices, total_portfolio_value).

    Sectors with unknown/empty names are grouped as "Unknown".
    Slices sorted descending by value.
    """
    buckets: dict[str, dict] = {}

    for r in active:
        if getattr(r, "error", None):
            continue
        mv = _market_value(r)
        if mv is None:
            continue
        sector = (getattr(r, "sector", "") or "Unknown").strip().upper()
        if not sector:
            sector = "Unknown"
        b = buckets.setdefault(sector, {"tickers": [], "value": 0.0})
        b["tickers"].append(r.ticker)
        b["value"] += mv

    total = sum(b["value"] for b in buckets.values())

    slices = [
        SectorSlice(
            sector      = s,
            tickers     = b["tickers"],
            value       = round(b["value"]),
            weight_pct  = _pct(b["value"], total),
            n_positions = len(b["tickers"]),
        )
        for s, b in sorted(buckets.items(), key=lambda x: -x[1]["value"])
    ]
    return slices, round(total)


# ---------------------------------------------------------------------------
# 2. Position Concentration
# ---------------------------------------------------------------------------

def compute_concentration(active: list, max_positions: int = 10) -> list[ConcentrationRow]:
    """
    Return up to max_positions ConcentrationRows sorted by weight descending.
    """
    rows_raw = []
    for r in active:
        if getattr(r, "error", None):
            continue
        mv = _market_value(r)
        if mv is None:
            continue
        rows_raw.append((r, mv))

    total = sum(mv for _, mv in rows_raw)
    rows_raw.sort(key=lambda x: -x[1])

    result = []
    for r, mv in rows_raw[:max_positions]:
        w_pct = _pct(mv, total)
        t_pct = getattr(r, "target_weight", None)
        if t_pct is not None:
            try:
                t_pct = float(t_pct)
            except (TypeError, ValueError):
                t_pct = None

        diff = round(w_pct - t_pct, 1) if t_pct is not None else None

        if t_pct is None:
            color = "neutral"
        elif w_pct <= t_pct * 1.0:
            color = "green"
        elif w_pct <= t_pct * 1.25:
            color = "amber"
        else:
            color = "red"

        result.append(ConcentrationRow(
            ticker     = r.ticker,
            sector     = (getattr(r, "sector", "") or "").upper(),
            value      = round(mv),
            weight_pct = w_pct,
            target_pct = t_pct,
            diff_pct   = diff,
            color      = color,
        ))
    return result


# ---------------------------------------------------------------------------
# 3. Sector Alignment vs Regime
# ---------------------------------------------------------------------------

_VERDICT_ORDER = {"Reduce": 0, "Add candidate": 1, "Overweight (on trend)": 2, "Aligned": 3}

# Human-readable description for each mode, shown in the Dashboard sub-header.
BASELINE_DESCRIPTIONS: dict[str, str] = {
    "equal_weight":   "manual targets where available, otherwise equal-weight across held sectors",
    "manual_targets": "manual targets only — sectors without a target show no verdict",
    # Future:
    # "spy_weight":  "manual targets where available, otherwise S&P 500 sector weights",
    # "custom_model":"manual targets where available, otherwise custom allocation model",
}


def compute_sector_alignment(
    active: list,
    sector_status_map: dict[str, str],      # {ETF_ticker: quadrant_string}
    baseline_mode: BaselineMode = "equal_weight",
) -> list[AlignmentRow]:
    """
    One row per sector.  Target weight is always populated (uses baseline when
    no manual ``target_weight`` is set on positions).

    Parameters
    ──────────
    active            : list of PositionResult (or duck-typed objects)
    sector_status_map : {sector_etf: quadrant}  from sector_engine
    baseline_mode     : controls how missing manual targets are filled in
                        (default: "equal_weight")

    Rebalancing hook
    ────────────────
    Every AlignmentRow carries:
        diff_pct  = weight_pct − target_pct
        Multiply by total_portfolio_value / 100 to get the dollar gap.
    The future ``compute_rebalancing_plan`` function will consume these rows
    directly without re-computing anything.
    """
    # ── 1. Aggregate per sector ───────────────────────────────────────────
    buckets: dict[str, dict] = {}
    for r in active:
        if getattr(r, "error", None):
            continue
        mv = _market_value(r)
        if mv is None:
            continue
        sector = (getattr(r, "sector", "") or "Unknown").strip().upper()
        t_raw  = getattr(r, "target_weight", None)
        t_pct: Optional[float] = None
        try:
            t_pct = float(t_raw) if t_raw not in (None, "", 0) else None
        except (TypeError, ValueError):
            pass
        b = buckets.setdefault(sector, {"value": 0.0, "target_pcts": []})
        b["value"] += mv
        if t_pct is not None and t_pct > 0:
            b["target_pcts"].append(t_pct)

    total     = sum(b["value"] for b in buckets.values())
    n_sectors = len(buckets)

    # ── 2. Equal-weight baseline: 100% / N sectors ───────────────────────
    # Only used when baseline_mode == "equal_weight" and no manual target exists.
    # To add "spy_weight": look up the sector in _SPY_WEIGHTS instead.
    ew_baseline = round(100.0 / n_sectors, 1) if n_sectors else 100.0

    # ── 3. Build rows ─────────────────────────────────────────────────────
    rows: list[AlignmentRow] = []
    for sector, b in buckets.items():
        w_pct   = _pct(b["value"], total)
        t_pct, source = _resolve_target(b["target_pcts"], ew_baseline, baseline_mode)

        # "No target" → skip verdict (only happens in "manual_targets" mode)
        if source == "No target":
            rows.append(AlignmentRow(
                sector        = sector,
                quadrant      = sector_status_map.get(sector, "Unknown"),
                weight_pct    = w_pct,
                target_pct    = 0.0,
                diff_pct      = 0.0,
                verdict        = "No target",
                verdict_color  = "neutral",
                target_source  = source,
            ))
            continue

        diff     = round(w_pct - t_pct, 1)
        quadrant = sector_status_map.get(sector, "Unknown")

        # ── Verdict rules ──────────────────────────────────────────────────
        # Thresholds: ±25% of target triggers over/underweight classification.
        # Future: expose these as parameters for user-configurable sensitivity.
        overweight  = w_pct > t_pct * 1.25
        underweight = w_pct < t_pct * 0.75
        weakening   = quadrant in ("Weakening", "Lagging")
        improving   = quadrant in ("Improving", "Leading")

        if overweight and weakening:
            verdict       = "Reduce"
            verdict_color = "red"
        elif overweight and improving:
            verdict       = "Overweight (on trend)"
            verdict_color = "amber"
        elif underweight and improving:
            verdict       = "Add candidate"
            verdict_color = "blue"
        else:
            verdict       = "Aligned"
            verdict_color = "neutral"

        rows.append(AlignmentRow(
            sector        = sector,
            quadrant      = quadrant,
            weight_pct    = w_pct,
            target_pct    = t_pct,
            diff_pct      = diff,
            verdict        = verdict,
            verdict_color  = verdict_color,
            target_source  = source,
        ))

    rows.sort(key=lambda r: (_VERDICT_ORDER.get(r.verdict, 99), -r.weight_pct))
    return rows


# ---------------------------------------------------------------------------
# 4. Capital breakdown (full portfolio including non-tracked positions)
# ---------------------------------------------------------------------------

@dataclass
class CapitalBreakdown:
    total_value:    float    # user-declared total ($)
    deployed_value: float    # sum of tracked active positions ($)
    deployed_pct:   float
    extra_items:    list[tuple[str, float, float]]  # [(label, $, %)]
    cash_value:     float
    cash_pct:       float
    reconciled:     bool     # True when sum of parts ≈ total (within $1)


def compute_capital_breakdown(
    active: list,
    total_portfolio_value: float,
    extra_buckets: list[tuple[str, float]],   # [("BOXX", 5000), ("NVDA", 3000)]
) -> CapitalBreakdown:
    """
    Compute full capital allocation including non-tracked holdings and cash.

    Parameters
    ──────────
    active                : list of PositionResult from analyze_portfolio
    total_portfolio_value : user-declared total portfolio value ($)
    extra_buckets         : non-tracked positions as [(label, dollar_value)]

    Returns
    ───────
    CapitalBreakdown with dollar amounts and percentages for every slice.
    cash_value = total − deployed − sum(extra_buckets)
    reconciled = abs(total − all_parts) < 1.0
    """
    if total_portfolio_value <= 0:
        return CapitalBreakdown(
            total_value=0, deployed_value=0, deployed_pct=0,
            extra_items=[], cash_value=0, cash_pct=0, reconciled=True,
        )

    deployed_value = sum(
        mv for r in active
        if not getattr(r, "error", None)
        for mv in [_market_value(r)]
        if mv is not None
    )

    def _pct_of_total(v: float) -> float:
        return round(v / total_portfolio_value * 100, 1)

    extra_items = [
        (label, round(val), _pct_of_total(val))
        for label, val in extra_buckets
        if val > 0
    ]
    extra_total  = sum(val for _, val in extra_buckets if val > 0)
    cash_value   = max(0.0, total_portfolio_value - deployed_value - extra_total)
    parts_total  = deployed_value + extra_total + cash_value
    reconciled   = abs(total_portfolio_value - parts_total) < 1.0

    return CapitalBreakdown(
        total_value    = round(total_portfolio_value),
        deployed_value = round(deployed_value),
        deployed_pct   = _pct_of_total(deployed_value),
        extra_items    = extra_items,
        cash_value     = round(cash_value),
        cash_pct       = _pct_of_total(cash_value),
        reconciled     = reconciled,
    )


# ---------------------------------------------------------------------------
# 5. Top-level concentration signal
# ---------------------------------------------------------------------------

@dataclass
class RebalancingRow:
    sector:      str
    actual_pct:  float    # current weight (%)
    target_pct:  float    # desired weight (%)
    diff_pct:    float    # actual − target  (+ve = overweight)
    # Sign convention (matches what the user should DO, not the gap size):
    #   trim_amount > 0  → dollars to trim/reduce in this sector
    #   add_amount  > 0  → dollars to add to this sector
    trim_amount: float    # dollars to reduce (0 if not trimming)
    add_amount:  float    # dollars to add    (0 if not adding)
    direction:   str      # "Trim" | "Add" | "Hold" | "Underweight, but avoid add"
    quadrant:    str      # RRG quadrant of this sector


# Neutral band: abs(diff) ≤ this value → direction = "Hold" (avoid noise)
_HOLD_BAND_PCT: float = 5.0

# RRG quadrants where adding to an underweight sector makes strategic sense
_IMPROVING_QUADRANTS = frozenset({"Improving", "Leading"})
_WEAKENING_QUADRANTS = frozenset({"Weakening", "Lagging"})


def compute_rebalancing_plan(
    align_rows: list[AlignmentRow],
    total_value: float,
    hold_band_pct: float = _HOLD_BAND_PCT,
) -> list[RebalancingRow]:
    """
    Convert sector alignment into dollar imbalances, regime-aware.

    Parameters
    ──────────
    align_rows    : from compute_sector_alignment (carries .quadrant)
    total_value   : total portfolio market value ($)
    hold_band_pct : sectors with |diff| ≤ this % show "Hold" (default 5%)

    Direction logic
    ───────────────
    1. abs(diff_pct) ≤ hold_band_pct         → "Hold"
    2. diff_pct > 0  (overweight)            → "Trim"
    3. diff_pct < 0  AND quadrant Improving/Leading  → "Add"
    4. diff_pct < 0  AND quadrant Weakening/Lagging  → "Underweight, but avoid add"

    Amount convention
    ─────────────────
    trim_amount  positive dollar value to reduce from the sector
    add_amount   positive dollar value to add to the sector
    Exactly one of them is non-zero per row (both 0 for Hold).
    """
    rows: list[RebalancingRow] = []

    for ar in align_rows:
        if ar.target_pct == 0 or ar.verdict == "No target":
            continue

        diff     = ar.diff_pct                       # actual − target
        gap_pct  = ar.target_pct - ar.weight_pct    # target − actual (+ = need to add)
        gap_abs  = round(abs(gap_pct) / 100.0 * total_value)

        if abs(diff) <= hold_band_pct:
            direction   = "Hold"
            trim_amount = 0.0
            add_amount  = 0.0
        elif diff > 0:
            # Overweight → trim regardless of quadrant
            direction   = "Trim"
            trim_amount = gap_abs   # positive: "reduce by this much"
            add_amount  = 0.0
        else:
            # Underweight — check quadrant
            if ar.quadrant in _IMPROVING_QUADRANTS:
                direction   = "Add"
                add_amount  = gap_abs
                trim_amount = 0.0
            else:
                direction   = "Underweight, but avoid add"
                add_amount  = gap_abs   # stored for info; user should NOT act
                trim_amount = 0.0

        rows.append(RebalancingRow(
            sector      = ar.sector,
            actual_pct  = ar.weight_pct,
            target_pct  = ar.target_pct,
            diff_pct    = ar.diff_pct,
            trim_amount = trim_amount,
            add_amount  = add_amount,
            direction   = direction,
            quadrant    = ar.quadrant,
        ))

    rows.sort(key=lambda r: -(r.trim_amount + r.add_amount))
    return rows


def compute_portfolio_signal(
    align_rows: list[AlignmentRow],
    threshold: float = 1.4,
) -> tuple[str, str]:
    """
    Return (message, severity) — severity is "warn" or "ok".

    Triggers a warning when any sector exceeds threshold × its target.
    Uses the target (manual or auto-baseline) already stored in AlignmentRow.
    """
    if not align_rows:
        return "Add positions to generate a signal.", "ok"

    flagged = [
        r for r in align_rows
        if r.target_pct and r.weight_pct > r.target_pct * threshold
    ]

    if flagged:
        worst = max(flagged, key=lambda r: r.weight_pct / (r.target_pct or 1))
        note  = "" if worst.target_source == "Manual target" else " (equal-weight baseline)"
        return (
            f"Portfolio heavily concentrated in {worst.sector} "
            f"({worst.weight_pct:.0f}% vs {worst.target_pct:.0f}%{note})",
            "warn",
        )

    non_aligned = [r for r in align_rows if r.verdict not in ("Aligned", "No target")]
    if non_aligned:
        labels = ", ".join(r.sector for r in non_aligned[:3])
        return f"Review: {labels} — see alignment table below.", "warn"

    return "Portfolio broadly balanced across sectors.", "ok"


# ---------------------------------------------------------------------------
# Dev / debug examples  (run:  python3 portfolio_exposure.py)
# ---------------------------------------------------------------------------
#
# Expected behaviour for three scenarios:
#
# SCENARIO A — All auto targets (no target_weight set)
# ────────────────────────────────────────────────────
# 4 held sectors → baseline = 25.0% each
#
#   XLE  actual=61%  target=25% (Auto equal-weight)  diff=+36%  → Reduce  (if Weakening)
#   XLY  actual=23%  target=25% (Auto equal-weight)  diff=−2%   → Aligned
#   XLF  actual=12%  target=25% (Auto equal-weight)  diff=−13%  → Add candidate (if Improving)
#   XLV  actual=5%   target=25% (Auto equal-weight)  diff=−20%  → Add candidate (if Improving)
#
# SCENARIO B — Mixed manual + auto
# ─────────────────────────────────
# XLE positions have target_weight=30.  Others have no target.
# 4 sectors → ew_baseline=25.0%
#
#   XLE  actual=61%  target=30% (Manual target)      diff=+31%  → Reduce  (if Weakening)
#   XLY  actual=23%  target=25% (Auto equal-weight)  diff=−2%   → Aligned
#   XLF  actual=12%  target=25% (Auto equal-weight)  diff=−13%  → Add candidate (if Improving)
#   XLV  actual=5%   target=25% (Auto equal-weight)  diff=−20%  → Add candidate (if Improving)
#
# SCENARIO C — All manual targets
# ────────────────────────────────
# All positions have target_weight set.
#
#   XLE  actual=61%  target=30% (Manual target)  diff=+31%  → Reduce  (if Weakening)
#   XLY  actual=23%  target=25% (Manual target)  diff=−2%   → Aligned
#   XLF  actual=12%  target=25% (Manual target)  diff=−13%  → Add candidate (if Improving)
#   XLV  actual=5%   target=20% (Manual target)  diff=−15%  → Add candidate (if Improving)
#
# In all three scenarios, the verdict columns are fully populated.

if __name__ == "__main__":
    # ── Minimal duck-typed position mock ─────────────────────────────────────
    class _P:
        def __init__(self, ticker, sector, mv, tw=None):
            self.ticker        = ticker
            self.sector        = sector
            self.shares        = mv / 50.0
            self.current_price = 50.0
            self.target_weight = tw
            self.error         = None
        @property
        def market_value(self): return self.shares * self.current_price

    smap = {"XLE": "Weakening", "XLY": "Leading",
            "XLF": "Improving", "XLV": "Improving"}

    scenarios = {
        "A — all auto": [
            _P("CNX",  "XLE", 9250),
            _P("DVN",  "XLE", 4000),
            _P("VFC",  "XLY", 5000),
            _P("BKU",  "XLF", 2500),
            _P("CI",   "XLV", 1000),
        ],
        "B — mixed": [
            _P("CNX",  "XLE", 9250, tw=20),
            _P("DVN",  "XLE", 4000, tw=10),
            _P("VFC",  "XLY", 5000),
            _P("BKU",  "XLF", 2500),
            _P("CI",   "XLV", 1000),
        ],
        "C — all manual": [
            _P("CNX",  "XLE", 9250, tw=20),
            _P("DVN",  "XLE", 4000, tw=10),
            _P("VFC",  "XLY", 5000, tw=25),
            _P("BKU",  "XLF", 2500, tw=25),
            _P("CI",   "XLV", 1000, tw=20),
        ],
    }

    for name, positions in scenarios.items():
        print(f"\n{'═'*60}")
        print(f"  Scenario {name}")
        print(f"{'═'*60}")
        rows = compute_sector_alignment(positions, smap)
        _, total_val = compute_sector_exposure(positions)

        for r in rows:
            print(f"  {r.sector:6}  {r.weight_pct:5.1f}%  →  "
                  f"target {r.target_pct:5.1f}%  ({r.target_source:20})  "
                  f"diff {r.diff_pct:+.1f}%   {r.verdict}")

        sig, sev = compute_portfolio_signal(rows)
        print(f"  Signal [{sev}]: {sig}")

        plan = compute_rebalancing_plan(rows, total_val)
        print(f"\n  Rebalancing plan (total=${total_val:,}):")
        for p in plan:
            if p.direction == "Trim":
                amt = f"Trim ${p.trim_amount:,.0f}"
            elif p.direction == "Add":
                amt = f"Add  ${p.add_amount:,.0f}"
            elif p.direction == "Underweight, but avoid add":
                amt = f"(avoid; gap ${p.add_amount:,.0f})"
            else:
                amt = "Hold"
            print(f"    {p.direction:28}  {p.sector:6}  "
                  f"actual={p.actual_pct:.1f}%  target={p.target_pct:.1f}%  "
                  f"diff={p.diff_pct:+.1f}%  {amt}  [{p.quadrant}]")
