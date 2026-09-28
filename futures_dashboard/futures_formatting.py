"""Display helpers for futures dashboard (copied minimal pattern from crypto)."""
from __future__ import annotations


def format_price(x: float | None, *, decimals: int | None = None) -> str:
    if x is None:
        return "—"
    v = float(x)
    ax = abs(v)
    if decimals is not None:
        return f"{v:,.{decimals}f}"
    if ax >= 1_000:
        return f"{v:,.2f}"
    if ax >= 1:
        return f"{v:,.2f}"
    if ax >= 0.01:
        return f"{v:,.4f}"
    return f"{v:,.6f}"
