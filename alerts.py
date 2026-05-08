"""
Alert generation and the top-line "today's verdict" sentence.

Alerts derive directly from the trim verdicts. Severity mapping:
  Strong trim     -> red
  Trim candidate  -> amber
  Watch           -> blue
  Hold            -> no alert
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from trim_monitor import PositionResult


SEVERITY_MAP = {
    "Strong trim": "red",
    "Trim candidate": "amber",
    "Watch": "blue",
}

SEVERITY_ORDER = {"red": 0, "amber": 1, "blue": 2}


@dataclass
class Alert:
    severity: str       # red | amber | blue
    ticker: str
    title: str
    detail: str


def build_alerts(results: list[PositionResult]) -> list[Alert]:
    """One alert per non-Hold position. Sorted by severity, then # of flags."""
    alerts = []
    for r in results:
        if r.error:
            alerts.append(Alert(
                severity="amber",
                ticker=r.ticker,
                title=f"{r.ticker} — data error",
                detail=r.error,
            ))
            continue

        sev = SEVERITY_MAP.get(r.verdict)
        if sev is None:
            continue

        title = f"{r.ticker} — {r.verdict.lower()}"
        detail = " · ".join(r.flags) if r.flags else "No active flags"
        alerts.append(Alert(severity=sev, ticker=r.ticker, title=title, detail=detail))

    alerts.sort(key=lambda a: (SEVERITY_ORDER.get(a.severity, 99), a.ticker))
    return alerts


def todays_verdict(results: list[PositionResult]) -> str:
    """One sentence summarizing what to do today."""
    if not results:
        return "Today's verdict: No positions loaded. Add positions to begin."

    errors = sum(1 for r in results if r.error)
    strong = sum(1 for r in results if r.verdict == "Strong trim")
    trim = sum(1 for r in results if r.verdict == "Trim candidate")
    watch = sum(1 for r in results if r.verdict == "Watch")
    hold = sum(1 for r in results if r.verdict == "Hold" and not r.error)

    parts = []
    if strong:
        parts.append(f"{strong} strong trim {'signal' if strong == 1 else 'signals'} to act on")
    if trim:
        parts.append(f"{trim} trim {'candidate' if trim == 1 else 'candidates'} to review")
    if watch:
        parts.append(f"{watch} on watch")

    if not parts:
        head = "All positions in normal range. No trim action required today."
    else:
        head = "Action required: " + ", ".join(parts) + "."

    suffix = ""
    if errors:
        suffix = f" {errors} ticker{'s' if errors > 1 else ''} failed to load — check data column."
    elif hold and (strong or trim):
        suffix = f" {hold} {'position' if hold == 1 else 'positions'} on hold."

    return f"Today's verdict: {head}{suffix}"
