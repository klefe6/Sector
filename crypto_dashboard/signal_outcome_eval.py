"""Signal-level post-alert outcome evaluation (offline, manual).

Reuses candle fetch patterns from ``scanner_validation`` where safe.
Never mutates ``signal_log.jsonl``.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

SCHEMA_VERSION = "1.0"
RECORD_TYPE_OUTCOME = "outcome_update"

_HERE = Path(__file__).resolve().parent
DEFAULT_SIGNAL_LOG_PATH = _HERE / "output" / "signal_log" / "signal_log.jsonl"
DEFAULT_OUTCOMES_PATH = _HERE / "output" / "signal_log" / "signal_outcomes.jsonl"

SCALP_SECTIONS = frozenset({"scalp_long", "scalp_short"})
DEFAULT_MIN_AGE_HOURS = {"swing": 24.0, "scalp": 4.0}


def parse_iso_utc(ts: str) -> datetime | None:
    raw = str(ts or "").strip()
    if not raw:
        return None
    try:
        s = raw.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ts_to_ms(ts: str) -> int:
    dt = parse_iso_utc(ts)
    return int(dt.timestamp() * 1000) if dt else 0


def load_jsonl_rows(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    rows: list[dict] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    rows.append(obj)
            except json.JSONDecodeError:
                continue
    except OSError as exc:
        log.warning("signal_outcome_eval: read failed %s — %s", path, exc)
    return rows


def load_evaluated_signal_ids(path: Path | None = None) -> set[str]:
    """Return signal_ids that already have an outcome row."""
    target = path or DEFAULT_OUTCOMES_PATH
    return {
        str(r.get("signal_id"))
        for r in load_jsonl_rows(target)
        if r.get("record_type") == RECORD_TYPE_OUTCOME and r.get("signal_id")
    }


def append_outcome_row(row: dict, *, path: Path | None = None) -> None:
    target = path or DEFAULT_OUTCOMES_PATH
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(row, ensure_ascii=False, default=str)
        with target.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError as exc:
        log.warning("signal_outcome_eval: append failed (%s) — %s", target, exc)
    except Exception as exc:  # noqa: BLE001
        log.warning("signal_outcome_eval: append unexpected error — %s", exc)


def min_age_hours_for_signal(signal_row: dict) -> float:
    section = str(signal_row.get("section") or "")
    if section in SCALP_SECTIONS:
        return DEFAULT_MIN_AGE_HOURS["scalp"]
    return DEFAULT_MIN_AGE_HOURS["swing"]


def evaluation_window_label(signal_row: dict) -> str:
    section = str(signal_row.get("section") or "")
    return "4h" if section in SCALP_SECTIONS else "24h"


def candle_bar_for_signal(signal_row: dict) -> str:
    section = str(signal_row.get("section") or "")
    return "1H" if section in SCALP_SECTIONS else "1D"


def anchor_timestamp(signal_row: dict) -> str:
    return str(
        signal_row.get("logged_at_utc")
        or signal_row.get("scan_timestamp")
        or ""
    )


def is_old_enough(signal_row: dict, *, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    ts = anchor_timestamp(signal_row)
    dt = parse_iso_utc(ts)
    if dt is None:
        return False
    age_h = (now - dt).total_seconds() / 3600.0
    return age_h >= min_age_hours_for_signal(signal_row)


def _entry_band(signal_row: dict) -> tuple[float | None, float | None]:
    entry = signal_row.get("entry")
    el = signal_row.get("entry_low")
    eh = signal_row.get("entry_high")
    if el is not None and eh is not None:
        return float(el), float(eh)
    if entry is not None:
        e = float(entry)
        return e, e
    price = signal_row.get("price_at_send")
    if price is not None:
        p = float(price)
        return p, p
    return None, None


def check_signal_outcome(signal_row: dict, df) -> str:
    """Direction-aware target/stop/no-entry/open/ambiguous classification."""
    if df is None or getattr(df, "empty", True):
        return "no_data"

    direction = str(signal_row.get("direction") or "LONG").upper()
    entry_low, entry_high = _entry_band(signal_row)
    stop = signal_row.get("stop")
    target = signal_row.get("target")

    if stop is None or target is None or entry_low is None or entry_high is None:
        return "no_levels"

    stop_f = float(stop)
    target_f = float(target)

    entry_triggered = False
    for _, row in df.iterrows():
        low = float(row["low"])
        high = float(row["high"])
        if not entry_triggered:
            if low <= entry_high and high >= entry_low:
                entry_triggered = True
            continue

        if direction == "SHORT":
            hit_tgt = low <= target_f
            hit_stp = high >= stop_f
        else:
            hit_tgt = high >= target_f
            hit_stp = low <= stop_f

        if hit_tgt and hit_stp:
            return "ambiguous"
        if hit_tgt:
            return "target_hit"
        if hit_stp:
            return "stop_hit"

    return "open" if entry_triggered else "no_entry"


def compute_signal_returns(
    signal_row: dict,
    df,
    btc_df=None,
) -> dict[str, float | None]:
    """Forward returns and direction-aware MFE/MAE from post-alert candles."""
    ref = signal_row.get("price_at_send") or signal_row.get("entry")
    if ref is None or df is None or getattr(df, "empty", True):
        return {}

    direction = str(signal_row.get("direction") or "LONG").upper()
    scan_close = float(ref)
    close_s = df["close"]
    result: dict[str, float | None] = {}

    def _ret(idx: int) -> float | None:
        if idx >= len(close_s):
            return None
        return (float(close_s.iloc[idx]) - scan_close) / scan_close * 100

    bar = candle_bar_for_signal(signal_row)
    if bar == "1H":
        result["return_1d_pct"] = _ret(min(23, len(close_s) - 1)) if len(close_s) > 0 else None
        result["return_3d_pct"] = None
        result["return_7d_pct"] = None
    else:
        result["return_1d_pct"] = _ret(0)
        result["return_3d_pct"] = _ret(2) if len(close_s) > 2 else None
        result["return_7d_pct"] = _ret(6) if len(close_s) > 6 else None

    if "high" in df.columns and "low" in df.columns:
        max_high = float(df["high"].max())
        min_low = float(df["low"].min())
        if direction == "SHORT":
            result["max_favorable_move_pct"] = (scan_close - min_low) / scan_close * 100
            result["max_adverse_move_pct"] = (scan_close - max_high) / scan_close * 100
        else:
            result["max_favorable_move_pct"] = (max_high - scan_close) / scan_close * 100
            result["max_adverse_move_pct"] = (min_low - scan_close) / scan_close * 100

    if btc_df is not None and not getattr(btc_df, "empty", True):
        sym_ret = result.get("return_1d_pct")
        if sym_ret is not None and len(btc_df) > 0:
            btc0 = float(btc_df["close"].iloc[0])
            btc1 = float(btc_df["close"].iloc[min(0, len(btc_df) - 1)])
            if bar == "1D" and len(btc_df) > 0:
                btc1 = float(btc_df["close"].iloc[0])
            btc_ret = (btc1 - btc0) / btc0 * 100 if btc0 else 0.0
            result["vs_btc_1d_pct"] = sym_ret - btc_ret

    return result


def fetch_post_alert_candles(inst_id: str, anchor_ts: str, bar: str):
    """Fetch candles strictly after alert anchor time."""
    try:
        import scanner_validation as sv

        return sv._fetch_post_scan_candles(inst_id, _ts_to_ms(anchor_ts), bar=bar)
    except Exception as exc:  # noqa: BLE001
        log.debug("fetch_post_alert_candles %s: %s", inst_id, exc)
        return None


def build_outcome_row(
    signal_row: dict,
    *,
    outcome_status: str,
    metrics: dict[str, float | None],
    notes: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "record_type": RECORD_TYPE_OUTCOME,
        "signal_id": signal_row.get("signal_id"),
        "evaluated_at_utc": _utc_now_iso(),
        "evaluation_window": evaluation_window_label(signal_row),
        "outcome_status": outcome_status,
        "return_1d_pct": metrics.get("return_1d_pct"),
        "return_3d_pct": metrics.get("return_3d_pct"),
        "return_7d_pct": metrics.get("return_7d_pct"),
        "max_favorable_move_pct": metrics.get("max_favorable_move_pct"),
        "max_adverse_move_pct": metrics.get("max_adverse_move_pct"),
        "vs_btc_1d_pct": metrics.get("vs_btc_1d_pct"),
        "vs_btc_7d_pct": metrics.get("vs_btc_7d_pct"),
        "notes": notes,
    }


def evaluate_one_signal(
    signal_row: dict,
    *,
    fetch_candles: bool = True,
) -> dict[str, Any]:
    """Evaluate a single logged alert. Uses live BloFin fetch when enabled."""
    inst_id = str(signal_row.get("inst_id") or "")
    anchor = anchor_timestamp(signal_row)
    bar = candle_bar_for_signal(signal_row)

    df = None
    btc_df = None
    if fetch_candles and inst_id and anchor:
        df = fetch_post_alert_candles(inst_id, anchor, bar)
        btc_df = fetch_post_alert_candles("BTC-USDT", anchor, "1D" if bar == "1D" else "1H")

    outcome = check_signal_outcome(signal_row, df)
    metrics = compute_signal_returns(signal_row, df, btc_df)
    notes = None
    if signal_row.get("is_preview"):
        notes = "skipped_preview_row"
    return build_outcome_row(signal_row, outcome_status=outcome, metrics=metrics, notes=notes)


def evaluate_pending_signals(
    *,
    signal_log_path: Path | None = None,
    outcomes_path: Path | None = None,
    force: bool = False,
    fetch_candles: bool = True,
    now: datetime | None = None,
) -> dict[str, int]:
    """Evaluate eligible alerts and append outcome rows. Returns summary counts."""
    log_path = signal_log_path or DEFAULT_SIGNAL_LOG_PATH
    out_path = outcomes_path or DEFAULT_OUTCOMES_PATH
    now = now or datetime.now(timezone.utc)

    evaluated = set() if force else load_evaluated_signal_ids(out_path)
    stats = {
        "read": 0,
        "skipped_preview": 0,
        "skipped_young": 0,
        "skipped_done": 0,
        "evaluated": 0,
        "errors": 0,
    }

    for row in load_jsonl_rows(log_path):
        if row.get("record_type") != "alert_sent":
            continue
        stats["read"] += 1

        if row.get("is_preview"):
            stats["skipped_preview"] += 1
            continue

        sid = str(row.get("signal_id") or "")
        if not sid:
            stats["errors"] += 1
            continue

        if sid in evaluated:
            stats["skipped_done"] += 1
            continue

        if not is_old_enough(row, now=now):
            stats["skipped_young"] += 1
            continue

        try:
            outcome_row = evaluate_one_signal(row, fetch_candles=fetch_candles)
            append_outcome_row(outcome_row, path=out_path)
            evaluated.add(sid)
            stats["evaluated"] += 1
        except Exception as exc:  # noqa: BLE001
            log.warning("signal_outcome_eval: failed %s — %s", sid, exc)
            stats["errors"] += 1

    return stats
