"""
theme_registry.py — User-defined investment theme overlay.

Themes are an ADDITIONAL layer on top of classic SPDR sectors.
Classic sector attribution (XLK, XLC, XLE, etc.) is never modified here.
A ticker may belong to zero, one, or many themes simultaneously.

Public API:
    load_themes(path=None)              → dict[str, ThemeEntry]
    get_ticker_themes(ticker)           → list[str]
    get_theme_tickers(theme_name)       → dict[str, str]   {ticker: rationale}
    get_all_theme_tickers()             → set[str]
    build_theme_prompt_block()          → str  (for Warren AI step_02 injection)
    get_portfolio_theme_exposure(active) → list[ThemeExposure]
    load_theme_diagnostics(path=None)   → dict[str, dict]
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# Config defaults
# ---------------------------------------------------------------------------

_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_THEMES_PATH = os.path.join(
    _HERE, "warren_ai_daily", "config", "themes.yaml"
)
DEFAULT_DIAG_PATH = os.path.join(
    _HERE, "warren_ai_daily", "output", "theme_candidate_diagnostics.json"
)
DEFAULT_SCORES_PATH = os.path.join(
    _HERE, "warren_ai_daily", "output", "theme_scores_latest.json"
)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ThemeEntry:
    name:        str
    description: str
    tickers:     dict[str, str]  # {TICKER: rationale}


@dataclass
class ThemeExposure:
    """Portfolio dollar exposure to a theme (overlapping — does not sum to 100%)."""
    theme:          str
    tickers_held:   list[str]
    dollar_value:   float
    pct_deployed:   Optional[float]  # None if deployed == 0


# ---------------------------------------------------------------------------
# Loader (defensive — never crashes)
# ---------------------------------------------------------------------------

_CACHE: dict[str, ThemeEntry] | None = None
_CACHE_PATH: str | None = None


def load_themes(path: str | None = None) -> dict[str, ThemeEntry]:
    """
    Load themes from YAML file. Falls back to empty dict on any error.
    Results are cached per path.
    """
    global _CACHE, _CACHE_PATH
    target = path or DEFAULT_THEMES_PATH
    if _CACHE is not None and _CACHE_PATH == target:
        return _CACHE

    themes: dict[str, ThemeEntry] = {}

    if not os.path.isfile(target):
        _CACHE = themes
        _CACHE_PATH = target
        return themes

    try:
        import yaml  # type: ignore[import]
        with open(target, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)

        raw_themes = (raw or {}).get("themes", {}) or {}
        for name, data in raw_themes.items():
            if not isinstance(data, dict):
                continue
            raw_tickers = data.get("tickers", {}) or {}
            # Normalize tickers uppercase
            tickers = {
                str(t).strip().upper(): str(r).strip()
                for t, r in raw_tickers.items()
            }
            themes[str(name)] = ThemeEntry(
                name=str(name),
                description=str(data.get("description", "")),
                tickers=tickers,
            )
    except ImportError:
        # PyYAML not installed — attempt JSON fallback at same path with .json extension
        json_path = target.replace(".yaml", ".json").replace(".yml", ".json")
        if os.path.isfile(json_path):
            try:
                with open(json_path, "r", encoding="utf-8") as fh:
                    raw = json.load(fh)
                for name, data in (raw.get("themes") or {}).items():
                    tickers = {
                        str(t).strip().upper(): str(r).strip()
                        for t, r in (data.get("tickers") or {}).items()
                    }
                    themes[str(name)] = ThemeEntry(
                        name=str(name),
                        description=str(data.get("description", "")),
                        tickers=tickers,
                    )
            except Exception:
                pass
    except Exception:
        pass

    _CACHE = themes
    _CACHE_PATH = target
    return themes


def _themes() -> dict[str, ThemeEntry]:
    """Return cached themes (auto-loads from default path)."""
    return load_themes()


def invalidate_cache() -> None:
    """Force reload on next call (e.g. after editing themes.yaml)."""
    global _CACHE
    _CACHE = None


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

def get_ticker_themes(ticker: str, path: str | None = None) -> list[str]:
    """Return sorted list of theme names that contain this ticker."""
    t = ticker.strip().upper()
    return sorted(
        name for name, entry in load_themes(path).items()
        if t in entry.tickers
    )


def get_theme_tickers(theme_name: str, path: str | None = None) -> dict[str, str]:
    """Return {ticker: rationale} dict for a theme. Empty dict if not found."""
    return load_themes(path).get(theme_name, ThemeEntry("", "", {})).tickers


def get_all_theme_tickers(path: str | None = None) -> set[str]:
    """Return the union of all tickers across all themes."""
    result: set[str] = set()
    for entry in load_themes(path).values():
        result.update(entry.tickers.keys())
    return result


# ---------------------------------------------------------------------------
# Prompt block builder (for Warren AI step_02 injection)
# ---------------------------------------------------------------------------

def build_theme_prompt_block(path: str | None = None) -> str:
    """
    Build the {{THEME_UNIVERSE}} replacement block for injection into prompt_02.
    Returns empty string if no themes configured.
    """
    themes = load_themes(path)
    if not themes:
        return ""

    lines: list[str] = [
        "---",
        "## User-Defined Investment Themes — Additional Idea Universe",
        "",
        "These are user-curated thematic ideas. They are **NOT classic sectors**.",
        "Do not relabel any ticker's classic sector (XLK, XLC, etc.) because of theme membership.",
        "A ticker may belong to multiple themes.",
        "",
        "For each theme, evaluate candidate tickers using your standard execution discipline:",
        "liquidity, price above trend, clean pullback or breakout setup, RSI / extension check,",
        "fair value upside if available, and entry quality.",
        "",
        "**Do not force a ticker into final recommendations only because it belongs to a hot theme.**",
        "Theme membership is an idea source, not a buy signal.",
        "",
        "For each theme, return:",
        "- Theme name",
        "- Ticker, classic sector ETF",
        "- Setup (Pullback / Breakout / Consolidation / Extended / Reject)",
        "- Entry, Stop, Target, R:R if setup qualifies",
        "- Pass or Reject with main rejection reason",
        "",
    ]

    for name, entry in themes.items():
        lines.append(f"### Theme: {name}")
        lines.append(f"_{entry.description}_")
        lines.append("")
        lines.append("| Ticker | Rationale |")
        lines.append("|--------|-----------|")
        for ticker, rationale in entry.tickers.items():
            lines.append(f"| {ticker} | {rationale} |")
        lines.append("")

    lines.append("---")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Portfolio theme exposure
# ---------------------------------------------------------------------------

def get_portfolio_theme_exposure(
    active: list,
    extra_buckets: list[tuple[str, float]] | None = None,
    total_deployed: float = 0.0,
    path: str | None = None,
) -> list[ThemeExposure]:
    """
    Compute overlapping theme exposure from active portfolio positions.
    Exposures are based on position market value where available.
    Note: exposures are overlapping and do NOT sum to 100%.
    """
    themes = load_themes(path)
    extra_buckets = extra_buckets or []

    # Build ticker → market value from PositionResult objects
    mv_map: dict[str, float] = {}
    for r in active:
        t = getattr(r, "ticker", None)
        if not t:
            continue
        price  = getattr(r, "current_price",  None)
        shares = getattr(r, "shares",         None) or getattr(r, "quantity", None)
        cost   = getattr(r, "market_value",   None) or getattr(r, "current_value", None)
        if cost is not None:
            mv_map[t.upper()] = float(cost)
        elif price is not None and shares is not None:
            mv_map[t.upper()] = float(price) * float(shares)

    # Extra non-tracked buckets
    for label, val in extra_buckets:
        key = label.strip().upper()
        if key and val > 0:
            mv_map[key] = mv_map.get(key, 0.0) + val

    total_dep = total_deployed or sum(mv_map.values())

    result: list[ThemeExposure] = []
    for name, entry in themes.items():
        held     = [t for t in entry.tickers if t in mv_map]
        dv       = sum(mv_map[t] for t in held)
        pct_dep  = (dv / total_dep * 100) if total_dep > 0 else None
        result.append(ThemeExposure(
            theme=name,
            tickers_held=held,
            dollar_value=dv,
            pct_deployed=round(pct_dep, 1) if pct_dep is not None else None,
        ))

    return result


# ---------------------------------------------------------------------------
# Theme diagnostics loader
# ---------------------------------------------------------------------------

def load_theme_diagnostics(path: str | None = None) -> dict[str, dict]:
    """
    Load theme_candidate_diagnostics.json produced by theme_diagnostic_parser.
    Strips the _meta key and returns only theme-keyed entries.
    Returns empty dict if the file is missing or unreadable.
    """
    p = path or DEFAULT_DIAG_PATH
    if not os.path.isfile(p):
        return {}
    try:
        with open(p, "r", encoding="utf-8") as fh:
            raw = json.load(fh) or {}
        return {k: v for k, v in raw.items() if not k.startswith("_")}
    except Exception:
        return {}


def load_theme_diagnostics_meta(path: str | None = None) -> dict:
    """Return the _meta block from the diagnostics file, or {}."""
    p = path or DEFAULT_DIAG_PATH
    if not os.path.isfile(p):
        return {}
    try:
        with open(p, "r", encoding="utf-8") as fh:
            raw = json.load(fh) or {}
        return raw.get("_meta", {})
    except Exception:
        return {}


def load_theme_scores(path: str | None = None) -> dict:
    """
    Load theme_scores_latest.json written by theme_scan_parser.
    Returns full file contents or {} if missing/unreadable.
    Structure: {"as_of": str, "themes": {theme_name: {...}}, "summary": {...}}
    """
    p = path or DEFAULT_SCORES_PATH
    if not os.path.isfile(p):
        return {}
    try:
        with open(p, "r", encoding="utf-8") as fh:
            return json.load(fh) or {}
    except Exception:
        return {}


def get_theme_score(theme_name: str, path: str | None = None) -> dict:
    """Return the score entry for a single theme, or {}."""
    scores = load_theme_scores(path)
    return (scores.get("themes") or {}).get(theme_name, {})
