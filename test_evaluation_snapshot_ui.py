"""Tests for evaluation_snapshot_ui.py — pure display helpers."""
from __future__ import annotations

import evaluation_snapshot_ui as esui


def _stock_manifest() -> dict:
    return {
        "available": True,
        "status": "fresh",
        "asset_class": "stock",
        "generated_at": "2026-06-11T12:00:00Z",
        "lookback_days": 30,
        "callout_count": 70,
        "outcome_count": 68,
        "report_path": "/tmp/report.json",
        "callouts_path": None,
        "outcomes_path": None,
        "latest_source_snapshot": {
            "warren_output_date": "2026-06-11",
            "warren_age_hours": 2.5,
            "nitter_file_date": "2026-06-10",
            "nitter_age_hours": 12.0,
        },
        "freshness_warnings": [],
    }


def _crypto_manifest() -> dict:
    return {
        "available": True,
        "status": "warn",
        "asset_class": "crypto",
        "generated_at": "2026-06-11T22:00:00Z",
        "lookback_days": 30,
        "callout_count": 100,
        "outcome_count": 100,
        "report_path": None,
        "callouts_path": None,
        "outcomes_path": None,
        "latest_source_snapshot": {
            "latest_scan_timestamp": "2026-06-11T22:03:04+00:00",
            "latest_scan_age_hours": 0.5,
        },
        "freshness_warnings": ["crypto scan older than 2h (3.0h)"],
    }


def test_valid_stock_summary():
    summary = esui.format_evaluation_snapshot_summary("stock", _stock_manifest())
    assert summary["status"] == "fresh"
    assert summary["status_label"] == "FRESH"
    assert summary["available"] is True
    assert "Snapshot: 2026-06-11T12:00:00Z" in summary["meta_line"]
    assert "Callouts: 70" in summary["meta_line"]
    assert any("Warren output" in line for line in summary["source_lines"])
    assert any("Nitter file" in line for line in summary["source_lines"])
    assert summary["warning_lines"] == []
    assert summary["api_path"] == "/api/evaluation-snapshot/stock"


def test_valid_crypto_summary():
    summary = esui.format_evaluation_snapshot_summary("crypto", _crypto_manifest())
    assert summary["status"] == "warn"
    assert summary["status_label"] == "WARN"
    assert "Callouts: 100" in summary["meta_line"]
    assert len(summary["source_lines"]) == 1
    assert "Latest scan" in summary["source_lines"][0]
    assert summary["warning_lines"] == ["crypto scan older than 2h (3.0h)"]
    assert summary["api_path"] == "/api/evaluation-snapshot/crypto"


def test_missing_fields_do_not_crash():
    summary = esui.format_evaluation_snapshot_summary("stock", {})
    assert summary["status"] == "missing"
    assert summary["available"] is False
    assert summary["meta_line"] == "No snapshot recorded yet."
    assert isinstance(summary["source_lines"], list)
    assert isinstance(summary["file_lines"], list)


def test_freshness_warnings_preserved():
    manifest = _stock_manifest()
    manifest["freshness_warnings"] = ["no callouts", "report missing"]
    manifest["status"] = "warn"
    summary = esui.format_evaluation_snapshot_summary("stock", manifest)
    assert summary["warning_lines"] == ["no callouts", "report missing"]
    assert summary["status_label"] == "WARN"


def test_missing_manifest_safe_summary():
    summary = esui.format_evaluation_snapshot_summary("stock", None)
    assert summary["status"] == "missing"
    assert summary["available"] is False
    assert summary["warning_lines"]
    assert summary["hint_line"] == esui.MISSING_HINT


def test_invalid_manifest_type_safe_summary():
    summary = esui.format_evaluation_snapshot_summary("crypto", "not-a-dict")  # type: ignore[arg-type]
    assert summary["status"] == "missing"
    assert "unavailable" in summary["warning_lines"][0].lower()
