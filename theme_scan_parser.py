"""
theme_scan_parser.py — Parse Warren AI theme_scan.md output into structured JSON.

Writes:
  warren_ai_daily/output/YYYY-MM-DD/theme_scores.json          — dated
  warren_ai_daily/output/theme_scores_latest.json              — latest pointer
  warren_ai_daily/output/YYYY-MM-DD/theme_candidate_diagnostics.json  — dated, explicit
  warren_ai_daily/output/theme_candidate_diagnostics.json      — root latest pointer

CLI usage:
  python3 theme_scan_parser.py --day-dir /path/to/YYYY-MM-DD
  python3 theme_scan_parser.py          # auto-detect latest dated folder

The parser is tolerant — it extracts what it can and fills defaults for missing fields.
It never crashes the Dashboard.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Optional

import theme_registry as tr

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_HERE     = os.path.dirname(os.path.abspath(__file__))
_OUT_ROOT = os.path.join(_HERE, "warren_ai_daily", "output")

SCORES_LATEST_PATH = os.path.join(_OUT_ROOT, "theme_scores_latest.json")
DIAG_LATEST_PATH   = os.path.join(_OUT_ROOT, "theme_candidate_diagnostics.json")

# ---------------------------------------------------------------------------
# Rejection-reason normalisation (same as in theme_diagnostic_parser.py)
# ---------------------------------------------------------------------------

_REASON_MAP: list[tuple[str, str]] = [
    ("falling knife",  "falling_knife"),
    ("overvalued",     "overvalued"),
    ("fv upside -",    "overvalued"),
    ("fv upside: -",   "overvalued"),
    ("overbought",     "overbought"),
    ("rsi",            "overbought"),
    ("extended",       "too_extended"),
    ("above 50 ema",   "too_extended"),
    ("above 20 ema",   "too_extended"),
    ("weak setup",     "weak_setup"),
    ("bearish",        "weak_setup"),
    ("no pullback",    "no_pullback"),
    ("chasing",        "too_extended"),
]


def _norm_reason(raw: str) -> str:
    lo = raw.lower()
    for needle, slug in _REASON_MAP:
        if needle in lo:
            return slug
    return "not_reported"


def _top_reason(reasons: list[str]) -> str:
    if not reasons:
        return "not_reported"
    return Counter(reasons).most_common(1)[0][0]


# ---------------------------------------------------------------------------
# Score component weights (must sum to 1.0)
# ---------------------------------------------------------------------------

COMPONENT_WEIGHTS = {
    "market_performance": 0.25,
    "breadth":            0.15,
    "setup_quality":      0.20,
    "news_sentiment":     0.20,
    "regulation_policy":  0.10,
    "valuation_risk":     0.05,
    "catalysts":          0.05,
}

COMPONENT_ALIASES: dict[str, str] = {
    "market performance":  "market_performance",
    "breadth":             "breadth",
    "setup quality":       "setup_quality",
    "news sentiment":      "news_sentiment",
    "news & sentiment":    "news_sentiment",
    "news and sentiment":  "news_sentiment",
    "regulation policy":   "regulation_policy",
    "regulation & policy": "regulation_policy",
    "regulation and policy": "regulation_policy",
    "valuation risk":      "valuation_risk",
    "valuation & risk":    "valuation_risk",
    "valuation and risk":  "valuation_risk",
    "catalysts":           "catalysts",
}

SCORE_LABELS = [
    (70,   "Very strong"),
    (35,   "Improving"),
    (-34,  "Neutral or mixed"),
    (-69,  "Deteriorating"),
    (-100, "Avoid or high risk"),
]


def _score_to_label(score: int) -> str:
    for threshold, label in SCORE_LABELS:
        if score >= threshold:
            return label
    return "Avoid or high risk"


def _components_to_final_score(components: dict[str, int]) -> int:
    """Convert 0–100 component scores to −100..+100 final score."""
    raw = 0.0
    for key, weight in COMPONENT_WEIGHTS.items():
        val = components.get(key, 50)
        raw += val * weight
    # raw is in [0, 100]. Scale to [−100, +100]: (raw − 50) * 2
    return round((raw - 50) * 2)


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------

def _parse_theme_scan_md(text: str, themes_cfg: dict[str, tr.ThemeEntry]) -> dict[str, dict]:
    """
    Parse the structured output from 02b_theme_scan.md.
    Returns {theme_name: parsed_dict}.
    """
    # Split on "## Theme:" markers
    blocks = re.split(r'\n##\s+Theme:\s*', text)
    parsed: dict[str, dict] = {}

    for block in blocks[1:]:
        lines = block.strip().splitlines()
        if not lines:
            continue

        # First line = theme name (strip markdown)
        raw_name = lines[0].strip().rstrip("*_#").strip()

        # Fuzzy match to configured themes
        matched = None
        for cfg_name in themes_cfg:
            if cfg_name.lower() == raw_name.lower() or \
               cfg_name.lower() in raw_name.lower() or \
               raw_name.lower() in cfg_name.lower():
                matched = cfg_name
                break
        if matched is None:
            # Try partial word match
            raw_words = set(raw_name.lower().split())
            for cfg_name in themes_cfg:
                cfg_words = set(cfg_name.lower().split())
                if len(raw_words & cfg_words) >= 1:
                    matched = cfg_name
                    break
        if matched is None:
            continue  # unknown theme block, skip

        entry: dict = {
            "score":           None,
            "label":           None,
            "direction":       "New",
            "previous_score":  None,
            "score_change":    None,
            "components":      {},
            "summary":         "",
            "positive_drivers": [],
            "negative_drivers": [],
            "best_candidates": [],
            "avoid_or_watch":  [],
            "passed_tickers":  [],
            "rejected_tickers": [],
        }

        block_text = "\n".join(lines[1:])

        # ── Scalar fields ────────────────────────────────────────────────
        def _extract(pattern: str, default=None):
            m = re.search(pattern, block_text, re.IGNORECASE | re.MULTILINE)
            return m.group(1).strip() if m else default

        raw_score = _extract(r'\*\*Weekly Score:\*\*\s*([+-]?\d+)')
        if raw_score is not None:
            try:
                entry["score"] = int(raw_score)
            except ValueError:
                pass

        raw_label = _extract(r'\*\*Label:\*\*\s*(.+)')
        if raw_label:
            entry["label"] = raw_label.strip()

        raw_dir = _extract(r'\*\*Direction:\*\*\s*(.+)')
        if raw_dir:
            entry["direction"] = raw_dir.strip()

        raw_prev = _extract(r'\*\*Previous Score:\*\*\s*([+-]?\d+|N/A|n/a)')
        if raw_prev and raw_prev.upper() != "N/A":
            try:
                entry["previous_score"] = int(raw_prev)
            except ValueError:
                pass

        raw_change = _extract(r'\*\*Score Change:\*\*\s*([+-]?\d+|N/A|n/a)')
        if raw_change and raw_change.upper() != "N/A":
            try:
                entry["score_change"] = int(raw_change.replace("+", ""))
            except ValueError:
                pass

        raw_summary = _extract(r'\*\*Summary:\*\*\s*(.+(?:\n.+)*?)(?=\n\*\*|\n###|\Z)')
        if raw_summary:
            entry["summary"] = raw_summary.strip()

        # ── Score components (pipe table) ────────────────────────────────
        comp_section = re.search(
            r'###\s+Score Components(.+?)(?=###|\*\*Summary|\Z)',
            block_text, re.DOTALL | re.IGNORECASE,
        )
        if comp_section:
            for line in comp_section.group(1).splitlines():
                if "|" not in line:
                    continue
                cells = [c.strip() for c in line.split("|") if c.strip()]
                if len(cells) < 2:
                    continue
                raw_comp = cells[0].lower()
                canonical = COMPONENT_ALIASES.get(raw_comp)
                if canonical is None:
                    # fuzzy
                    for alias, key in COMPONENT_ALIASES.items():
                        if alias in raw_comp or raw_comp in alias:
                            canonical = key
                            break
                if canonical is None:
                    continue
                try:
                    val = int(cells[1])
                    entry["components"][canonical] = max(0, min(100, val))
                except ValueError:
                    pass

        # ── If score was not explicitly returned, compute from components ─
        if entry["score"] is None and entry["components"]:
            entry["score"] = _components_to_final_score(entry["components"])
        if entry["score"] is None:
            entry["score"] = 0
        if entry["label"] is None:
            entry["label"] = _score_to_label(entry["score"])

        # ── Driver lists ─────────────────────────────────────────────────
        def _parse_driver_list(section_header: str) -> list[str]:
            m = re.search(
                rf'\*\*{re.escape(section_header)}:\*\*\s*\n((?:\d+\..+\n?)+)',
                block_text, re.IGNORECASE,
            )
            if not m:
                return []
            return [
                re.sub(r'^\d+\.\s*', '', ln.strip())
                for ln in m.group(1).splitlines()
                if ln.strip() and re.match(r'^\d+\.', ln.strip())
            ]

        entry["positive_drivers"] = _parse_driver_list("Positive Drivers")
        entry["negative_drivers"] = _parse_driver_list("Negative Drivers")

        # ── Candidate scan table ─────────────────────────────────────────
        cand_section = re.search(
            r'###\s+Candidate Scan(.+?)(?=\n###|\n\*\*Best|\n\*\*Avoid|\Z)',
            block_text, re.DOTALL | re.IGNORECASE,
        )
        if cand_section:
            for line in cand_section.group(1).splitlines():
                if "|" not in line:
                    continue
                cells = [c.strip() for c in line.split("|") if c.strip()]
                if len(cells) < 4:
                    continue
                ticker_raw = cells[0].upper()
                if not re.match(r'^[A-Z]{1,5}$', ticker_raw):
                    continue
                # header/divider
                if ticker_raw in ("TICKER", "XXXX", "YYYY"):
                    continue
                verdict_raw = cells[3].lower() if len(cells) > 3 else ""
                if "pass" in verdict_raw and "reject" not in verdict_raw:
                    entry["passed_tickers"].append(ticker_raw)
                elif "reject" in verdict_raw:
                    reason_raw = cells[4] if len(cells) > 4 else ""
                    entry["rejected_tickers"].append({
                        "ticker": ticker_raw,
                        "reason": _norm_reason(reason_raw),
                    })

        # ── Best candidates ──────────────────────────────────────────────
        best_m = re.search(r'\*\*Best Candidates:\*\*\s*(.+)', block_text, re.IGNORECASE)
        if best_m:
            raw_best = best_m.group(1).strip()
            if raw_best.lower() not in ("none", "—", "-", ""):
                entry["best_candidates"] = [
                    t.strip().upper()
                    for t in re.split(r'[,;]+', raw_best)
                    if re.match(r'^[A-Z]{1,5}$', t.strip().upper())
                ]

        # ── Avoid or watch ───────────────────────────────────────────────
        avoid_m = re.search(r'\*\*Avoid or Watch:\*\*\s*(.+)', block_text, re.IGNORECASE)
        if avoid_m:
            raw_avoid = avoid_m.group(1).strip()
            if raw_avoid.lower() not in ("none", "—", "-", ""):
                entry["avoid_or_watch"] = [a.strip() for a in re.split(r'[,;]+', raw_avoid) if a.strip()]

        # ── Fallback: if no passed/rejected from table, use best_candidates as passed ──
        if not entry["passed_tickers"] and entry["best_candidates"]:
            entry["passed_tickers"] = list(entry["best_candidates"])

        parsed[matched] = entry

    return parsed


def _parse_summary_table(text: str) -> dict:
    """
    Parse the '## Weekly Theme Score Summary' table at the end of theme_scan.md.
    Returns {top_improving, strong_extended, high_score_no_setup, weak_deteriorating}.
    """
    result = {
        "top_improving":         [],
        "strong_but_extended":   [],
        "high_score_no_setup":   [],
        "weak_or_deteriorating": [],
    }

    patterns = {
        "top_improving":         r'\*\*Top Improving Themes?:\*\*\s*(.+)',
        "strong_but_extended":   r'\*\*Strong but Extended:\*\*\s*(.+)',
        "high_score_no_setup":   r'\*\*High Score,?\s*No (?:Clean )?Setup:\*\*\s*(.+)',
        "weak_or_deteriorating": r'\*\*Weak or (?:Deteriorating|Avoid):\*\*\s*(.+)',
    }

    for key, pattern in patterns.items():
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            raw = m.group(1).strip()
            if raw.lower() not in ("none", "—", "-", ""):
                result[key] = [s.strip() for s in re.split(r'[,;]+', raw) if s.strip()]

    return result


# ---------------------------------------------------------------------------
# Build diagnostics entry from parsed theme data
# ---------------------------------------------------------------------------

def _to_diagnostics_entry(theme_name: str, parsed: dict, themes_cfg: dict) -> dict:
    theme_entry = themes_cfg.get(theme_name, tr.ThemeEntry(theme_name, "", {}))
    considered = len(theme_entry.tickers)
    return {
        "candidates_considered":          considered,
        "candidates_passed":              len(parsed.get("passed_tickers", [])),
        "passed_tickers":                 parsed.get("passed_tickers", []),
        "rejected_tickers":               parsed.get("rejected_tickers", []),
        "top_rejection_reason":           _top_reason(
            [r["reason"] for r in parsed.get("rejected_tickers", [])]
        ),
        "best_candidates":                parsed.get("best_candidates", []),
        "source":                         "explicit_theme_scan",
    }


# ---------------------------------------------------------------------------
# Write outputs
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_theme_scores(day_dir: str, date: str, parsed: dict[str, dict],
                       summary: dict, themes_cfg: dict) -> str:
    """Write theme_scores.json (dated + latest)."""
    # Load prior scores for comparison
    prior: dict[str, dict] = {}
    if os.path.isfile(SCORES_LATEST_PATH):
        try:
            with open(SCORES_LATEST_PATH) as fh:
                raw = json.load(fh)
            prior = {k: v for k, v in (raw.get("themes") or {}).items()}
        except Exception:
            pass

    themes_out: dict[str, dict] = {}
    for theme_name in themes_cfg:
        p = parsed.get(theme_name, {})
        score = p.get("score", 0)

        prior_entry = prior.get(theme_name, {})
        prior_score = prior_entry.get("score", None)
        score_change = (score - prior_score) if prior_score is not None else None

        if prior_score is not None and score_change is not None:
            direction = (
                "Improving"    if score_change >= 5  else
                "Deteriorating" if score_change <= -5 else
                "Stable"
            )
        else:
            direction = p.get("direction", "New")

        themes_out[theme_name] = {
            "score":            score,
            "label":            p.get("label") or _score_to_label(score),
            "direction":        direction,
            "previous_score":   prior_score,
            "score_change":     score_change,
            "components":       p.get("components", {}),
            "summary":          p.get("summary", ""),
            "positive_drivers": p.get("positive_drivers", []),
            "negative_drivers": p.get("negative_drivers", []),
            "best_candidates":  p.get("best_candidates", []),
            "avoid_or_watch":   p.get("avoid_or_watch", []),
        }

    output = {
        "as_of":        date,
        "generated_at": _now_iso(),
        "source":       "warren_ai_theme_scan",
        "summary":      summary,
        "themes":       themes_out,
    }

    # Dated path
    dated_path = os.path.join(day_dir, "theme_scores.json")
    os.makedirs(day_dir, exist_ok=True)
    with open(dated_path, "w") as fh:
        json.dump(output, fh, indent=2)

    # Latest pointer
    with open(SCORES_LATEST_PATH, "w") as fh:
        json.dump(output, fh, indent=2)

    return dated_path


def write_theme_diagnostics(day_dir: str, date: str, parsed: dict[str, dict],
                             themes_cfg: dict) -> str:
    """Write theme_candidate_diagnostics.json (dated + root latest)."""
    diag_data: dict = {}
    for theme_name in themes_cfg:
        p = parsed.get(theme_name, {})
        diag_data[theme_name] = _to_diagnostics_entry(theme_name, p, themes_cfg)

    output = {
        "_meta": {
            "generated_at": _now_iso(),
            "scan_date":    date,
            "mode":         "explicit_theme_scan" if parsed else "inferred",
        },
        **diag_data,
    }

    # Dated path
    dated_path = os.path.join(day_dir, "theme_candidate_diagnostics.json")
    with open(dated_path, "w") as fh:
        json.dump(output, fh, indent=2)

    # Root latest pointer
    with open(DIAG_LATEST_PATH, "w") as fh:
        json.dump(output, fh, indent=2)

    return dated_path


# ---------------------------------------------------------------------------
# Auto-detect latest day dir
# ---------------------------------------------------------------------------

def _find_latest_day_dir() -> str | None:
    if not os.path.isdir(_OUT_ROOT):
        return None
    candidates = sorted(
        [d for d in os.listdir(_OUT_ROOT) if re.match(r'^\d{4}-\d{2}-\d{2}$', d)],
        reverse=True,
    )
    for c in candidates[:7]:
        full = os.path.join(_OUT_ROOT, c)
        if os.path.isfile(os.path.join(full, "theme_scan.md")):
            return full
    # Fall back to any dated folder with step files
    for c in candidates[:7]:
        full = os.path.join(_OUT_ROOT, c)
        if any(os.path.isfile(os.path.join(full, f"step_0{n}.md")) for n in [1, 2, 3, 4, 5]):
            return full
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_theme_scan(day_dir: str | None = None, force: bool = False) -> dict:
    """
    Parse theme_scan.md from day_dir (or auto-detect) and write JSON outputs.
    Returns summary dict.
    """
    themes_cfg = tr.load_themes()
    summary = {
        "day_dir":               day_dir or "(auto-detect)",
        "mode":                  "empty",
        "date":                  "—",
        "themes_parsed":         0,
        "themes_with_scores":    0,
        "themes_with_passed":    0,
        "total_passed":          0,
        "scores_path":           None,
        "diagnostics_path":      None,
        "error":                 None,
    }

    if not themes_cfg:
        summary["error"] = "No themes configured in themes.yaml"
        return summary

    target_dir = day_dir or _find_latest_day_dir()
    if not target_dir:
        summary["error"] = "No dated output folder found"
        return summary

    date = os.path.basename(target_dir)
    summary["day_dir"] = target_dir
    summary["date"]    = date

    # Read theme_scan.md
    scan_path = os.path.join(target_dir, "theme_scan.md")
    if not os.path.isfile(scan_path):
        summary["error"] = f"theme_scan.md not found in {target_dir}"
        return summary

    with open(scan_path, "r", encoding="utf-8") as fh:
        scan_text = fh.read()

    # Parse
    parsed = _parse_theme_scan_md(scan_text, themes_cfg)
    scan_summary = _parse_summary_table(scan_text)

    summary["themes_parsed"]      = len(parsed)
    summary["themes_with_scores"] = sum(1 for p in parsed.values() if p.get("score") is not None)
    summary["themes_with_passed"] = sum(1 for p in parsed.values() if p.get("passed_tickers"))
    summary["total_passed"]       = sum(len(p.get("passed_tickers", [])) for p in parsed.values())
    summary["mode"]               = "explicit_theme_scan" if parsed else "empty"

    # Write outputs
    scores_path = write_theme_scores(target_dir, date, parsed, scan_summary, themes_cfg)
    diag_path   = write_theme_diagnostics(target_dir, date, parsed, themes_cfg)

    summary["scores_path"]      = scores_path
    summary["diagnostics_path"] = diag_path

    print(
        f"[theme_scan_parser] ✓ Parsed {len(parsed)} themes from {scan_path}\n"
        f"  Scores → {scores_path}\n"
        f"  Diagnostics → {diag_path}\n"
        f"  themes_with_passed={summary['themes_with_passed']}"
        f"  total_passed={summary['total_passed']}"
    )

    # Invalidate theme_registry cache so dashboard reloads fresh data
    tr.invalidate_cache()

    return summary


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Parse Warren AI theme_scan.md → JSON outputs")
    ap.add_argument("--day-dir",  default=None, help="Path to dated output folder (auto-detect if omitted)")
    ap.add_argument("--force",    action="store_true", help="Force re-parse even if outputs exist")
    args = ap.parse_args()

    result = parse_theme_scan(day_dir=args.day_dir, force=args.force)
    print(json.dumps(result, indent=2))
