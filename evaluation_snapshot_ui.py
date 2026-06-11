"""
evaluation_snapshot_ui.py — Pure display helpers for Evaluation Snapshot cards.

Read-only formatting over evaluation_manifest_loader. No Dash, no network,
no trading/scoring/Telegram logic, no auto-refresh side effects.
"""
from __future__ import annotations

from typing import Any

import evaluation_manifest_loader as eml

CARD_TITLE = "Evaluation Snapshot"

STATUS_LABELS: dict[str, str] = {
    "fresh": "FRESH",
    "warn": "WARN",
    "missing": "MISSING",
    "error": "ERROR",
}

MISSING_HINT = "Run: python3 scripts/run_evaluation_snapshot.py --skip-persist"

API_PATHS: dict[str, str] = {
    "stock": "/api/evaluation-snapshot/stock",
    "crypto": "/api/evaluation-snapshot/crypto",
}


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def format_evaluation_snapshot_summary(
    asset_class: str,
    manifest: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Normalize a loaded manifest into dashboard-neutral display fields.

  Never raises. Missing/invalid ``manifest`` yields a safe warning summary.
    """
    ac = _safe_str(asset_class) or "stock"
    if not manifest or not isinstance(manifest, dict):
        return {
            "title": CARD_TITLE,
            "asset_class": ac,
            "status": "missing",
            "status_label": STATUS_LABELS["missing"],
            "available": False,
            "meta_line": "No snapshot recorded yet.",
            "source_lines": [],
            "file_lines": [],
            "warning_lines": ["evaluation manifest unavailable"],
            "hint_line": MISSING_HINT,
            "api_path": API_PATHS.get(ac, ""),
        }

    status = _safe_str(manifest.get("status")) or "missing"
    status_label = STATUS_LABELS.get(status, status.upper() or "UNKNOWN")

    meta_bits: list[str] = []
    if manifest.get("generated_at"):
        meta_bits.append(f"Snapshot: {manifest['generated_at']}")
    if manifest.get("lookback_days") is not None:
        meta_bits.append(f"Lookback: {manifest['lookback_days']}d")
    if manifest.get("callout_count") is not None:
        meta_bits.append(f"Callouts: {manifest['callout_count']}")
    if manifest.get("outcome_count") is not None:
        meta_bits.append(f"Outcomes: {manifest['outcome_count']}")

    warnings = [str(w) for w in (manifest.get("freshness_warnings") or []) if str(w).strip()]
    available = bool(manifest.get("available"))
    hint_line: str | None = None
    if not warnings and not available:
        hint_line = MISSING_HINT

    return {
        "title": CARD_TITLE,
        "asset_class": _safe_str(manifest.get("asset_class")) or ac,
        "status": status,
        "status_label": status_label,
        "available": available,
        "meta_line": "  •  ".join(meta_bits) if meta_bits else "No snapshot recorded yet.",
        "source_lines": list(eml.source_freshness_lines(manifest)),
        "file_lines": list(eml.file_availability_lines(manifest)),
        "warning_lines": warnings,
        "hint_line": hint_line,
        "api_path": API_PATHS.get(ac, ""),
    }


def load_evaluation_snapshot_summary(asset_class: str) -> dict[str, Any]:
    """Load manifest from disk and return formatted summary. Never raises."""
    try:
        manifest = eml.load_evaluation_manifest(asset_class)
    except Exception:  # noqa: BLE001
        manifest = None
    return format_evaluation_snapshot_summary(asset_class, manifest)
