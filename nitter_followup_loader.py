"""
nitter_followup_loader.py — Read-only loader for the Decision Cockpit.

Exposes the latest "Nitter-Informed Warren Follow-Up" result so the Cockpit
can show a compact card. It only READS the JSON written by the follow-up
runner (warren_ai_daily/src/run_nitter_followup.ts); it never parses Nitter
or talks to Warren itself. Always degrades gracefully when missing.

Public API:
    get_latest_nitter_followup() -> NitterFollowup
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime

import warren_loader as wl  # reuse the configured output dir

# Pointer file written by the follow-up runner.
_LATEST_POINTER = os.path.join(wl.WARREN_OUTPUT_DIR, "latest_nitter_followup.json")

# How many days old before we label the follow-up STALE in the UI.
STALE_AFTER_DAYS = 2


@dataclass
class NitterFollowup:
    """Compact, UI-friendly view of the latest follow-up run."""
    status: str = "missing"          # ok | dry-run | skipped-* | failed | stale | missing
    ui_status: str = "missing"       # fresh | skipped | failed | stale | missing
    date: str = ""
    generated_at: str = ""
    source_file: str = ""
    nitter_freshness: str = ""
    freshness_status: str = ""
    run_date: str = ""
    age_hours: float | None = None
    date_matches_run: bool = False
    warning: str = ""
    credits: dict | None = None
    skip_reason: str = ""
    leads_count: int = 0
    results: list[dict] = field(default_factory=list)
    available: bool = False


def _classify_ui_status(status: str, run_date: str) -> str:
    if status == "ok":
        # Age out an old successful run.
        try:
            d = datetime.strptime(run_date, "%Y-%m-%d").date()
            if (date.today() - d).days > STALE_AFTER_DAYS:
                return "stale"
        except ValueError:
            pass
        return "fresh"
    if status == "dry-run":
        return "fresh"
    if status == "failed":
        return "failed"
    if status.startswith("skipped"):
        return "skipped"
    return "missing"


def get_latest_nitter_followup() -> NitterFollowup:
    """Load the latest follow-up pointer. Never raises."""
    if not os.path.isfile(_LATEST_POINTER):
        return NitterFollowup()
    try:
        with open(_LATEST_POINTER, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:  # noqa: BLE001
        return NitterFollowup(status="missing", ui_status="missing")

    status = str(data.get("status", "missing"))
    run_date = str(data.get("date", ""))
    return NitterFollowup(
        status=status,
        ui_status=_classify_ui_status(status, run_date),
        date=run_date,
        generated_at=str(data.get("generated_at", "")),
        source_file=str(data.get("source_file", "")),
        nitter_freshness=str(data.get("nitter_freshness", "")),
        freshness_status=str(data.get("freshness_status", "")),
        run_date=str(data.get("run_date", run_date)),
        age_hours=data.get("age_hours", data.get("source_file_age_hours")),
        date_matches_run=bool(data.get("date_matches_run", False)),
        warning=str(data.get("warning", data.get("source_date_warning", ""))),
        credits=data.get("credits"),
        skip_reason=str(data.get("skip_reason", "")),
        leads_count=int(data.get("leads_count", 0) or 0),
        results=list(data.get("results", []) or []),
        available=True,
    )


def to_dict(r: NitterFollowup) -> dict:
    return {
        "status": r.status,
        "ui_status": r.ui_status,
        "date": r.date,
        "generated_at": r.generated_at,
        "source_file": r.source_file,
        "nitter_freshness": r.nitter_freshness,
        "freshness_status": r.freshness_status,
        "run_date": r.run_date,
        "age_hours": r.age_hours,
        "date_matches_run": r.date_matches_run,
        "warning": r.warning,
        "credits": r.credits,
        "skip_reason": r.skip_reason,
        "leads_count": r.leads_count,
        "results": r.results,
        "available": r.available,
    }
