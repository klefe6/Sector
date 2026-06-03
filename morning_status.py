"""
morning_status.py — Observability helpers for the Dashboard's Morning Scan Status section.

Provides:
  get_automation_health(output_dir, days=7)
  get_suggested_tickers(w: WarrenResult, active_tickers, watchlist_tickers)
  get_forward_walk_results(output_dir, days=14)
  forward_walk_summary(rows)

No AI logic. No trade recommendations. No browser automation. Read-only.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

import yfinance as yf

import warren_loader as wl

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

WARREN_OUTPUT_DIR = wl.WARREN_OUTPUT_DIR

_PRICE_CACHE: dict[str, dict] = {}          # ticker → {"history": df, "ts": float}
_PRICE_CACHE_TTL = 1800                      # 30 min in-process cache


# ---------------------------------------------------------------------------
# 1. Automation Health
# ---------------------------------------------------------------------------

@dataclass
class DayHealth:
    date:           str
    full_run:       bool   # run_summary.json success=True
    warren_scan:    bool   # step_01..step_05 + final_action_plan.md present
    portfolio_mon:  bool   # portfolio_step_06.md or portfolio_step_07.md
    export_done:    bool   # portfolio_summary.json or config/current_portfolio.json
    last_modified:  str    # ISO-like timestamp of the newest file in folder
    status:         str    # Complete | Partial | Missing | Stale | Parse error
    parse_error:    str    = ""


def get_automation_health(days: int = 7) -> list[DayHealth]:
    """
    Inspect output_dir for the last `days` calendar days.
    Returns a list of DayHealth (most-recent first).
    """
    today = date.today()
    results: list[DayHealth] = []

    for delta in range(days):
        d = today - timedelta(days=delta)
        ds = d.strftime("%Y-%m-%d")
        folder = os.path.join(WARREN_OUTPUT_DIR, ds)

        if not os.path.isdir(folder):
            results.append(DayHealth(
                date=ds, full_run=False, warren_scan=False,
                portfolio_mon=False, export_done=False,
                last_modified="—", status="Missing",
            ))
            continue

        # Gather modification times of all files in folder
        all_mtimes: list[float] = []
        files_present: set[str] = set()
        for fname in os.listdir(folder):
            fpath = os.path.join(folder, fname)
            if os.path.isfile(fpath):
                files_present.add(fname)
                all_mtimes.append(os.path.getmtime(fpath))

        last_mod = "—"
        if all_mtimes:
            ts = max(all_mtimes)
            last_mod = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")

        # run_summary success flag
        full_run = False
        parse_error = ""
        summary_path = os.path.join(folder, "run_summary.json")
        if "run_summary.json" in files_present:
            try:
                with open(summary_path, "r", encoding="utf-8") as fh:
                    s = json.load(fh)
                full_run = bool(s.get("success"))
            except Exception as exc:
                parse_error = f"run_summary parse error: {exc}"

        # Warren scan: all 5 steps + final plan
        SCAN_FILES = {"step_01.md", "step_02.md", "step_03.md",
                      "step_04.md", "step_05.md", "final_action_plan.md"}
        warren_scan = SCAN_FILES.issubset(files_present)

        # Portfolio monitor
        portfolio_mon = bool(
            "portfolio_step_06.md" in files_present
            or "portfolio_step_07.md" in files_present
        )

        # Export done (portfolio json somewhere accessible)
        export_done = bool(
            "portfolio_summary.json" in files_present
        )

        # Status classification
        if parse_error:
            status = "Parse error"
        elif full_run and warren_scan:
            status = "Complete"
        elif warren_scan or full_run:
            status = "Partial"
        elif files_present:
            # Something is here but not enough
            status = "Partial"
        else:
            status = "Missing"

        results.append(DayHealth(
            date=ds, full_run=full_run, warren_scan=warren_scan,
            portfolio_mon=portfolio_mon, export_done=export_done,
            last_modified=last_mod, status=status,
            parse_error=parse_error,
        ))

    return results


AUTOMATION_LAST_FAILURE_JSON = "automation_last_failure.json"


def get_automation_last_failure_banner() -> str:
    """
    Return a single-line banner for the Dashboard when the Warren runner
    logged a failure to output/automation_last_failure.json (e.g. CDP down).
    Empty string if no file or unreadable.
    """
    p = os.path.join(WARREN_OUTPUT_DIR, AUTOMATION_LAST_FAILURE_JSON)
    if not os.path.isfile(p):
        return ""
    try:
        with open(p, "r", encoding="utf-8") as fh:
            d = json.load(fh)
        label = (d.get("label") or "").strip()
        if not label:
            return ""
        at = (d.get("at") or "").strip()
        if at and "T" in at:
            at = at.replace("T", " ").split(".")[0].split("+")[0].rstrip("Z")
        suffix = f" — {at}" if at else ""
        return f"Last failure: {label}{suffix}"
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# 2. Suggested Ticker Classification
# ---------------------------------------------------------------------------

@dataclass
class SuggestedTicker:
    ticker:      str
    company:     str = ""
    sector_etf:  str = ""
    source:      str = "Warren AI"      # Warren AI | RRG overlap | Both | Unknown
    rank:        int = 0
    setup:       str = ""
    portfolio_status: str = "New"       # Already owned | Watchlist | New | Archived
    verdict:     str = ""               # Already owned | New candidate | Watchlist candidate | Avoid
    scan_date:   str = ""
    is_top3:     bool = False
    # Display-only strings from step_04 / final Top 3 (no invented levels)
    entry_disp:  str = ""
    sl_disp:     str = ""
    tp_disp:     str = ""
    rr_disp:     str = ""


def classify_suggested_tickers(
    w: wl.WarrenResult,
    active_tickers: set[str],
    watchlist_tickers: set[str],
    archived_tickers: set[str] | None = None,
) -> list[SuggestedTicker]:
    """
    Build a list of SuggestedTicker from a WarrenResult, tagged by portfolio status.
    """
    archived = archived_tickers or set()
    top3_set = {t["ticker"] for t in w.top3_trades}
    results: list[SuggestedTicker] = []
    seen: set[str] = set()

    # Deduplicate actionable (step_04 double-parses sometimes)
    actionable_deduped = list(dict.fromkeys(w.actionable))

    # Build unified list: top3 first, then full actionable, then remaining all_tickers
    order = list(dict.fromkeys(
        [t["ticker"] for t in w.top3_trades]
        + actionable_deduped
        + w.all_tickers
        + w.watch_tickers
    ))

    for idx, ticker in enumerate(order):
        if ticker in seen:
            continue
        seen.add(ticker)

        tr = w.ticker_rows.get(ticker)
        sector = w.ticker_sector.get(ticker, tr.sector_etf if tr else "")
        setup = tr.setup if tr else ""
        rank = tr.rank if tr else 0

        if ticker in active_tickers:
            p_status = "Already owned"
            verdict = "Already owned"
        elif ticker in watchlist_tickers:
            p_status = "Watchlist"
            verdict = "Watchlist candidate"
        elif ticker in archived:
            p_status = "Archived"
            verdict = "Avoid or not actionable"
        else:
            p_status = "New"
            verdict = "New candidate"

        # Downgrade verdict for avoid list
        if ticker in w.avoid:
            verdict = "Avoid or not actionable"

        er, sr, tpr, rr = warren_level_strings(w, ticker)
        results.append(SuggestedTicker(
            ticker=ticker,
            sector_etf=sector,
            source="Warren AI",
            rank=rank,
            setup=setup,
            portfolio_status=p_status,
            verdict=verdict,
            scan_date=w.date,
            is_top3=(ticker in top3_set),
            entry_disp=_truncate_disp(er),
            sl_disp=_truncate_disp(sr),
            tp_disp=_truncate_disp(tpr),
            rr_disp=_truncate_disp(rr),
        ))

    return results


# ---------------------------------------------------------------------------
# 2b. Warren level strings (step_04 TickerRow + final Top 3 fallback)
# ---------------------------------------------------------------------------

_FLOAT_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _truncate_disp(s: str, n: int = 26) -> str:
    s = (s or "").strip()
    if len(s) <= n:
        return s if s else "—"
    return s[: n - 1] + "…"


def _parse_scalar_price(text: str) -> Optional[float]:
    """Extract first plausible price from a Warren text cell. No guessing beyond regex."""
    if not text:
        return None
    clean = text.replace("$", "").replace(",", "").replace("%", "")
    m = _FLOAT_RE.search(clean)
    if not m:
        return None
    try:
        v = float(m.group(0))
        return v if v > 0 else None
    except Exception:
        return None


def _parse_entry_range_bounds(entry_raw: str) -> tuple[Optional[float], Optional[float]]:
    """
    Parse entry zone text into (low, high). Uses price-like numbers in the zone text.
    Strips trailing parenthetical notes (e.g. \"(20/50 EMA zone)\") so \"20/50\" does not
    corrupt the entry range.
    """
    if not entry_raw or not entry_raw.strip():
        return None, None
    s = entry_raw.replace("–", "-").replace("—", "-")
    # Remove trailing parenthetical commentary that often contains extra digits (20/50 EMA, etc.)
    if "(" in s:
        s = s.split("(", 1)[0].strip()
    nums = [float(x) for x in _FLOAT_RE.findall(s)]
    if len(nums) >= 2:
        lo, hi = min(nums[0], nums[1]), max(nums[0], nums[1])
        if len(nums) > 2:
            lo, hi = min(nums), max(nums)
        return lo, hi
    if len(nums) == 1:
        return nums[0], nums[0]
    return None, None


def warren_level_strings(result: "wl.WarrenResult", ticker: str) -> tuple[str, str, str, str]:
    """
    Return raw display strings (entry, stop, target, rr) from step_04 row
    with final_action_plan Top 3 trade fields as fallback.
    """
    tr = result.ticker_rows.get(ticker)
    t3 = next((x for x in result.top3_trades if x.get("ticker") == ticker), None)

    def _from_tr(attr: str, t3_key: str) -> str:
        v = ""
        if tr:
            v = (getattr(tr, attr, "") or "").strip()
        if not v and t3:
            v = (t3.get(t3_key) or "").strip()
        return v

    entry = _from_tr("entry_zone", "entry")
    stop  = _from_tr("stop", "stop")
    tgt   = _from_tr("target", "target")
    rr    = _from_tr("rr", "rr")
    return entry, stop, tgt, rr


def _entry_bar_triggered(high: float, low: float, el: float, eh: float) -> bool:
    """True when the daily bar overlaps the entry zone (Warren spec: high>=lo and low<=hi)."""
    return high >= el and low <= eh


def evaluate_long_levels_outcome(
    hist,
    scan_date: date,
    entry_lo: Optional[float],
    entry_hi: Optional[float],
    stop: Optional[float],
    target: Optional[float],
) -> str:
    """
    Accountability-only outcome from scan date through last bar in hist.
    Long-only. Does not invent prices.
    """
    if stop is None or target is None:
        return "Levels missing"
    if entry_lo is None or entry_hi is None:
        return "Levels missing"
    if hist is None or getattr(hist, "empty", True):
        return "Levels missing"

    ts0 = _tz_safe_ts(scan_date, hist)
    bars = hist[hist.index >= ts0]
    if bars is None or bars.empty:
        return "Levels missing"

    trigger_idx: Optional[int] = None
    for i in range(len(bars)):
        row = bars.iloc[i]
        hi = float(row["High"])
        lo = float(row["Low"])
        if _entry_bar_triggered(hi, lo, entry_lo, entry_hi):
            trigger_idx = i
            break

    if trigger_idx is None:
        return "No entry"

    after = bars.iloc[trigger_idx + 1 :]
    if after is None or after.empty:
        return "Open"

    for _, row in after.iterrows():
        hi = float(row["High"])
        lo = float(row["Low"])
        hit_stop = lo <= stop
        hit_tgt = hi >= target
        if hit_stop and hit_tgt:
            return "Ambiguous same day"
        if hit_stop:
            return "Stop hit"
        if hit_tgt:
            return "Target hit"
    return "Open"


def summarize_forward_walk_level_outcomes(rows: list["ForwardWalkRow"]) -> dict[str, int]:
    """Count outcomes for the level summary line (includes all rows)."""
    keys = [
        "triggered", "no_entry", "target_hit", "stop_hit", "open",
        "ambiguous", "levels_missing", "rejected",
    ]
    out = {k: 0 for k in keys}
    for r in rows:
        o = (r.outcome or "").strip()
        if o == "No entry":
            out["no_entry"] += 1
        elif o == "Target hit":
            out["target_hit"] += 1
            out["triggered"] += 1
        elif o == "Stop hit":
            out["stop_hit"] += 1
            out["triggered"] += 1
        elif o == "Rejected":
            out["rejected"] += 1
        elif o == "Open":
            out["open"] += 1
            out["triggered"] += 1
        elif o == "Ambiguous same day":
            out["ambiguous"] += 1
            out["triggered"] += 1
        elif o == "Levels missing":
            out["levels_missing"] += 1
        elif o not in ("Rejected",):
            out["levels_missing"] += 1
    return out


def format_level_outcome_summary(counts: dict[str, int]) -> str:
    """Single-line factual summary for the Dashboard."""
    tr = counts.get("triggered", 0)
    ne = counts.get("no_entry", 0)
    th = counts.get("target_hit", 0)
    sh = counts.get("stop_hit", 0)
    op = counts.get("open", 0)
    am = counts.get("ambiguous", 0)
    ms = counts.get("levels_missing", 0)
    rj = counts.get("rejected", 0)
    return (
        f"Levels: {tr} entries triggered · {th} target hit · {sh} stop hit · {op} open · {ne} no entry"
        + (f" · {am} same-day ambiguous" if am else "")
        + (f" · {ms} levels missing" if ms else "")
        + (f" · {rj} rejected by Warren (no plan)" if rj else "")
    )


# ---------------------------------------------------------------------------
# 3. Forward Walk Test
# ---------------------------------------------------------------------------

@dataclass
class ForwardWalkRow:
    scan_date:      str
    ticker:         str
    source:         str = "Warren AI"
    rank:           int = 0
    setup:          str = ""
    scan_price:     Optional[float] = None
    current_price:  Optional[float] = None
    return_pct:     Optional[float] = None
    days_since:     int = 0
    max_high:       Optional[float] = None   # MFE ref: highest close since scan
    min_low:        Optional[float] = None   # MAE ref: lowest close since scan
    mfe_pct:        Optional[float] = None   # max favourable excursion %
    mae_pct:        Optional[float] = None   # max adverse excursion %
    portfolio_status: str = "Unknown"
    price_missing:  bool = False
    # Parsed / display levels (Warren step_04 + Top 3 fallback; long-only evaluation)
    entry_low:      Optional[float] = None
    entry_high:     Optional[float] = None
    stop_loss:      Optional[float] = None
    target_price:   Optional[float] = None
    risk_reward:    str = ""                 # raw R:R text
    entry_disp:     str = ""
    sl_disp:        str = ""
    tp_disp:        str = ""
    rr_disp:        str = ""
    outcome:        str = ""                 # No entry | Target hit | Stop hit | Open | Ambiguous same day | Levels missing
    sector_etf:     str = ""                 # sector ETF from step_03 / ticker_sector (e.g. XLK)


def _fetch_history_cached(ticker: str) -> Optional[object]:
    """Fetch ~120d daily history with in-process cache (forward walk + level outcomes)."""
    now = time.time()
    cached = _PRICE_CACHE.get(ticker)
    if cached and (now - cached["ts"]) < _PRICE_CACHE_TTL:
        return cached["hist"]
    try:
        hist = yf.Ticker(ticker).history(period="120d", auto_adjust=True)
        if hist is None or hist.empty:
            return None
        _PRICE_CACHE[ticker] = {"hist": hist, "ts": now}
        return hist
    except Exception:
        return None


def _tz_safe_ts(target_date: date, hist) -> "pd.Timestamp":
    """Return a Timestamp compatible with hist.index tz (aware or naive)."""
    import pandas as pd
    ts = pd.Timestamp(target_date)
    idx_tz = getattr(hist.index, "tz", None)
    if idx_tz is not None:
        # Match yfinance's index tz (e.g. America/New_York); do not assume UTC.
        ts = ts.tz_localize(idx_tz)
    return ts


def _get_price_on_or_after(hist, target_date: date) -> Optional[float]:
    """Return the first closing price on or after target_date from a yfinance history df."""
    if hist is None or hist.empty:
        return None
    ts = _tz_safe_ts(target_date, hist)
    after = hist[hist.index >= ts]
    if after.empty:
        return None
    return float(after["Close"].iloc[0])


def _get_range_stats(hist, start_date: date) -> tuple[Optional[float], Optional[float]]:
    """Return (max_close, min_close) from start_date to today."""
    if hist is None or hist.empty:
        return None, None
    ts = _tz_safe_ts(start_date, hist)
    sub = hist[hist.index >= ts]["Close"]
    if sub.empty:
        return None, None
    return float(sub.max()), float(sub.min())


def get_forward_walk_results(
    days: int = 14,
    active_tickers: set[str] | None = None,
    watchlist_tickers: set[str] | None = None,
) -> list[ForwardWalkRow]:
    """
    For every Warren AI scan in the last `days` days, pull suggested tickers and
    compute forward performance to today using yfinance.

    Skips today's scan (no forward data yet).
    Returns rows sorted newest scan first, then by return_pct desc.
    """
    today = date.today()
    active  = active_tickers or set()
    wl_set  = watchlist_tickers or set()
    rows: list[ForwardWalkRow] = []

    for delta in range(1, days + 1):        # skip delta=0 (today)
        d = today - timedelta(days=delta)
        ds = d.strftime("%Y-%m-%d")
        folder = os.path.join(WARREN_OUTPUT_DIR, ds)
        summary_path = os.path.join(folder, "run_summary.json")

        if not os.path.isfile(summary_path):
            continue
        try:
            with open(summary_path, "r", encoding="utf-8") as fh:
                s = json.load(fh)
            if not s.get("success"):
                continue
        except Exception:
            continue

        # Parse the day's result
        try:
            day_result = wl.WarrenResult(
                date=ds, freshness="STALE", success=True,
                output_path=folder, generated_at="",
            )
            wl._parse_step02(wl._read_file(folder, "step_02.md"), day_result)
            wl._parse_step03(wl._read_file(folder, "step_03.md"), day_result)
            wl._parse_step04(wl._read_file(folder, "step_04.md"), day_result)
            wl._parse_final_plan(wl._read_file(folder, "final_action_plan.md"), day_result)
        except Exception:
            continue

        top3_set      = {t["ticker"] for t in day_result.top3_trades}
        actionable_set = set(day_result.actionable)   # tickers with a trade plan
        scan_date_obj = d

        # Deduplicate suggested tickers for this day
        seen_day: set[str] = set()
        ordered = list(dict.fromkeys(
            [t["ticker"] for t in day_result.top3_trades]
            + list(dict.fromkeys(day_result.actionable))
            + day_result.all_tickers
        ))

        for ticker in ordered:
            if ticker in seen_day:
                continue
            seen_day.add(ticker)

            tr = day_result.ticker_rows.get(ticker)
            rank = tr.rank if tr else (1 if ticker in top3_set else 0)
            setup = tr.setup if tr else ""
            sector_etf = ""
            if tr and getattr(tr, "sector_etf", None):
                sector_etf = str(tr.sector_etf).strip().upper()
            if not sector_etf:
                sector_etf = str(day_result.ticker_sector.get(ticker, "")).strip().upper()

            # Scan-date price: use step_03 price string, then fallback to yfinance
            scan_price: Optional[float] = None
            if tr and tr.price:
                try:
                    scan_price = float(tr.price.replace(",", ""))
                except Exception:
                    pass

            hist = _fetch_history_cached(ticker)

            if scan_price is None:
                scan_price = _get_price_on_or_after(hist, scan_date_obj)

            current_price: Optional[float] = None
            if hist is not None and not hist.empty:
                current_price = float(hist["Close"].iloc[-1])

            return_pct: Optional[float] = None
            if scan_price and current_price and scan_price > 0:
                return_pct = (current_price - scan_price) / scan_price * 100

            max_high, min_low = _get_range_stats(hist, scan_date_obj)

            mfe_pct: Optional[float] = None
            mae_pct: Optional[float] = None
            if scan_price and scan_price > 0:
                if max_high is not None:
                    mfe_pct = (max_high - scan_price) / scan_price * 100
                if min_low is not None:
                    mae_pct = (min_low - scan_price) / scan_price * 100

            days_since = (today - scan_date_obj).days

            if ticker in active:
                p_status = "Already owned"
            elif ticker in wl_set:
                p_status = "Watchlist"
            else:
                p_status = "New"

            # Levels from step_04 / Top 3 — display + parsed accountability
            er, sr, tpr, rr_txt = warren_level_strings(day_result, ticker)
            elo, ehi = _parse_entry_range_bounds(er)
            sl_f = _parse_scalar_price(sr)
            tp_f = _parse_scalar_price(tpr)

            # Distinguish between:
            #   "Rejected" — Warren AI evaluated this ticker and explicitly rejected it
            #                (in all_tickers but NOT in actionable; no plan was ever given)
            #   "Levels missing" — ticker was actionable but levels couldn't be parsed/fetched
            has_levels = bool(er and sl_f is not None and tp_f is not None)
            was_rejected = (ticker not in actionable_set) and (ticker in (set(day_result.all_tickers) - actionable_set))
            try:
                if was_rejected and not has_levels:
                    outcome = "Rejected"
                else:
                    outcome = evaluate_long_levels_outcome(
                        hist, scan_date_obj, elo, ehi, sl_f, tp_f,
                    )
            except Exception:
                outcome = "Levels missing"

            rows.append(ForwardWalkRow(
                scan_date=ds,
                ticker=ticker,
                source="Warren AI",
                rank=rank,
                setup=setup,
                scan_price=round(scan_price, 2) if scan_price else None,
                current_price=round(current_price, 2) if current_price else None,
                return_pct=round(return_pct, 2) if return_pct is not None else None,
                days_since=days_since,
                max_high=round(max_high, 2) if max_high else None,
                min_low=round(min_low, 2) if min_low else None,
                mfe_pct=round(mfe_pct, 2) if mfe_pct is not None else None,
                mae_pct=round(mae_pct, 2) if mae_pct is not None else None,
                portfolio_status=p_status,
                price_missing=(scan_price is None or current_price is None),
                entry_low=round(elo, 4) if elo is not None else None,
                entry_high=round(ehi, 4) if ehi is not None else None,
                stop_loss=round(sl_f, 4) if sl_f is not None else None,
                target_price=round(tp_f, 4) if tp_f is not None else None,
                risk_reward=rr_txt.strip(),
                entry_disp=_truncate_disp(er, 22),
                sl_disp=_truncate_disp(sr, 14),
                tp_disp=_truncate_disp(tpr, 14),
                rr_disp=_truncate_disp(rr_txt, 12),
                outcome=outcome,
                sector_etf=sector_etf,
            ))

    return rows


def get_forward_walk_rows_for_scan_date(
    scan_date: str,
    active_tickers: set[str] | None = None,
    watchlist_tickers: set[str] | None = None,
    max_lookback_days: int = 400,
) -> list[ForwardWalkRow]:
    """Reuse forward-walk engine; return rows for a single scan_date (YYYY-MM-DD)."""
    rows = get_forward_walk_results(
        days=max(1, max_lookback_days),
        active_tickers=active_tickers,
        watchlist_tickers=watchlist_tickers,
    )
    return [r for r in rows if r.scan_date == scan_date]


@dataclass
class WalkSummary:
    total:          int = 0
    with_prices:    int = 0
    positive:       int = 0
    negative:       int = 0
    avg_return:     Optional[float] = None
    avg_mfe:        Optional[float] = None
    avg_mae:        Optional[float] = None
    win_rate:       Optional[float] = None
    top_winners:    list[tuple[str, float]] = field(default_factory=list)
    worst_losers:   list[tuple[str, float]] = field(default_factory=list)


def forward_walk_summary(rows: list[ForwardWalkRow]) -> WalkSummary:
    """Compute aggregate stats across all forward-walk rows."""
    s = WalkSummary(total=len(rows))
    priced = [r for r in rows if r.return_pct is not None]
    s.with_prices = len(priced)
    if not priced:
        return s

    returns = [r.return_pct for r in priced]   # type: ignore[misc]
    s.positive  = sum(1 for v in returns if v > 0)
    s.negative  = sum(1 for v in returns if v <= 0)
    s.avg_return = round(sum(returns) / len(returns), 2)
    s.win_rate   = round(s.positive / len(priced) * 100, 1)

    mfe_vals = [r.mfe_pct for r in priced if r.mfe_pct is not None]
    mae_vals = [r.mae_pct for r in priced if r.mae_pct is not None]
    if mfe_vals:
        s.avg_mfe = round(sum(mfe_vals) / len(mfe_vals), 2)
    if mae_vals:
        s.avg_mae = round(sum(mae_vals) / len(mae_vals), 2)

    sorted_by_return = sorted(priced, key=lambda r: r.return_pct, reverse=True)  # type: ignore[arg-type]
    s.top_winners  = [(r.ticker, r.return_pct) for r in sorted_by_return[:3]]   # type: ignore[misc]
    s.worst_losers = [(r.ticker, r.return_pct) for r in sorted_by_return[-3:]]  # type: ignore[misc]

    return s


# ---------------------------------------------------------------------------
# 4. Sector Candidate Diagnostics
# ---------------------------------------------------------------------------

# All 11 sector ETFs with representative large/mega-cap names per sector
_SECTOR_UNIVERSE: dict[str, list[str]] = {
    "XLK":  ["NVDA", "MSFT", "AAPL", "AVGO", "AMD", "ORCL", "CRM", "ADBE",
              "INTC", "AMAT", "MU", "QCOM", "NOW", "PANW", "CRWD", "SNOW",
              "PLTR", "SMCI", "TSM", "ASML", "ARM"],
    "XLF":  ["BRK-B", "JPM", "V", "MA", "BAC", "WFC", "GS", "MS", "AXP",
              "BLK", "USB", "PNC", "TFC", "KEY", "BKU", "BFH"],
    "XLE":  ["XOM", "CVX", "COP", "EOG", "SLB", "MPC", "PSX", "VLO",
              "OXY", "DVN", "CNX", "KNTK", "EQT"],
    "XLI":  ["GE", "CAT", "HON", "UNP", "RTX", "LMT", "DE", "ETN",
              "PH", "EMR", "MMM", "GD", "NOC"],
    "XLY":  ["AMZN", "TSLA", "MCD", "NKE", "HD", "LOW", "BKNG", "CMG",
              "TJX", "SBUX", "VFC", "YUMC", "DHI"],
    "XLC":  ["META", "GOOGL", "GOOG", "NFLX", "DIS", "T", "VZ", "CMCSA",
              "EA", "TTWO", "ATVI"],
    "XLV":  ["LLY", "UNH", "JNJ", "MRK", "ABBV", "TMO", "ABT", "DHR",
              "BMY", "AMGN", "GILD", "CI", "HUM"],
    "XLP":  ["PG", "KO", "PEP", "WMT", "COST", "PM", "MO", "CL",
              "MDLZ", "STZ"],
    "XLU":  ["NEE", "DUK", "SO", "D", "AEP", "EXC", "SRE", "PCG",
              "ED", "XEL"],
    "XLB":  ["LIN", "APD", "ECL", "SHW", "FCX", "NUE", "DD", "EMN",
              "MOS", "ALB"],
    "XLRE": ["AMT", "PLD", "EQIX", "CCI", "O", "SPG", "VICI", "PSA",
              "EQR", "AVB"],
}


def _check_filter_reason(tr: "wl.TickerRow | None") -> str:
    """
    Determine why a ticker was likely filtered out in step_04.
    Returns a short reason label based on step_03 data.
    """
    if tr is None:
        return "no_data"
    reasons = []
    # RSI
    try:
        rsi = float(tr.rsi)
        if rsi > 72:
            reasons.append("extended_RSI")
    except (ValueError, TypeError):
        pass
    # Extension vs 50 EMA
    try:
        vs50 = tr.vs_50ema
        # Parse "+12.5%" style
        m = re.search(r'([+-]?\d+\.?\d*)', vs50)
        if m and float(m.group(1)) > 12:
            reasons.append("too_extended")
    except Exception:
        pass
    # Setup label
    if tr.setup and tr.setup.lower() in ("extended", "falling knife"):
        reasons.append(f"setup_{tr.setup.lower().replace(' ', '_')}")
    # Fair value upside (negative = overvalued by InvestingPro)
    try:
        fv = float(tr.fv_upside.rstrip('%'))
        if fv < 0:
            reasons.append("negative_FV_upside")
    except (ValueError, AttributeError, TypeError):
        pass
    # Step_04 verdict
    if tr.verdict:
        v = tr.verdict.lower()
        if "avoid" in v:
            reasons.append("verdict_avoid")
    return ", ".join(reasons) if reasons else "unknown"


def _load_rrg_sectors_file() -> dict:
    """
    Load rrg_sectors.json written by export_rrg_sectors.py.
    Returns empty dict if missing.
    """
    path = os.path.join(WARREN_OUTPUT_DIR, "rrg_sectors.json")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def generate_sector_diagnostics(
    w: "wl.WarrenResult",
    all_positions: list[dict] | None = None,
    output_path: str | None = None,
) -> dict:
    """
    Produce per-sector diagnostics from the latest Warren AI run.

    For each sector:
      - sector_selected_by_warren: Warren AI picked this as a top sector in step 01
      - sector_selected_by_rrg: RRG model has it as Leading/Improving with score > 0
      - sector_source: Warren AI | RRG | Both | Neither
      - sector_in_final_scan: True when either source included it
      - candidates_in_scan: tickers Warren AI included
      - candidates_passed: tickers that survived step_04 filters
      - top_rejection_reason: most common filter that rejected candidates

    Writes to output_path (or WARREN_OUTPUT_DIR/sector_candidate_diagnostics.json).
    """
    diag: dict = {}
    warren_sectors = set(w.top_sectors) if w.top_sectors else set()

    # Load RRG data
    rrg_data = _load_rrg_sectors_file()
    rrg_mandatory = {s["etf"] for s in rrg_data.get("mandatory_sectors", [])}
    rrg_scores: dict[str, dict] = {
        s["etf"]: s for s in rrg_data.get("all_sectors", [])
    }

    all_sectors = set(_SECTOR_UNIVERSE.keys()) | warren_sectors | rrg_mandatory

    for etf in sorted(all_sectors):
        # Source classification
        by_warren = etf in warren_sectors
        by_rrg    = etf in rrg_mandatory
        if by_warren and by_rrg:
            source = "Both"
        elif by_warren:
            source = "Warren AI"
        elif by_rrg:
            source = "RRG"
        else:
            source = "Neither"

        in_final_scan = by_warren or by_rrg

        # RRG details for this sector
        rrg_info = rrg_scores.get(etf, {})
        rrg_quadrant  = rrg_info.get("quadrant", "—")
        rrg_direction = rrg_info.get("direction", "—")
        rrg_score     = rrg_info.get("accel_score", None)

        # Tickers Warren AI placed in this sector
        warren_tickers = [tk for tk, sec in w.ticker_sector.items() if sec == etf]

        # Tickers from our known universe for this sector
        universe = _SECTOR_UNIVERSE.get(etf, [])

        # Tickers that made it to step_04 actionable
        # Deduplicate actionable list
        actionable_set = set(w.actionable)
        passed = [tk for tk in warren_tickers if tk in actionable_set]

        # Per-ticker rejection reasons (from step_03 data)
        rejections: dict[str, str] = {}
        for tk in warren_tickers:
            if tk not in passed:
                tr = w.ticker_rows.get(tk)
                rejections[tk] = _check_filter_reason(tr)

        # Aggregate rejection reason counts
        failed_by_filter: dict[str, int] = {}
        for reason_str in rejections.values():
            for r in reason_str.split(", "):
                r = r.strip()
                if r:
                    failed_by_filter[r] = failed_by_filter.get(r, 0) + 1

        top_rejection = (
            max(failed_by_filter, key=failed_by_filter.get)  # type: ignore[arg-type]
            if failed_by_filter else "—"
        )

        diag[etf] = {
            # Source labels (Step 5 requirement)
            "scan_date":               w.date,
            "sector_selected_by_warren": by_warren,
            "sector_selected_by_rrg":    by_rrg,
            "sector_source":             source,
            "sector_in_final_scan":      in_final_scan,
            "rrg_quadrant":             rrg_quadrant,
            "rrg_direction":            rrg_direction,
            "rrg_score":                rrg_score,
            "top_sectors_today":        list(w.top_sectors),
            # Candidate counts
            "candidates_in_scan":       len(warren_tickers),
            "candidates_considered":    len(warren_tickers),
            "candidate_tickers_in_scan": warren_tickers,
            "known_universe_size":      len(universe),
            "known_universe_tickers":   universe,
            "candidates_passed":        len(passed),
            "candidates_passed_step04": len(passed),
            "passed_tickers":           passed,
            # Rejection analysis
            "rejection_reasons_per_ticker": rejections,
            "failed_by_filter_counts":      failed_by_filter,
            "top_rejection_reason":         top_rejection,
            # Audit trail
            "prompt_change_applied": True,
            "market_cap_note": (
                "Prompt 02 '(not micro-cap)' bias removed. Large/mega-cap names now "
                "explicitly welcome. Sector reached scan because: " + source + "."
                if in_final_scan else
                "Sector not in Warren AI scan and not Leading/Improving by RRG. "
                "Will appear in scan only if RRG model upgrades it."
            ),
        }

    # Save to file
    if output_path is None:
        output_path = os.path.join(WARREN_OUTPUT_DIR, "sector_candidate_diagnostics.json")
    try:
        with open(output_path, "w", encoding="utf-8") as fh:
            json.dump(diag, fh, indent=2)
    except Exception:
        pass   # Don't crash if we can't write

    return diag


def get_sector_diagnostics_cached(
    w: "wl.WarrenResult",
    all_positions: list[dict] | None = None,
    refresh: bool = False,
) -> dict:
    """
    Return cached sector diagnostics, regenerating if the scan date changed
    or refresh is forced.
    """
    cache_path = os.path.join(WARREN_OUTPUT_DIR, "sector_candidate_diagnostics.json")

    if not refresh and os.path.isfile(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as fh:
                cached = json.load(fh)
            # Use if the scan date matches
            sample = next(iter(cached.values()), {})
            if sample.get("scan_date") == w.date:
                return cached
        except Exception:
            pass

    return generate_sector_diagnostics(w, all_positions, cache_path)


# ---------------------------------------------------------------------------
# 5. Benchmark Definitions and Price Fetching
# ---------------------------------------------------------------------------

MAG7: list[str] = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA"]

BENCHMARKS: dict[str, str] = {
    "MAG7 (equal weight)": "MAG7",   # synthetic — computed from MAG7 list
    "QQQ":  "QQQ",
    "XLK":  "XLK",
    "SPY":  "SPY",
}

_BM_CACHE: dict[str, dict] = {}   # symbol → {"hist": df, "ts": float}
_BM_TTL   = 1800                   # 30-minute in-process cache


def _fetch_bm_hist(symbol: str):
    """Fetch 90-day daily history for a benchmark ticker. Cached."""
    now = time.time()
    entry = _BM_CACHE.get(symbol)
    if entry and (now - entry["ts"]) < _BM_TTL:
        return entry["hist"]
    try:
        hist = yf.Ticker(symbol).history(period="90d", auto_adjust=True)
        if hist is None or hist.empty:
            return None
        _BM_CACHE[symbol] = {"hist": hist, "ts": now}
        return hist
    except Exception:
        return None


def _bm_price_on_or_after(hist, target_date: date) -> Optional[float]:
    """First closing price on or after target_date, tz-safe."""
    if hist is None or hist.empty:
        return None
    import pandas as pd
    ts = pd.Timestamp(target_date)
    if hist.index.tz is not None:
        ts = ts.tz_localize("UTC")
    after = hist[hist.index >= ts]
    if after.empty:
        return None
    return float(after["Close"].iloc[0])


def _bm_current_price(hist) -> Optional[float]:
    """Latest closing price from history df."""
    if hist is None or hist.empty:
        return None
    return float(hist["Close"].iloc[-1])


def fetch_benchmark_returns(
    start_date: date,
    end_date: date | None = None,
) -> dict[str, Optional[float]]:
    """
    Compute percentage return from start_date to end_date (default: today)
    for each benchmark.

    Returns:
        {
          "QQQ": 2.4,
          "XLK": 3.1,
          "SPY": 1.8,
          "MAG7 (equal weight)": 2.9,
        }
    Values are percent (e.g. 2.4 means +2.4%). None if data unavailable.
    """
    end_date = end_date or date.today()
    results: dict[str, Optional[float]] = {}

    # ETF benchmarks
    for label, symbol in [("QQQ", "QQQ"), ("XLK", "XLK"), ("SPY", "SPY")]:
        hist = _fetch_bm_hist(symbol)
        p0 = _bm_price_on_or_after(hist, start_date)
        p1 = _bm_current_price(hist)
        if p0 and p1 and p0 > 0:
            results[label] = round((p1 - p0) / p0 * 100, 2)
        else:
            results[label] = None

    # MAG7 equal-weight composite
    mag7_start: list[float] = []
    mag7_end:   list[float] = []
    for sym in MAG7:
        hist = _fetch_bm_hist(sym)
        p0 = _bm_price_on_or_after(hist, start_date)
        p1 = _bm_current_price(hist)
        if p0 and p1 and p0 > 0:
            mag7_start.append(p0)
            mag7_end.append(p1)
    if mag7_start and len(mag7_start) >= 5:
        # Equal-weight: average individual return
        avg_ret = sum((e - s) / s * 100 for s, e in zip(mag7_start, mag7_end)) / len(mag7_start)
        results["MAG7 (equal weight)"] = round(avg_ret, 2)
    else:
        results["MAG7 (equal weight)"] = None

    return results


def _portfolio_return_approx(
    active: list,           # PositionResult or dict items
    lookback_days: int = 30,
) -> Optional[float]:
    """
    Approximate portfolio return over the last `lookback_days` days, using
    equal-weight average of individual position returns over the period.
    Uses _fetch_history_cached from the walk-test cache.

    Returns percent or None if insufficient data.
    """
    start = date.today() - timedelta(days=lookback_days)
    returns: list[float] = []
    for pos in active:
        ticker = getattr(pos, "ticker", None) or (pos.get("ticker") if isinstance(pos, dict) else None)
        if not ticker:
            continue
        hist = _fetch_history_cached(ticker)
        p0 = _get_price_on_or_after(hist, start)
        p1 = _bm_current_price(hist)
        if p0 and p1 and p0 > 0:
            returns.append((p1 - p0) / p0 * 100)
    if not returns:
        return None
    return round(sum(returns) / len(returns), 2)


# Return periods for the Opportunity Cost Check
_OCC_PERIODS: list[tuple[str, int]] = [
    ("1 Day",   1),
    ("5 Days",  5),
    ("1 Month", 30),
]


@dataclass
class BenchmarkRow:
    """One row in the Opportunity Cost Check table."""
    label:       str
    ret_1d:      Optional[float] = None
    ret_5d:      Optional[float] = None
    ret_1m:      Optional[float] = None


def get_opportunity_cost_rows(active: list) -> list[BenchmarkRow]:
    """
    Build rows for the Opportunity Cost Check section.

    Rows:
      Active Portfolio (approx)
      MAG7 (equal weight)
      QQQ
      XLK
      SPY
    """
    rows: list[BenchmarkRow] = []

    # Precompute benchmark returns for each period
    bm_data: dict[str, dict[str, Optional[float]]] = {}
    for label, days in _OCC_PERIODS:
        bm_data[label] = fetch_benchmark_returns(date.today() - timedelta(days=days))

    # Active portfolio row
    port_row = BenchmarkRow(label="Active Portfolio (approx)")
    for period_label, days in _OCC_PERIODS:
        ret = _portfolio_return_approx(active, lookback_days=days)
        if period_label == "1 Day":   port_row.ret_1d = ret
        elif period_label == "5 Days":  port_row.ret_5d = ret
        elif period_label == "1 Month": port_row.ret_1m = ret
    rows.append(port_row)

    # Benchmark rows
    bm_labels = ["MAG7 (equal weight)", "QQQ", "XLK", "SPY"]
    for bm in bm_labels:
        row = BenchmarkRow(label=bm)
        for period_label, _ in _OCC_PERIODS:
            val = bm_data[period_label].get(bm)
            if period_label == "1 Day":   row.ret_1d = val
            elif period_label == "5 Days":  row.ret_5d = val
            elif period_label == "1 Month": row.ret_1m = val
        rows.append(row)

    return rows


# ---------------------------------------------------------------------------
# 6. Forward Walk Test — benchmark enrichment
# ---------------------------------------------------------------------------

@dataclass
class ForwardWalkRowEnriched(ForwardWalkRow):
    """ForwardWalkRow + benchmark comparison columns."""
    mag7_ret:   Optional[float] = None
    qqq_ret:    Optional[float] = None
    xlk_ret:    Optional[float] = None
    spy_ret:    Optional[float] = None
    excess_mag7: Optional[float] = None
    excess_qqq:  Optional[float] = None


def enrich_forward_walk_with_benchmarks(
    rows: list[ForwardWalkRow],
) -> list[ForwardWalkRowEnriched]:
    """
    Add benchmark return columns to existing ForwardWalkRow list.

    For each row, benchmarks are computed over the same [scan_date → today] window.
    Results are cached per scan_date to avoid redundant fetches.
    """
    from datetime import date as _date
    today = _date.today()

    # Cache benchmark returns keyed by scan_date string
    bm_by_date: dict[str, dict[str, Optional[float]]] = {}

    enriched: list[ForwardWalkRowEnriched] = []
    for r in rows:
        if r.scan_date not in bm_by_date:
            try:
                scan_d = datetime.strptime(r.scan_date, "%Y-%m-%d").date()
                bm_by_date[r.scan_date] = fetch_benchmark_returns(scan_d, today)
            except Exception:
                bm_by_date[r.scan_date] = {}

        bm = bm_by_date[r.scan_date]
        mag7 = bm.get("MAG7 (equal weight)")
        qqq  = bm.get("QQQ")
        xlk  = bm.get("XLK")
        spy  = bm.get("SPY")

        excess_mag7 = (
            round(r.return_pct - mag7, 2)
            if r.return_pct is not None and mag7 is not None
            else None
        )
        excess_qqq = (
            round(r.return_pct - qqq, 2)
            if r.return_pct is not None and qqq is not None
            else None
        )

        enriched.append(ForwardWalkRowEnriched(
            scan_date=r.scan_date,
            ticker=r.ticker,
            source=r.source,
            rank=r.rank,
            setup=r.setup,
            scan_price=r.scan_price,
            current_price=r.current_price,
            return_pct=r.return_pct,
            days_since=r.days_since,
            max_high=r.max_high,
            min_low=r.min_low,
            mfe_pct=r.mfe_pct,
            mae_pct=r.mae_pct,
            portfolio_status=r.portfolio_status,
            price_missing=r.price_missing,
            entry_low=r.entry_low,
            entry_high=r.entry_high,
            stop_loss=r.stop_loss,
            target_price=r.target_price,
            risk_reward=r.risk_reward,
            entry_disp=r.entry_disp,
            sl_disp=r.sl_disp,
            tp_disp=r.tp_disp,
            rr_disp=r.rr_disp,
            outcome=r.outcome,
            mag7_ret=mag7,
            qqq_ret=qqq,
            xlk_ret=xlk,
            spy_ret=spy,
            excess_mag7=excess_mag7,
            excess_qqq=excess_qqq,
        ))

    return enriched


@dataclass
class WalkSummaryEnriched(WalkSummary):
    avg_excess_mag7:   Optional[float] = None
    avg_excess_qqq:    Optional[float] = None
    pct_beat_mag7:     Optional[float] = None
    pct_beat_qqq:      Optional[float] = None


def forward_walk_summary_enriched(rows: list[ForwardWalkRowEnriched]) -> WalkSummaryEnriched:
    """Extended summary including benchmark excess-return stats."""
    base = forward_walk_summary(rows)   # type: ignore[arg-type]
    s = WalkSummaryEnriched(
        total=base.total,
        with_prices=base.with_prices,
        positive=base.positive,
        negative=base.negative,
        avg_return=base.avg_return,
        avg_mfe=base.avg_mfe,
        avg_mae=base.avg_mae,
        win_rate=base.win_rate,
        top_winners=base.top_winners,
        worst_losers=base.worst_losers,
    )

    excess_mag7 = [r.excess_mag7 for r in rows if r.excess_mag7 is not None]
    excess_qqq  = [r.excess_qqq  for r in rows if r.excess_qqq  is not None]

    if excess_mag7:
        s.avg_excess_mag7 = round(sum(excess_mag7) / len(excess_mag7), 2)
        s.pct_beat_mag7   = round(sum(1 for v in excess_mag7 if v > 0) / len(excess_mag7) * 100, 1)
    if excess_qqq:
        s.avg_excess_qqq  = round(sum(excess_qqq) / len(excess_qqq), 2)
        s.pct_beat_qqq    = round(sum(1 for v in excess_qqq if v > 0) / len(excess_qqq) * 100, 1)

    return s


# ---------------------------------------------------------------------------
# 7. Mag 7 Exposure
# ---------------------------------------------------------------------------

@dataclass
class Mag7Exposure:
    holdings_in_mag7:    list[str]   # tickers from active portfolio that are Mag 7
    deployed_mag7_value: float       # sum of market values for held Mag 7 positions
    extra_mag7_value:    float       # configured NVDA_VALUE etc. that is also Mag 7
    total_mag7_value:    float       # deployed + extra
    total_portfolio_value: float     # from config (0 if unset)
    pct_of_total:        Optional[float]   # None if total_portfolio_value == 0
    pct_of_deployed:     Optional[float]   # None if deployed == 0
    xlk_deployed_value:  float       # sum of active positions in sector XLK
    xlk_pct_of_total:    Optional[float]
    xlk_pct_of_deployed: Optional[float]
    leadership_alert:    bool        # True if XLK is top sector but exposure is low


def compute_mag7_exposure(
    active: list,                        # PositionResult objects
    extra_buckets: list[tuple[str, float]],  # e.g. [("NVDA", 317)]
    total_portfolio_value: float = 0,
) -> Mag7Exposure:
    """
    Compute Mag 7 and XLK exposure from active portfolio + configured extra buckets.
    """
    mag7_set = set(MAG7)

    deployed_mag7_value = 0.0
    xlk_deployed_value  = 0.0
    holdings_in_mag7: list[str] = []
    total_deployed = 0.0

    for pos in active:
        ticker = getattr(pos, "ticker", None)
        mv     = getattr(pos, "market_value", None)
        sector = getattr(pos, "sector", "")
        if mv is None:
            continue
        total_deployed += mv
        if ticker and ticker.upper() in mag7_set:
            deployed_mag7_value += mv
            holdings_in_mag7.append(ticker.upper())
        if sector and sector.upper() == "XLK":
            xlk_deployed_value += mv

    # Extra buckets (NVDA etc.) — include if they are Mag 7
    extra_mag7_value = sum(
        val for lbl, val in extra_buckets
        if lbl.upper() in mag7_set
    )

    total_mag7_value = deployed_mag7_value + extra_mag7_value

    pct_total    = round(total_mag7_value / total_portfolio_value * 100, 1) if total_portfolio_value > 0 else None
    pct_deployed = round(total_mag7_value / total_deployed * 100, 1) if total_deployed > 0 else None

    xlk_pct_total    = round(xlk_deployed_value / total_portfolio_value * 100, 1) if total_portfolio_value > 0 else None
    xlk_pct_deployed = round(xlk_deployed_value / total_deployed * 100, 1) if total_deployed > 0 else None

    # Leadership alert: XLK is a top sector but combined tech exposure is low
    rrg_data = _load_rrg_sectors_file()
    xlk_mandatory = any(s["etf"] == "XLK" for s in rrg_data.get("mandatory_sectors", []))
    leadership_alert = xlk_mandatory and (pct_deployed or 0) < 10.0   # <10% of deployed

    return Mag7Exposure(
        holdings_in_mag7=holdings_in_mag7,
        deployed_mag7_value=round(deployed_mag7_value, 2),
        extra_mag7_value=round(extra_mag7_value, 2),
        total_mag7_value=round(total_mag7_value, 2),
        total_portfolio_value=total_portfolio_value,
        pct_of_total=pct_total,
        pct_of_deployed=pct_deployed,
        xlk_deployed_value=round(xlk_deployed_value, 2),
        xlk_pct_of_total=xlk_pct_total,
        xlk_pct_of_deployed=xlk_pct_deployed,
        leadership_alert=leadership_alert,
    )


# ---------------------------------------------------------------------------
# 8. Post Alert Performance — normalized ticker price paths
# ---------------------------------------------------------------------------

_BM_SYMBOL_MAP: dict[str, str] = {
    "MAG7": "MAG7",  # synthetic equal-weight — handled specially
    "QQQ":  "QQQ",
    "XLK":  "XLK",
    "SPY":  "SPY",
}


def build_post_alert_series(
    rows: list[ForwardWalkRow],
) -> tuple[list[dict], int]:
    """
    Build normalized price paths for every ForwardWalkRow.

    For each row:
      - Anchor = scan_price if available, else first available close on or after scan_date.
      - Each subsequent daily close is expressed as ((close / anchor) - 1) * 100.

    Returns:
        (series_list, skipped_count)

    Each series dict contains:
        ticker, scan_date, anchor_price, portfolio_status, rank, setup, is_top3,
        cal_dates: [[date_str, pct], ...],
        day_returns: [[day_int, pct], ...],
    """
    series: list[dict] = []
    skipped = 0

    for r in rows:
        hist = _fetch_history_cached(r.ticker)
        if hist is None or hist.empty:
            skipped += 1
            continue

        scan_date_obj = datetime.strptime(r.scan_date, "%Y-%m-%d").date()
        anchor: Optional[float] = r.scan_price
        if anchor is None or anchor <= 0:
            anchor = _get_price_on_or_after(hist, scan_date_obj)
        if anchor is None or anchor <= 0:
            skipped += 1
            continue

        ts0 = _tz_safe_ts(scan_date_obj, hist)
        bars = hist[hist.index >= ts0]
        if bars.empty:
            skipped += 1
            continue

        cal_dates: list[list] = []
        day_returns: list[list] = []
        for i, (idx, bar_row) in enumerate(bars.iterrows()):
            close = float(bar_row["Close"])
            pct = round((close / anchor - 1) * 100, 4)
            cal_dates.append([str(idx)[:10], pct])
            day_returns.append([i, pct])

        lvl_n = sum(
            1 for x in (r.entry_low, r.entry_high, r.stop_loss, r.target_price)
            if x is not None
        )
        series.append({
            "ticker":           r.ticker,
            "scan_date":        r.scan_date,
            "anchor_price":     round(anchor, 4),
            "portfolio_status": r.portfolio_status,
            "rank":             r.rank,
            "setup":            r.setup or "",
            "is_top3":          bool(r.rank > 0 and r.rank <= 3),
            "cal_dates":        cal_dates,
            "day_returns":      day_returns,
            "has_scan_price":   bool(r.scan_price is not None and r.scan_price > 0),
            "level_fill_count": lvl_n,
            "sector_etf":       (r.sector_etf or "").strip().upper(),
            "path_kind":        "stock",
        })

    return series, skipped


def build_post_alert_sector_series(
    rows: list[ForwardWalkRow],
) -> tuple[list[dict], int]:
    """
    One normalized path per (sector ETF, scan_date) implied by scan suggestions.

    Anchor: first ETF daily close on or after scan_date (same rule as missing stock scan_price).
    Each dict includes any_* flags so dashboard filters (Top 3 / New / Owned) can subset sectors.
    """
    from collections import defaultdict

    groups: dict[tuple[str, str], list[ForwardWalkRow]] = defaultdict(list)
    for r in rows:
        etf = (r.sector_etf or "").strip().upper()
        if not etf:
            continue
        groups[(etf, r.scan_date)].append(r)

    # Always surface Technology (XLK) for every scan date in the walk, even when
    # no suggestion mapped to XLK — so the sector chart always has a tech tape reference.
    XLK = "XLK"
    synthetic_xlk_dates: set[str] = set()
    for sd in sorted({r.scan_date for r in rows}):
        key = (XLK, sd)
        if key not in groups:
            groups[key] = []
            synthetic_xlk_dates.add(sd)

    series: list[dict] = []
    skipped = 0

    for (etf, scan_date_str), rlist in groups.items():
        scan_date_obj = datetime.strptime(scan_date_str, "%Y-%m-%d").date()
        hist = _fetch_history_cached(etf)
        if hist is None or hist.empty:
            skipped += 1
            continue

        anchor: Optional[float] = _get_price_on_or_after(hist, scan_date_obj)
        if anchor is None or anchor <= 0:
            skipped += 1
            continue

        ts0 = _tz_safe_ts(scan_date_obj, hist)
        bars = hist[hist.index >= ts0]
        if bars.empty:
            skipped += 1
            continue

        cal_dates: list[list] = []
        day_returns: list[list] = []
        for i, (idx, bar_row) in enumerate(bars.iterrows()):
            close = float(bar_row["Close"])
            pct = round((close / anchor - 1) * 100, 4)
            cal_dates.append([str(idx)[:10], pct])
            day_returns.append([i, pct])

        any_top3 = any((x.rank > 0 and x.rank <= 3) for x in rlist)
        any_new = any(x.portfolio_status == "New" for x in rlist)
        any_owned = any(x.portfolio_status == "Already owned" for x in rlist)
        any_watch = any(x.portfolio_status == "Watchlist" for x in rlist)
        always_include = etf == XLK and scan_date_str in synthetic_xlk_dates

        series.append({
            "ticker":            etf,
            "scan_date":         scan_date_str,
            "anchor_price":      round(anchor, 4),
            "portfolio_status": "Sector",
            "rank":              0,
            "setup":             "",
            "is_top3":           bool(any_top3),
            "cal_dates":         cal_dates,
            "day_returns":       day_returns,
            "has_scan_price":    False,
            "level_fill_count":  0,
            "sector_etf":        etf,
            "path_kind":         "sector",
            "any_top3":          bool(any_top3),
            "any_new":           bool(any_new),
            "any_owned":         bool(any_owned),
            "any_watchlist":     bool(any_watch),
            "post_alert_always_include": bool(always_include),
        })

    return series, skipped


def post_alert_series_quality_score(s: dict) -> int:
    """Prefer richer rows when collapsing duplicate (ticker, scan_date) paths."""
    lvl = int(s.get("level_fill_count") or 0)
    scan_px = 5 if s.get("has_scan_price") else 0
    rank = int(s.get("rank") or 0)
    rank_pts = 2 if rank > 0 else 0
    setup = len((s.get("setup") or "").strip())
    setup_pts = min(setup, 80)
    return lvl * 10 + scan_px + rank_pts + setup_pts


def collapse_post_alert_series_same_scan(series: list[dict]) -> list[dict]:
    """
    If the same ticker appears more than once for the same scan_date, keep one row:
    pick the highest post_alert_series_quality_score.
    """
    if not series:
        return []
    best: dict[tuple[str, str], dict] = {}
    for s in series:
        tk = (s.get("ticker") or "").strip().upper()
        sd = (s.get("scan_date") or "").strip()
        key = (tk, sd)
        cur = best.get(key)
        if cur is None or post_alert_series_quality_score(s) > post_alert_series_quality_score(cur):
            best[key] = s
    return list(best.values())


def dedupe_post_alert_series_by_ticker(series: list[dict], mode: str) -> list[dict]:
    """
    mode:
      'none'    — keep every (ticker, scan_date) row (caller should collapse same-scan first)
      'latest'  — one row per ticker: keep most recent scan_date
      'earliest'— one row per ticker: keep earliest scan_date
    """
    if mode == "none" or not series:
        return list(series)
    buckets: dict[str, dict] = {}
    for s in series:
        tk = (s.get("ticker") or "").strip().upper()
        if not tk:
            continue
        cur = buckets.get(tk)
        if cur is None:
            buckets[tk] = s
            continue
        sd = s.get("scan_date") or ""
        cd = cur.get("scan_date") or ""
        if mode == "latest":
            if sd > cd or (
                sd == cd and post_alert_series_quality_score(s) > post_alert_series_quality_score(cur)
            ):
                buckets[tk] = s
        elif mode == "earliest":
            if sd < cd or (
                sd == cd and post_alert_series_quality_score(s) > post_alert_series_quality_score(cur)
            ):
                buckets[tk] = s
    return list(buckets.values())


def build_average_post_alert_path(series_list: list[dict]) -> list[list]:
    """
    Average normalized return path in day-offset space.
    At each day d, averages over all series that have at least d+1 bars.
    Day 0 is always 0.0 by construction (anchor = close[0]).
    Returns [[day_int, avg_pct], ...].
    """
    if not series_list:
        return []
    max_day = max(
        (s["day_returns"][-1][0] if s["day_returns"] else 0)
        for s in series_list
    )
    avg_path: list[list] = []
    for day in range(max_day + 1):
        vals = [
            s["day_returns"][day][1]
            for s in series_list
            if day < len(s["day_returns"])
        ]
        if vals:
            avg_path.append([day, round(sum(vals) / len(vals), 4)])
    return avg_path


def _bm_daily_path_from_date(symbol: str, scan_date_obj: date) -> list[list]:
    """
    Normalized daily path for a single benchmark from scan_date_obj.
    Returns [[day_int, pct], ...] where day 0 = 0.0.
    """
    if symbol == "MAG7":
        return _mag7_daily_path_from_date(scan_date_obj)

    hist = _fetch_bm_hist(symbol)
    if hist is None or hist.empty:
        return []
    anchor = _bm_price_on_or_after(hist, scan_date_obj)
    if anchor is None or anchor <= 0:
        return []

    ts0 = _tz_safe_ts(scan_date_obj, hist)
    bars = hist[hist.index >= ts0]
    if bars.empty:
        return []

    path: list[list] = []
    for i, (_, bar_row) in enumerate(bars.iterrows()):
        pct = round((float(bar_row["Close"]) / anchor - 1) * 100, 4)
        path.append([i, pct])
    return path


def _mag7_daily_path_from_date(scan_date_obj: date) -> list[list]:
    """Equal-weight MAG7 normalized path from scan_date_obj."""
    paths: list[list[list]] = []
    for sym in MAG7:
        p = _bm_daily_path_from_date(sym, scan_date_obj)
        if p:
            paths.append(p)
    if not paths:
        return []
    max_day = max(p[-1][0] for p in paths)
    avg: list[list] = []
    for day in range(max_day + 1):
        vals = [p[day][1] for p in paths if day < len(p)]
        if vals:
            avg.append([day, round(sum(vals) / len(vals), 4)])
    return avg


def build_benchmark_avg_path(
    series_list: list[dict],
    bm_symbol: str,
) -> list[list]:
    """
    Average benchmark path aligned to day-offset space.
    For each unique scan_date in series_list, compute the benchmark path
    from that date, then average at each day offset across scan dates.
    Returns [[day_int, avg_pct], ...].
    """
    from datetime import datetime as _dt
    unique_dates: set[str] = {s["scan_date"] for s in series_list}
    per_date_paths: list[list[list]] = []
    for ds in unique_dates:
        try:
            scan_date_obj = _dt.strptime(ds, "%Y-%m-%d").date()
        except Exception:
            continue
        path = _bm_daily_path_from_date(bm_symbol, scan_date_obj)
        if path:
            per_date_paths.append(path)

    if not per_date_paths:
        return []

    max_day = max(p[-1][0] for p in per_date_paths)
    avg: list[list] = []
    for day in range(max_day + 1):
        vals = [p[day][1] for p in per_date_paths if day < len(p)]
        if vals:
            avg.append([day, round(sum(vals) / len(vals), 4)])
    return avg


# ---------------------------------------------------------------------------
# 9. Sector ETF Allocation Check
# ---------------------------------------------------------------------------

def _ret_at_day(path: list[list], max_day: int) -> "Optional[float]":
    """Return the last pct value where day_int <= max_day. None if path empty."""
    result = None
    for d, p in path:
        if d <= max_day:
            result = p
        else:
            break
    return result


def _etf_allocation_decision(
    *,
    primary_ret: "Optional[float]",
    new_ret: "Optional[float]",
    owned_ret: "Optional[float]",
    etf_ret: "Optional[float]",
    spy_ret: "Optional[float]",
    etf_edge_threshold: float = 3.0,
) -> dict:
    """
    Pure decision function — no I/O.

    Rules (applied in order; later rules can override earlier ones):
      1. ETF beats stock basket by > threshold  → prefer sector ETF
      2. Stock basket negative AND ETF positive → strongly prefer sector ETF
      3. Owned underperforms both ETF and SPY   → mark owned as avoid_adding
      4. New alerts beat owned alerts           → mark new as prefer_new
      5. Stock alerts beat ETF                  → allow stock allocation
      6. Both stock AND ETF weak vs SPY         → wait

    Returns a dict with vehicle, owned_signal, new_signal, rules_fired.
    """
    rules_fired: list[str] = []
    vehicle = "stock_alerts"
    owned_signal = "ok"
    new_signal = "ok"

    # Rule 1 — ETF edge
    if primary_ret is not None and etf_ret is not None:
        gap = etf_ret - primary_ret
        if gap > etf_edge_threshold:
            vehicle = "sector_etf"
            rules_fired.append(
                f"ETF leads stock basket by {gap:.1f}% (>{etf_edge_threshold:.0f}% threshold)"
            )

    # Rule 2 — basket negative, ETF positive
    if primary_ret is not None and etf_ret is not None:
        if primary_ret < 0 and etf_ret > 0:
            vehicle = "sector_etf"
            if "ETF leads" not in " ".join(rules_fired):
                rules_fired.append("Stock basket negative while ETF positive — strong ETF preference")

    # Rule 3 — owned lagging
    if owned_ret is not None and etf_ret is not None and spy_ret is not None:
        if owned_ret < etf_ret and owned_ret < spy_ret:
            owned_signal = "avoid_adding"
            rules_fired.append("Owned positions lagging both ETF and SPY — avoid adding to owned")

    # Rule 4 — new > owned
    if new_ret is not None and owned_ret is not None:
        if new_ret > owned_ret:
            new_signal = "prefer_new"
            rules_fired.append(
                f"New alerts ({new_ret:+.1f}%) outperforming owned ({owned_ret:+.1f}%) "
                "— prefer new setups over adding to laggards"
            )

    # Rule 5 — stocks beat ETF (only applies if we haven't already set ETF preference)
    if primary_ret is not None and etf_ret is not None:
        if primary_ret > etf_ret and vehicle not in ("sector_etf",):
            vehicle = "stock_alerts"
            rules_fired.append(
                f"Stock alerts ({primary_ret:+.1f}%) leading ETF ({etf_ret:+.1f}%) "
                "— individual stock allocation allowed"
            )

    # Rule 6 — both weak vs SPY
    if primary_ret is not None and etf_ret is not None and spy_ret is not None:
        if primary_ret < 0 and etf_ret < 0 and primary_ret < spy_ret and etf_ret < spy_ret:
            vehicle = "wait"
            rules_fired.append(
                "Both stock alerts and ETF negative and trailing SPY — wait or broad index only"
            )

    vehicle_labels = {
        "stock_alerts": "Stock Alerts",
        "sector_etf":   "Sector ETF",
        "spy_qqq":      "SPY or QQQ",
        "wait":         "Cash / Wait",
    }
    return {
        "vehicle":       vehicle,
        "vehicle_label": vehicle_labels.get(vehicle, vehicle),
        "owned_signal":  owned_signal,
        "new_signal":    new_signal,
        "rules_fired":   rules_fired,
    }


def _compute_allocation_splits(decision: dict) -> dict:
    """
    Return conservative / balanced / aggressive weight dicts based on vehicle signal.

    Base (neutral):
        conservative: 70% ETF  / 30% stocks
        balanced:     50% ETF  / 50% stocks
        aggressive:   30% ETF  / 70% stocks

    ETF clearly winning → shift +10–20% toward ETF.
    Stocks clearly winning → shift +15–20% toward stocks.
    Both weak → reduce to cash.
    """
    vehicle = decision.get("vehicle", "stock_alerts")

    if vehicle == "wait":
        return {
            "conservative": {"etf": 0,  "stocks": 0,  "cash": 100},
            "balanced":     {"etf": 0,  "stocks": 10, "cash": 90},
            "aggressive":   {"etf": 10, "stocks": 20, "cash": 70},
            "note": "Both markets weak — reduce size and wait for a cleaner tape.",
        }

    if vehicle == "sector_etf":
        return {
            "conservative": {"etf": 80, "stocks": 20, "cash": 0},
            "balanced":     {"etf": 65, "stocks": 35, "cash": 0},
            "aggressive":   {"etf": 50, "stocks": 50, "cash": 0},
            "note": "ETF leading — weight toward sector ETF over individual names.",
        }

    if vehicle == "spy_qqq":
        return {
            "conservative": {"etf": 70, "stocks": 0,  "cash": 30},
            "balanced":     {"etf": 60, "stocks": 20, "cash": 20},
            "aggressive":   {"etf": 50, "stocks": 30, "cash": 20},
            "note": "Broad index preferred — SPY / QQQ over individual sectors.",
        }

    # stock_alerts
    return {
        "conservative": {"etf": 40, "stocks": 60, "cash": 0},
        "balanced":     {"etf": 25, "stocks": 75, "cash": 0},
        "aggressive":   {"etf": 15, "stocks": 85, "cash": 0},
        "note": "Stock alerts leading — stock-heavy allocation supported by data.",
    }


def compute_etf_allocation_check(
    pa_data: dict,
    *,
    day_windows: "tuple[int, int]" = (5, 10),
    etf_edge_threshold: float = 3.0,
) -> dict:
    """
    Compute ETF vs individual-stock allocation decision from post-alert store data.

    Parameters
    ----------
    pa_data          : dict from post-alert-data-store
                       {"series": [...], "sector_series": [...], ...}
    day_windows      : (short_window, primary_window) in trading days
    etf_edge_threshold: % gap at which ETF is considered clearly winning

    Returns a flat dict suitable for rendering in the dashboard:
        primary_etf, day_window, primary_ret, new_ret, owned_ret,
        etf_ret, spy_ret, qqq_ret, mag7_ret,
        diff_stock_vs_etf, n_stocks, n_new, n_owned,
        decision  (vehicle, vehicle_label, owned_signal, new_signal, rules_fired),
        allocation (conservative, balanced, aggressive, note),
        error
    """
    series        = pa_data.get("series") or []
    sector_series = pa_data.get("sector_series") or []

    if not series:
        return {
            "error": "No post-alert series data available.",
            "decision": {"vehicle": "wait", "vehicle_label": "Cash / Wait",
                         "owned_signal": "ok", "new_signal": "ok", "rules_fired": []},
            "allocation": _compute_allocation_splits({"vehicle": "wait"}),
        }

    # Determine the primary sector ETF (most frequently mapped in stock series)
    etf_counts: dict[str, int] = {}
    for s in series:
        etf = (s.get("sector_etf") or "").strip().upper()
        if etf:
            etf_counts[etf] = etf_counts.get(etf, 0) + 1
    primary_etf = max(etf_counts, key=lambda k: etf_counts[k]) if etf_counts else "XLK"

    d_short, d_primary = day_windows

    # Stock series subsets
    new_series   = [s for s in series if s.get("portfolio_status") == "New"]
    owned_series = [s for s in series if s.get("portfolio_status") == "Already owned"]

    # Average paths — stock basket
    primary_avg_path = build_average_post_alert_path(series)
    new_avg_path     = build_average_post_alert_path(new_series)   if new_series   else []
    owned_avg_path   = build_average_post_alert_path(owned_series) if owned_series else []

    # Sector ETF path: use primary_etf rows from sector_series
    etf_rows = [s for s in sector_series if s.get("ticker") == primary_etf]
    etf_avg_path = build_average_post_alert_path(etf_rows) if etf_rows else []

    # Fallback chain: primary_etf → XLK → QQQ
    if not etf_avg_path and primary_etf != "XLK":
        xlk_rows = [s for s in sector_series if s.get("ticker") == "XLK"]
        if xlk_rows:
            etf_avg_path = build_average_post_alert_path(xlk_rows)
            primary_etf  = "XLK"
    if not etf_avg_path:
        # Last resort: compute directly from yfinance benchmark path
        etf_avg_path = build_benchmark_avg_path(series, primary_etf)

    # Benchmark avg paths (aligned to scan dates in the stock series)
    spy_avg_path  = build_benchmark_avg_path(series, "SPY")
    qqq_avg_path  = build_benchmark_avg_path(series, "QQQ")
    mag7_avg_path = build_benchmark_avg_path(series, "MAG7")

    # Extract scalar returns at target windows
    primary_ret  = _ret_at_day(primary_avg_path, d_primary)
    primary_ret5 = _ret_at_day(primary_avg_path, d_short)
    new_ret      = _ret_at_day(new_avg_path,     d_primary)
    owned_ret    = _ret_at_day(owned_avg_path,   d_primary)
    etf_ret      = _ret_at_day(etf_avg_path,     d_primary)
    etf_ret5     = _ret_at_day(etf_avg_path,     d_short)
    spy_ret      = _ret_at_day(spy_avg_path,     d_primary)
    qqq_ret      = _ret_at_day(qqq_avg_path,     d_primary)
    mag7_ret     = _ret_at_day(mag7_avg_path,    d_primary)

    # Decision
    decision   = _etf_allocation_decision(
        primary_ret=primary_ret,
        new_ret=new_ret,
        owned_ret=owned_ret,
        etf_ret=etf_ret,
        spy_ret=spy_ret,
        etf_edge_threshold=etf_edge_threshold,
    )
    allocation = _compute_allocation_splits(decision)

    diff = (
        round(primary_ret - etf_ret, 2)
        if primary_ret is not None and etf_ret is not None
        else None
    )

    return {
        "primary_etf":      primary_etf,
        "day_window":       d_primary,
        "day_window_short": d_short,
        "primary_ret":      primary_ret,
        "primary_ret5":     primary_ret5,
        "new_ret":          new_ret,
        "owned_ret":        owned_ret,
        "etf_ret":          etf_ret,
        "etf_ret5":         etf_ret5,
        "spy_ret":          spy_ret,
        "qqq_ret":          qqq_ret,
        "mag7_ret":         mag7_ret,
        "diff_stock_vs_etf": diff,
        "n_stocks":         len(series),
        "n_new":            len(new_series),
        "n_owned":          len(owned_series),
        "decision":         decision,
        "allocation":       allocation,
        "error":            None,
    }


def debug_etf_allocation_check(
    primary_avg: "Optional[float]",
    etf_avg: "Optional[float]",
    spy_avg: "Optional[float]",
    owned_avg: "Optional[float]" = None,
    new_avg: "Optional[float]" = None,
    etf_edge_threshold: float = 3.0,
) -> dict:
    """
    Deterministic entry point — bypasses all yfinance / data loading.
    Pass scalar return values directly to get a full decision + allocation dict.
    Useful for unit tests and CI.

    Example::
        debug_etf_allocation_check(primary_avg=1.0, etf_avg=5.0, spy_avg=2.0)
        # → vehicle: "sector_etf"
    """
    decision   = _etf_allocation_decision(
        primary_ret=primary_avg,
        new_ret=new_avg,
        owned_ret=owned_avg,
        etf_ret=etf_avg,
        spy_ret=spy_avg,
        etf_edge_threshold=etf_edge_threshold,
    )
    allocation = _compute_allocation_splits(decision)
    diff = (
        round(primary_avg - etf_avg, 2)
        if primary_avg is not None and etf_avg is not None
        else None
    )
    return {
        "primary_ret":       primary_avg,
        "new_ret":           new_avg,
        "owned_ret":         owned_avg,
        "etf_ret":           etf_avg,
        "spy_ret":           spy_avg,
        "diff_stock_vs_etf": diff,
        "decision":          decision,
        "allocation":        allocation,
    }
