"""
Load Theme Catalyst Radar config — private company proxy mappings and catalysts.
"""
from __future__ import annotations

import json
import os
from typing import Any

_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG_PATH = os.path.join(_HERE, "config", "atlas_theme_catalysts.yaml")

_CACHE: dict[str, Any] | None = None
_CACHE_PATH: str | None = None

# Built-in fallback if YAML unavailable (SpaceX example)
_BUILTIN: dict[str, Any] = {
    "event_types": [
        "IPO", "S1 filing", "roadshow", "pricing date", "listing date",
        "major contract", "government funding", "earnings cluster",
        "regulatory decision", "product launch", "geopolitical event",
        "sector wide policy event",
    ],
    "private_company_mappings": {
        "SpaceX": {
            "themes": ["space", "satellites", "launch", "Starlink",
                       "defense aerospace", "communications infrastructure"],
            "proxy_tickers": ["RKLB", "ASTS", "LUNR", "PL", "BKSY",
                              "IRDM", "GSAT", "SATS", "SPCE"],
            "proxy_etfs": ["ARKX", "UFO", "ITA", "XAR", "PPA", "XLC"],
            "primary_etf": "ARKX",
        },
    },
    "catalysts": [{
        "id": "spacex_ipo_june_2026",
        "private_company": "SpaceX",
        "event_type": "IPO",
        "theme": "space",
        "themes_affected": ["space", "satellites", "launch", "Starlink",
                            "defense aerospace", "communications infrastructure"],
        "catalyst_label": "SpaceX IPO target date",
        "catalyst_date": "2026-06-12",
        "status": "upcoming",
        "confidence": "medium",
        "source": "reported media target",
        "source_name": "Financial media reports",
        "source_url": "",
        "date_added": "2026-06-02",
        "last_verified": "2026-06-02",
        "verification_status": "pending",
        "manual_review_required": True,
        "source_freshness": "manual",
        "action_context": "Watch theme — do not chase without proxy confirmation",
        "proxy_tickers": ["RKLB", "ASTS", "LUNR", "PL", "BKSY",
                          "IRDM", "GSAT", "SATS", "SPCE"],
        "proxy_etfs": ["ARKX", "UFO", "ITA", "XAR", "PPA", "XLC"],
        "sector_etf_basket": ["ARKX", "UFO", "ITA", "XAR"],
    }],
}


def load_config(path: str | None = None) -> dict[str, Any]:
    global _CACHE, _CACHE_PATH
    target = path or DEFAULT_CONFIG_PATH
    if _CACHE is not None and _CACHE_PATH == target:
        return _CACHE

    if not os.path.isfile(target):
        _CACHE = _BUILTIN
        _CACHE_PATH = target
        return _CACHE

    try:
        import yaml  # type: ignore[import]
        with open(target, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        _CACHE = raw if raw.get("catalysts") else _BUILTIN
    except ImportError:
        json_path = target.replace(".yaml", ".json")
        if os.path.isfile(json_path):
            with open(json_path, encoding="utf-8") as fh:
                _CACHE = json.load(fh)
        else:
            _CACHE = _BUILTIN
    except Exception:
        _CACHE = _BUILTIN

    _CACHE_PATH = target
    return _CACHE


def get_private_company_mapping(company: str, path: str | None = None) -> dict[str, Any]:
    cfg = load_config(path)
    return (cfg.get("private_company_mappings") or {}).get(company, {})


def get_catalysts(path: str | None = None) -> list[dict[str, Any]]:
    return list(load_config(path).get("catalysts") or [])
