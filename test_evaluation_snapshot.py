"""Tests for scripts/run_evaluation_snapshot.py (read-only evaluation manifests)."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT / "scripts"))

import run_evaluation_snapshot as evs  # noqa: E402


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def _minimal_manifest(asset_class: str) -> dict:
    return evs.build_manifest(
        asset_class,
        lookback_days=30,
        paths={
            "callouts": "/tmp/callouts.jsonl",
            "outcomes": "/tmp/outcomes.jsonl",
            "report": "/tmp/report.json",
        },
        source_snapshot={},
        generated_at="2026-06-11T12:00:00Z",
    )


def test_manifest_schema_required_keys():
    for asset in ("stock", "crypto"):
        manifest = _minimal_manifest(asset)
        assert evs.validate_manifest_schema(manifest) == []
        assert evs.REQUIRED_MANIFEST_KEYS <= set(manifest.keys())


def test_manifest_schema_rejects_bad_asset_class():
    manifest = _minimal_manifest("stock")
    manifest["asset_class"] = "bonds"
    issues = evs.validate_manifest_schema(manifest)
    assert any("asset_class" in i for i in issues)


def test_idempotent_manifest_content_except_generated_at(tmp_path: Path):
    base = tmp_path / "stock"
    paths = evs._default_perf_paths(str(base))
    _write_jsonl(Path(paths["callouts"]), [{"callout_id": "a"}])
    _write_jsonl(Path(paths["outcomes"]), [{"callout_id": "a"}])
    Path(paths["report"]).write_text("{}", encoding="utf-8")

    snap = {"warren_output_path": "/x", "warren_age_hours": 1.0}
    m1 = evs.build_manifest("stock", lookback_days=30, paths=paths, source_snapshot=snap, generated_at="FIXED")
    m2 = evs.build_manifest("stock", lookback_days=30, paths=paths, source_snapshot=snap, generated_at="FIXED")
    assert m1 == m2


def test_missing_source_folders_do_not_crash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    warren_out = tmp_path / "warren_output"
    crypto_out = tmp_path / "crypto_output"
    nitter_dir = tmp_path / "no_nitter"

    monkeypatch.setattr(evs.wl, "WARREN_OUTPUT_DIR", str(warren_out))
    monkeypatch.setattr(evs.cpf, "_crypto_output_dir", lambda: str(crypto_out))

    result = evs.run_snapshot(30, skip_persist=True, nitter_source_dir=str(nitter_dir))
    assert "manifests" in result
    assert Path(result["manifests"]["stock"]).is_file()
    assert Path(result["manifests"]["crypto"]).is_file()

    with Path(result["manifests"]["stock"]).open(encoding="utf-8") as fh:
        stock = json.load(fh)
    assert evs.validate_manifest_schema(stock) == []
    assert "missing latest Warren output" in stock["freshness_warnings"]
    assert "missing latest Nitter file" in stock["freshness_warnings"]


def test_warnings_not_failures_when_data_sparse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    warren_out = tmp_path / "warren_output"
    warren_day = warren_out / "2026-06-10"
    warren_day.mkdir(parents=True)
    (warren_day / "run_summary.json").write_text(json.dumps({"success": True}), encoding="utf-8")

    crypto_out = tmp_path / "crypto_output"
    scans = crypto_out / "scans"
    scans.mkdir(parents=True)
    old_ts = "2020-01-01T00:00:00+00:00"
    (scans / "latest.json").write_text(json.dumps({"timestamp": old_ts}), encoding="utf-8")

    stock_perf = warren_out / "callout_performance" / "stock"
    crypto_perf = crypto_out / "callout_performance" / "crypto"

    monkeypatch.setattr(evs.wl, "WARREN_OUTPUT_DIR", str(warren_out))
    monkeypatch.setattr(evs.cpf, "_crypto_output_dir", lambda: str(crypto_out))

    result = evs.run_snapshot(7, skip_persist=True, nitter_source_dir=str(tmp_path / "empty"))
    assert result["persist_errors"] == []

    with Path(result["manifests"]["stock"]).open(encoding="utf-8") as fh:
        stock = json.load(fh)
    with Path(result["manifests"]["crypto"]).open(encoding="utf-8") as fh:
        crypto = json.load(fh)

    assert "no callouts" in stock["freshness_warnings"]
    assert "no outcomes" in stock["freshness_warnings"]
    assert "report missing" in stock["freshness_warnings"]
    assert stock["latest_source_snapshot"]["warren_output_date"] == "2026-06-10"

    assert "no callouts" in crypto["freshness_warnings"]
    assert any("crypto scan older" in w for w in crypto["freshness_warnings"])


def test_write_manifest_roundtrip(tmp_path: Path):
    manifest = _minimal_manifest("crypto")
    manifest["callout_count"] = 0
    manifest["outcome_count"] = 0
    path = evs.write_manifest(manifest, str(tmp_path))
    loaded = json.loads(Path(path).read_text(encoding="utf-8"))
    assert loaded["asset_class"] == "crypto"
    assert evs.validate_manifest_schema(loaded) == []
