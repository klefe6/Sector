"""Micro Product Pulse — daily overview (current Yahoo over stale/demo CSV).

Not a trade signal. Read-only. Reuses ``futures_data`` and ``futures_yfinance``.

Resolution order (1D):
1. Real fresh micro CSV
2. Real fresh parent CSV
3. Yahoo micro (if mapped)
4. Yahoo parent (if mapped)
5. Stale real micro CSV
6. Stale real parent CSV
7. Demo micro CSV
8. Demo parent CSV

Demo or stale local data does not override Yahoo when Yahoo is available.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import pandas as pd

import futures_data as fd
import futures_providers as fp
import futures_universe as fu
import futures_yfinance as fy

# Enough daily bars for 30-session style return plus buffer
_PULSE_YF_LOOKBACK = 400


@dataclass
class PulseRowResult:
    micro: str
    parent: str
    name: str
    asset_class: str
    r1: Optional[float]
    r7: Optional[float]
    r30: Optional[float]
    source: str
    status: str
    proxy: bool
    demo: bool


def _pct_back(close: pd.Series, bars: int) -> Optional[float]:
    if close is None or len(close) <= bars:
        return None
    a = float(close.iloc[-1])
    b = float(close.iloc[-1 - bars])
    if b == 0:
        return None
    return (a / b - 1.0) * 100.0


def _returns_from_daily(df: pd.DataFrame) -> tuple[Optional[float], Optional[float], Optional[float]]:
    if df is None or df.empty or "close" not in df.columns:
        return None, None, None
    c = df["close"].astype(float)
    return _pct_back(c, 1), _pct_back(c, 7), _pct_back(c, 30)


def _yahoo_available(sym: str) -> bool:
    return fy.yahoo_ticker_for_symbol(sym) is not None


def _csv_ok(res: fd.LoadResult) -> bool:
    return res.rows > 0 and res.df is not None and not res.df.empty


def _yahoo_df(sym: str) -> tuple[pd.DataFrame, str]:
    return fy.load_yfinance_ohlcv(sym, "1D", _PULSE_YF_LOOKBACK)


def resolve_daily_series(micro: str, parent: Optional[str]) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Resolve daily OHLCV for pulse row (prefers current Yahoo over demo/stale CSV)."""
    meta: dict[str, Any] = {
        "symbol_used": "",
        "proxy": False,
        "source": "—",
        "demo": False,
        "status": "Missing",
    }

    def _set(
        symbol_used: str,
        *,
        proxy: bool,
        source: str,
        demo: bool,
        status: str,
    ) -> dict[str, Any]:
        meta.update(
            symbol_used=symbol_used,
            proxy=proxy,
            source=source,
            demo=demo,
            status=status,
        )
        return meta

    rm = fd.load_ohlcv(micro, "1D")
    rp = fd.load_ohlcv(parent, "1D") if parent else None

    # 1–2: fresh real CSV (micro then parent)
    if _csv_ok(rm) and fd.csv_pulse_tier(rm) == "fresh_real":
        _set(micro, proxy=False, source=fp.PULSE_SOURCE_CSV_REAL, demo=False, status="Loaded")
        return rm.df, meta
    if parent and rp is not None and _csv_ok(rp) and fd.csv_pulse_tier(rp) == "fresh_real":
        _set(parent, proxy=True, source=fp.PULSE_SOURCE_CSV_REAL, demo=False, status="Proxy parent")
        return rp.df, meta

    # 3–4: Yahoo before stale/demo CSV
    if _yahoo_available(micro):
        ydf, yerr = _yahoo_df(micro)
        if not yerr and ydf is not None and not ydf.empty:
            _set(micro, proxy=False, source=fp.PULSE_SOURCE_YAHOO_UNOFFICIAL, demo=False, status="Loaded")
            return ydf, meta
    if parent and _yahoo_available(parent):
        y2, e2 = _yahoo_df(parent)
        if not e2 and y2 is not None and not y2.empty:
            _set(
                parent,
                proxy=True,
                source=fp.PULSE_SOURCE_YAHOO_PARENT_PROXY,
                demo=False,
                status="Proxy parent",
            )
            return y2, meta

    # 5–6: stale real CSV
    if _csv_ok(rm) and fd.csv_pulse_tier(rm) == "stale_real":
        _set(micro, proxy=False, source=fp.PULSE_SOURCE_CSV_REAL, demo=False, status="Stale local")
        return rm.df, meta
    if parent and rp is not None and _csv_ok(rp) and fd.csv_pulse_tier(rp) == "stale_real":
        _set(parent, proxy=True, source=fp.PULSE_SOURCE_CSV_REAL, demo=False, status="Stale parent")
        return rp.df, meta

    # 7–8: demo CSV last resort
    if _csv_ok(rm) and rm.is_demo:
        _set(micro, proxy=False, source=fp.PULSE_SOURCE_CSV_DEMO, demo=True, status="Demo")
        return rm.df, meta
    if parent and rp is not None and _csv_ok(rp) and rp.is_demo:
        _set(parent, proxy=True, source=fp.PULSE_SOURCE_CSV_DEMO, demo=True, status="Demo")
        return rp.df, meta

    if not _yahoo_available(micro) and not (parent and _yahoo_available(parent)):
        meta["status"] = "Unsupported"
        meta["source"] = fp.PULSE_SOURCE_PROVIDER_UNSUPPORTED
    return pd.DataFrame(), meta


def compute_micro_pulse_rows() -> list[PulseRowResult]:
    rows: list[PulseRowResult] = []
    for micro, parent in fu.micro_pulse_definitions():
        mp = fu.get_product(micro)
        name = mp.name if mp else micro
        ac = mp.asset_class if mp else "Unknown"
        par_s = parent or "—"
        df, meta = resolve_daily_series(micro, parent)
        if df.empty:
            rows.append(
                PulseRowResult(
                    micro=micro,
                    parent=par_s,
                    name=name,
                    asset_class=ac,
                    r1=None,
                    r7=None,
                    r30=None,
                    source=str(meta.get("source", "—")),
                    status=str(meta.get("status", "Missing")),
                    proxy=bool(meta.get("proxy")),
                    demo=bool(meta.get("demo")),
                )
            )
            continue
        r1, r7, r30 = _returns_from_daily(df)
        rows.append(
            PulseRowResult(
                micro=micro,
                parent=par_s,
                name=name,
                asset_class=ac,
                r1=r1,
                r7=r7,
                r30=r30,
                source=str(meta.get("source", "—")),
                status=str(meta.get("status", "Loaded")),
                proxy=bool(meta.get("proxy")),
                demo=bool(meta.get("demo")),
            )
        )
    return rows


def pulse_extreme_lines(rows: list[PulseRowResult]) -> dict[str, str]:
    """Human-readable strongest / weakest lines for 7D and 30D."""
    need = "Need more data."

    def _pick(attr: str) -> tuple[str, str]:
        pts: list[tuple[str, float]] = []
        for r in rows:
            v = getattr(r, attr)
            if v is None:
                continue
            pts.append((r.micro, float(v)))
        if len(pts) < 2:
            return need, need
        pts.sort(key=lambda x: x[1])
        lo, hi = pts[0], pts[-1]
        return f"{hi[0]} {hi[1]:+.1f}%", f"{lo[0]} {lo[1]:+.1f}%"

    s7, w7 = _pick("r7")
    s30, w30 = _pick("r30")
    return {
        "strong_7": s7,
        "weak_7": w7,
        "strong_30": s30,
        "weak_30": w30,
    }
