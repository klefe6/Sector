"""Futures chart data provider identifiers and UI copy.

Selectors use stable ids (``csv``, ``yahoo``, …). Actual Yahoo HTTP access stays in
``futures_yfinance`` — this module is routing / product copy only.

Future integrations (IBKR, Databento, …) should register here without hardcoding
the Dash app to a single vendor.
"""
from __future__ import annotations

from enum import StrEnum


class DataProviderId(StrEnum):
    """User-selectable chart data provider preference (dropdown ``value`` strings)."""

    AUTO = "auto"
    CSV = "csv"
    YAHOO = "yahoo"
    DATABENTO_PLACEHOLDER = "databento_placeholder"
    IBKR_PLACEHOLDER = "ibkr_placeholder"


PLACEHOLDER_IDS: frozenset[str] = frozenset(
    {
        DataProviderId.DATABENTO_PLACEHOLDER,
        DataProviderId.IBKR_PLACEHOLDER,
    }
)

PROVIDER_NOT_CONFIGURED_MSG = (
    "Provider not configured yet. Use CSV or Yahoo for now."
)

# --- Chart dropdown (labels are user-facing; values are API-stable ids) ---

AUTO_LABEL = "Auto (fresh real CSV → Yahoo → demo/stale CSV)"
CSV_LABEL = "CSV"
YAHOO_LABEL = "Yahoo Finance unofficial"
DATABENTO_LABEL = "Databento (placeholder)"
IBKR_LABEL = "Interactive Brokers (placeholder)"

DROPDOWN_OPTIONS: list[dict[str, str]] = [
    {"label": AUTO_LABEL, "value": DataProviderId.AUTO},
    {"label": CSV_LABEL, "value": DataProviderId.CSV},
    {"label": YAHOO_LABEL, "value": DataProviderId.YAHOO},
    {"label": DATABENTO_LABEL, "value": DataProviderId.DATABENTO_PLACEHOLDER},
    {"label": IBKR_LABEL, "value": DataProviderId.IBKR_PLACEHOLDER},
]

DROPDOWN_LABELS: dict[str, str] = {o["value"]: o["label"] for o in DROPDOWN_OPTIONS}

# Resolved chart ``LoadResult.resolved_source`` keys (not the same as preference enum for ``auto``).
RESOLVED_PROVIDER_LABELS: dict[str, str] = {
    "csv": "CSV",
    "yahoo": "Yahoo Finance unofficial",
    "none": "Not loaded",
}


def normalize_data_src_preference(raw: str | None) -> str:
    """Normalize dropdown value (handles legacy ``yfinance`` saved state)."""
    if raw is None or raw == "":
        return str(DataProviderId.AUTO)
    if raw == "yfinance":
        return str(DataProviderId.YAHOO)
    return raw


def is_placeholder(provider_id: str) -> bool:
    return provider_id in PLACEHOLDER_IDS


# --- Micro Product Pulse source column (canonical strings) ---

PULSE_SOURCE_CSV_REAL = "CSV real"
PULSE_SOURCE_CSV_DEMO = "CSV demo"
PULSE_SOURCE_YAHOO_PARENT_PROXY = "Yahoo parent proxy"
PULSE_SOURCE_YAHOO_UNOFFICIAL = "Yahoo unofficial"
PULSE_SOURCE_PROVIDER_UNSUPPORTED = "Provider unsupported"
