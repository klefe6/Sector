"""
theme_diagnostic_parser.py — Build theme_candidate_diagnostics.json from Warren AI output.

Two modes (tried in order, first that yields data wins):

1. EXPLICIT  — Warren AI step_02.md contains structured theme sections returned by the
               THEME_UNIVERSE prompt injection:
                 ### Theme: AI Infrastructure
                 | Ticker | ... | Verdict | Rejection Reason |
               Produces exact pass/reject counts and reasons.

2. INFERRED  — Step_04.md "Filtered Out" table exists and we can cross-reference the 16
               candidates with the theme universe.  We know:
               • candidates_considered  = theme tickers that appear in the 16-ticker step_02 list
               • passed_tickers         = actionable tickers that belong to the theme
               • rejected_tickers       = theme∩candidates that were filtered out in step_04
               • top_rejection_reason   = most common normalised reason from the filter table
               Truthful and useful without inventing anything.

If neither mode produces data: writes an empty-but-valid diagnostics file so the
Dashboard shows "Themes configured, but no diagnostics for latest scan."

Output path:  warren_ai_daily/output/theme_candidate_diagnostics.json  (root-level,
              same pattern as sector_candidate_diagnostics.json)

Also writes a companion  meta  block so the Dashboard can tell the user which mode was
used and which scan date the data came from.
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter
from datetime import date
from typing import Optional

import warren_loader as wl
import theme_registry as tr

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_HERE    = os.path.dirname(os.path.abspath(__file__))
_OUT_DIR = os.path.join(_HERE, "warren_ai_daily", "output")
DIAG_PATH = os.path.join(_OUT_DIR, "theme_candidate_diagnostics.json")


# ---------------------------------------------------------------------------
# Rejection reason normalisation
# ---------------------------------------------------------------------------

_REASON_MAP: list[tuple[str, str]] = [
    # (substring to match lowercase in raw reason text, canonical slug)
    ("falling knife",  "falling_knife"),
    ("overvalued",     "overvalued"),
    ("fv upside -",    "overvalued"),
    ("fv upside: -",   "overvalued"),
    ("overbought",     "overbought"),
    ("rsi",            "overbought"),      # RSI > 72 → overbought
    ("extended",       "too_extended"),
    ("above 50 ema",   "too_extended"),
    ("above 20 ema",   "too_extended"),
    ("weak setup",     "weak_setup"),
    ("bearish",        "weak_setup"),
    ("no pullback",    "no_pullback"),
    ("chasing",        "too_extended"),
]


def _normalise_reason(raw: str) -> str:
    lo = raw.lower()
    for needle, slug in _REASON_MAP:
        if needle in lo:
            return slug
    return "not_reported"


def _top_reason(reasons: list[str]) -> str:
    if not reasons:
        return "not_reported"
    c = Counter(reasons)
    return c.most_common(1)[0][0]


# ---------------------------------------------------------------------------
# EXPLICIT parser  (future: Warren AI returns structured theme tables)
# ---------------------------------------------------------------------------

def _parse_explicit_themes(step02_text: str, themes_cfg: dict[str, tr.ThemeEntry]) -> dict[str, dict] | None:
    """
    Look for ### Theme: <name> ... table sections in step_02.md.
    Returns None if no theme sections are found.
    """
    if not step02_text:
        return None

    # Quick check — requires at least one theme header
    if "### Theme:" not in step02_text and "**Theme:**" not in step02_text:
        return None

    results: dict[str, dict] = {}
    # Split on theme headers
    blocks = re.split(r'#{1,4}\s+Theme:\s*', step02_text)
    if len(blocks) < 2:
        return None

    found_any = False
    for block in blocks[1:]:
        # First line is the theme name
        first_line = block.split("\n")[0].strip().rstrip("*_")
        theme_name = first_line

        # Fuzzy match to configured themes
        matched_theme = None
        for cfg_name in themes_cfg:
            if cfg_name.lower() in theme_name.lower() or theme_name.lower() in cfg_name.lower():
                matched_theme = cfg_name
                break
        if matched_theme is None:
            continue

        passed: list[str]  = []
        rejected: list[dict] = []

        # Parse markdown table rows
        for line in block.split("\n"):
            if "|" not in line:
                continue
            cells = [c.strip() for c in line.split("|") if c.strip()]
            if not cells:
                continue
            # Header or divider rows
            if cells[0].replace("-", "").replace(" ", "") == "" or \
               cells[0].lower() in ("ticker", "#"):
                continue

            # Attempt to find ticker in first or second cell
            ticker = ""
            for cell in cells[:3]:
                m = re.match(r'^([A-Z]{1,5})$', cell.strip())
                if m:
                    ticker = m.group(1)
                    break
            if not ticker:
                continue

            # Look for verdict/pass/reject in cells
            row_text = " ".join(cells).lower()
            if "pass" in row_text and "reject" not in row_text:
                passed.append(ticker)
            elif "reject" in row_text:
                # find reason in last cell
                reason_raw = cells[-1] if len(cells) > 1 else ""
                rejected.append({"ticker": ticker, "reason": _normalise_reason(reason_raw)})

        if passed or rejected:
            found_any = True
            all_reasons = [r["reason"] for r in rejected]
            results[matched_theme] = {
                "candidates_considered": len(passed) + len(rejected),
                "candidates_passed":     len(passed),
                "passed_tickers":        passed,
                "rejected_tickers":      rejected,
                "top_rejection_reason":  _top_reason(all_reasons),
                "source":                "explicit",
            }

    return results if found_any else None


# ---------------------------------------------------------------------------
# Step-04 "Filtered Out" table parser
# ---------------------------------------------------------------------------

def _parse_step04_filter_table(step04_text: str) -> dict[str, str]:
    """
    Parse the "Filtered Out:" table in step_04.md.
    Returns {TICKER: normalised_reason_slug}.

    Handles two formats:
      Tab-separated:   NVDA\tExtended, >12% above 50 EMA, ...
      Pipe table:      | NVDA | Extended ... |
    """
    if not step04_text:
        return {}

    result: dict[str, str] = {}
    lines = step04_text.splitlines()

    # Find the "Filtered Out" section
    in_filter = False
    for line in lines:
        stripped = line.strip()

        # Start of filter section
        if re.search(r"Filtered\s*Out", stripped, re.IGNORECASE):
            in_filter = True
            continue

        # End of filter section
        if in_filter:
            if re.search(r"Qualified\s*(for)?\s*Ranking|Part\s*B|🔑", stripped, re.IGNORECASE):
                break

        if not in_filter:
            continue

        # Skip header/divider rows
        lo = stripped.lower()
        if "ticker" in lo and "reason" in lo:
            continue
        if re.match(r'^[\s|\-:]+$', stripped):
            continue

        # Tab-separated
        if "\t" in stripped:
            parts = [p.strip() for p in stripped.split("\t")]
            if len(parts) >= 2:
                ticker = parts[0].upper()
                if re.match(r'^[A-Z]{1,5}$', ticker):
                    result[ticker] = _normalise_reason(parts[1])
            continue

        # Pipe table
        if stripped.startswith("|"):
            cells = [c.strip() for c in stripped.split("|") if c.strip()]
            if len(cells) >= 2:
                ticker = cells[0].upper()
                if re.match(r'^[A-Z]{1,5}$', ticker):
                    result[ticker] = _normalise_reason(cells[1])
            continue

        # Bare "TICKER  reason" or "TICKER: reason"
        m = re.match(r'^([A-Z]{1,5})[\s:—–]+(.+)$', stripped)
        if m:
            ticker = m.group(1)
            result[ticker] = _normalise_reason(m.group(2))

    return result


# ---------------------------------------------------------------------------
# INFERRED diagnostics (cross-reference theme universe with parsed Warren output)
# ---------------------------------------------------------------------------

def _build_inferred(
    w: wl.WarrenResult,
    step04_text: str,
    themes_cfg: dict[str, tr.ThemeEntry],
) -> dict[str, dict]:
    """
    Build inferred theme diagnostics from:
      - w.all_tickers          (16 candidates from step_02)
      - w.actionable           (qualified tickers from step_04)
      - filter_table           (rejected tickers + normalised reasons)
    """
    all16        = {t.upper() for t in (w.all_tickers or [])}
    actionable   = {t.upper() for t in (w.actionable  or [])}
    filter_table = _parse_step04_filter_table(step04_text)

    results: dict[str, dict] = {}
    for theme_name, entry in themes_cfg.items():
        theme_tickers = set(entry.tickers.keys())           # full universe
        considered    = sorted(theme_tickers & all16)       # appeared in today's 16
        passed        = sorted(theme_tickers & actionable)
        rejected_t    = [
            {"ticker": t, "reason": filter_table.get(t, "not_reported")}
            for t in sorted(theme_tickers & set(filter_table.keys()))
        ]
        all_rej_reasons = [r["reason"] for r in rejected_t]

        results[theme_name] = {
            "candidates_considered":  len(considered),
            "candidates_considered_tickers": considered,
            "candidates_passed":      len(passed),
            "passed_tickers":         passed,
            "rejected_tickers":       rejected_t,
            "top_rejection_reason":   _top_reason(all_rej_reasons) if all_rej_reasons else "not_reported",
            "source":                 "inferred",
        }

    return results


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_theme_diagnostics(
    force: bool = False,
    diag_path: str | None = None,
) -> dict:
    """
    Build and write theme_candidate_diagnostics.json.

    Returns a summary dict:
      {
        "path":           str,
        "mode":           "explicit" | "inferred" | "empty",
        "scan_date":      str,
        "themes_total":   int,
        "themes_with_passed": int,
        "total_passed":   int,
        "error":          str | None,
      }
    """
    out_path = diag_path or DIAG_PATH
    themes_cfg = tr.load_themes()
    summary = {
        "path":               out_path,
        "mode":               "empty",
        "scan_date":          "—",
        "themes_total":       len(themes_cfg),
        "themes_with_passed": 0,
        "total_passed":       0,
        "error":              None,
    }

    if not themes_cfg:
        summary["error"] = "No themes configured in themes.yaml"
        return summary

    try:
        w = wl.get_latest_warren()
    except Exception as e:
        summary["error"] = f"Could not load Warren AI output: {e}"
        return summary

    if w.freshness == "NONE":
        summary["error"] = "No successful Warren AI run found"
        return summary

    summary["scan_date"] = w.date

    # Check if we already have up-to-date diagnostics (skip unless force=True)
    if not force and os.path.isfile(out_path):
        try:
            with open(out_path, "r") as fh:
                existing = json.load(fh)
            if existing.get("_meta", {}).get("scan_date") == w.date:
                # Already current — recompute summary from file
                data = {k: v for k, v in existing.items() if not k.startswith("_")}
                summary["mode"]               = existing.get("_meta", {}).get("mode", "inferred")
                summary["themes_with_passed"] = sum(1 for v in data.values() if v.get("candidates_passed", 0) > 0)
                summary["total_passed"]       = sum(v.get("candidates_passed", 0) for v in data.values())
                return summary
        except Exception:
            pass  # regenerate

    # Read raw step files
    step02_text = _read_step(w.output_path, "step_02.md")
    step04_text = _read_step(w.output_path, "step_04.md")

    # Try explicit parse first, fall back to inferred
    data = _parse_explicit_themes(step02_text, themes_cfg)
    mode = "explicit"
    if data is None:
        data = _build_inferred(w, step04_text, themes_cfg)
        mode = "inferred"

    # Fill in any themes that the explicit parser missed
    for theme_name in themes_cfg:
        if theme_name not in data:
            data[theme_name] = {
                "candidates_considered": 0,
                "candidates_passed":     0,
                "passed_tickers":        [],
                "rejected_tickers":      [],
                "top_rejection_reason":  "not_reported",
                "source":                "inferred",
            }

    # Write file
    output = {
        "_meta": {
            "generated_at": _now_iso(),
            "scan_date":    w.date,
            "mode":         mode,
        },
        **data,
    }
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as fh:
        json.dump(output, fh, indent=2)

    summary["mode"]               = mode
    summary["themes_with_passed"] = sum(1 for v in data.values() if v.get("candidates_passed", 0) > 0)
    summary["total_passed"]       = sum(v.get("candidates_passed", 0) for v in data.values())

    print(
        f"[theme_diag] ✓ Diagnostics written ({mode}) → {out_path}\n"
        f"  scan_date={w.date}  themes={len(data)}"
        f"  with_passed={summary['themes_with_passed']}"
        f"  total_passed={summary['total_passed']}"
    )

    return summary


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_step(folder: str | None, filename: str) -> str:
    if not folder:
        return ""
    p = os.path.join(folder, filename)
    if not os.path.isfile(p):
        return ""
    try:
        with open(p, "r", encoding="utf-8") as fh:
            return fh.read()
    except Exception:
        return ""


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    force = "--force" in sys.argv
    result = build_theme_diagnostics(force=force)
    print(json.dumps(result, indent=2))
