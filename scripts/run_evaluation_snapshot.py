#!/usr/bin/env python3
"""
run_evaluation_snapshot.py — Read-only daily evaluation snapshot for stock and crypto callouts.

Persists callout performance records via existing adapters, then writes
evaluation_manifest.json under each callout_performance folder.

Does NOT send Telegram, change scoring, or modify source reports.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Any

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import callout_performance as cpf  # noqa: E402
import warren_loader as wl  # noqa: E402

MANIFEST_FILENAME = "evaluation_manifest.json"

DEFAULT_NITTER_SOURCE_DIR = os.environ.get(
    "WARREN_NITTER_SOURCE_DIR",
    "/home/klefe6/agentic/control_center/backend/data/morning_reports",
)

_NITTER_FILE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})_nitter\.txt$")

# Freshness thresholds (hours) — evaluation-only warnings, not alert gates.
WARREN_STALE_HOURS = 48.0
NITTER_STALE_HOURS = 36.0
CRYPTO_SCAN_STALE_HOURS = 2.0

REQUIRED_MANIFEST_KEYS = frozenset({
    "generated_at",
    "asset_class",
    "lookback_days",
    "callout_count",
    "outcome_count",
    "report_path",
    "callouts_path",
    "outcomes_path",
    "latest_source_snapshot",
    "freshness_warnings",
})


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _age_hours_from_mtime(path: str, now: datetime | None = None) -> float | None:
    if not path or not os.path.isfile(path):
        return None
    now = now or datetime.now(timezone.utc)
    try:
        mtime = datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc)
        return max(0.0, (now - mtime).total_seconds() / 3600.0)
    except OSError:
        return None


def _age_hours_from_iso(ts: str, now: datetime | None = None) -> float | None:
    if not ts:
        return None
    now = now or datetime.now(timezone.utc)
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (now - dt.astimezone(timezone.utc)).total_seconds() / 3600.0)
    except Exception:
        return None


def _count_jsonl(path: str) -> int | None:
    if not os.path.isfile(path):
        return None
    try:
        count = 0
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    count += 1
        return count
    except OSError:
        return None


def _stock_perf_dir() -> str:
    return os.path.join(wl.WARREN_OUTPUT_DIR, "callout_performance", "stock")


def _crypto_perf_dir() -> str:
    return os.path.join(cpf._crypto_output_dir(), "callout_performance", "crypto")


def _default_perf_paths(base_dir: str) -> dict[str, str]:
    return {
        "callouts": os.path.join(base_dir, "callouts.jsonl"),
        "outcomes": os.path.join(base_dir, "outcomes.jsonl"),
        "report": os.path.join(base_dir, "report.json"),
    }


def _find_latest_warren_success() -> dict[str, Any]:
    """Most recent YYYY-MM-DD folder with run_summary.success == true."""
    out: dict[str, Any] = {
        "warren_output_date": "",
        "warren_output_path": "",
        "warren_age_hours": None,
    }
    if not os.path.isdir(wl.WARREN_OUTPUT_DIR):
        return out

    best_folder: str | None = None
    best_date: date | None = None
    for name in os.listdir(wl.WARREN_OUTPUT_DIR):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", name):
            continue
        folder = os.path.join(wl.WARREN_OUTPUT_DIR, name)
        summary = os.path.join(folder, "run_summary.json")
        if not os.path.isfile(summary):
            continue
        try:
            with open(summary, encoding="utf-8") as fh:
                data = json.load(fh)
            if data.get("success") is not True:
                continue
            d = date.fromisoformat(name)
            if best_date is None or d > best_date:
                best_date = d
                best_folder = folder
        except Exception:
            continue

    if best_folder and best_date:
        out["warren_output_date"] = best_date.isoformat()
        out["warren_output_path"] = best_folder
        out["warren_age_hours"] = round(
            (datetime.now(timezone.utc).date() - best_date).days * 24.0, 2
        )
    return out


def _find_latest_nitter(source_dir: str) -> dict[str, Any]:
    out: dict[str, Any] = {
        "nitter_file_path": "",
        "nitter_file_date": "",
        "nitter_age_hours": None,
    }
    if not source_dir or not os.path.isdir(source_dir):
        return out

    best_path = ""
    best_mtime = -1.0
    for name in os.listdir(source_dir):
        if not _NITTER_FILE_RE.search(name):
            continue
        path = os.path.join(source_dir, name)
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        if mtime > best_mtime:
            best_mtime = mtime
            best_path = path

    if not best_path:
        return out

    m = _NITTER_FILE_RE.search(os.path.basename(best_path))
    out["nitter_file_path"] = best_path
    out["nitter_file_date"] = m.group(1) if m else ""
    age = _age_hours_from_mtime(best_path)
    out["nitter_age_hours"] = round(age, 2) if age is not None else None
    return out


def _find_latest_crypto_scan() -> dict[str, Any]:
    out: dict[str, Any] = {
        "latest_scan_path": "",
        "latest_scan_timestamp": "",
        "latest_scan_age_hours": None,
    }
    latest = os.path.join(cpf._crypto_output_dir(), "scans", "latest.json")
    if not os.path.isfile(latest):
        return out

    try:
        with open(latest, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        out["latest_scan_path"] = latest
        return out

    out["latest_scan_path"] = latest
    ts = str(data.get("timestamp") or "")
    out["latest_scan_timestamp"] = ts
    age = _age_hours_from_iso(ts)
    if age is None:
        age = _age_hours_from_mtime(latest)
    out["latest_scan_age_hours"] = round(age, 2) if age is not None else None
    return out


def _collect_freshness_warnings(
    asset_class: str,
    *,
    paths: dict[str, str],
    callout_count: int | None,
    outcome_count: int | None,
    source_snapshot: dict[str, Any],
) -> list[str]:
    warnings: list[str] = []

    if not os.path.isfile(paths.get("report", "")):
        warnings.append("report missing")
    if callout_count is None or callout_count == 0:
        warnings.append("no callouts")
    if outcome_count is None or outcome_count == 0:
        warnings.append("no outcomes")

    if asset_class == "stock":
        if not source_snapshot.get("warren_output_path"):
            warnings.append("missing latest Warren output")
        else:
            age = source_snapshot.get("warren_age_hours")
            if age is not None and age > WARREN_STALE_HOURS:
                warnings.append(
                    f"Warren output older than {WARREN_STALE_HOURS:.0f}h ({age:.1f}h)"
                )

        if not source_snapshot.get("nitter_file_path"):
            warnings.append("missing latest Nitter file")
        else:
            n_age = source_snapshot.get("nitter_age_hours")
            if n_age is not None and n_age > NITTER_STALE_HOURS:
                warnings.append(
                    f"Nitter source older than {NITTER_STALE_HOURS:.0f}h ({n_age:.1f}h)"
                )

    if asset_class == "crypto":
        if not source_snapshot.get("latest_scan_path"):
            warnings.append("missing latest crypto scan")
        elif not source_snapshot.get("latest_scan_timestamp"):
            warnings.append("latest crypto scan has no timestamp")
        else:
            s_age = source_snapshot.get("latest_scan_age_hours")
            if s_age is not None and s_age > CRYPTO_SCAN_STALE_HOURS:
                warnings.append(
                    f"crypto scan older than {CRYPTO_SCAN_STALE_HOURS:.0f}h ({s_age:.1f}h)"
                )

    return warnings


def build_manifest(
    asset_class: str,
    *,
    lookback_days: int,
    paths: dict[str, str],
    source_snapshot: dict[str, Any],
    generated_at: str | None = None,
    persist_errors: list[str] | None = None,
) -> dict[str, Any]:
    """Build evaluation manifest dict (does not write to disk)."""
    callout_count = _count_jsonl(paths.get("callouts", ""))
    outcome_count = _count_jsonl(paths.get("outcomes", ""))

    report_path = paths.get("report", "")
    callouts_path = paths.get("callouts", "")
    outcomes_path = paths.get("outcomes", "")

    manifest: dict[str, Any] = {
        "generated_at": generated_at or _utc_now_iso(),
        "asset_class": asset_class,
        "lookback_days": lookback_days,
        "callout_count": callout_count,
        "outcome_count": outcome_count,
        "report_path": report_path if os.path.isfile(report_path) else None,
        "callouts_path": callouts_path if os.path.isfile(callouts_path) else None,
        "outcomes_path": outcomes_path if os.path.isfile(outcomes_path) else None,
        "latest_source_snapshot": source_snapshot,
        "freshness_warnings": _collect_freshness_warnings(
            asset_class,
            paths=paths,
            callout_count=callout_count,
            outcome_count=outcome_count,
            source_snapshot=source_snapshot,
        ),
    }
    if persist_errors:
        manifest["persist_errors"] = list(persist_errors)
    return manifest


def validate_manifest_schema(manifest: dict[str, Any]) -> list[str]:
    """Return schema violation messages (empty list = valid)."""
    issues: list[str] = []
    missing = REQUIRED_MANIFEST_KEYS - set(manifest.keys())
    if missing:
        issues.append(f"missing keys: {sorted(missing)}")
    if manifest.get("asset_class") not in ("stock", "crypto"):
        issues.append("asset_class must be 'stock' or 'crypto'")
    if not isinstance(manifest.get("freshness_warnings"), list):
        issues.append("freshness_warnings must be a list")
    if not isinstance(manifest.get("latest_source_snapshot"), dict):
        issues.append("latest_source_snapshot must be a dict")
    return issues


def write_manifest(manifest: dict[str, Any], base_dir: str) -> str:
    os.makedirs(base_dir, exist_ok=True)
    path = os.path.join(base_dir, MANIFEST_FILENAME)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, sort_keys=True)
        fh.write("\n")
    return path


def run_snapshot(
    lookback_days: int = 30,
    *,
    skip_persist: bool = False,
    nitter_source_dir: str | None = None,
) -> dict[str, Any]:
    """
    Run full evaluation snapshot for stock and crypto.

    Returns summary dict with manifest paths and any persist_errors.
    Never raises on missing source data.
    """
    nitter_dir = nitter_source_dir or DEFAULT_NITTER_SOURCE_DIR
    summary: dict[str, Any] = {
        "generated_at": _utc_now_iso(),
        "lookback_days": lookback_days,
        "manifests": {},
        "persist_errors": [],
    }

    stock_base = _stock_perf_dir()
    crypto_base = _crypto_perf_dir()
    stock_paths = _default_perf_paths(stock_base)
    crypto_paths = _default_perf_paths(crypto_base)

    if not skip_persist:
        try:
            stock_paths = cpf.persist_stock_performance(lookback_days)
        except Exception as exc:  # noqa: BLE001
            summary["persist_errors"].append(f"stock: {exc}")
        try:
            crypto_paths = cpf.persist_crypto_performance(lookback_days)
        except Exception as exc:  # noqa: BLE001
            summary["persist_errors"].append(f"crypto: {exc}")

    stock_snapshot = {
        **_find_latest_warren_success(),
        **_find_latest_nitter(nitter_dir),
    }
    stock_manifest = build_manifest(
        "stock",
        lookback_days=lookback_days,
        paths=stock_paths,
        source_snapshot=stock_snapshot,
        persist_errors=summary["persist_errors"] or None,
    )
    crypto_manifest = build_manifest(
        "crypto",
        lookback_days=lookback_days,
        paths=crypto_paths,
        source_snapshot=_find_latest_crypto_scan(),
        persist_errors=summary["persist_errors"] or None,
    )

    summary["manifests"]["stock"] = write_manifest(stock_manifest, stock_base)
    summary["manifests"]["crypto"] = write_manifest(crypto_manifest, crypto_base)
    summary["stock_warnings"] = stock_manifest.get("freshness_warnings", [])
    summary["crypto_warnings"] = crypto_manifest.get("freshness_warnings", [])
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Write read-only evaluation manifests for stock and crypto callouts",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Lookback days for performance persistence (default: 30)",
    )
    parser.add_argument(
        "--skip-persist",
        action="store_true",
        help="Skip persist_stock/crypto (no network); write manifests from existing files only",
    )
    parser.add_argument(
        "--nitter-source-dir",
        default=DEFAULT_NITTER_SOURCE_DIR,
        help="Directory containing YYYY-MM-DD_nitter.txt files",
    )
    args = parser.parse_args()

    result = run_snapshot(
        lookback_days=max(1, args.days),
        skip_persist=args.skip_persist,
        nitter_source_dir=args.nitter_source_dir,
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
