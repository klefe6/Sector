#!/usr/bin/env python3
"""
nitter_followup.py — Turn Nitter research findings into a small number of
Warren AI "second-pass" research prompts.

This module is the brain of the Nitter-informed Warren follow-up:

    load_latest_nitter(...)   find + read the newest YYYY-MM-DD_nitter.txt
    extract_leads(text)       parse free-text findings into normalized leads
    rank_leads(leads, ...)    score, dedupe, filter, and keep the top N
    build_prompt(lead)        render the Warren research prompt for one lead

It is deliberately conservative: Nitter findings are treated as *unverified
leads*, never as truth. The generated Warren prompts always ask Warren to
verify independently.

The Nitter output in this environment is FREE TEXT (not JSON). Each finding
looks like:

    According to Kobeissi Letter, Nvidia, $NVDA, and the Semiconductor Index,
    $SOX, have moved in opposite directions ...

So parsing is heuristic and flexible. See `extract_leads` for the shape.

CLI (used by the TypeScript runner; also handy for debugging):

    python3 nitter_followup.py --emit --out <dir> \\
        --max 3 --min-confidence 0.55 --max-age-hours 36 \\
        [--source-dir DIR] [--date YYYY-MM-DD] [--force]

Prints a JSON status object to stdout. With --emit also writes
leads.json and prompts.json into --out.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# Config / constants
# ---------------------------------------------------------------------------

DEFAULT_SOURCE_DIR = "/home/klefe6/agentic/control_center/backend/data/morning_reports"
NY_TZ = ZoneInfo("America/New_York")

# Newest file matching this pattern is the daily Nitter research output.
_NITTER_FILE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})_nitter\.txt$")

# Cashtag: $AAPL, $NVDA, $SOX (1–5 letters). The most reliable ticker signal.
_CASHTAG_RE = re.compile(r"\$([A-Za-z]{1,5})\b")

# Per-source attribution prefix produced by the Nitter summarizer.
_SOURCE_LINE_RE = re.compile(r"^According to ([^,]+),\s*(.*)$", re.IGNORECASE)

# Tokens that look like tickers but are noise — never treat as a ticker lead.
_TICKER_STOPWORDS: frozenset[str] = frozenset({
    "THE", "AND", "FOR", "NOT", "ALL", "USD", "CEO", "CFO", "GDP", "CPI",
    "PMI", "ETF", "ETFS", "AUM", "PPI", "FED", "ECB", "BOE", "BOJ", "IMF",
    "USA", "US", "UK", "EU", "UN", "AI", "WTI", "OPEC", "PCE", "FOMC", "ISM",
    "Q1", "Q2", "Q3", "Q4", "YOY", "MOM", "VIX", "SEC", "DOJ", "FBI", "IRS",
    "NATO", "OPEC", "ATH", "IPO", "FDA", "SPX", "ES", "NQ", "DXY",
})

# Theme keyword map: keyword -> (theme label, optional proxy ticker/etf).
# Order matters only for stable labeling; all matches contribute to mentions.
_THEME_KEYWORDS: list[tuple[tuple[str, ...], str, str]] = [
    (("oil", "crude", "wti", "brent", "opec", "barrel", "refinery", "hormuz"),
     "Energy / oil", "XLE"),
    (("gold", "silver", "bullion", "precious metal"),
     "Precious metals", "GLD"),
    (("fed", "fomc", "rate", "rates", "yield", "inflation", "cpi", "ppi",
      "pce", "treasury", "bond", "dxy", "dollar", "disinflation"),
     "Rates & inflation", "TLT"),
    (("iran", "israel", "ukraine", "russia", "taiwan", "war", "missile",
      "strike", "sanction", "nuclear", "geopolit", "conflict"),
     "Geopolitics / defense", "ITA"),
    (("ai ", "a.i.", "nvidia", "chip", "semiconductor", "semis", "gpu",
      "datacenter", "data center", "openai", "compute"),
     "AI / semiconductors", "SMH"),
    (("bitcoin", "btc", "ethereum", "ether", "crypto", "stablecoin"),
     "Crypto", "IBIT"),
    (("recession", "layoff", "jobless", "unemployment", "default",
      "credit risk", "money market", "outflow", "liquidity"),
     "Macro risk", "SPY"),
    (("china", "hong kong", "yuan", "pboc"),
     "China", "FXI"),
]

_BEARISH_TERMS: tuple[str, ...] = (
    "recession", "crash", "selloff", "sell-off", "plunge", "halt", "sidecar",
    "sanction", "war", "strike", "attack", "miss", "cut", "default", "fear",
    "risk-off", "bearish", "drop", "fell", "tumble", "outflow", "downgrade",
    "slump", "warning", "slowdown", "contraction", "layoff",
)

_BULLISH_TERMS: tuple[str, ...] = (
    "rally", "surge", "breakout", "beat", "upgrade", "soar", "jump", "gain",
    "bullish", "record high", "all-time high", "all time high", "risk-on",
    "rebound", "inflow", "record", "surpass", "outperform",
)

_CATALYST_TERMS: tuple[str, ...] = (
    "earnings", "guidance", "fda", "approval", "merger", "acquisition", "ipo",
    "filing", "lawsuit", "downgrade", "upgrade", "cpi", "fomc", "jobs report",
    "results", "launch", "announcement", "report", "deal", "buyback",
)

# Words suggesting low-quality / pump-y noise around a small ticker.
_NOISE_TERMS: tuple[str, ...] = (
    "moon", "pump", "squeeze", "to the moon", "yolo", "meme", "100x", "1000x",
)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Lead:
    """A single normalized research lead derived from Nitter findings."""
    ticker: str = ""              # "" when this is a pure theme lead
    theme: str = ""
    direction_bias: str = "unknown"   # bullish | bearish | mixed | unknown
    confidence: float = 0.0           # 0..1
    why_from_nitter: list[str] = field(default_factory=list)
    supporting_terms: list[str] = field(default_factory=list)
    risk_notes: list[str] = field(default_factory=list)
    mentions: int = 1                 # how many findings referenced this lead

    def key(self) -> str:
        """Dedup key: ticker if present, else theme."""
        return self.ticker.upper() if self.ticker else f"theme::{self.theme.lower()}"

    def label(self) -> str:
        if self.ticker and self.theme:
            return f"{self.ticker} ({self.theme})"
        return self.ticker or self.theme or "unknown"


# ---------------------------------------------------------------------------
# Source loading
# ---------------------------------------------------------------------------

def find_latest_nitter_file(source_dir: str) -> Optional[str]:
    """Return the newest YYYY-MM-DD_nitter.txt by modified time, or None."""
    if not source_dir or not os.path.isdir(source_dir):
        return None
    best_path: Optional[str] = None
    best_mtime = -1.0
    for name in os.listdir(source_dir):
        m = _NITTER_FILE_RE.search(name)
        if not m:
            continue
        path = os.path.join(source_dir, name)
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        if mtime > best_mtime:
            best_mtime = mtime
            best_path = path
    return best_path


def _file_date(path: str) -> str:
    m = _NITTER_FILE_RE.search(os.path.basename(path))
    return m.group(1) if m else ""


def _source_metadata(path: str, now: Optional[datetime] = None) -> dict:
    now_dt = now or datetime.now(NY_TZ)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {"source_file_mtime": "", "source_file_age_hours": None}
    mtime_dt = datetime.fromtimestamp(mtime, tz=NY_TZ)
    age_hours = max(0.0, (now_dt.timestamp() - mtime_dt.timestamp()) / 3600)
    return {
        "source_file_mtime": mtime_dt.isoformat(timespec="seconds"),
        "source_file_age_hours": round(age_hours, 2),
    }


def compute_freshness(path: str, max_age_hours: float) -> str:
    """
    Classify the source file as 'fresh' or 'stale' based on mtime age.
    'missing' and 'error' are returned by the caller, not here.
    """
    try:
        age_seconds = datetime.now(NY_TZ).timestamp() - os.path.getmtime(path)
    except OSError:
        return "error"
    return "fresh" if age_seconds <= max_age_hours * 3600 else "stale"


def assess_source_freshness(
    *,
    file_date: str,
    run_date: str,
    age_hours: float | None,
    max_age_hours: float,
    source_selection_reason: str,
    existing_warning: str = "",
) -> dict[str, object]:
    """
    Stricter source-date guard for Warren/Nitter follow-up.

    freshness_status:
      same_day | age_fresh_but_previous_day | stale | missing | error

    Warning-only for previous-day files that are still within max_age_hours.
    """
    warnings: list[str] = [w.strip() for w in [existing_warning] if w and w.strip()]

    if age_hours is None:
        return {
            "freshness_status": "error",
            "date_matches_run": False,
            "warning": "; ".join(warnings),
        }

    if age_hours > max_age_hours:
        warnings.append(
            f"Nitter source older than {max_age_hours}h (file {file_date or '?'})."
        )
        return {
            "freshness_status": "stale",
            "date_matches_run": bool(run_date and file_date and file_date == run_date),
            "warning": "; ".join(warnings),
        }

    if not run_date or not file_date:
        return {
            "freshness_status": "same_day",
            "date_matches_run": True,
            "warning": "; ".join(warnings),
        }

    date_matches = file_date == run_date
    if not date_matches:
        warnings.append(
            f"Nitter source date {file_date} does not match run date {run_date} "
            f"(selected via {source_selection_reason or 'unknown'})."
        )
        return {
            "freshness_status": "age_fresh_but_previous_day",
            "date_matches_run": False,
            "warning": "; ".join(warnings),
        }

    return {
        "freshness_status": "same_day",
        "date_matches_run": True,
        "warning": "; ".join(warnings),
    }


def load_latest_nitter(
    source_dir: str,
    max_age_hours: float = 36.0,
    expected_date: str = "",
) -> dict:
    """
    Load the newest Nitter research file.

    Returns a dict:
        {
          "freshness": "fresh|stale|missing|error",
          "freshness_status": "same_day|age_fresh_but_previous_day|stale|missing|error",
          "source_file": "<path or ''>",
          "file_date": "YYYY-MM-DD",
          "source_file_date": "YYYY-MM-DD",
          "run_date": "YYYY-MM-DD",
          "age_hours": number|null,
          "source_file_mtime": "ISO timestamp in America/New_York",
          "source_file_age_hours": number|null,
          "date_matches_run": bool,
          "expected_source_file": "<path>",
          "source_selection_reason": "expected-date-file|fallback-latest-mtime|missing|error",
          "source_date_warning": "",
          "warning": "",
          "text": "<raw text or ''>",
        }
    Never raises.
    """
    expected_file = os.path.join(source_dir, f"{expected_date}_nitter.txt") if expected_date else ""
    out: dict[str, object] = {
        "freshness": "missing",
        "freshness_status": "missing",
        "source_file": "",
        "file_date": "",
        "source_file_date": "",
        "run_date": expected_date,
        "age_hours": None,
        "source_file_mtime": "",
        "source_file_age_hours": None,
        "date_matches_run": False,
        "expected_source_file": expected_file,
        "source_selection_reason": "missing",
        "source_date_warning": "",
        "warning": "No Nitter research file found.",
        "text": "",
    }
    try:
        path = expected_file if expected_file and os.path.isfile(expected_file) else None
        if path:
            out["source_selection_reason"] = "expected-date-file"
        else:
            path = find_latest_nitter_file(source_dir)
            if path:
                out["source_selection_reason"] = "fallback-latest-mtime"
                if expected_file:
                    out["source_date_warning"] = (
                        f"Expected {os.path.basename(expected_file)} was missing; using latest available file "
                        f"{os.path.basename(path)}."
                    )
        if not path:
            return out
        out["source_file"] = path
        out["file_date"] = _file_date(path)
        out["source_file_date"] = out["file_date"]
        out.update(_source_metadata(path))
        out["age_hours"] = out["source_file_age_hours"]
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            out["text"] = fh.read()
        if not str(out["text"]).strip():
            out["freshness"] = "error"
            out["freshness_status"] = "error"
            out["warning"] = "Nitter research file unreadable or empty."
            out["source_date_warning"] = str(out["warning"])
            return out
        out["freshness"] = compute_freshness(path, max_age_hours)
        assessed = assess_source_freshness(
            file_date=str(out["file_date"]),
            run_date=expected_date,
            age_hours=out["age_hours"] if isinstance(out["age_hours"], (int, float)) else None,
            max_age_hours=max_age_hours,
            source_selection_reason=str(out["source_selection_reason"]),
            existing_warning=str(out["source_date_warning"]),
        )
        out["freshness_status"] = assessed["freshness_status"]
        out["date_matches_run"] = assessed["date_matches_run"]
        out["warning"] = assessed["warning"]
        if out["warning"]:
            out["source_date_warning"] = out["warning"]
        if out["freshness_status"] == "stale":
            out["freshness"] = "stale"
        return out
    except Exception:  # noqa: BLE001 — never let loading break the caller
        out["freshness"] = "error"
        out["freshness_status"] = "error"
        out["source_selection_reason"] = "error"
        out["warning"] = "Error loading Nitter research file."
        out["source_date_warning"] = str(out["warning"])
        return out


# ---------------------------------------------------------------------------
# Parsing free-text findings into normalized leads
# ---------------------------------------------------------------------------

def _iter_findings(text: str) -> list[tuple[str, str]]:
    """
    Split the recap into (source, body) findings.

    Lines beginning "According to X, ..." start a new finding; following
    indented/continuation lines are appended to the current finding body.
    """
    findings: list[tuple[str, str]] = []
    cur_source = ""
    cur_body: list[str] = []

    def flush() -> None:
        if cur_body:
            body = " ".join(p.strip() for p in cur_body).strip()
            if body:
                findings.append((cur_source, body))

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.lower().startswith("overnight market recap"):
            continue
        if line.lower().startswith("tweet source status"):
            continue
        m = _SOURCE_LINE_RE.match(line)
        if m:
            flush()
            cur_source = m.group(1).strip()
            cur_body = [m.group(2).strip()]
        else:
            if cur_body:
                cur_body.append(line)
    flush()
    return findings


def _detect_bias(text_lower: str) -> str:
    bull = sum(1 for t in _BULLISH_TERMS if t in text_lower)
    bear = sum(1 for t in _BEARISH_TERMS if t in text_lower)
    if bull and bear:
        return "mixed"
    if bull:
        return "bullish"
    if bear:
        return "bearish"
    return "unknown"


def _matched_terms(text_lower: str, terms: tuple[str, ...]) -> list[str]:
    return [t.strip() for t in terms if t in text_lower]


def _themes_for(text_lower: str) -> list[tuple[str, str, list[str]]]:
    """Return list of (theme_label, proxy, matched_keywords) for a finding."""
    hits: list[tuple[str, str, list[str]]] = []
    for keywords, label, proxy in _THEME_KEYWORDS:
        matched = [k.strip() for k in keywords if k in text_lower]
        if matched:
            hits.append((label, proxy, matched))
    return hits


def extract_leads(text: str, universe: Optional[set[str]] = None) -> list[Lead]:
    """
    Parse the free-text Nitter recap into normalized leads.

    Produces two kinds of leads:
      * ticker leads   — anchored on a $CASHTAG seen in the findings
      * theme leads    — anchored on a recognized macro/sector narrative

    `universe` is an optional set of tickers already in the user's research
    universe; used only to down-rank unknown micro-tickers later.
    """
    universe = universe or set()
    findings = _iter_findings(text)

    # ticker_key -> aggregation
    tickers: dict[str, Lead] = {}
    themes: dict[str, Lead] = {}

    for source, body in findings:
        lower = body.lower()
        bias = _detect_bias(lower)
        catalysts = _matched_terms(lower, _CATALYST_TERMS)
        short = body if len(body) <= 180 else body[:177].rstrip() + "..."
        attribution = f"{source}: {short}" if source else short

        # ── ticker leads (cashtags) ──────────────────────────────────────
        seen_here: set[str] = set()
        for raw in _CASHTAG_RE.findall(body):
            tk = raw.upper()
            if tk in _TICKER_STOPWORDS or len(tk) < 1:
                continue
            if tk in seen_here:
                continue
            seen_here.add(tk)
            lead = tickers.get(tk)
            if lead is None:
                lead = Lead(ticker=tk, theme="", direction_bias=bias)
                tickers[tk] = lead
            else:
                lead.mentions += 1
                lead.direction_bias = _merge_bias(lead.direction_bias, bias)
            if attribution not in lead.why_from_nitter:
                lead.why_from_nitter.append(attribution)
            for c in catalysts:
                if c not in lead.supporting_terms:
                    lead.supporting_terms.append(c)
            # attach a theme to the ticker when one is obvious in the finding
            theme_hits = _themes_for(lower)
            if theme_hits and not lead.theme:
                lead.theme = theme_hits[0][0]

        # ── theme leads ──────────────────────────────────────────────────
        for label, proxy, matched in _themes_for(lower):
            tlead = themes.get(label)
            if tlead is None:
                tlead = Lead(ticker="", theme=label, direction_bias=bias)
                themes[label] = tlead
            else:
                tlead.mentions += 1
                tlead.direction_bias = _merge_bias(tlead.direction_bias, bias)
            for kw in matched:
                if kw not in tlead.supporting_terms:
                    tlead.supporting_terms.append(kw)
            for c in catalysts:
                if c not in tlead.supporting_terms:
                    tlead.supporting_terms.append(c)
            if len(tlead.why_from_nitter) < 4 and attribution not in tlead.why_from_nitter:
                tlead.why_from_nitter.append(attribution)
            # record proxy as a hint in risk notes context (not a ticker lead)
            if proxy and proxy not in tlead.supporting_terms:
                tlead.supporting_terms.append(proxy)

    leads = list(tickers.values()) + list(themes.values())

    for lead in leads:
        _finalize_lead(lead, universe)

    return leads


def _merge_bias(a: str, b: str) -> str:
    if a == b:
        return a
    if a == "unknown":
        return b
    if b == "unknown":
        return a
    return "mixed"


def _finalize_lead(lead: Lead, universe: set[str]) -> None:
    """Compute confidence + risk notes for a lead (deterministic)."""
    conf = 0.30
    if lead.ticker:
        conf += 0.15  # explicit cashtag is a stronger signal than a theme
    conf += min(0.30, 0.10 * max(0, lead.mentions - 1))
    if any(c in _CATALYST_TERMS for c in lead.supporting_terms):
        conf += 0.10
    if lead.theme:
        conf += 0.08
    if lead.ticker and lead.ticker in universe:
        conf += 0.05

    text_blob = " ".join(lead.why_from_nitter).lower()
    is_noise = any(n in text_blob for n in _NOISE_TERMS)
    unknown_micro = bool(lead.ticker) and lead.ticker not in universe and lead.mentions <= 1
    if is_noise:
        conf -= 0.15
    if unknown_micro and is_noise:
        conf -= 0.10

    lead.confidence = round(max(0.0, min(1.0, conf)), 2)

    # Standard, always-present risk framing — these are unverified leads.
    notes = ["Unverified social/Nitter signal — treat as a lead, not a fact."]
    if is_noise:
        notes.append("Contains hype/pump language — high noise risk.")
    if unknown_micro:
        notes.append("Single mention of a ticker outside the research universe.")
    if lead.direction_bias == "unknown":
        notes.append("Direction unclear from social text.")
    lead.risk_notes = notes


# ---------------------------------------------------------------------------
# Ranking + dedupe + selection
# ---------------------------------------------------------------------------

def _score(lead: Lead) -> float:
    score = lead.confidence
    score += 0.05 * max(0, lead.mentions - 1)
    if lead.ticker:
        score += 0.05
    if any(c in _CATALYST_TERMS for c in lead.supporting_terms):
        score += 0.03
    return score


def rank_leads(
    leads: list[Lead],
    max_prompts: int = 3,
    min_confidence: float = 0.55,
) -> list[Lead]:
    """
    Deduplicate by ticker/theme, drop leads below min_confidence, and return
    the top `max_prompts` by score (highest first). Stable and deterministic.
    """
    # Deduplicate (merge) by key, keeping the strongest aggregate.
    merged: dict[str, Lead] = {}
    for lead in leads:
        k = lead.key()
        existing = merged.get(k)
        if existing is None:
            merged[k] = lead
            continue
        existing.mentions = max(existing.mentions, lead.mentions)
        existing.confidence = max(existing.confidence, lead.confidence)
        for w in lead.why_from_nitter:
            if w not in existing.why_from_nitter:
                existing.why_from_nitter.append(w)
        for t in lead.supporting_terms:
            if t not in existing.supporting_terms:
                existing.supporting_terms.append(t)
        existing.direction_bias = _merge_bias(existing.direction_bias, lead.direction_bias)

    qualified = [ld for ld in merged.values() if ld.confidence >= min_confidence]
    qualified.sort(key=lambda ld: (_score(ld), ld.mentions, ld.label()), reverse=True)
    return qualified[: max(0, max_prompts)]


# ---------------------------------------------------------------------------
# Prompt generation
# ---------------------------------------------------------------------------

def build_prompt(lead: Lead) -> str:
    """Render the Warren AI research prompt for a single lead."""
    summary = " | ".join(lead.why_from_nitter[:3]) or "(no summary text)"
    risk = "; ".join(lead.risk_notes) or "Unverified social signal."
    no_ticker_instruction = ""
    if not lead.ticker:
        no_ticker_instruction = """

This lead does not contain a single ticker. First translate the theme into 3-5 liquid US-listed stocks or ETFs that could benefit or suffer over the next 1-5 weeks. Then rank them by:
- technical setup
- sector rotation
- catalyst confirmation
- liquidity
- risk/reward
- whether the trade is actionable now or only watchlist-worthy

Keep the final answer ticker-focused."""
    return f"""Nitter-informed research lead. Treat this as an unverified market lead, not as truth.

Lead:
Ticker/theme: {lead.label()}
Social/Nitter signal: {summary}
Direction bias: {lead.direction_bias}
Confidence from Nitter ranking: {lead.confidence:.2f}
Risk notes: {risk}
{no_ticker_instruction}

Please verify independently using your available market, technical, fundamental, news, sector rotation, and sentiment context.

I am looking for high-risk/high-reward 1-5 week trades on moomoo.

Answer in this structure:
1. Is this lead actually worth researching further? Yes/No/Maybe.
2. Bull case.
3. Bear case.
4. Current technical setup.
5. Sector/theme context.
6. Catalyst or narrative confirmation.
7. Entry zone, invalidation, upside target, downside risk.
8. Score from 0-100 for a 1-5 week swing idea.
9. Final action: Watch / Avoid / Starter position / Strong candidate.
10. What would change your mind?"""


# ---------------------------------------------------------------------------
# Universe loading (best-effort)
# ---------------------------------------------------------------------------

def load_universe(project_root: str) -> set[str]:
    """Load tickers already in the research universe from current_portfolio.json."""
    universe: set[str] = set()
    pf = os.path.join(project_root, "config", "current_portfolio.json")
    try:
        with open(pf, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        for p in data.get("positions", []):
            tk = str(p.get("ticker", "")).strip().upper()
            if tk:
                universe.add(tk)
    except Exception:  # noqa: BLE001
        pass
    return universe


# ---------------------------------------------------------------------------
# Orchestration helper (used by CLI and importable by tests)
# ---------------------------------------------------------------------------

def generate(
    source_dir: str,
    max_prompts: int = 3,
    min_confidence: float = 0.55,
    max_age_hours: float = 36.0,
    expected_date: str = "",
    universe: Optional[set[str]] = None,
) -> dict:
    """
    Full pipeline: load → extract → rank → build prompts.

    Returns a JSON-serializable status dict. Never raises.
    """
    loaded = load_latest_nitter(source_dir, max_age_hours=max_age_hours, expected_date=expected_date)
    freshness = loaded["freshness"]

    base = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "nitter",
        "source_file": loaded["source_file"],
        "file_date": loaded["file_date"],
        "source_file_date": loaded["source_file_date"],
        "run_date": loaded.get("run_date", expected_date),
        "age_hours": loaded.get("age_hours"),
        "source_file_mtime": loaded["source_file_mtime"],
        "source_file_age_hours": loaded["source_file_age_hours"],
        "date_matches_run": loaded.get("date_matches_run", False),
        "expected_source_file": loaded["expected_source_file"],
        "source_selection_reason": loaded["source_selection_reason"],
        "source_date_warning": loaded["source_date_warning"],
        "warning": loaded.get("warning", loaded["source_date_warning"]),
        "freshness": freshness,
        "freshness_status": loaded.get("freshness_status", freshness),
        "leads": [],
        "prompts": [],
    }

    if freshness in ("missing", "error"):
        base["status"] = f"skipped-{freshness}"
        base["skip_reason"] = str(base["warning"] or (
            "No Nitter research file found." if freshness == "missing"
            else "Nitter research file unreadable or empty."
        ))
        return base

    if freshness == "stale" or base["freshness_status"] == "stale":
        base["status"] = "skipped-stale"
        base["skip_reason"] = str(base["warning"] or (
            f"Nitter source older than {max_age_hours}h (file {loaded['file_date']})."
        ))
        return base

    all_leads = extract_leads(loaded["text"], universe=universe)
    ranked = rank_leads(all_leads, max_prompts=max_prompts, min_confidence=min_confidence)

    base["leads"] = [asdict(ld) for ld in ranked]
    base["prompts"] = [
        {"lead": asdict(ld), "prompt": build_prompt(ld)} for ld in ranked
    ]
    base["candidates_considered"] = len(all_leads)

    if not ranked:
        base["status"] = "skipped-no-leads"
        base["skip_reason"] = (
            f"No leads met confidence >= {min_confidence} "
            f"({len(all_leads)} candidate(s) considered)."
        )
    else:
        base["status"] = "ok"
    return base


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Generate Nitter-informed Warren prompts")
    parser.add_argument("--source-dir", default=os.environ.get(
        "WARREN_NITTER_SOURCE_DIR", DEFAULT_SOURCE_DIR))
    parser.add_argument("--max", type=int, default=int(os.environ.get(
        "WARREN_NITTER_FOLLOWUP_MAX_PROMPTS", "3")))
    parser.add_argument("--min-confidence", type=float, default=float(os.environ.get(
        "WARREN_NITTER_FOLLOWUP_MIN_CONFIDENCE", "0.55")))
    parser.add_argument("--max-age-hours", type=float, default=float(os.environ.get(
        "WARREN_NITTER_FOLLOWUP_MAX_AGE_HOURS", "36")))
    parser.add_argument("--out", default="", help="Directory to write leads.json/prompts.json")
    parser.add_argument("--emit", action="store_true", help="Write leads.json + prompts.json")
    parser.add_argument("--date", default="", help="Run date label (informational)")
    parser.add_argument("--force", action="store_true", help="(informational; handled by runner)")
    args = parser.parse_args(argv)

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    universe = load_universe(project_root)

    result = generate(
        source_dir=args.source_dir,
        max_prompts=args.max,
        min_confidence=args.min_confidence,
        max_age_hours=args.max_age_hours,
        expected_date=args.date,
        universe=universe,
    )
    if args.date:
        result["date"] = args.date

    if args.emit and args.out:
        try:
            os.makedirs(args.out, exist_ok=True)
            with open(os.path.join(args.out, "leads.json"), "w", encoding="utf-8") as fh:
                json.dump({k: result[k] for k in (
                    "status", "freshness", "freshness_status", "source_file", "file_date",
                    "source_file_date", "run_date", "age_hours", "date_matches_run",
                    "source_file_mtime", "source_file_age_hours",
                    "expected_source_file", "source_selection_reason", "source_date_warning",
                    "warning", "generated_at", "leads",
                ) if k in result}, fh, indent=2)
            with open(os.path.join(args.out, "prompts.json"), "w", encoding="utf-8") as fh:
                json.dump({"status": result["status"], "prompts": result["prompts"]},
                          fh, indent=2)
        except Exception as exc:  # noqa: BLE001
            result["emit_error"] = str(exc)

    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
