"""
Deterministic tests for the Sector ETF Allocation Check decision layer.

These tests are pure — no yfinance, no file I/O.  They call
morning_status.debug_etf_allocation_check() directly with scalar return
values and assert the expected vehicle + signals.

Run:
    cd "/home/klefe6/Coding Projects/rrg"
    python -m pytest test_etf_allocation.py -v
"""
import pytest
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

import morning_status as ms


class TestEtfWinsVsWeakStockBasket:
    """Scenario 1: XLK clearly winning, weak stock basket → Sector ETF preferred."""

    def test_vehicle_is_sector_etf(self):
        r = ms.debug_etf_allocation_check(
            primary_avg=1.0,   # stock basket weak
            etf_avg=5.0,       # ETF up +5% (edge = +4%, > 3% threshold)
            spy_avg=2.5,
        )
        assert r["decision"]["vehicle"] == "sector_etf", r["decision"]

    def test_etf_edge_rule_fires(self):
        r = ms.debug_etf_allocation_check(primary_avg=1.0, etf_avg=5.0, spy_avg=2.5)
        rules = r["decision"]["rules_fired"]
        assert any("ETF leads" in rule for rule in rules), rules

    def test_allocation_etf_heavy(self):
        r = ms.debug_etf_allocation_check(primary_avg=1.0, etf_avg=5.0, spy_avg=2.5)
        bal = r["allocation"]["balanced"]
        assert bal["etf"] > bal["stocks"], \
            f"Balanced should weight ETF higher; got {bal}"

    def test_exactly_at_threshold_no_flip(self):
        """Edge = exactly threshold — should NOT trigger (must be strictly greater)."""
        r = ms.debug_etf_allocation_check(
            primary_avg=2.0,
            etf_avg=5.0,   # gap = exactly 3.0, NOT > 3.0
            spy_avg=2.5,
            etf_edge_threshold=3.0,
        )
        # gap is exactly threshold → rule 1 does NOT fire → falls to rule 5
        assert r["decision"]["vehicle"] == "stock_alerts"

    def test_basket_negative_etf_positive_strong_etf_preference(self):
        r = ms.debug_etf_allocation_check(
            primary_avg=-1.5,  # negative basket
            etf_avg=2.0,       # ETF positive but edge < 3%
            spy_avg=1.8,
        )
        assert r["decision"]["vehicle"] == "sector_etf"
        rules = r["decision"]["rules_fired"]
        assert any("negative" in rule.lower() or "ETF leads" in rule for rule in rules), rules


class TestStockBasketWinsVsEtf:
    """Scenario 2: Stock basket outperforms ETF → Stock Alerts preferred."""

    def test_vehicle_is_stock_alerts(self):
        r = ms.debug_etf_allocation_check(
            primary_avg=7.5,   # stock basket strong
            etf_avg=2.0,       # ETF lags
            spy_avg=3.0,
        )
        assert r["decision"]["vehicle"] == "stock_alerts", r["decision"]

    def test_rule5_fires(self):
        r = ms.debug_etf_allocation_check(primary_avg=7.5, etf_avg=2.0, spy_avg=3.0)
        rules = r["decision"]["rules_fired"]
        assert any("Stock alerts" in rule for rule in rules), rules

    def test_allocation_stock_heavy(self):
        r = ms.debug_etf_allocation_check(primary_avg=7.5, etf_avg=2.0, spy_avg=3.0)
        bal = r["allocation"]["balanced"]
        assert bal["stocks"] > bal["etf"], \
            f"Balanced should weight stocks higher; got {bal}"

    def test_diff_is_positive(self):
        r = ms.debug_etf_allocation_check(primary_avg=7.5, etf_avg=2.0, spy_avg=3.0)
        assert r["diff_stock_vs_etf"] == pytest.approx(5.5, abs=0.01)


class TestBothWeakVsSpy:
    """Scenario 3: Both stock alerts AND ETF negative and trailing SPY → Wait preferred."""

    def test_vehicle_is_wait(self):
        r = ms.debug_etf_allocation_check(
            primary_avg=-3.0,  # negative
            etf_avg=-2.0,      # negative
            spy_avg=1.0,       # SPY positive → both below SPY
        )
        assert r["decision"]["vehicle"] == "wait", r["decision"]

    def test_rule6_fires(self):
        r = ms.debug_etf_allocation_check(
            primary_avg=-3.0, etf_avg=-2.0, spy_avg=1.0
        )
        rules = r["decision"]["rules_fired"]
        assert any("wait" in rule.lower() or "broad index" in rule.lower() for rule in rules), rules

    def test_allocation_mostly_cash(self):
        r = ms.debug_etf_allocation_check(
            primary_avg=-3.0, etf_avg=-2.0, spy_avg=1.0
        )
        cons = r["allocation"]["conservative"]
        assert cons["cash"] == 100, f"Conservative should be all-cash in wait; got {cons}"

    def test_only_etf_negative_does_not_trigger_wait(self):
        """Only the stock basket is negative; ETF positive — should NOT be wait."""
        r = ms.debug_etf_allocation_check(
            primary_avg=-1.0, etf_avg=3.0, spy_avg=2.0
        )
        assert r["decision"]["vehicle"] != "wait"


class TestOwnedLaggingAvoidAdding:
    """Scenario 4: Owned positions lagging ETF and SPY → avoid_adding signal."""

    def test_owned_signal_is_avoid_adding(self):
        r = ms.debug_etf_allocation_check(
            primary_avg=4.0,
            etf_avg=3.0,
            spy_avg=2.5,
            owned_avg=-1.0,   # owned lagging both ETF (+3%) and SPY (+2.5%)
            new_avg=6.0,
        )
        assert r["decision"]["owned_signal"] == "avoid_adding", r["decision"]

    def test_rule3_fires(self):
        r = ms.debug_etf_allocation_check(
            primary_avg=4.0, etf_avg=3.0, spy_avg=2.5,
            owned_avg=-1.0, new_avg=6.0,
        )
        rules = r["decision"]["rules_fired"]
        assert any("avoid adding" in rule.lower() for rule in rules), rules

    def test_new_preferred_when_new_beats_owned(self):
        r = ms.debug_etf_allocation_check(
            primary_avg=4.0, etf_avg=3.0, spy_avg=2.5,
            owned_avg=-1.0, new_avg=6.0,
        )
        assert r["decision"]["new_signal"] == "prefer_new", r["decision"]

    def test_owned_lagging_only_etf_not_spy_no_signal(self):
        """Owned lags ETF but NOT SPY — rule 3 requires lagging both."""
        r = ms.debug_etf_allocation_check(
            primary_avg=4.0,
            etf_avg=3.0,
            spy_avg=0.5,    # SPY barely positive
            owned_avg=1.0,  # owned beats SPY but not ETF
        )
        assert r["decision"]["owned_signal"] == "ok"


class TestEdgeCases:
    """None values and missing data should not raise exceptions."""

    def test_all_none_returns_wait_or_stock(self):
        r = ms.debug_etf_allocation_check(
            primary_avg=None, etf_avg=None, spy_avg=None
        )
        # Should not raise; vehicle should be the default fallback
        assert r["decision"]["vehicle"] in ("stock_alerts", "wait", "sector_etf", "spy_qqq")

    def test_spy_none_no_crash(self):
        r = ms.debug_etf_allocation_check(primary_avg=2.0, etf_avg=1.0, spy_avg=None)
        assert r["diff_stock_vs_etf"] == pytest.approx(1.0, abs=0.01)

    def test_etf_none_fallback(self):
        r = ms.debug_etf_allocation_check(primary_avg=2.0, etf_avg=None, spy_avg=1.0)
        assert r["diff_stock_vs_etf"] is None
        assert r["decision"]["vehicle"] == "stock_alerts"

    def test_allocation_keys_always_present(self):
        for scenario in [
            dict(primary_avg=5.0, etf_avg=1.0, spy_avg=2.0),
            dict(primary_avg=1.0, etf_avg=5.0, spy_avg=2.0),
            dict(primary_avg=-2.0, etf_avg=-1.0, spy_avg=1.5),
        ]:
            r = ms.debug_etf_allocation_check(**scenario)
            for profile in ("conservative", "balanced", "aggressive"):
                assert profile in r["allocation"], f"{profile} missing for {scenario}"
                w = r["allocation"][profile]
                assert "etf" in w and "stocks" in w, f"keys missing in {profile}: {w}"
