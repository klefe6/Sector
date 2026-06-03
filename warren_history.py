"""
warren_history.py — Browse dated Warren AI output folders (local archive only).

Reads warren_ai_daily/output/YYYY-MM-DD/ — no Investing.com, no automation calls.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Optional

import warren_loader as wl

# Reuse canonical output root from warren_loader
WARREN_OUTPUT_DIR = wl.WARREN_OUTPUT_DIR

_FLOAT_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _first_price(text: str) -> Optional[float]:
    if not text or not str(text).strip():
        return None
    clean = str(text).replace("$", "").replace(",", "").replace("%", "")
    m = _FLOAT_RE.search(clean)
    if not m:
        return None
    try:
        v = float(m.group(0))
        return v if v > 0 else None
    except Exception:
        return None


def _entry_has_numbers(entry_raw: str) -> bool:
    if not entry_raw or not entry_raw.strip():
        return False
    nums = _FLOAT_RE.findall(entry_raw.replace("–", "-").replace("—", "-"))
    return len(nums) >= 1


def _has_complete_trade_levels(entry: str, stop: str, target: str) -> bool:
    return _entry_has_numbers(entry) and _first_price(stop) is not None and _first_price(target) is not None


@dataclass
class HistoryDayMeta:
    date: str
    path: str
    has_final_action_plan: bool
    has_step_04: bool
    has_run_summary: bool
    has_portfolio_monitor: bool
    last_modified: str
    status: str


def _is_date_folder(name: str) -> bool:
    return len(name) == 10 and name[4] == "-" and name[7] == "-" and name[:4].isdigit()


def list_history_dates(output_root: str | None = None, limit: int = 500) -> list[HistoryDayMeta]:
    """
    Scan output_root for YYYY-MM-DD folders, newest first.
    """
    root = os.path.abspath(output_root or WARREN_OUTPUT_DIR)
    if not os.path.isdir(root):
        return []

    names = [n for n in os.listdir(root) if _is_date_folder(n)]
    names.sort(reverse=True)
    names = names[:limit]

    out: list[HistoryDayMeta] = []
    for name in names:
        folder = os.path.join(root, name)
        if not os.path.isdir(folder):
            continue

        files = set()
        mtimes: list[float] = []
        for fn in os.listdir(folder):
            fp = os.path.join(folder, fn)
            if os.path.isfile(fp):
                files.add(fn)
                try:
                    mtimes.append(os.path.getmtime(fp))
                except OSError:
                    pass

        has_final = "final_action_plan.md" in files
        has_s04 = "step_04.md" in files
        has_sum = "run_summary.json" in files
        has_pm = any(
            f in files
            for f in ("portfolio_step_06.md", "portfolio_step_07.md", "portfolio_summary.json")
        )

        last_mod = "—"
        if mtimes:
            last_mod = datetime.fromtimestamp(max(mtimes)).strftime("%Y-%m-%d %H:%M")

        scan_ok = {
            "step_01.md", "step_02.md", "step_03.md", "step_04.md", "step_05.md", "final_action_plan.md"
        }.issubset(files)

        run_ok = False
        parse_err = ""
        if has_sum:
            try:
                with open(os.path.join(folder, "run_summary.json"), "r", encoding="utf-8") as fh:
                    s = json.load(fh)
                run_ok = bool(s.get("success"))
            except Exception as exc:
                parse_err = str(exc)

        if scan_ok and run_ok and has_final:
            status = "Complete"
        elif scan_ok and has_final:
            status = "Partial"
        elif has_final or has_s04:
            status = "Partial"
        elif files:
            status = "Partial"
        else:
            status = "Empty"

        if parse_err:
            status = "Parse issue"

        if not has_final:
            status = "Missing final plan" if status in ("Complete", "Partial") else status

        out.append(
            HistoryDayMeta(
                date=name,
                path=folder,
                has_final_action_plan=has_final,
                has_step_04=has_s04,
                has_run_summary=has_sum,
                has_portfolio_monitor=has_pm,
                last_modified=last_mod,
                status=status,
            )
        )

    return out


def load_history_day(date_str: str, output_root: str | None = None) -> dict[str, Any]:
    """
    Raw presence + bounded text for a date. Never raises.
    """
    root = os.path.abspath(output_root or WARREN_OUTPUT_DIR)
    folder = os.path.join(root, date_str.strip())
    out: dict[str, Any] = {
        "date": date_str,
        "path": folder,
        "files": {},
        "missing": [],
        "snippets": {},
        "run_summary_raw": None,
        "parse_warnings": [],
    }
    if not _is_date_folder(date_str) or not os.path.isdir(folder):
        out["parse_warnings"].append("No output folder for this date")
        return out

    want = [
        "final_action_plan.md",
        "step_04.md",
        "step_03.md",
        "step_02.md",
        "step_01.md",
        "run_summary.json",
        "portfolio_step_06.md",
        "portfolio_step_07.md",
        "portfolio_summary.json",
    ]
    for name in want:
        path = os.path.join(folder, name)
        if not os.path.isfile(path):
            out["missing"].append(name)
            out["files"][name] = {"present": False, "bytes": 0}
            continue
        try:
            sz = os.path.getsize(path)
            out["files"][name] = {"present": True, "bytes": sz}
        except OSError:
            out["files"][name] = {"present": False, "bytes": 0}
            out["missing"].append(name)
            continue

        if name == "run_summary.json":
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    out["run_summary_raw"] = json.load(fh)
            except Exception as exc:
                out["parse_warnings"].append(f"run_summary.json: {exc}")
                out["run_summary_raw"] = None
        elif name.endswith(".md"):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    txt = fh.read()
                out["snippets"][name] = txt[:4000] + ("…" if len(txt) > 4000 else "")
            except Exception as exc:
                out["parse_warnings"].append(f"{name}: {exc}")

    return out


def parse_history_day(date_str: str, output_root: str | None = None) -> dict[str, Any]:
    """
    Structured parse for History UI. Uses warren_loader parsers (WarrenResult).
    """
    root = os.path.abspath(output_root or WARREN_OUTPUT_DIR)
    folder = os.path.join(root, date_str.strip())

    meta_list = [m for m in list_history_dates(root, limit=900) if m.date == date_str]
    meta = meta_list[0] if meta_list else None

    raw = load_history_day(date_str, root)
    warnings: list[str] = list(raw.get("parse_warnings") or [])

    if not os.path.isdir(folder):
        return {
            "date": date_str,
            "meta": asdict(meta) if meta else None,
            "raw": raw,
            "warren": None,
            "suggestion_rows": [],
            "counts": {},
            "warnings": warnings or ["No folder"],
        }

    try:
        w = wl.load_warren_from_folder(folder)
    except Exception as exc:
        warnings.append(f"parse: {exc}")
        w = None

    if w is not None:
        warnings.extend(w.warnings or [])

    suggestion_rows: list[dict[str, Any]] = []
    counts: dict[str, Any] = {
        "suggested_tickers": 0,
        "complete_plans": 0,
        "incomplete_plans": 0,
        "top3": 0,
        "mentioned_only": 0,
    }

    if w is None:
        return {
            "date": date_str,
            "meta": asdict(meta) if meta else None,
            "raw": raw,
            "warren": None,
            "suggestion_rows": [],
            "counts": counts,
            "warnings": warnings,
        }

    # Import here to avoid heavy import at module load for non-UI scripts
    import morning_status as ms

    top3_set = {t.get("ticker") for t in w.top3_trades if t.get("ticker")}
    actionable_set = set(w.actionable or [])
    avoid_names = set(w.avoid or [])
    watch_tm = set(w.watch_tomorrow or [])
    watch_final = set(w.watch_tickers or [])

    ordered: list[str] = []
    for t in [x.get("ticker") for x in w.top3_trades if x.get("ticker")]:
        if t and t not in ordered:
            ordered.append(t)
    for t in w.actionable or []:
        if t not in ordered:
            ordered.append(t)
    for t in w.all_tickers or []:
        if t not in ordered:
            ordered.append(t)
    for t in w.ticker_rows.keys():
        if t not in ordered:
            ordered.append(t)

    for ticker in ordered:
        tr = w.ticker_rows.get(ticker)
        rank = int(tr.rank) if tr and tr.rank else (1 if ticker in top3_set else 0)
        sector = (tr.sector_etf if tr and tr.sector_etf else w.ticker_sector.get(ticker, "")) or ""
        setup = (tr.setup if tr else "") or ""
        verdict = (tr.verdict if tr else "") or ""

        er, sr, tpr, rr_txt = ms.warren_level_strings(w, ticker)
        complete = _has_complete_trade_levels(er, sr, tpr)

        if ticker in top3_set:
            status = "Top 3"
        elif ticker in actionable_set:
            status = "Complete plan" if complete else "Incomplete plan"
        elif ticker in avoid_names:
            status = "Avoid"
        elif ticker in watch_tm or ticker in watch_final:
            status = "Watch"
        else:
            status = "Mentioned only"

        if status == "Complete plan":
            counts["complete_plans"] += 1
        elif status == "Incomplete plan":
            counts["incomplete_plans"] += 1
        elif status == "Mentioned only":
            counts["mentioned_only"] += 1

        why = ""
        if ticker in top3_set:
            t3 = next((x for x in w.top3_trades if x.get("ticker") == ticker), None)
            if t3:
                why = (t3.get("why") or t3.get("verdict") or "").strip()

        src = "step_04.md" if ticker in actionable_set or (tr and tr.rank) else "final_action_plan.md"
        if ticker in top3_set:
            src = "final_action_plan.md"

        suggestion_rows.append(
            {
                "ticker": ticker,
                "rank": rank,
                "sector": sector,
                "setup": setup,
                "status": status,
                "entry": er,
                "stop": sr,
                "target": tpr,
                "rr": rr_txt,
                "verdict": verdict,
                "source": src,
                "notes": why[:500] if why else "",
                "themes": [],
            }
        )

    counts["suggested_tickers"] = len(set(ordered))
    counts["top3"] = len(w.top3_trades)
    # Theme tags (optional)
    try:
        import theme_registry as trg

        for row in suggestion_rows:
            row["themes"] = trg.get_ticker_themes(row["ticker"])
    except Exception:
        pass

    return {
        "date": date_str,
        "meta": asdict(meta) if meta else None,
        "raw": raw,
        "warren": w,
        "suggestion_rows": suggestion_rows,
        "counts": counts,
        "warnings": warnings,
    }


def recent_signal_day_stats(
    dates: list[str],
    forward_rows_by_date: dict[str, list[Any]],
    output_root: str | None = None,
) -> list[dict[str, Any]]:
    """
    Build summary rows for the last N dates (strings YYYY-MM-DD).
    forward_rows_by_date: scan_date -> list of ForwardWalkRow (or empty).
    """
    rows_out: list[dict[str, Any]] = []
    root = os.path.abspath(output_root or WARREN_OUTPUT_DIR)
    for ds in dates:
        folder = os.path.join(root, ds)
        if not os.path.isdir(folder):
            rows_out.append(
                {
                    "date": ds,
                    "suggestions": "—",
                    "complete_plans": "—",
                    "top3": "—",
                    "avg_return": "—",
                    "win_rate": "—",
                    "best": "—",
                    "worst": "—",
                    "status": "—",
                }
            )
            continue

        parsed = parse_history_day(ds, root)
        c = parsed.get("counts") or {}
        meta = parsed.get("meta") or {}
        fw_rows = forward_rows_by_date.get(ds) or []
        priced = [r for r in fw_rows if getattr(r, "return_pct", None) is not None]

        avg_ret = "—"
        win_rate = "—"
        best = "—"
        worst = "—"
        if len(priced) >= 2:
            rets = [float(r.return_pct) for r in priced]  # type: ignore[arg-type]
            avg_ret = f"{sum(rets) / len(rets):+.1f}%"
            wins = sum(1 for v in rets if v > 0)
            win_rate = f"{100.0 * wins / len(rets):.0f}%"
            srt = sorted(priced, key=lambda r: r.return_pct, reverse=True)
            best = f"{srt[0].ticker} ({srt[0].return_pct:+.1f}%)"
            worst = f"{srt[-1].ticker} ({srt[-1].return_pct:+.1f}%)"
        elif len(priced) == 1:
            avg_ret = f"{priced[0].return_pct:+.1f}%"
            win_rate = "100%" if priced[0].return_pct > 0 else "0%"
            best = f"{priced[0].ticker} ({priced[0].return_pct:+.1f}%)"
            worst = best
        else:
            avg_ret = "Not enough data"
            win_rate = "—"

        rows_out.append(
            {
                "date": ds,
                "suggestions": str(c.get("suggested_tickers", 0)),
                "complete_plans": str(c.get("complete_plans", 0)),
                "top3": str(c.get("top3", 0)),
                "avg_return": avg_ret,
                "win_rate": win_rate,
                "best": best,
                "worst": worst,
                "status": meta.get("status", "—"),
            }
        )

    return rows_out
