"""Optional Yahoo Finance fallback for futures charting (unofficial, not CME).

CSV files remain the preferred path for serious work. This module never places orders.
"""
from __future__ import annotations

import logging
from typing import Optional

import pandas as pd

log = logging.getLogger(__name__)

YFINANCE_MISSING_MSG = (
    "yfinance not installed. Install yfinance to enable Yahoo fallback data."
)

YFINANCE_UNOFFICIAL = (
    "Unofficial Yahoo Finance continuous futures data. Use for charting and prototyping only."
)

# Root symbol -> Yahoo continuous futures ticker (subset; extend as needed)
YAHOO_FUTURES_TICKER: dict[str, str] = {
    "ES": "ES=F",
    "NQ": "NQ=F",
    "YM": "YM=F",
    "RTY": "RTY=F",
    "CL": "CL=F",
    "NG": "NG=F",
    "GC": "GC=F",
    "SI": "SI=F",
    "ZN": "ZN=F",
    "ZB": "ZB=F",
    "6E": "6E=F",
    "6J": "6J=F",
    "6B": "6B=F",
    "ZC": "ZC=F",
    "ZS": "ZS=F",
    "ZW": "ZW=F",
    "BTC": "BTC=F",
    "ETH": "ETH=F",
}

TIMEFRAME_UNSUPPORTED_MSG = "Selected timeframe not available from yfinance."


def yfinance_installed() -> bool:
    try:
        import yfinance  # noqa: F401, PLC0415
    except ImportError:
        return False
    return True


def yahoo_ticker_for_symbol(symbol: str) -> Optional[str]:
    return YAHOO_FUTURES_TICKER.get(symbol.strip().upper())


def _interval_and_period(timeframe: str, lookback: int) -> tuple[str, str] | None:
    """Map dashboard TF to yfinance ``interval`` and ``period`` strings."""
    lb = max(int(lookback), 1)
    if timeframe == "1D":
        if lb <= 250:
            return "1d", "2y"
        if lb <= 800:
            return "1d", "5y"
        if lb <= 2000:
            return "1d", "10y"
        return "1d", "max"
    if timeframe == "1H":
        return "60m", "730d"
    if timeframe == "15m":
        return "15m", "60d"
    if timeframe == "5m":
        return "5m", "60d"
    if timeframe in ("4H",):
        return None
    return None


def load_yfinance_ohlcv(symbol: str, timeframe: str, lookback: int) -> tuple[pd.DataFrame, str]:
    """Download OHLCV from Yahoo and return the same frame shape as ``futures_data.load_ohlcv``.

    Columns: ``dt`` (UTC), ``open``, ``high``, ``low``, ``close``, ``volume``, ``vol``.

    Returns ``(df, error)`` where ``error`` is empty on success.
    """
    try:
        import yfinance as yf  # noqa: PLC0415
    except ImportError:
        return pd.DataFrame(), YFINANCE_MISSING_MSG

    sym_u = symbol.strip().upper()
    ytick = yahoo_ticker_for_symbol(sym_u)
    if not ytick:
        return pd.DataFrame(), f"No Yahoo Finance mapping for {sym_u}."

    ip = _interval_and_period(timeframe.strip(), lookback)
    if ip is None:
        return pd.DataFrame(), TIMEFRAME_UNSUPPORTED_MSG

    interval, period = ip

    try:
        ticker = yf.Ticker(ytick)
        hist = ticker.history(period=period, interval=interval, auto_adjust=False, prepost=False)
    except Exception as exc:  # noqa: BLE001
        log.warning("yfinance history failed %s %s: %s", ytick, interval, exc)
        return pd.DataFrame(), f"Yahoo request failed: {exc}"

    if hist is None or hist.empty:
        return pd.DataFrame(), "No data returned from Yahoo for this symbol."

    hist = hist.dropna(how="all")
    if hist.empty:
        return pd.DataFrame(), "No data returned from Yahoo for this symbol."

    # Build with plain RangeIndex so Yahoo columns align by position (not by DatetimeIndex labels).
    out = pd.DataFrame()
    out["dt"] = pd.to_datetime(hist.index, utc=True)
    for col, name in (("Open", "open"), ("High", "high"), ("Low", "low"), ("Close", "close")):
        if col not in hist.columns:
            return pd.DataFrame(), f"Yahoo response missing {col!r}."
        out[name] = pd.to_numeric(hist[col].to_numpy(), errors="coerce")
    if "Volume" in hist.columns:
        out["volume"] = pd.to_numeric(hist["Volume"].to_numpy(), errors="coerce")
        out["volume"] = out["volume"].fillna(0.0)
    else:
        out["volume"] = 0.0

    out.dropna(subset=["dt", "open", "high", "low", "close"], inplace=True)
    out.sort_values("dt", inplace=True)
    out.drop_duplicates(subset=["dt"], keep="last", inplace=True)
    out.reset_index(drop=True, inplace=True)

    if out.empty:
        return pd.DataFrame(), "No data returned from Yahoo for this symbol."

    if len(out) > lookback:
        out = out.iloc[-int(lookback) :].copy().reset_index(drop=True)

    out["vol"] = out["volume"]
    return out, ""
