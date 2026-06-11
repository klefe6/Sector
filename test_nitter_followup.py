"""
Unit tests for the Nitter-informed Warren follow-up lead pipeline
(warren_ai_daily/scripts/nitter_followup.py).

Covers:
  * parsing free-text Nitter findings into normalized leads
  * lead ranking, deduplication, and confidence filtering
  * Warren prompt generation shape
  * source freshness handling (fresh / stale / missing) via generate()
"""
from __future__ import annotations

import os
import sys

import pytest

# Make the follow-up module importable (it lives under warren_ai_daily/scripts).
_SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "warren_ai_daily", "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import nitter_followup as nf  # noqa: E402


SAMPLE_RECAP = """Tweet source status: fetched live from Nitter mirrors at 2026-06-05 10:00 UTC for report date 2026-06-04.

Overnight Market Recap.

According to Kobeissi Letter, Nvidia, $NVDA, and the Semiconductor Index, $SOX, have diverged sharply on earnings.
This is the biggest gap in months...
According to Kobeissi Letter, Nvidia $NVDA continues to surge to a record high on strong AI datacenter demand.
According to Unusual Whales, Breaking news, Kuwait is now reportedly under attack by Iran amid escalating war.
According to Zero Hedge, CPI Preview: First 4%+ inflation print in three years, Fed under pressure on rates.
According to Financial Juice, Oil prices surge as Hormuz tensions and OPEC cuts tighten crude supply.
"""


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def test_extract_leads_finds_ticker_and_theme_leads():
    leads = nf.extract_leads(SAMPLE_RECAP)
    tickers = {ld.ticker for ld in leads if ld.ticker}
    themes = {ld.theme for ld in leads if not ld.ticker}

    assert "NVDA" in tickers
    assert "SOX" in tickers
    # Macro/sector narratives become theme leads.
    assert "Geopolitics / defense" in themes
    assert "Rates & inflation" in themes
    assert "Energy / oil" in themes


def test_repeated_ticker_increments_mentions_and_confidence():
    leads = nf.extract_leads(SAMPLE_RECAP)
    nvda = next(ld for ld in leads if ld.ticker == "NVDA")
    # NVDA appears in two findings.
    assert nvda.mentions >= 2
    # Repeated mention + cashtag should clear the default min confidence.
    assert nvda.confidence >= 0.55


def test_ticker_stopwords_are_not_leads():
    leads = nf.extract_leads("According to X, the $CEO and $GDP and $USD figures rose.")
    assert all(ld.ticker not in {"CEO", "GDP", "USD"} for ld in leads)


def test_direction_bias_detection():
    leads = nf.extract_leads(SAMPLE_RECAP)
    geo = next(ld for ld in leads if ld.theme == "Geopolitics / defense")
    assert geo.direction_bias in ("bearish", "mixed")


def test_every_lead_has_unverified_risk_note():
    leads = nf.extract_leads(SAMPLE_RECAP)
    assert leads
    for ld in leads:
        assert any("Unverified" in note for note in ld.risk_notes)


# ---------------------------------------------------------------------------
# Ranking / dedupe / filtering
# ---------------------------------------------------------------------------

def test_rank_leads_respects_max_and_min_confidence():
    leads = nf.extract_leads(SAMPLE_RECAP)
    ranked = nf.rank_leads(leads, max_prompts=2, min_confidence=0.55)
    assert len(ranked) <= 2
    assert all(ld.confidence >= 0.55 for ld in ranked)
    # Sorted by score descending.
    scores = [nf._score(ld) for ld in ranked]
    assert scores == sorted(scores, reverse=True)


def test_rank_leads_deduplicates_by_ticker():
    dup1 = nf.Lead(ticker="AAPL", theme="AI / semiconductors", confidence=0.7, mentions=2)
    dup2 = nf.Lead(ticker="AAPL", theme="", confidence=0.6, mentions=1,
                   why_from_nitter=["second mention"])
    ranked = nf.rank_leads([dup1, dup2], max_prompts=5, min_confidence=0.5)
    aapl = [ld for ld in ranked if ld.ticker == "AAPL"]
    assert len(aapl) == 1
    # Merge keeps the strongest aggregate.
    assert aapl[0].confidence >= 0.7
    assert aapl[0].mentions >= 2


def test_rank_leads_filters_low_confidence():
    weak = nf.Lead(ticker="XYZ", confidence=0.30, mentions=1)
    assert nf.rank_leads([weak], max_prompts=3, min_confidence=0.55) == []


# ---------------------------------------------------------------------------
# Prompt generation
# ---------------------------------------------------------------------------

def test_build_prompt_contains_required_sections():
    lead = nf.Lead(ticker="NVDA", theme="AI / semiconductors",
                   direction_bias="bullish", confidence=0.72,
                   why_from_nitter=["surging on AI demand"],
                   risk_notes=["Unverified social signal."])
    prompt = nf.build_prompt(lead)
    assert "unverified market lead" in prompt.lower()
    assert "NVDA (AI / semiconductors)" in prompt
    assert "Direction bias: bullish" in prompt
    assert "0.72" in prompt
    assert "1-5 week" in prompt
    # The 10-point verification structure must be present.
    for n in range(1, 11):
        assert f"{n}." in prompt
    assert "Final action" in prompt


# ---------------------------------------------------------------------------
# Source loading / freshness (drives the "skip cleanly" behavior)
# ---------------------------------------------------------------------------

def _write_nitter(tmp_path, date_str, text, age_seconds=0):
    p = tmp_path / f"{date_str}_nitter.txt"
    p.write_text(text, encoding="utf-8")
    if age_seconds:
        old = p.stat().st_mtime - age_seconds
        os.utime(p, (old, old))
    return str(p)


def test_generate_ok_with_fresh_file(tmp_path):
    _write_nitter(tmp_path, "2026-06-04", SAMPLE_RECAP)
    out = nf.generate(str(tmp_path), max_prompts=3, min_confidence=0.55, max_age_hours=36)
    assert out["status"] == "ok"
    assert out["freshness"] == "fresh"
    assert len(out["prompts"]) >= 1
    assert out["prompts"][0]["prompt"]


def test_generate_skips_stale_file(tmp_path):
    _write_nitter(tmp_path, "2026-06-04", SAMPLE_RECAP, age_seconds=80 * 3600)
    out = nf.generate(str(tmp_path), max_age_hours=36)
    assert out["status"] == "skipped-stale"
    assert out["freshness"] == "stale"
    assert out["prompts"] == []


def test_generate_skips_when_missing(tmp_path):
    out = nf.generate(str(tmp_path / "does_not_exist"))
    assert out["status"] == "skipped-missing"
    assert out["freshness"] == "missing"


def test_generate_skips_when_no_leads_qualify(tmp_path):
    _write_nitter(tmp_path, "2026-06-04",
                  "Overnight Market Recap.\n\nAccording to X, nothing notable happened today.")
    out = nf.generate(str(tmp_path), min_confidence=0.99)
    assert out["status"] == "skipped-no-leads"
    assert out["prompts"] == []


def test_load_same_day_source_accepted(tmp_path):
    run_date = "2026-06-11"
    _write_nitter(tmp_path, run_date, SAMPLE_RECAP)
    loaded = nf.load_latest_nitter(str(tmp_path), max_age_hours=36, expected_date=run_date)
    assert loaded["freshness"] == "fresh"
    assert loaded["freshness_status"] == "same_day"
    assert loaded["date_matches_run"] is True
    assert loaded["run_date"] == run_date
    assert loaded["source_selection_reason"] == "expected-date-file"
    assert not loaded.get("warning")


def test_load_previous_day_age_fresh_warns(tmp_path):
    run_date = "2026-06-11"
    _write_nitter(tmp_path, "2026-06-10", SAMPLE_RECAP)
    loaded = nf.load_latest_nitter(str(tmp_path), max_age_hours=36, expected_date=run_date)
    assert loaded["freshness"] == "fresh"
    assert loaded["freshness_status"] == "age_fresh_but_previous_day"
    assert loaded["date_matches_run"] is False
    assert "2026-06-10" in str(loaded["warning"])
    assert loaded["source_selection_reason"] == "fallback-latest-mtime"


def test_generate_warns_but_continues_on_previous_day(tmp_path):
    run_date = "2026-06-11"
    _write_nitter(tmp_path, "2026-06-10", SAMPLE_RECAP)
    out = nf.generate(str(tmp_path), max_age_hours=36, expected_date=run_date, min_confidence=0.55)
    assert out["status"] == "ok"
    assert out["freshness_status"] == "age_fresh_but_previous_day"
    assert out["date_matches_run"] is False
    assert out["prompts"]


def test_generate_stale_marks_unsafe(tmp_path):
    _write_nitter(tmp_path, "2026-06-04", SAMPLE_RECAP, age_seconds=80 * 3600)
    out = nf.generate(str(tmp_path), max_age_hours=36, expected_date="2026-06-11")
    assert out["status"] == "skipped-stale"
    assert out["freshness_status"] == "stale"
    assert out["prompts"] == []


def test_generate_missing_source_clean(tmp_path):
    out = nf.generate(str(tmp_path / "missing"), expected_date="2026-06-11")
    assert out["status"] == "skipped-missing"
    assert out["freshness_status"] == "missing"
    assert out["warning"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
