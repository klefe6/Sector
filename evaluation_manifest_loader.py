"""
evaluation_manifest_loader.py — Read-only loader for evaluation snapshot manifests.

Reads evaluation_manifest.json written by scripts/run_evaluation_snapshot.py.
No network calls. Never raises from public load helpers.
"""
from __future__ import annotations

import json
import os
from typing import Any

import callout_performance as cpf
import warren_loader as wl

MANIFEST_FILENAME = "evaluation_manifest.json"


def stock_manifest_path() -> str:
    return os.path.join(wl.WARREN_OUTPUT_DIR, "callout_performance", "stock", MANIFEST_FILENAME)


def crypto_manifest_path() -> str:
    return os.path.join(cpf._crypto_output_dir(), "callout_performance", "crypto", MANIFEST_FILENAME)


def manifest_path_for(asset_class: str) -> str:
    if asset_class == "stock":
        return stock_manifest_path()
    if asset_class == "crypto":
        return crypto_manifest_path()
    raise ValueError(f"unknown asset_class: {asset_class}")


def _missing_manifest(asset_class: str, path: str, *, reason: str) -> dict[str, Any]:
    return {
        "available": False,
        "status": "missing",
        "asset_class": asset_class,
        "path": path,
        "generated_at": None,
        "lookback_days": None,
        "callout_count": None,
        "outcome_count": None,
        "report_path": None,
        "callouts_path": None,
        "outcomes_path": None,
        "latest_source_snapshot": {},
        "freshness_warnings": [reason],
        "error": reason,
    }


def load_evaluation_manifest(asset_class: str) -> dict[str, Any]:
    """
    Load and normalize an evaluation manifest for UI/API display.

    Returns a dict with ``available`` (bool) and ``status`` (fresh|warn|missing|error).
    Never raises.
    """
    try:
        path = manifest_path_for(asset_class)
    except ValueError as exc:
        return _missing_manifest(asset_class, "", reason=str(exc))

    if not os.path.isfile(path):
        return _missing_manifest(
            asset_class,
            path,
            reason="evaluation manifest not found — run: python3 scripts/run_evaluation_snapshot.py",
        )

    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except Exception as exc:  # noqa: BLE001
        return {
            **_missing_manifest(asset_class, path, reason=f"invalid manifest JSON: {exc}"),
            "status": "error",
        }

    if not isinstance(raw, dict):
        return {
            **_missing_manifest(asset_class, path, reason="manifest root must be a JSON object"),
            "status": "error",
        }

    warnings = list(raw.get("freshness_warnings") or [])
    status = "fresh" if not warnings else "warn"
    return {
        "available": True,
        "status": status,
        "path": path,
        "asset_class": raw.get("asset_class") or asset_class,
        "generated_at": raw.get("generated_at"),
        "lookback_days": raw.get("lookback_days"),
        "callout_count": raw.get("callout_count"),
        "outcome_count": raw.get("outcome_count"),
        "report_path": raw.get("report_path"),
        "callouts_path": raw.get("callouts_path"),
        "outcomes_path": raw.get("outcomes_path"),
        "latest_source_snapshot": dict(raw.get("latest_source_snapshot") or {}),
        "freshness_warnings": warnings,
        "persist_errors": list(raw.get("persist_errors") or []),
    }


def source_freshness_lines(manifest: dict[str, Any]) -> list[str]:
    """Human-readable source freshness lines for dashboard display."""
    snap = manifest.get("latest_source_snapshot") or {}
    asset = manifest.get("asset_class") or ""

    if asset == "stock":
        lines: list[str] = []
        if snap.get("warren_output_date"):
            age = snap.get("warren_age_hours")
            age_s = f"{age:.1f}h" if isinstance(age, (int, float)) else "?"
            lines.append(f"Warren output: {snap['warren_output_date']} ({age_s} old)")
        if snap.get("nitter_file_date"):
            age = snap.get("nitter_age_hours")
            age_s = f"{age:.1f}h" if isinstance(age, (int, float)) else "?"
            lines.append(f"Nitter file: {snap['nitter_file_date']} ({age_s} old)")
        return lines

    if asset == "crypto":
        if snap.get("latest_scan_timestamp"):
            age = snap.get("latest_scan_age_hours")
            age_s = f"{age:.2f}h" if isinstance(age, (int, float)) else "?"
            return [f"Latest scan: {snap['latest_scan_timestamp']} ({age_s} old)"]
        if snap.get("latest_scan_path"):
            return [f"Latest scan file: {snap['latest_scan_path']}"]
        return []

    return []


def file_availability_lines(manifest: dict[str, Any]) -> list[str]:
    """Compact report/callout/outcome file availability."""
    lines: list[str] = []
    for key, label in (
        ("report_path", "report"),
        ("callouts_path", "callouts"),
        ("outcomes_path", "outcomes"),
    ):
        p = manifest.get(key)
        if p and os.path.isfile(str(p)):
            lines.append(f"{label}: ok")
        elif p:
            lines.append(f"{label}: path set, file missing")
        else:
            lines.append(f"{label}: —")
    return lines
