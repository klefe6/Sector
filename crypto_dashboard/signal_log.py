"""Append-only send-time signal log for post-hoc alert evaluation.

Writes one JSON object per line to ``output/signal_log/signal_log.jsonl``.
Never raises into Telegram or scanner callers.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

SCHEMA_VERSION = "1.0"
RECORD_TYPE_ALERT_SENT = "alert_sent"

_HERE = Path(__file__).resolve().parent
DEFAULT_LOG_PATH = _HERE / "output" / "signal_log" / "signal_log.jsonl"

_SECTIONS = ("swing", "scalp_long", "scalp_short")
_DEFAULT_CHART_TF = {
    "swing": "4H",
    "scalp_long": "15m",
    "scalp_short": "15m",
}
_DEFAULT_OUTCOMES_PATH = _HERE / "output" / "signal_log" / "signal_outcomes.jsonl"

# Outcome + manual review keys — always null at send time.
_NULL_LATER_FIELDS: dict[str, None] = {
    "outcome_status": None,
    "outcome_updated_at_utc": None,
    "return_1d_pct": None,
    "return_3d_pct": None,
    "return_7d_pct": None,
    "return_4h_pct": None,
    "max_favorable_move_pct": None,
    "max_adverse_move_pct": None,
    "vs_btc_1d_pct": None,
    "vs_btc_7d_pct": None,
    "alert_useful": None,
    "failure_reason": None,
    "review_notes": None,
    "reviewed_at_utc": None,
    "reviewed_by": None,
}


def make_signal_id(
    scan_ts: str,
    inst_id: str,
    section: str,
    direction: str,
) -> str:
    """Deterministic id: ``{scan_ts_utc_compact}_{inst_id}_{section}_{direction}``."""
    compact = _compact_scan_ts(scan_ts)
    safe_inst = str(inst_id or "UNKNOWN").strip() or "UNKNOWN"
    safe_section = str(section or "unknown").strip() or "unknown"
    safe_dir = str(direction or "LONG").strip().upper() or "LONG"
    return f"{compact}_{safe_inst}_{safe_section}_{safe_dir}"


def _compact_scan_ts(scan_ts: str) -> str:
    """Normalize ISO scan timestamp to ``YYYYMMDDTHHMMSS`` UTC."""
    raw = str(scan_ts or "").strip()
    if not raw:
        return "UNKNOWN"
    try:
        s = raw.replace("Z", "+00:00")
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt.strftime("%Y%m%dT%H%M%S")
    except (TypeError, ValueError):
        cleaned = re.sub(r"[^0-9T]", "", raw.replace(" ", "T"))[:15]
        return cleaned or "UNKNOWN"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _as_bool_yes(value: Any) -> bool:
    return str(value or "").strip().lower() == "yes"


def _relative_path(path_str: str) -> str:
    """Store paths relative to ``crypto_dashboard/`` when possible."""
    if not path_str:
        return ""
    try:
        p = Path(path_str)
        if p.is_absolute():
            try:
                return str(p.relative_to(_HERE))
            except ValueError:
                return str(p)
        return str(p)
    except (TypeError, ValueError):
        return str(path_str)


def trim_market_context_snapshot(mc: dict[str, Any] | None) -> dict[str, Any]:
    """Small BTC/ETH/alts snapshot for feedback rows."""
    if not mc or not isinstance(mc, dict):
        return {}
    btc = mc.get("btc") or {}
    eth = mc.get("eth") or {}
    alts = mc.get("alts_proxy") or {}
    overall = mc.get("overall") or {}
    return {
        "data_quality": mc.get("data_quality"),
        "overall_label": overall.get("label"),
        "long_bias_score": overall.get("long_bias_score"),
        "short_bias_score": overall.get("short_bias_score"),
        "btc_trend_1h": btc.get("trend_1h"),
        "btc_trend_1d": btc.get("trend_1d"),
        "eth_trend_1h": eth.get("trend_1h"),
        "eth_trend_1d": eth.get("trend_1d"),
        "alts_trend_1h": alts.get("trend_1h"),
        "alts_trend_1d": alts.get("trend_1d"),
        "alts_breadth_positive_pct": alts.get("breadth_positive_pct"),
        "alts_above_ema50_pct": alts.get("above_ema50_pct"),
        "alts_avg_vs_btc_7d": alts.get("avg_vs_btc_7d"),
    }


def _chart_plan_keys(
    chart_plan: list[tuple[str, dict, str, str]],
) -> dict[tuple[str, str, str], str]:
    """Map (section, inst_id, direction) → timeframe bar from chart plan."""
    out: dict[tuple[str, str, str], str] = {}
    for section, sig, tf_bar, direction in chart_plan or []:
        inst_id = str(sig.get("inst_id") or "")
        if not inst_id:
            continue
        key = (section, inst_id, str(direction or "").upper())
        out[key] = tf_bar
    return out


def _index_chart_audit(chart_audit: list[dict]) -> dict[tuple[str, str, str], dict]:
    """Index delivery audit rows by (section, symbol, direction)."""
    out: dict[tuple[str, str, str], dict] = {}
    for row in chart_audit or []:
        if not isinstance(row, dict):
            continue
        section = str(row.get("section") or "")
        if section == "omission" or not section:
            continue
        sym = str(row.get("symbol") or "")
        direction = str(row.get("direction") or "").upper()
        if not sym:
            continue
        out[(section, sym, direction)] = row
    return out


def _merge_chart_fields(
    *,
    selected_for_chart: bool,
    audit: dict | None,
) -> dict[str, Any]:
    if not selected_for_chart:
        return {
            "selected_for_chart": False,
            "chart_generated": False,
            "chart_path": None,
            "chart_file_size_bytes": None,
            "telegram_photo_attempted": False,
            "telegram_photo_succeeded": False,
            "telegram_message_type": None,
            "telegram_skip_reason": "not_in_chart_plan",
        }
    if not audit:
        return {
            "selected_for_chart": True,
            "chart_generated": False,
            "chart_path": None,
            "chart_file_size_bytes": None,
            "telegram_photo_attempted": False,
            "telegram_photo_succeeded": False,
            "telegram_message_type": None,
            "telegram_skip_reason": "chart_audit_missing",
        }
    fsz = audit.get("file_size_bytes")
    try:
        fsz_int = int(fsz) if fsz is not None and fsz != "" else None
    except (TypeError, ValueError):
        fsz_int = None
    skip = str(audit.get("skip_reason") or "").strip()
    return {
        "selected_for_chart": True,
        "chart_generated": _as_bool_yes(audit.get("chart_generated")),
        "chart_path": _relative_path(str(audit.get("chart_path") or "")) or None,
        "chart_file_size_bytes": fsz_int,
        "telegram_photo_attempted": _as_bool_yes(audit.get("telegram_send_attempted")),
        "telegram_photo_succeeded": _as_bool_yes(audit.get("telegram_send_succeeded")),
        "telegram_message_type": audit.get("telegram_message_type") or None,
        "telegram_skip_reason": skip or None,
    }


def build_send_row(
    *,
    run_dict: dict,
    sig: dict,
    section: str,
    display_rank: int,
    digest_reason: str,
    is_preview: bool,
    regime: str,
    selected_for_chart: bool,
    chart_timeframe_bar: str | None,
    chart_audit_row: dict | None,
) -> dict:
    """Build one immutable send-time JSONL row."""
    scan_ts = str(run_dict.get("timestamp") or "")
    inst_id = str(sig.get("inst_id") or "")
    direction = str(sig.get("direction") or "LONG").upper()
    symbol = str(sig.get("symbol") or inst_id.replace("-USDT", "") or "?")
    mc = run_dict.get("market_context") if isinstance(run_dict, dict) else None

    chart_fields = _merge_chart_fields(
        selected_for_chart=selected_for_chart,
        audit=chart_audit_row,
    )

    row: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "record_type": RECORD_TYPE_ALERT_SENT,
        "signal_id": make_signal_id(scan_ts, inst_id, section, direction),
        "logged_at_utc": _utc_now_iso(),
        "scan_timestamp": scan_ts or None,
        "scan_version": run_dict.get("version"),
        "digest_reason": digest_reason or None,
        "is_preview": bool(is_preview),
        "symbol": symbol,
        "inst_id": inst_id or None,
        "section": section,
        "signal_type": sig.get("signal_type") or section.replace("scalp_", "scalp"),
        "direction": direction,
        "timeframe_label": sig.get("timeframe_label"),
        "chart_timeframe_bar": chart_timeframe_bar or _DEFAULT_CHART_TF.get(section),
        "status": sig.get("status"),
        "confirmation_required": sig.get("confirmation_required"),
        "setup_quality": sig.get("setup_quality"),
        "score_100": sig.get("score_100"),
        "signal_score_100": sig.get("signal_score_100"),
        "actionability_score_100": sig.get("actionability_score_100"),
        "score_before_market_context": sig.get("score_before_market_context"),
        "market_context_score": sig.get("market_context_score"),
        "market_context_label": sig.get("market_context_label"),
        "market_context_effect": sig.get("market_context_effect"),
        "market_context_note": sig.get("market_context_note"),
        "reason": sig.get("reason"),
        "narrative": sig.get("narrative"),
        "invalid_if": sig.get("invalid_if"),
        "price_at_send": sig.get("price"),
        "entry": sig.get("entry"),
        "entry_low": sig.get("entry_low"),
        "entry_high": sig.get("entry_high"),
        "stop": sig.get("stop"),
        "target": sig.get("target"),
        "risk_reward": sig.get("risk_reward"),
        "regime_primary": run_dict.get("regime_primary") or regime or None,
        "regime_signal": sig.get("regime"),
        "market_context_snapshot": trim_market_context_snapshot(
            mc if isinstance(mc, dict) else None,
        ),
        "displayed_in_telegram_text": True,
        "display_rank_in_section": display_rank,
        "telegram_text_included": True,
        **chart_fields,
        **_NULL_LATER_FIELDS,
    }
    return row


def append_signal_log(row: dict, *, path: Path | None = None) -> None:
    """Append one JSON line. Swallows all errors — never raises to callers."""
    target = path or DEFAULT_LOG_PATH
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(row, ensure_ascii=False, default=str)
        with target.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError as exc:
        log.warning("signal_log: append failed (%s) — %s", target, exc)
    except Exception as exc:  # noqa: BLE001
        log.warning("signal_log: append unexpected error — %s", exc)


def log_displayed_signals(
    *,
    run_dict: dict,
    display: dict,
    chart_plan: list[tuple[str, dict, str, str]],
    chart_audit: list[dict],
    digest_reason: str,
    is_preview: bool,
    regime: str,
    log_path: Path | None = None,
) -> int:
    """Log every signal shown in Telegram text sections. Returns rows written.

    Preview sends are skipped — only real Telegram digests are logged.
    """
    if is_preview:
        log.debug("signal_log: skipping preview send (is_preview=True)")
        return 0

    plan_keys = _chart_plan_keys(chart_plan)
    audit_index = _index_chart_audit(chart_audit)
    written = 0

    for section in _SECTIONS:
        sigs = list(display.get(section) or [])
        for rank, sig in enumerate(sigs, start=1):
            if not isinstance(sig, dict):
                continue
            inst_id = str(sig.get("inst_id") or "")
            direction = str(sig.get("direction") or "LONG").upper()
            symbol = str(sig.get("symbol") or "")
            plan_key = (section, inst_id, direction)
            selected = plan_key in plan_keys
            tf_bar = plan_keys.get(plan_key) or _DEFAULT_CHART_TF.get(section)
            audit_row = audit_index.get((section, symbol, direction))

            try:
                row = build_send_row(
                    run_dict=run_dict,
                    sig=sig,
                    section=section,
                    display_rank=rank,
                    digest_reason=digest_reason,
                    is_preview=False,
                    regime=regime,
                    selected_for_chart=selected,
                    chart_timeframe_bar=tf_bar,
                    chart_audit_row=audit_row,
                )
                append_signal_log(row, path=log_path)
                written += 1
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "signal_log: skip row for %s %s — %s",
                    section, symbol or inst_id, exc,
                )
    return written
