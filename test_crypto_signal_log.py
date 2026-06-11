from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

_CRYPTO_DIR = Path(__file__).resolve().parent / "crypto_dashboard"
sys.path.insert(0, str(_CRYPTO_DIR))

import signal_log as sl  # noqa: E402
import signal_outcome_eval as soe  # noqa: E402


def test_make_signal_id_deterministic():
    ts = "2026-06-11T22:03:04.442679+00:00"
    a = sl.make_signal_id(ts, "RUNE-USDT", "swing", "LONG")
    b = sl.make_signal_id(ts, "RUNE-USDT", "swing", "LONG")
    assert a == b
    assert a == "20260611T220304_RUNE-USDT_swing_LONG"


def test_preview_sends_do_not_log(tmp_path: Path):
    log_file = tmp_path / "signal_log.jsonl"
    run = {"timestamp": "2026-06-11T22:03:04+00:00", "regime_primary": "Risk Off"}
    display = {
        "swing": [{"inst_id": "RUNE-USDT", "symbol": "RUNE", "direction": "LONG"}],
        "scalp_long": [],
        "scalp_short": [],
    }
    n = sl.log_displayed_signals(
        run_dict=run,
        display=display,
        chart_plan=[],
        chart_audit=[],
        digest_reason="preview",
        is_preview=True,
        regime="Risk Off",
        log_path=log_file,
    )
    assert n == 0
    assert not log_file.exists() or log_file.read_text().strip() == ""


def test_one_row_per_displayed_signal(tmp_path: Path):
    log_file = tmp_path / "signal_log.jsonl"
    run = {"timestamp": "2026-06-11T22:03:04+00:00", "regime_primary": "Risk Off"}
    display = {
        "swing": [
            {"inst_id": "RUNE-USDT", "symbol": "RUNE", "direction": "LONG", "signal_type": "swing"},
            {"inst_id": "ONDO-USDT", "symbol": "ONDO", "direction": "LONG", "signal_type": "swing"},
        ],
        "scalp_long": [{"inst_id": "SOL-USDT", "symbol": "SOL", "direction": "LONG", "signal_type": "scalp"}],
        "scalp_short": [],
    }
    n = sl.log_displayed_signals(
        run_dict=run,
        display=display,
        chart_plan=[],
        chart_audit=[],
        digest_reason="test",
        is_preview=False,
        regime="Risk Off",
        log_path=log_file,
    )
    assert n == 3
    lines = [ln for ln in log_file.read_text().splitlines() if ln.strip()]
    assert len(lines) == 3
    ids = {json.loads(ln)["signal_id"] for ln in lines}
    assert len(ids) == 3


def test_build_send_row_required_keys():
    run_dict = {
        "timestamp": "2026-06-11T22:03:04+00:00",
        "version": "2D.0",
        "regime_primary": "Risk Off",
        "market_context": {
            "btc": {"trend_1h": "bullish"},
            "eth": {"trend_1h": "bullish"},
            "alts_proxy": {"trend_1h": "bearish"},
            "overall": {"label": "Neutral", "long_bias_score": 56.5},
        },
    }
    sig = {
        "inst_id": "RUNE-USDT",
        "symbol": "RUNE",
        "direction": "LONG",
        "signal_type": "swing",
        "timeframe_label": "1–6 weeks",
        "status": "WATCHLIST",
        "score_100": 34,
        "signal_score_100": 39,
        "setup_quality": "Reject",
        "reason": "+10.9% vs BTC",
        "invalid_if": "daily close below EMA50",
        "entry": 0.398,
        "stop": 0.425,
        "price": 0.398,
    }
    row = sl.build_send_row(
        run_dict=run_dict,
        sig=sig,
        section="swing",
        display_rank=1,
        digest_reason="scheduled 2h digest",
        is_preview=False,
        regime="Risk Off",
        selected_for_chart=True,
        chart_timeframe_bar="4H",
        chart_audit_row={
            "chart_generated": "yes",
            "chart_path": "/tmp/RUNE.png",
            "file_size_bytes": 1000,
            "telegram_send_attempted": "yes",
            "telegram_send_succeeded": "yes",
            "telegram_message_type": "photo",
            "skip_reason": "",
        },
    )
    required = {
        "schema_version", "record_type", "signal_id", "logged_at_utc",
        "scan_timestamp", "symbol", "inst_id", "section", "direction",
        "status", "score_100", "setup_quality", "reason", "invalid_if",
        "entry", "stop", "target", "risk_reward", "market_context_snapshot",
        "selected_for_chart", "chart_generated", "outcome_status",
        "alert_useful", "review_notes", "is_preview",
    }
    assert required.issubset(row.keys())
    assert row["is_preview"] is False
    assert row["outcome_status"] is None


def test_append_signal_log_writes_valid_jsonl(tmp_path: Path):
    log_file = tmp_path / "signal_log.jsonl"
    row = {"signal_id": "test", "symbol": "BTC"}
    sl.append_signal_log(row, path=log_file)
    lines = log_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["signal_id"] == "test"


def test_append_signal_log_failure_does_not_raise(tmp_path: Path, monkeypatch):
    def _boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "open", _boom)
    sl.append_signal_log({"signal_id": "x"}, path=tmp_path / "nope.jsonl")


def _make_df(rows: list[tuple[float, float, float]]) -> pd.DataFrame:
    base = datetime(2026, 6, 11, 12, tzinfo=timezone.utc)
    data = []
    for i, (low, high, close) in enumerate(rows):
        ts = int((base + timedelta(hours=i)).timestamp() * 1000)
        data.append({"ts_ms": ts, "open": close, "high": high, "low": low, "close": close})
    return pd.DataFrame(data)


def test_check_signal_outcome_target_hit():
    signal = {
        "direction": "LONG",
        "entry": 100.0,
        "stop": 95.0,
        "target": 110.0,
        "price_at_send": 100.0,
    }
    df = _make_df([
        (99, 101, 100),   # entry trigger
        (100, 111, 108),  # target hit
    ])
    assert soe.check_signal_outcome(signal, df) == "target_hit"


def test_check_signal_outcome_stop_hit():
    signal = {
        "direction": "LONG",
        "entry": 100.0,
        "stop": 95.0,
        "target": 110.0,
        "price_at_send": 100.0,
    }
    df = _make_df([
        (99, 101, 100),
        (94, 98, 95),
    ])
    assert soe.check_signal_outcome(signal, df) == "stop_hit"


def test_check_signal_outcome_no_entry():
    signal = {
        "direction": "LONG",
        "entry": 100.0,
        "stop": 95.0,
        "target": 110.0,
        "price_at_send": 100.0,
    }
    df = _make_df([
        (102, 105, 103),
        (103, 106, 104),
    ])
    assert soe.check_signal_outcome(signal, df) == "no_entry"


def test_outcome_evaluator_skips_already_evaluated(tmp_path: Path):
    log_file = tmp_path / "signal_log.jsonl"
    out_file = tmp_path / "signal_outcomes.jsonl"
    old = datetime.now(timezone.utc) - timedelta(hours=30)
    row = {
        "record_type": "alert_sent",
        "signal_id": "20260610T120000_BTC-USDT_swing_LONG",
        "logged_at_utc": old.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "scan_timestamp": old.isoformat(),
        "inst_id": "BTC-USDT",
        "section": "swing",
        "direction": "LONG",
        "entry": 100,
        "stop": 95,
        "target": 110,
        "price_at_send": 100,
        "is_preview": False,
    }
    log_file.write_text(json.dumps(row) + "\n", encoding="utf-8")
    out_file.write_text(
        json.dumps({"record_type": "outcome_update", "signal_id": row["signal_id"]}) + "\n",
        encoding="utf-8",
    )

    stats = soe.evaluate_pending_signals(
        signal_log_path=log_file,
        outcomes_path=out_file,
        fetch_candles=False,
        now=datetime.now(timezone.utc),
    )
    assert stats["skipped_done"] == 1
    assert stats["evaluated"] == 0


def test_outcome_evaluator_skips_young_signals(tmp_path: Path):
    log_file = tmp_path / "signal_log.jsonl"
    out_file = tmp_path / "signal_outcomes.jsonl"
    recent = datetime.now(timezone.utc) - timedelta(minutes=30)
    row = {
        "record_type": "alert_sent",
        "signal_id": "20260611T220000_SOL-USDT_scalp_long_LONG",
        "logged_at_utc": recent.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "inst_id": "SOL-USDT",
        "section": "scalp_long",
        "direction": "LONG",
        "entry": 150,
        "stop": 145,
        "target": 160,
        "is_preview": False,
    }
    log_file.write_text(json.dumps(row) + "\n", encoding="utf-8")

    stats = soe.evaluate_pending_signals(
        signal_log_path=log_file,
        outcomes_path=out_file,
        fetch_candles=False,
    )
    assert stats["skipped_young"] == 1
    assert stats["evaluated"] == 0
