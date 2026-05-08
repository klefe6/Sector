"""
macro_regime.py — FRED-based macro regime classification.

Uses FRED API data to classify the current macro environment into one of:
  Risk On | Neutral | Caution | Risk Off

All thresholds are module-level constants.
Gracefully degrades: missing series show warnings, never crash the app.

Required env variable:  FRED_API_KEY
If not set, returns a clean placeholder so the UI can display a help message.

Caching: results cached for CACHE_TTL seconds (6 hours by default).
Cache is also invalidated when force_refresh=True is passed.
"""
from __future__ import annotations

import os
import re
import time
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv is optional

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config constants — edit to tune regime classification
# ---------------------------------------------------------------------------

CACHE_TTL = 6 * 3600          # 6 hours

YIELD_CURVE_INVERSION_THRESHOLD = 0.0    # T10Y2Y < 0 → inverted
RATES_RISING_BPS                = 0.25   # 10Y vs 3-month avg
RATES_FALLING_BPS               = 0.25

VIX_RISK_OFF_THRESHOLD          = 25.0
VIX_CAUTIOUS_THRESHOLD          = 18.0

# FRED series IDs
SERIES = {
    "dgs10":     "DGS10",     # 10-year Treasury yield
    "dgs2":      "DGS2",      # 2-year Treasury yield
    "t10y2y":    "T10Y2Y",    # 10Y-2Y spread
    "fedfunds":  "FEDFUNDS",  # Fed funds rate
    "cpi":       "CPIAUCSL",  # CPI (urban consumers)
    "unrate":    "UNRATE",    # Unemployment rate
    "vix":       "VIXCLS",    # VIX
}

# How many months of history to fetch (max window)
SERIES_LOOKBACK = "1y"

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class SeriesResult:
    key:     str
    label:   str
    value:   Optional[float] = None
    error:   Optional[str]   = None
    updated: Optional[str]   = None   # date string of latest obs


@dataclass
class MacroRegime:
    label:              str = "Unknown"     # Risk On | Neutral | Caution | Risk Off
    yield_curve:        str = "—"
    rates_trend:        str = "—"
    inflation_trend:    str = "—"
    labor_trend:        str = "—"
    risk_regime:        str = "—"
    dgs10:              Optional[float] = None
    dgs2:               Optional[float] = None
    spread_10y2y:       Optional[float] = None
    fedfunds:           Optional[float] = None
    cpi_yoy:            Optional[float] = None
    unrate:             Optional[float] = None
    vix:                Optional[float] = None
    last_updated:       str = "—"
    reason:             str = "—"
    warnings:           list = field(default_factory=list)
    key_missing:        bool = False

    @property
    def label_color(self) -> str:
        return {
            "Risk On":  "green",
            "Neutral":  "blue",
            "Caution":  "amber",
            "Risk Off": "red",
        }.get(self.label, "neutral")


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

_cache: Optional[MacroRegime] = None
_cache_ts: float = 0.0


# ---------------------------------------------------------------------------
# FRED helpers
# ---------------------------------------------------------------------------

def _fred_key() -> Optional[str]:
    return os.environ.get("FRED_API_KEY", "").strip() or None


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _observation_date(obs: dict) -> Optional[str]:
    """
    FRED observation objects normally include 'date' (YYYY-MM-DD).
    Some responses may use different keys or nulls; try fallbacks.
    """
    for key in ("date", "Date", "DATE"):
        v = obs.get(key)
        if isinstance(v, str):
            v = v.strip()
            if _DATE_RE.match(v):
                return v[:10]
    for key in ("realtime_end", "realtime_start", "REALTIME_END", "REALTIME_START"):
        v = obs.get(key)
        if isinstance(v, str):
            v = v.strip()
            if _DATE_RE.match(v):
                return v[:10]
    return None


def _fetch_series(key: str, fred_api_key: str) -> SeriesResult:
    """Fetch the latest observation for a single FRED series."""
    import urllib.request, urllib.parse, json

    fred_id = SERIES[key]
    label   = fred_id
    params  = urllib.parse.urlencode({
        "series_id":    fred_id,
        "api_key":      fred_api_key,
        "file_type":    "json",
        "sort_order":   "desc",
        "limit":        "400",          # enough to compute moving averages
        "observation_start": "2022-01-01",
    })
    url = f"https://api.stlouisfed.org/fred/series/observations?{params}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
    except Exception as exc:
        return SeriesResult(key=key, label=label, error=str(exc))

    obs = [o for o in data.get("observations", []) if o.get("value") != "."]
    if not obs:
        return SeriesResult(key=key, label=label, error="No observations returned")

    # obs sorted desc, so obs[0] is latest
    latest = obs[0]
    try:
        val = float(latest["value"])
    except (KeyError, ValueError):
        return SeriesResult(key=key, label=label, error="Could not parse value")

    obs_date = _observation_date(latest)

    return SeriesResult(
        key=key, label=label, value=val,
        updated=obs_date,
    )


def _recent_values(key: str, fred_api_key: str, n: int) -> list[float]:
    """
    Return the n most recent non-null float values for a FRED series.
    Returns [] on any failure.
    """
    import urllib.request, urllib.parse, json

    fred_id = SERIES[key]
    params  = urllib.parse.urlencode({
        "series_id":  fred_id,
        "api_key":    fred_api_key,
        "file_type":  "json",
        "sort_order": "desc",
        "limit":      str(max(n + 5, 20)),
        "observation_start": "2022-01-01",
    })
    url = f"https://api.stlouisfed.org/fred/series/observations?{params}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
    except Exception:
        return []

    vals = []
    for o in data.get("observations", []):
        if o.get("value") == ".":
            continue
        try:
            vals.append(float(o["value"]))
        except (KeyError, ValueError):
            continue
        if len(vals) >= n:
            break
    return vals


# ---------------------------------------------------------------------------
# Regime classification helpers
# ---------------------------------------------------------------------------

def _classify_yield_curve(spread: Optional[float]) -> str:
    if spread is None:
        return "Unavailable"
    if spread < YIELD_CURVE_INVERSION_THRESHOLD:
        return "Inverted (risk signal)"
    if spread < 0.3:
        return "Flat (normalizing)"
    return "Normal"


def _classify_rates_trend(latest_10y: Optional[float],
                          hist_10y: list[float]) -> str:
    if latest_10y is None or len(hist_10y) < 2:
        return "Unavailable"
    avg = sum(hist_10y[1:4]) / len(hist_10y[1:4])  # skip index 0 (=latest)
    delta = latest_10y - avg
    if delta > RATES_RISING_BPS:
        return "Rising"
    if delta < -RATES_FALLING_BPS:
        return "Falling"
    return "Stable"


def _classify_inflation(cpi_vals: list[float]) -> str:
    """
    CPI YoY vs trailing 3-month average YoY.
    FRED CPIAUCSL is monthly; 12-month change ≈ (val[0]/val[12] - 1)*100.
    """
    if len(cpi_vals) < 14:
        return "Unavailable"
    # val[0] = latest month, val[12] = 12 months ago
    yoy_now = (cpi_vals[0] / cpi_vals[12] - 1) * 100

    # average of prior 3 months' yoy
    prior_yoys = []
    for i in range(1, 4):
        if i + 12 < len(cpi_vals):
            prior_yoys.append((cpi_vals[i] / cpi_vals[i + 12] - 1) * 100)

    if not prior_yoys:
        return f"CPI YoY {yoy_now:.1f}%"

    avg_prior = sum(prior_yoys) / len(prior_yoys)
    delta = yoy_now - avg_prior

    if delta > 0.2:
        trend = "Heating"
    elif delta < -0.2:
        trend = "Cooling"
    else:
        trend = "Stable"
    return f"{trend} ({yoy_now:.1f}% YoY)"


def _cpi_yoy(cpi_vals: list[float]) -> Optional[float]:
    if len(cpi_vals) < 13:
        return None
    return round((cpi_vals[0] / cpi_vals[12] - 1) * 100, 2)


def _classify_labor(unrate_vals: list[float]) -> str:
    if len(unrate_vals) < 2:
        return "Unavailable"
    latest = unrate_vals[0]
    avg6   = sum(unrate_vals[:6]) / min(6, len(unrate_vals))
    delta  = latest - avg6
    if delta > 0.2:
        return f"Softening ({latest:.1f}%)"
    if delta < -0.2:
        return f"Tightening ({latest:.1f}%)"
    return f"Stable ({latest:.1f}%)"


def _classify_vix(vix: Optional[float]) -> str:
    if vix is None:
        return "Unavailable"
    if vix >= VIX_RISK_OFF_THRESHOLD:
        return f"Risk Off (VIX {vix:.1f})"
    if vix >= VIX_CAUTIOUS_THRESHOLD:
        return f"Cautious (VIX {vix:.1f})"
    return f"Risk On (VIX {vix:.1f})"


def _overall_label(yield_curve, rates_trend, inflation_trend,
                   labor_trend, risk_regime, warnings) -> tuple[str, str]:
    """
    Score-based regime: each component contributes −1 / 0 / +1.
    +3 to +4 → Risk On
    +1 to +2 → Neutral
    −1 to 0  → Caution
    ≤ −2     → Risk Off
    """
    score = 0
    parts = []

    if "Inverted" in yield_curve:
        score -= 1; parts.append("yield curve inverted")
    elif "Normal" in yield_curve:
        score += 1; parts.append("yield curve normal")

    if "Rising" in rates_trend:
        score -= 1; parts.append("rates rising")
    elif "Falling" in rates_trend:
        score += 1; parts.append("rates falling")
    else:
        parts.append("rates stable")

    if inflation_trend.startswith("Unavailable"):
        parts.append("inflation unavailable")
    elif "Heating" in inflation_trend:
        score -= 1; parts.append("inflation heating")
    elif "Cooling" in inflation_trend:
        score += 1; parts.append("inflation cooling")
    else:
        parts.append("inflation stable")

    if labor_trend.startswith("Unavailable"):
        parts.append("labor unavailable")
    elif "Softening" in labor_trend:
        score -= 1; parts.append("labor softening")
    elif "Tightening" in labor_trend:
        score += 1; parts.append("labor tight")
    else:
        parts.append("labor stable")

    if "Risk Off" in risk_regime:
        score -= 1; parts.append("VIX elevated")
    elif "Risk On" in risk_regime:
        score += 1; parts.append("VIX calm")
    else:
        parts.append("VIX neutral")

    if score >= 3:
        label = "Risk On"
    elif score >= 1:
        label = "Neutral"
    elif score >= -1:
        label = "Caution"
    else:
        label = "Risk Off"

    if warnings:
        parts.append(f"{len(warnings)} data warning(s)")

    return label, "; ".join(parts)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_macro_regime(force_refresh: bool = False) -> MacroRegime:
    """
    Return a MacroRegime object, using in-process cache.
    If FRED_API_KEY is missing, returns a placeholder with key_missing=True.
    """
    global _cache, _cache_ts

    if (not force_refresh
            and _cache is not None
            and (time.monotonic() - _cache_ts) < CACHE_TTL):
        return _cache

    api_key = _fred_key()
    if not api_key:
        result = MacroRegime(
            label       = "Unknown",
            reason      = "FRED_API_KEY not configured.",
            key_missing = True,
            last_updated = "—",
        )
        _cache, _cache_ts = result, time.monotonic()
        return result

    warnings: list[str] = []
    observation_dates: list[str] = []

    # ── Fetch series ────────────────────────────────────────────────────────
    def _metric(key: str) -> Optional[float]:
        """Fetch latest value; record observation date when FRED returns one."""
        r = _fetch_series(key, api_key)
        if r.error:
            warnings.append(f"{SERIES[key]}: {r.error}")
            return None
        if r.updated:
            observation_dates.append(r.updated)
        return r.value

    def _safe_hist(key: str, n: int) -> list[float]:
        try:
            return _recent_values(key, api_key, n)
        except Exception as exc:
            warnings.append(f"{SERIES[key]} history: {exc}")
            return []

    dgs10    = _metric("dgs10")
    dgs2     = _metric("dgs2")
    t10y2y   = _metric("t10y2y")
    fedfunds = _metric("fedfunds")
    vix      = _metric("vix")

    unrate_vals = _safe_hist("unrate", 8)
    cpi_vals    = _safe_hist("cpi",    16)
    dgs10_hist  = _safe_hist("dgs10",   6)

    # ── Classify ────────────────────────────────────────────────────────────
    yield_curve_str     = _classify_yield_curve(t10y2y)
    rates_trend_str     = _classify_rates_trend(dgs10, dgs10_hist)
    inflation_trend_str = _classify_inflation(cpi_vals)
    labor_trend_str     = _classify_labor(unrate_vals)
    risk_regime_str     = _classify_vix(vix)

    label, reason = _overall_label(
        yield_curve_str, rates_trend_str,
        inflation_trend_str, labor_trend_str,
        risk_regime_str, warnings,
    )

    # Latest FRED observation date across series we successfully pulled (lexical max = newest)
    if observation_dates:
        last_updated = max(observation_dates)
    else:
        last_updated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    result = MacroRegime(
        label            = label,
        yield_curve      = yield_curve_str,
        rates_trend      = rates_trend_str,
        inflation_trend  = inflation_trend_str,
        labor_trend      = labor_trend_str,
        risk_regime      = risk_regime_str,
        dgs10            = dgs10,
        dgs2             = dgs2,
        spread_10y2y     = t10y2y,
        fedfunds         = fedfunds,
        cpi_yoy          = _cpi_yoy(cpi_vals),
        unrate           = unrate_vals[0] if unrate_vals else None,
        vix              = vix,
        last_updated     = last_updated,
        reason           = reason,
        warnings         = warnings,
        key_missing      = False,
    )

    _cache, _cache_ts = result, time.monotonic()
    return result
