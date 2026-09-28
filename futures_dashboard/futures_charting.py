"""Plotly futures chart — Phase 1 (pattern aligned with crypto_dashboard)."""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from futures_formatting import format_price


def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period).mean()


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    prev = close.shift(1)
    tr = pd.concat(
        [
            (high - low).abs(),
            (high - prev).abs(),
            (low - prev).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(period).mean()


def _hline(fig: go.Figure, y: float, name: str, color: str, dash: str = "dash") -> None:
    if y is None or (isinstance(y, float) and np.isnan(y)):
        return
    fig.add_hline(
        y=float(y),
        line=dict(color=color, dash=dash, width=1.5),
        annotation_text=f" {name}: {format_price(y)}",
        annotation_position="left",
        annotation=dict(font=dict(color=color, size=11)),
        row=1, col=1,
    )


def build_futures_chart(
    df: pd.DataFrame,
    symbol: str,
    timeframe: str,
    *,
    entry_price: Optional[float] = None,
    stop_price: Optional[float] = None,
    target_price: Optional[float] = None,
    current_price: Optional[float] = None,
    display_last: Optional[int] = None,
    demo_banner: Optional[str] = None,
    unofficial_banner: Optional[str] = None,
) -> go.Figure:
    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        row_heights=[0.78, 0.22],
        vertical_spacing=0.03,
    )

    if df.empty:
        fig.add_annotation(
            text="No chart data",
            xref="paper", yref="paper",
            x=0.5, y=0.5, showarrow=False,
            font=dict(size=18, color="#9ca3af"),
        )
        fig.update_layout(
            _base_layout(
                symbol,
                timeframe,
                extra_top_margin=bool(demo_banner) or bool(unofficial_banner),
            )
        )
        if unofficial_banner:
            fig.add_annotation(
                text=unofficial_banner,
                xref="paper", yref="paper",
                x=0, y=1.0, xanchor="left", yanchor="top",
                showarrow=False,
                font=dict(size=11, color="#fbbf24", family="sans-serif"),
            )
        if demo_banner:
            fig.add_annotation(
                text=demo_banner,
                xref="paper", yref="paper",
                x=0, y=0.968 if unofficial_banner else 1.0, xanchor="left", yanchor="top",
                showarrow=False,
                font=dict(size=12, color="#f87171", family="sans-serif"),
            )
        return fig

    df_full = df.reset_index(drop=True)
    close_full = df_full["close"]
    ema20_full = _ema(close_full, 20)
    ema50_full = _ema(close_full, 50)
    sma200_full: Optional[pd.Series] = None
    if len(close_full) >= 200:
        sma200_full = _sma(close_full, 200)

    n = len(df_full)
    if display_last is not None and n > int(display_last):
        sl = slice(-int(display_last), None)
        df = df_full.iloc[sl].copy().reset_index(drop=True)
        close = df["close"]
        ema20 = ema20_full.iloc[sl].reset_index(drop=True)
        ema50 = ema50_full.iloc[sl].reset_index(drop=True)
        sma200 = sma200_full.iloc[sl].reset_index(drop=True) if sma200_full is not None else None
    else:
        df = df_full
        close = close_full
        ema20 = ema20_full
        ema50 = ema50_full
        sma200 = sma200_full

    fig.add_trace(
        go.Candlestick(
            x=df["dt"],
            open=df["open"],
            high=df["high"],
            low=df["low"],
            close=close,
            name="Price",
            increasing_line_color="#22c55e",
            decreasing_line_color="#ef4444",
            showlegend=False,
        ),
        row=1, col=1,
    )
    fig.add_trace(
        go.Scatter(x=df["dt"], y=ema20, name="20 EMA",
                   line=dict(color="#f97316", width=1.5), mode="lines"),
        row=1, col=1,
    )
    fig.add_trace(
        go.Scatter(x=df["dt"], y=ema50, name="50 EMA",
                   line=dict(color="#3b82f6", width=1.5), mode="lines"),
        row=1, col=1,
    )
    if sma200 is not None and sma200.notna().any():
        fig.add_trace(
            go.Scatter(x=df["dt"], y=sma200, name="200 SMA",
                       line=dict(color="#9ca3af", width=1.2, dash="dot"), mode="lines"),
            row=1, col=1,
        )

    colors = [
        "#22c55e" if row["close"] >= row["open"] else "#ef4444"
        for _, row in df.iterrows()
    ]
    fig.add_trace(
        go.Bar(
            x=df["dt"], y=df["vol"],
            name="Volume", marker_color=colors, opacity=0.5,
            showlegend=False,
        ),
        row=2, col=1,
    )

    if current_price:
        _hline(fig, current_price, "Last", "#e5e7eb", dash="solid")
    if entry_price:
        _hline(fig, entry_price, "Entry", "#3b82f6", dash="dash")
    if stop_price:
        _hline(fig, stop_price, "Stop", "#ef4444", dash="dash")
    if target_price:
        _hline(fig, target_price, "Target", "#22c55e", dash="dash")

    layout = _base_layout(
        symbol,
        timeframe,
        extra_top_margin=bool(demo_banner) or bool(unofficial_banner),
    )
    fig.update_layout(layout)
    fig.update_yaxes(title_text="Price", row=1, col=1)
    fig.update_yaxes(title_text="Volume", row=2, col=1)
    fig.update_xaxes(rangeslider_visible=False, showgrid=True, gridcolor="#1f2937", row=2, col=1)
    fig.update_xaxes(showgrid=True, gridcolor="#1f2937", row=1, col=1)

    if unofficial_banner:
        fig.add_annotation(
            text=unofficial_banner,
            xref="paper",
            yref="paper",
            x=0,
            y=1.0,
            xanchor="left",
            yanchor="top",
            showarrow=False,
            font=dict(size=11, color="#fbbf24", family="sans-serif"),
        )
    if demo_banner:
        fig.add_annotation(
            text=demo_banner,
            xref="paper",
            yref="paper",
            x=0,
            y=0.968 if unofficial_banner else 1.0,
            xanchor="left",
            yanchor="top",
            showarrow=False,
            font=dict(size=12, color="#f87171", family="sans-serif"),
        )

    level_ys: list[float] = []
    for v in (entry_price, stop_price, target_price, current_price):
        if v is not None:
            try:
                level_ys.append(float(v))
            except (TypeError, ValueError):
                pass
    if level_ys and not df.empty:
        candle_lo = float(df["low"].min())
        candle_hi = float(df["high"].max())
        y_lo = min(candle_lo, *level_ys) * 0.994
        y_hi = max(candle_hi, *level_ys) * 1.006
        fig.update_yaxes(range=[y_lo, y_hi], row=1, col=1)

    return fig


def _base_layout(symbol: str, timeframe: str, *, extra_top_margin: bool = False) -> dict:
    top_m = 68 if extra_top_margin else 50
    return dict(
        title=dict(
            text=f"{symbol}  ·  {timeframe}",
            font=dict(size=16, color="#e5e7eb"),
            x=0.01,
        ),
        plot_bgcolor="#111827",
        paper_bgcolor="#111827",
        font=dict(color="#e5e7eb"),
        legend=dict(orientation="h", y=1.02, x=0, bgcolor="rgba(0,0,0,0)", font=dict(color="#e5e7eb")),
        margin=dict(l=60, r=20, t=top_m, b=40),
        height=600,
        yaxis=dict(showgrid=True, gridcolor="#1f2937", zerolinecolor="#374151", side="right"),
        yaxis2=dict(showgrid=False),
        hovermode="x unified",
    )


def trend_metrics(df: pd.DataFrame) -> dict:
    """Compute trend context from full-history DataFrame (not sliced)."""
    out: dict = {
        "above_ema20": None,
        "above_ema50": None,
        "above_sma200": None,
        "ret_1d": None,
        "ret_5d": None,
        "ret_20d": None,
        "atr_pct": None,
        "last_close": None,
    }
    if df.empty or len(df) < 2:
        return out
    c = df["close"].astype(float)
    h = df["high"].astype(float)
    l = df["low"].astype(float)
    ema20 = _ema(c, 20)
    ema50 = _ema(c, 50)
    sma200 = _sma(c, 200) if len(c) >= 200 else pd.Series([np.nan] * len(c))
    last = c.iloc[-1]
    out["last_close"] = float(last)

    def _abv(series: pd.Series) -> Optional[bool]:
        v = series.iloc[-1]
        if pd.isna(v):
            return None
        return bool(last > v)

    out["above_ema20"]  = _abv(ema20)
    out["above_ema50"]  = _abv(ema50)
    out["above_sma200"] = _abv(sma200) if len(c) >= 200 else None

    def _ret(bars: int) -> Optional[float]:
        if len(c) <= bars:
            return None
        prev = c.iloc[-1 - bars]
        if prev == 0:
            return None
        return (float(last) - float(prev)) / float(prev) * 100.0

    out["ret_1d"]  = _ret(1)
    out["ret_5d"]  = _ret(5)
    out["ret_20d"] = _ret(20)

    atr = _atr(h, l, c, 14)
    av = atr.iloc[-1]
    if not pd.isna(av) and last:
        out["atr_pct"] = float(av) / float(last) * 100.0
    return out
