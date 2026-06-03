"""
Tests for AtlasInvestor Theme Catalyst Radar.

Run:
    cd "/home/klefe6/Coding Projects/rrg"
    .venv/bin/python -m pytest test_atlas_theme_catalyst_radar.py test_atlas_morning_audio_source.py test_etf_allocation.py -v
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))

import atlas_theme_catalyst_config as atcfg
import atlas_theme_catalyst_radar as atcr
import atlas_theme_catalyst_warren as atw
import warren_loader as wl


def _fake_hist(closes, volumes=None):
    import pandas as pd
    n = len(closes)
    dates = pd.date_range(end=pd.Timestamp("2026-06-02"), periods=n, freq="B")
    vols = volumes or [1_000_000] * n
    return pd.DataFrame({"Close": closes, "Volume": vols}, index=dates)


def _long_closes(start: float, step: float, n: int = 22) -> list[float]:
    return [round(start + step * i, 2) for i in range(n)]


def _long_vols(base: float, ramp: float, n: int = 22) -> list[float]:
    return [base + ramp * i for i in range(n)]


def _strong_space_fetcher(ticker: str):
    strong = {"RKLB", "ASTS", "LUNR", "PL", "BKSY", "IRDM", "GSAT", "SATS", "SPCE"}
    if ticker in strong:
        return _fake_hist(
            _long_closes(100, 1.0),
            _long_vols(1e6, 0.08e6),
        )
    if ticker in ("SPY", "QQQ"):
        return _fake_hist(_long_closes(100, 0.05))
    return _fake_hist(_long_closes(100, 0.4))


def _crowded_space_fetcher(ticker: str):
    strong = {"RKLB", "ASTS", "LUNR", "PL", "BKSY", "IRDM", "GSAT", "SATS", "SPCE"}
    if ticker in strong:
        return _fake_hist(
            _long_closes(100, 2.5),
            _long_vols(1e6, 0.25e6),
        )
    if ticker in ("SPY", "QQQ"):
        return _fake_hist(_long_closes(100, 0.03))
    return _fake_hist(_long_closes(100, 0.4))


def _weak_space_fetcher(ticker: str):
    if ticker in {"RKLB", "ASTS", "LUNR", "PL", "BKSY", "IRDM", "GSAT", "SATS", "SPCE"}:
        return _fake_hist(_long_closes(100, -0.35))
    return _fake_hist(_long_closes(100, 0.05))


def _weak_early_space_fetcher(ticker: str):
    """Two leaders beat SPY — early phase, no RS confirmation."""
    leaders = {"RKLB", "ASTS"}
    space = {"RKLB", "ASTS", "LUNR", "PL", "BKSY", "IRDM", "GSAT", "SATS", "SPCE"}
    if ticker in leaders:
        return _fake_hist(_long_closes(100, 0.35))
    if ticker in space:
        return _fake_hist(_long_closes(100, 0.05))
    if ticker in ("SPY", "QQQ"):
        return _fake_hist(_long_closes(100, 0.05))
    return _fake_hist(_long_closes(100, 0.4))


def _mixed_space_fetcher(ticker: str):
    leaders = {"RKLB", "ASTS", "LUNR"}
    laggards = {"PL", "BKSY", "IRDM"}
    neutral = {"GSAT", "SATS", "SPCE"}
    if ticker in leaders:
        return _fake_hist(_long_closes(100, 0.45))
    if ticker in laggards:
        return _fake_hist(_long_closes(100, -0.25))
    if ticker in neutral:
        return _fake_hist(_long_closes(100, 0.05))
    if ticker in ("SPY", "QQQ"):
        return _fake_hist(_long_closes(100, 0.05))
    return _fake_hist(_long_closes(100, 0.4))


def _warren_empty():
    return wl.WarrenResult(
        date="2026-06-02", freshness="FRESH", success=True,
        output_path="/tmp", generated_at="",
        all_tickers=[], actionable=[], top3_trades=[], watch_tickers=[],
    )


def _spacex_config(**cat_overrides):
    cat = {
        "id": "spacex_ipo_june_2026",
        "private_company": "SpaceX",
        "event_type": "IPO",
        "theme": "space",
        "catalyst_label": "SpaceX IPO target date",
        "catalyst_date": "2026-06-12",
        "status": "upcoming",
        "confidence": "medium",
        "source_name": "Financial media reports",
        "date_added": "2026-06-02",
        "last_verified": "2026-06-02",
        "verification_status": "pending",
        "manual_review_required": True,
        "source_freshness": "manual",
        "action_context": "Watch theme — do not chase without proxy confirmation",
        "proxy_tickers": ["RKLB", "ASTS", "LUNR", "PL", "BKSY", "IRDM", "GSAT", "SATS", "SPCE"],
        "proxy_etfs": ["ARKX", "UFO"],
        "sector_etf_basket": ["ARKX", "UFO"],
    }
    cat.update(cat_overrides)
    return {
        "catalysts": [cat],
        "private_company_mappings": {
            "SpaceX": {
                "themes": ["space", "satellites", "launch"],
                "proxy_tickers": cat["proxy_tickers"],
                "proxy_etfs": cat["proxy_etfs"],
                "primary_etf": "ARKX",
            },
        },
    }


class TestSpaceXCatalyst:
    def test_spacex_ipo_creates_space_theme(self):
        cats = atcfg.get_catalysts()
        spacex = next(c for c in cats if c.get("private_company") == "SpaceX")
        assert spacex["theme"] == "space"
        assert spacex["event_type"] == "IPO"
        assert spacex["catalyst_date"] == "2026-06-12"
        assert spacex.get("source_name")
        assert "last_verified" in spacex

    def test_private_company_maps_to_proxy_basket(self):
        mapping = atcfg.get_private_company_mapping("SpaceX")
        assert "RKLB" in mapping["proxy_tickers"]
        assert "ARKX" in mapping["proxy_etfs"]
        assert "space" in mapping["themes"]


class TestThemePhaseClassification:
    def test_outperformance_creates_confirmed_theme(self):
        payload = atcr.build_theme_catalyst_radar(
            warren=_warren_empty(),
            price_fetcher=_strong_space_fetcher,
            bm_fetcher=_strong_space_fetcher,
            theme_scores={"themes": {}},
        )
        space = next(c for c in payload["catalysts"] if c["theme"] == "space")
        assert space["theme_phase"] in ("confirmed", "crowded")
        assert space["relative_strength_confirmation"] is True
        assert space["volume_confirmation"] is True
        assert space["theme_scores"]["overall_theme_score"] > 0

    def test_weak_basket_fading(self):
        payload = atcr.build_theme_catalyst_radar(
            warren=_warren_empty(),
            price_fetcher=_weak_space_fetcher,
            bm_fetcher=_weak_space_fetcher,
            theme_scores={"themes": {}},
        )
        space = next(c for c in payload["catalysts"] if c["theme"] == "space")
        assert space["theme_phase"] == "fading"

    def test_mixed_basket_stays_early_watch(self):
        payload = atcr.build_theme_catalyst_radar(
            warren=_warren_empty(),
            price_fetcher=_mixed_space_fetcher,
            bm_fetcher=_mixed_space_fetcher,
            theme_scores={"themes": {}},
        )
        space = next(c for c in payload["catalysts"] if c["theme"] == "space")
        assert space["theme_phase"] == "early"
        assert "space" in payload["early_watch_themes"]

    def test_crowded_basket_creates_crowded_theme(self):
        payload = atcr.build_theme_catalyst_radar(
            warren=_warren_empty(),
            price_fetcher=_crowded_space_fetcher,
            bm_fetcher=_crowded_space_fetcher,
        )
        space = next(c for c in payload["catalysts"] if c["theme"] == "space")
        assert space["theme_phase"] == "crowded"
        assert space["theme_scores"]["crowding_score"] > 30


class TestFreshnessRules:
    def test_manual_catalyst_older_than_7_days_creates_review_warning(self, monkeypatch):
        monkeypatch.setattr(
            atcfg, "load_config",
            lambda path=None: _spacex_config(last_verified="2026-05-20"),
        )
        payload = atcr.build_theme_catalyst_radar(
            warren=_warren_empty(),
            price_fetcher=_mixed_space_fetcher,
            bm_fetcher=_mixed_space_fetcher,
            as_of_date=date(2026, 6, 2),
        )
        assert "catalyst_needs_review" in payload["warnings"]

    def test_near_term_catalyst_requires_recent_verification(self, monkeypatch):
        monkeypatch.setattr(
            atcfg, "load_config",
            lambda path=None: _spacex_config(
                catalyst_date="2026-06-06",
                last_verified="2026-05-28",
            ),
        )
        payload = atcr.build_theme_catalyst_radar(
            warren=_warren_empty(),
            price_fetcher=_mixed_space_fetcher,
            bm_fetcher=_mixed_space_fetcher,
            as_of_date=date(2026, 6, 2),
        )
        assert "near_term_catalyst_needs_review" in payload["warnings"]

    def test_expired_catalyst_not_active(self, monkeypatch):
        monkeypatch.setattr(
            atcfg, "load_config",
            lambda path=None: _spacex_config(catalyst_date="2026-05-20"),
        )
        payload = atcr.build_theme_catalyst_radar(
            warren=_warren_empty(),
            price_fetcher=_weak_space_fetcher,
            bm_fetcher=_weak_space_fetcher,
            as_of_date=date(2026, 6, 2),
        )
        space = next(c for c in payload["catalysts"] if c["theme"] == "space")
        assert space["status"] == "expired"
        assert space["theme_phase"] == "expired"
        assert "space" not in payload["early_watch_themes"]
        assert "space" in payload["expired_themes"]


class TestAudioReadyFields:
    def test_audio_summary_deterministic_and_not_empty(self, monkeypatch):
        monkeypatch.setattr(
            atcfg, "load_config",
            lambda path=None: _spacex_config(),
        )
        payload = atcr.build_theme_catalyst_radar(
            warren=_warren_empty(),
            price_fetcher=_mixed_space_fetcher,
            bm_fetcher=_mixed_space_fetcher,
            as_of_date=date(2026, 6, 2),
        )
        space = payload["theme_summaries"]["space"]
        assert space["theme_audio_summary"]
        assert len(space["theme_audio_summary"]) > 40
        assert space["promotion_trigger"]
        assert space["invalidation_trigger"]
        assert len(space["top_theme_bullets"]) >= 2
        assert "do not chase" in space["theme_audio_summary"].lower()
        assert payload["suggested_audio_bullets"]


class TestWarningsAndMissedAudit:
    def test_missing_source_creates_warning_not_fake_confidence(self, monkeypatch):
        monkeypatch.setattr(
            atcfg, "load_config",
            lambda path=None: {"catalysts": [], "private_company_mappings": {}},
        )
        payload = atcr.build_theme_catalyst_radar(warren=_warren_empty())
        assert payload["readiness"] == "not_ready"
        assert "no_catalysts_configured" in payload["warnings"]

    def test_missed_theme_audit_when_proxies_outperform(self):
        payload = atcr.build_theme_catalyst_radar(
            warren=_warren_empty(),
            price_fetcher=_strong_space_fetcher,
            bm_fetcher=_strong_space_fetcher,
            theme_scores={"themes": {}},
        )
        space = next(c for c in payload["catalysts"] if c["theme"] == "space")
        assert space["missed_theme_candidate"] is True
        assert len(payload["missed_theme_candidates"]) >= 1


class TestEndpointContract:
    STABLE_KEYS = {
        "contract_version", "generated_at", "readiness", "warnings",
        "catalysts", "theme_summaries", "proxy_baskets",
        "top_confirmed_themes", "early_watch_themes",
        "crowded_or_extended_themes", "missed_theme_candidates",
        "suggested_audio_bullets", "expired_themes", "as_of_date",
    }

    def test_endpoint_returns_stable_keys(self):
        payload = atcr.build_theme_catalyst_radar(
            warren=_warren_empty(),
            price_fetcher=_strong_space_fetcher,
            bm_fetcher=_strong_space_fetcher,
        )
        assert self.STABLE_KEYS.issubset(payload.keys())
        assert payload["contract_version"] == "atlas_theme_catalyst_radar_v3"
        space = payload["theme_summaries"]["space"]
        assert "theme_scores" in space
        assert "theme_audio_summary" in space

    def test_spacex_payload_example(self, monkeypatch):
        monkeypatch.setattr(
            atcfg, "load_config",
            lambda path=None: _spacex_config(),
        )
        payload = atcr.build_theme_catalyst_radar(
            warren=_warren_empty(),
            price_fetcher=_mixed_space_fetcher,
            bm_fetcher=_mixed_space_fetcher,
            as_of_date=date(2026, 6, 2),
        )
        space = next(c for c in payload["catalysts"] if c["theme"] == "space")
        assert space["catalyst_label"] == "SpaceX IPO target date"
        assert space["catalyst_date"] == "2026-06-12"
        assert space["theme_phase"] == "early"
        assert "watch" in space["action_context"].lower()
        assert "mixed" in space["theme_audio_summary"].lower()


def _write_warren_json(tmp_path, payload: dict) -> str:
    p = tmp_path / "theme_catalyst_radar_latest.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return str(p)


def _warren_payload(catalysts: list[dict], *, generated_at: str | None = None, parse_ok: bool = True) -> dict:
    return {
        "generated_at": generated_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "warren_ai",
        "parse_ok": parse_ok,
        "catalysts": catalysts,
    }


class TestWarrenThemeMerge:
    def test_warren_adds_new_theme_catalyst(self, tmp_path, monkeypatch):
        monkeypatch.setattr(atcfg, "load_config", lambda path=None: {"catalysts": [], "private_company_mappings": {}})
        warren_path = _write_warren_json(tmp_path, _warren_payload([{
            "theme": "ai_datacenter",
            "event_type": "earnings cluster",
            "event_date": "2026-06-15",
            "confidence": "medium",
            "source_freshness": "warren_ai",
            "public_proxy_tickers": ["NVDA", "SMCI"],
            "proxy_etfs": ["SMH"],
            "suggested_watch_status": "confirmed",
        }]))
        payload = atcr.build_theme_catalyst_radar(
            warren=_warren_empty(),
            warren_theme_path=warren_path,
            price_fetcher=_mixed_space_fetcher,
            bm_fetcher=_mixed_space_fetcher,
        )
        themes = [c["theme"] for c in payload["catalysts"]]
        assert "ai_datacenter" in themes
        new_cat = next(c for c in payload["catalysts"] if c["theme"] == "ai_datacenter")
        assert new_cat["data_source"] == "warren"
        assert new_cat["theme_phase"] == "early"

    def test_warren_enriches_spacex_without_replacing_manual_date(self, tmp_path, monkeypatch):
        monkeypatch.setattr(atcfg, "load_config", lambda path=None: _spacex_config())
        warren_path = _write_warren_json(tmp_path, _warren_payload([{
            "theme": "space",
            "private_company": "SpaceX",
            "event_type": "IPO",
            "event_date": "2026-06-20",
            "confidence": "medium",
            "source_freshness": "warren_ai",
            "why_it_matters": "Wall Street IPO chatter rising.",
            "public_proxy_tickers": ["RKLB"],
            "proxy_etfs": ["ARKX"],
            "suggested_watch_status": "confirmed",
        }]))
        payload = atcr.build_theme_catalyst_radar(
            warren=_warren_empty(),
            warren_theme_path=warren_path,
            price_fetcher=_mixed_space_fetcher,
            bm_fetcher=_mixed_space_fetcher,
        )
        space = next(c for c in payload["catalysts"] if c["theme"] == "space")
        assert space["catalyst_date"] == "2026-06-12"
        assert space["data_source"] == "merged"
        assert space["why_it_matters"] == "Wall Street IPO chatter rising."
        assert space["warren_suggested_watch_status"] == "confirmed"

    def test_warren_higher_confidence_fresh_replaces_date(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            atcfg, "load_config",
            lambda path=None: _spacex_config(confidence="medium"),
        )
        warren_path = _write_warren_json(tmp_path, _warren_payload([{
            "theme": "space",
            "private_company": "SpaceX",
            "event_date": "2026-06-20",
            "confidence": "high",
            "source_freshness": "warren_ai",
        }]))
        payload = atcr.build_theme_catalyst_radar(
            warren=_warren_empty(),
            warren_theme_path=warren_path,
            price_fetcher=_mixed_space_fetcher,
            bm_fetcher=_mixed_space_fetcher,
        )
        space = next(c for c in payload["catalysts"] if c["theme"] == "space")
        assert space["catalyst_date"] == "2026-06-20"

    def test_stale_warren_warns_without_breaking_manual(self, tmp_path, monkeypatch):
        monkeypatch.setattr(atcfg, "load_config", lambda path=None: _spacex_config())
        stale_at = "2026-05-01T12:00:00Z"
        warren_path = _write_warren_json(tmp_path, _warren_payload([], generated_at=stale_at))
        payload = atcr.build_theme_catalyst_radar(
            warren=_warren_empty(),
            warren_theme_path=warren_path,
            price_fetcher=_mixed_space_fetcher,
            bm_fetcher=_mixed_space_fetcher,
            as_of_date=date(2026, 6, 2),
        )
        assert "warren_theme_output_stale" in payload["warnings"]
        assert any(c["theme"] == "space" for c in payload["catalysts"])

    def test_warren_hot_but_weak_proxies_stay_early(self, tmp_path, monkeypatch):
        monkeypatch.setattr(atcfg, "load_config", lambda path=None: _spacex_config())
        warren_path = _write_warren_json(tmp_path, _warren_payload([{
            "theme": "space",
            "private_company": "SpaceX",
            "confidence": "high",
            "source_freshness": "warren_ai",
            "suggested_watch_status": "confirmed",
        }]))
        payload = atcr.build_theme_catalyst_radar(
            warren=_warren_empty(),
            warren_theme_path=warren_path,
            price_fetcher=_weak_early_space_fetcher,
            bm_fetcher=_weak_early_space_fetcher,
        )
        space = next(c for c in payload["catalysts"] if c["theme"] == "space")
        assert space["warren_suggested_watch_status"] == "confirmed"
        assert space["theme_phase"] == "early"
        assert space["relative_strength_confirmation"] is False

    def test_strong_proxies_confirm_warren_discovered_theme(self, tmp_path, monkeypatch):
        monkeypatch.setattr(atcfg, "load_config", lambda path=None: {"catalysts": [], "private_company_mappings": {}})
        warren_path = _write_warren_json(tmp_path, _warren_payload([{
            "theme": "space",
            "event_type": "IPO",
            "confidence": "medium",
            "source_freshness": "warren_ai",
            "public_proxy_tickers": ["RKLB", "ASTS", "LUNR", "PL", "BKSY", "IRDM", "GSAT", "SATS", "SPCE"],
            "proxy_etfs": ["ARKX"],
            "suggested_watch_status": "early_watch",
        }]))
        payload = atcr.build_theme_catalyst_radar(
            warren=_warren_empty(),
            warren_theme_path=warren_path,
            price_fetcher=_strong_space_fetcher,
            bm_fetcher=_strong_space_fetcher,
        )
        space = next(c for c in payload["catalysts"] if c["theme"] == "space")
        assert space["data_source"] == "warren"
        assert space["theme_phase"] in ("confirmed", "crowded")
        assert space["relative_strength_confirmation"] is True

    def test_invalid_warren_json_warns_not_crash(self, tmp_path, monkeypatch):
        monkeypatch.setattr(atcfg, "load_config", lambda path=None: _spacex_config())
        bad_path = tmp_path / "theme_catalyst_radar_latest.json"
        bad_path.write_text("{not valid json", encoding="utf-8")
        payload = atcr.build_theme_catalyst_radar(
            warren=_warren_empty(),
            warren_theme_path=str(bad_path),
            price_fetcher=_mixed_space_fetcher,
            bm_fetcher=_mixed_space_fetcher,
        )
        assert "warren_theme_output_invalid" in payload["warnings"]
        assert payload["warren_theme_source_status"] == "invalid"
        assert any(c["theme"] == "space" for c in payload["catalysts"])

    def test_warren_source_status_fields_present(self, tmp_path, monkeypatch):
        monkeypatch.setattr(atcfg, "load_config", lambda path=None: _spacex_config())
        warren_path = _write_warren_json(tmp_path, _warren_payload([{
            "theme": "space",
            "private_company": "SpaceX",
            "confidence": "medium",
            "source_freshness": "warren_ai",
        }]))
        payload = atcr.build_theme_catalyst_radar(
            warren=_warren_empty(),
            warren_theme_path=warren_path,
            price_fetcher=_mixed_space_fetcher,
            bm_fetcher=_mixed_space_fetcher,
        )
        for key in (
            "warren_theme_source_status", "warren_theme_output_path",
            "warren_theme_generated_at", "warren_theme_catalyst_count",
            "warren_theme_stale", "manual_catalyst_count", "merged_catalyst_count",
        ):
            assert key in payload
