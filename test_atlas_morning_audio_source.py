"""
Tests for Atlas Morning Audio Source readiness and sector ETF mapping.

Run:
    cd "/home/klefe6/Coding Projects/rrg"
    .venv/bin/python -m pytest test_atlas_morning_audio_source.py test_etf_allocation.py -v
"""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

import atlas_sector_etf_mapping as asem
import atlas_morning_audio_source as amas
import atlas_owned_performance as aop
import atlas_portfolio_export as ape
import morning_status as ms


def _warn_args(**overrides):
    base = {
        "w": amas.wl.WarrenResult(
            date="2026-06-02", freshness="FRESH", success=True,
            output_path="/tmp", generated_at="",
        ),
        "pa_rows": [{"scan_date": "2026-06-02"}],
        "coverage": {"coverage_percent": 80},
        "allocation_check": {"etf_ret": 5.0, "n_owned": 0},
        "portfolio_ready": True,
        "lag_status": "expected_lag",
        "owned_perf": {"data_status": "ok", "positions_with_price_data": 2},
        "owned_tickers": {"BKU", "CI"},
        "alert_tickers": set(),
    }
    base.update(overrides)
    return base


def _mock_owned_perf(**overrides):
    base = {
        "source": "config/current_portfolio.json",
        "baseline_date": "2026-06-02",
        "positions_total": 2,
        "positions_with_price_data": 2,
        "positions_missing_price_data": [],
        "owned_rows": [],
        "owned_average_return": 1.5,
        "owned_vs_spy": -0.5,
        "owned_vs_qqq": -1.0,
        "owned_vs_primary_sector_etf": -2.0,
        "laggards": [{"ticker": "BKU", "return_pct": -2.0, "vs_spy": -3.0, "sector_etf": "XLF"}],
        "leaders": [{"ticker": "MSFT", "return_pct": 4.0, "vs_spy": 2.0, "sector_etf": "XLK"}],
        "avoid_adding_candidates": [{"ticker": "BKU", "return_pct": -2.0, "vs_spy": -3.0, "sector_etf": "XLF"}],
        "hold_candidates": [{"ticker": "MSFT", "return_pct": 4.0, "vs_spy": 2.0, "sector_etf": "XLK"}],
        "trim_review_candidates": [],
        "data_status": "ok",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Sector ETF mapping
# ---------------------------------------------------------------------------

class TestSectorEtfMapping:
    def test_existing_sector_etf_wins_over_inferred(self):
        r = asem.infer_sector_etf(
            existing_etf="XLK",
            setup="semiconductor breakout",
            sector_text="Technology",
        )
        assert r["sector_etf"] == "XLK"
        assert r["source"] == "existing"

    def test_semiconductor_maps_to_smh(self):
        r = asem.infer_sector_etf(setup="Semiconductor momentum play")
        assert r["sector_etf"] == "SMH"
        assert r["source"] == "inferred"
        assert "SOXX" in r["candidate_etfs"]

    def test_software_maps_to_igv(self):
        r = asem.infer_sector_etf(sector_text="Application software sector")
        assert r["sector_etf"] == "IGV"
        assert r["source"] == "inferred"

    def test_cloud_maps_to_wcld(self):
        r = asem.infer_sector_etf(setup="Cloud computing infrastructure")
        assert r["sector_etf"] in ("WCLD", "SKYY")
        assert r["source"] == "inferred"

    def test_cybersecurity_maps_to_cibr(self):
        r = asem.infer_sector_etf(industry_text="Cybersecurity software")
        assert r["sector_etf"] == "CIBR"

    def test_unknown_sector_stays_unknown(self):
        r = asem.infer_sector_etf(setup="Pullback to EMA", ticker="XYZ")
        assert r["source"] == "unknown"
        assert r["sector_etf"] == ""

    def test_financials_maps_to_xlf(self):
        r = asem.infer_sector_etf(sector_text="Financials sector leaders")
        assert r["sector_etf"] == "XLF"

    def test_notes_and_thesis_used_for_inference(self):
        row = {"ticker": "CRWD", "setup": "Pullback", "notes": "Cybersecurity leader"}
        enriched = asem.enrich_alert_row(row)
        assert enriched["sector_etf"] == "CIBR"
        assert enriched["sector_etf_source"] == "inferred"


class TestSectorEtfCoverage:
    def _rows(self):
        return [
            {"ticker": "NVDA", "sector_etf": "XLK", "sector_etf_source": "existing"},
            {"ticker": "AMD", "sector_etf": "SMH", "sector_etf_source": "inferred"},
            {"ticker": "FOO", "sector_etf": "", "sector_etf_source": "unknown"},
            {"ticker": "MSFT", "sector_etf": "IGV", "sector_etf_source": "inferred"},
        ]

    def test_coverage_percent(self):
        cov = asem.compute_sector_etf_coverage(self._rows())
        assert cov["total_alert_rows"] == 4
        assert cov["rows_with_existing_sector_etf"] == 1
        assert cov["rows_inferred_by_mapping"] == 2
        assert cov["rows_still_unknown"] == 1
        assert cov["coverage_percent"] == 75.0


class TestTechnologySubsectorBenchmarks:
    def test_benchmark_fields_when_data_exists(self):
        returns = {"XLK": 5.0, "SMH": 7.0, "IGV": 2.0, "SPY": 1.0, "QQQ": 3.0}
        summary = asem.technology_subsector_summary(
            [{"sector_etf": "SMH", "sector_etf_source": "inferred"}],
            benchmark_returns=returns,
        )
        assert summary["subsector_benchmark_returns"]["SMH"] == 7.0
        assert summary["subsector_leaders"][0]["etf"] == "SMH"
        assert summary["subsector_data_status"] in ("ok", "partial")


# ---------------------------------------------------------------------------
# Post alert lag classification
# ---------------------------------------------------------------------------

class TestPostAlertLag:
    def test_expected_lag_no_stale_warning(self):
        lag = amas._classify_post_alert_lag(
            latest_scan_date="2026-06-02",
            post_alert_latest_date="2026-05-29",
        )
        assert lag["lag_status"] == "expected_lag"
        warnings, notes = amas._build_warnings_and_notes(**_warn_args(lag_status=lag["lag_status"]))
        assert "post_alert_lag_stale" not in warnings
        assert "post_alert_data_stale" not in warnings

    def test_stale_lag_creates_warning(self):
        lag = amas._classify_post_alert_lag(
            latest_scan_date="2026-06-02",
            post_alert_latest_date="2026-05-15",
        )
        assert lag["lag_status"] == "stale_lag"
        warnings, _ = amas._build_warnings_and_notes(**_warn_args(lag_status=lag["lag_status"]))
        assert "post_alert_lag_stale" in warnings


# ---------------------------------------------------------------------------
# Portfolio export readiness
# ---------------------------------------------------------------------------

class TestPortfolioExportReadiness:
    def test_missing_portfolio_creates_warning(self, monkeypatch):
        monkeypatch.setattr(
            ape, "resolve_portfolio_readiness",
            lambda **kw: {
                "portfolio_source": "none",
                "portfolio_position_count": 0,
                "portfolio_export_exists": False,
                "portfolio_export_path": None,
                "portfolio_last_modified": None,
                "owned_tickers_detected": [],
                "portfolio_ready": False,
            },
        )
        warnings, _ = amas._build_warnings_and_notes(**_warn_args(
            portfolio_ready=False,
            owned_tickers=set(),
            owned_perf={"data_status": "missing", "positions_with_price_data": 0},
        ))
        assert "portfolio_export_missing" in warnings

    def test_portfolio_found_clears_warning(self, monkeypatch):
        warnings, _ = amas._build_warnings_and_notes(**_warn_args(
            owned_tickers={"AAPL"},
            owned_perf={"data_status": "ok", "positions_with_price_data": 1},
            alert_tickers={"NVDA"},
        ))
        assert "portfolio_export_missing" not in warnings
        assert "owned_positions_missing_from_alerts" not in warnings


# ---------------------------------------------------------------------------
# Readiness rules
# ---------------------------------------------------------------------------

class TestReadinessRules:
    def test_low_coverage_not_ready(self):
        warnings = ["sector_etf_coverage_too_low", "portfolio_export_missing"]
        cov = {"coverage_percent": 20.0}
        assert amas._overall_readiness(warnings, cov) == "not_ready"

    def test_warnings_only_ready_with_warnings(self):
        warnings = ["sector_etf_coverage_too_low"]
        cov = {"coverage_percent": 65.0}
        assert amas._overall_readiness(warnings, cov) == "ready_with_warnings"

    def test_no_warnings_ready(self):
        assert amas._overall_readiness([], {"coverage_percent": 90.0}) == "ready"

    def test_missing_post_alert_not_ready(self):
        warnings = ["post_alert_data_missing"]
        assert amas._overall_readiness(warnings, {"coverage_percent": 90.0}) == "not_ready"

    def test_notes_only_becomes_ready(self):
        assert amas._overall_readiness([], {"coverage_percent": 90.0}) == "ready"


class TestOwnedPerformanceWarnings:
    def test_owned_paths_clear_blocking_warning(self):
        warnings, notes = amas._build_warnings_and_notes(**_warn_args(
            owned_tickers={"BKU", "MSFT"},
            alert_tickers={"NVDA"},
            owned_perf={"data_status": "ok", "positions_with_price_data": 2},
        ))
        assert "owned_positions_missing_from_alerts" not in warnings
        assert "owned_position_performance_missing" not in warnings
        assert "owned_positions_not_in_alert_paths" in notes

    def test_missing_owned_price_data_warns(self):
        warnings, notes = amas._build_warnings_and_notes(**_warn_args(
            owned_tickers={"BKU"},
            owned_perf={"data_status": "missing", "positions_with_price_data": 0},
        ))
        assert "owned_position_performance_missing" in warnings
        assert "owned_positions_not_in_alert_paths" not in notes

    def test_readiness_ready_when_only_informational_note(self):
        warnings, notes = amas._build_warnings_and_notes(**_warn_args(
            owned_tickers={"BKU"},
            alert_tickers=set(),
            owned_perf={"data_status": "ok", "positions_with_price_data": 1},
        ))
        readiness = amas._overall_readiness(warnings, {"coverage_percent": 100.0})
        assert readiness == "ready"
        assert notes == ["owned_positions_not_in_alert_paths"]


class TestOwnedPerformanceCompute:
    def _positions_file(self, tmp_path, monkeypatch):
        path = tmp_path / "portfolio.json"
        path.write_text('{"positions": [{"ticker": "LAG", "sector": "XLF"}, {"ticker": "WIN", "sector": "XLK"}]}')
        monkeypatch.setattr(ape, "CONFIG_EXPORT_PATH", str(path))
        return str(path)

    def test_laggards_produce_avoid_adding_candidates(self, tmp_path, monkeypatch):
        path = self._positions_file(tmp_path, monkeypatch)

        def price_fetch(ticker):
            if ticker == "LAG":
                return _fake_hist([100, 98, 96, 94])
            return _fake_hist([100, 102, 104, 108])

        def bm_fetch(sym):
            if sym == "SPY":
                return _fake_hist([100, 101, 102, 103])
            if sym == "XLF":
                return _fake_hist([100, 101, 102, 103])
            if sym == "XLK":
                return _fake_hist([100, 105, 110, 115])
            return _fake_hist([100, 101, 102, 103])

        result = aop.compute_owned_position_performance(
            baseline_date="2026-05-26",
            primary_sector_etf="XLK",
            alert_tickers=set(),
            portfolio_path=path,
            price_fetcher=price_fetch,
            bm_fetcher=bm_fetch,
        )
        assert result["data_status"] == "ok"
        assert any(x["ticker"] == "LAG" for x in result["avoid_adding_candidates"])
        assert result["laggards"][0]["ticker"] == "LAG"

    def test_leaders_produce_hold_candidates(self, tmp_path, monkeypatch):
        path = self._positions_file(tmp_path, monkeypatch)

        def price_fetch(ticker):
            if ticker == "WIN":
                return _fake_hist([100, 105, 110, 115])
            return _fake_hist([100, 98, 96, 94])

        def bm_fetch(sym):
            return _fake_hist([100, 101, 102, 103])

        result = aop.compute_owned_position_performance(
            baseline_date="2026-05-26",
            primary_sector_etf="XLK",
            alert_tickers=set(),
            portfolio_path=path,
            price_fetcher=price_fetch,
            bm_fetcher=bm_fetch,
        )
        assert any(x["ticker"] == "WIN" for x in result["hold_candidates"])
        assert result["leaders"][0]["ticker"] == "WIN"


def _fake_hist(closes):
    import pandas as pd
    dates = pd.date_range(end=pd.Timestamp("2026-06-02"), periods=len(closes), freq="B")
    return pd.DataFrame({"Close": closes}, index=dates)


def _mock_allocation(**overrides):
    base = {
        "primary_etf": "XLK", "primary_ret": 1.0, "etf_ret": 5.0,
        "spy_ret": 2.0, "qqq_ret": 3.0, "mag7_ret": 2.5,
        "diff_stock_vs_etf": -4.0, "n_stocks": 2, "n_new": 1, "n_owned": 1,
        "day_window": 10,
        "decision": {
            "vehicle": "sector_etf", "vehicle_label": "Sector ETF",
            "owned_signal": "avoid_adding", "new_signal": "prefer_new",
            "rules_fired": ["ETF leads"],
        },
        "allocation": ms._compute_allocation_splits({"vehicle": "sector_etf"}),
        "error": None,
    }
    base.update(overrides)
    return base


class TestBuildPayloadWithMocks:
    def test_payload_has_stable_keys(self, monkeypatch):
        """Public endpoint contract — all top-level keys must be present."""
        monkeypatch.setattr(
            amas.wl, "get_latest_warren",
            lambda force_refresh=False: amas.wl.WarrenResult(
                date="2026-06-02", freshness="FRESH", success=True,
                output_path="/tmp", generated_at="",
                top_sectors=["XLK"], ticker_sector={"NVDA": "XLK"},
            ),
        )
        monkeypatch.setattr(amas, "_load_warren_context_by_date", lambda days=60: {})
        monkeypatch.setattr(
            ape, "resolve_portfolio_readiness",
            lambda **kw: {
                "portfolio_source": "config/current_portfolio.json",
                "portfolio_position_count": 3,
                "portfolio_export_exists": True,
                "portfolio_export_path": "/tmp/p.json",
                "portfolio_last_modified": "2026-06-02T10:00:00Z",
                "owned_tickers_detected": ["NVDA"],
                "portfolio_ready": True,
            },
        )
        monkeypatch.setattr(
            ms, "compute_etf_allocation_check",
            lambda pa_data, **kw: _mock_allocation(),
        )
        monkeypatch.setattr(
            ms, "classify_suggested_tickers",
            lambda w, a, wl, archived=None: [],
        )
        monkeypatch.setattr(
            amas.ab, "fetch_subsector_benchmark_returns",
            lambda start_date, **kw: {
                "XLK": 5.0, "SMH": 6.0, "SPY": 2.0, "QQQ": 3.0,
            },
        )
        monkeypatch.setattr(
            aop, "compute_owned_position_performance",
            lambda **kw: _mock_owned_perf(),
        )

        pa_data = {
            "series": [
                {"ticker": "NVDA", "scan_date": "2026-06-02", "sector_etf": "XLK",
                 "setup": "Breakout", "portfolio_status": "New", "rank": 1},
                {"ticker": "AMD", "scan_date": "2026-06-02", "sector_etf": "",
                 "setup": "semiconductor pullback", "portfolio_status": "Already owned", "rank": 2},
            ],
            "sector_series": [],
        }
        payload = amas.build_atlas_morning_audio_source(
            pa_data,
            auto_export_portfolio=False,
            fetch_benchmarks=True,
        )

        required_keys = {
            "readiness", "warnings", "notes", "freshness", "sector_etf_coverage",
            "sector_etf_allocation_check", "sector_strength_summary",
            "portfolio_discipline_summary", "technology_subsector_summary",
            "owned_position_performance", "suggested_audio_bullets",
            "contract_version", "generated_at", "source_status_summary",
            "portfolio_ready", "lag_status",
        }
        assert required_keys.issubset(payload.keys())
        assert payload["contract_version"].startswith("atlas_morning_audio_source_v")
        opp = payload["owned_position_performance"]
        assert opp["data_status"] == "ok"
        assert "owned_rows" in opp
        assert "avoid_adding_candidates" in opp
        assert payload["readiness"] in ("ready", "ready_with_warnings", "not_ready")
        assert isinstance(payload["warnings"], list)
        assert isinstance(payload["suggested_audio_bullets"], list)
        assert payload["sector_etf_coverage"]["rows_inferred_by_mapping"] >= 1
        assert payload["technology_subsector_summary"]["subsector_benchmark_returns"]["XLK"] == 5.0

    def test_suggested_bullets_not_empty_templates(self, monkeypatch):
        monkeypatch.setattr(
            amas.wl, "get_latest_warren",
            lambda force_refresh=False: amas.wl.WarrenResult(
                date="2026-06-02", freshness="FRESH", success=True,
                output_path="/tmp", generated_at="",
                top_sectors=["XLK"],
            ),
        )
        monkeypatch.setattr(amas, "_load_warren_context_by_date", lambda days=60: {})
        monkeypatch.setattr(
            ape, "resolve_portfolio_readiness",
            lambda **kw: {
                "portfolio_source": "positions.db_export",
                "portfolio_position_count": 2,
                "portfolio_export_exists": True,
                "portfolio_export_path": "/tmp/p.json",
                "portfolio_last_modified": None,
                "owned_tickers_detected": ["NVDA"],
                "portfolio_ready": True,
            },
        )
        monkeypatch.setattr(
            ms, "compute_etf_allocation_check",
            lambda pa_data, **kw: _mock_allocation(),
        )
        monkeypatch.setattr(ms, "classify_suggested_tickers", lambda *a, **k: [])
        monkeypatch.setattr(
            amas.ab, "fetch_subsector_benchmark_returns",
            lambda start_date, **kw: {"XLK": 7.2, "SMH": 8.0, "SPY": 2.0},
        )
        monkeypatch.setattr(
            aop, "compute_owned_position_performance",
            lambda **kw: _mock_owned_perf(),
        )

        pa_data = {"series": [
            {"ticker": "NVDA", "scan_date": "2026-06-02", "sector_etf": "XLK",
             "setup": "Breakout", "portfolio_status": "New", "rank": 1},
        ]}
        payload = amas.build_atlas_morning_audio_source(
            pa_data, auto_export_portfolio=False, fetch_benchmarks=True,
        )
        bullets = payload["suggested_audio_bullets"]
        assert 6 <= len(bullets) <= 8
        assert all(len(b) > 20 for b in bullets)
        assert any("XLK" in b or "Sector ETF" in b for b in bullets)
        assert not any("TODO" in b or "template" in b.lower() for b in bullets)

    def test_unknown_sector_creates_coverage_warning(self, monkeypatch):
        monkeypatch.setattr(
            amas.wl, "get_latest_warren",
            lambda force_refresh=False: amas.wl.WarrenResult(
                date="2026-06-02", freshness="FRESH", success=True,
                output_path="/tmp", generated_at="",
            ),
        )
        monkeypatch.setattr(amas, "_load_warren_context_by_date", lambda days=60: {})
        monkeypatch.setattr(
            ape, "resolve_portfolio_readiness",
            lambda **kw: {
                "portfolio_source": "none", "portfolio_position_count": 0,
                "portfolio_export_exists": False, "portfolio_export_path": None,
                "portfolio_last_modified": None, "owned_tickers_detected": [],
                "portfolio_ready": False,
            },
        )
        monkeypatch.setattr(
            ms, "compute_etf_allocation_check",
            lambda pa_data, **kw: _mock_allocation(
                primary_ret=None, etf_ret=None, spy_ret=None,
                n_stocks=1, n_new=0, n_owned=0,
                decision={"vehicle": "wait", "vehicle_label": "Cash / Wait",
                          "owned_signal": "ok", "new_signal": "ok", "rules_fired": []},
                allocation={},
            ),
        )
        monkeypatch.setattr(ms, "classify_suggested_tickers", lambda *a, **k: [])
        monkeypatch.setattr(amas.ab, "fetch_subsector_benchmark_returns", lambda *a, **kw: {})
        monkeypatch.setattr(
            aop, "compute_owned_position_performance",
            lambda **kw: _mock_owned_perf(data_status="missing", positions_with_price_data=0),
        )

        pa_data = {"series": [
            {"ticker": "ZZZZ", "scan_date": "2026-06-02", "sector_etf": "",
             "setup": "unknown pattern", "portfolio_status": "New", "rank": 1},
        ]}
        payload = amas.build_atlas_morning_audio_source(
            pa_data, auto_export_portfolio=False, fetch_benchmarks=False,
        )
        assert "sector_etf_coverage_too_low" in payload["warnings"]
        assert payload["sector_etf_coverage"]["rows_still_unknown"] == 1


class TestEtfAllocationStillPasses:
    """Regression — existing ETF allocation tests still valid."""

    def test_xlk_winning_weak_basket(self):
        r = ms.debug_etf_allocation_check(primary_avg=1.0, etf_avg=5.0, spy_avg=2.0)
        assert r["decision"]["vehicle"] == "sector_etf"

    def test_owned_lagging(self):
        r = ms.debug_etf_allocation_check(
            primary_avg=4.0, etf_avg=3.0, spy_avg=2.5,
            owned_avg=-1.0, new_avg=6.0,
        )
        assert r["decision"]["owned_signal"] == "avoid_adding"
