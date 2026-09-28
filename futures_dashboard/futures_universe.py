"""Static futures universe — Phase 1. Edit freely; metadata may be incomplete."""
from __future__ import annotations

from futures_models import FutureProduct

# Root symbols for CSV naming (e.g. ES_1D.csv). Micros share root or use own file.
PRODUCTS: list[FutureProduct] = [
    # Equity index
    FutureProduct("ES",  "E-mini S&P 500",           "Equity Index", 50.0, 0.25, None, ""),
    FutureProduct("NQ",  "E-mini Nasdaq-100",        "Equity Index", 20.0, 0.25, None, ""),
    FutureProduct("YM",  "E-mini Dow",               "Equity Index", 5.0,  1.0,  None, ""),
    FutureProduct("RTY", "E-mini Russell 2000",        "Equity Index", 50.0, 0.10, None, ""),
    FutureProduct("MES", "Micro E-mini S&P 500",     "Equity Index", 5.0,  0.25, "ES", ""),
    FutureProduct("MNQ", "Micro E-mini Nasdaq-100",  "Equity Index", 2.0,  0.25, "NQ", ""),
    FutureProduct("MYM", "Micro E-mini Dow",         "Equity Index", 0.5,  1.0,  "YM", ""),
    FutureProduct("M2K", "Micro E-mini Russell 2000", "Equity Index", 5.0, 0.10, "RTY", ""),
    # Energy
    FutureProduct("CL",  "Crude Oil WTI",            "Energy",       1000.0, 0.01, None, ""),
    FutureProduct("NG",  "Natural Gas",              "Energy",       10000.0, 0.001, None, ""),
    FutureProduct("MCL", "Micro WTI Crude Oil",      "Energy",       None,   None, "CL", ""),
    # Metals
    FutureProduct("GC",  "Gold",                     "Metals",       100.0, 0.10, None, ""),
    FutureProduct("SI",  "Silver",                   "Metals",       5000.0, 0.005, None, ""),
    FutureProduct("MGC", "Micro Gold",               "Metals",       None,   None, "GC", ""),
    # Rates
    FutureProduct("ZN",  "10-Year Note",             "Rates",        None,   None, None, ""),
    FutureProduct("ZF",  "5-Year Note",              "Rates",        None,   None, None, ""),
    FutureProduct("ZT",  "2-Year Note",              "Rates",        None,   None, None, ""),
    FutureProduct("ZB",  "30-Year Bond",             "Rates",        None,   None, None, ""),
    # FX
    FutureProduct("6E",  "Euro FX",                  "FX",           None,   None, None, ""),
    FutureProduct("6J",  "Japanese Yen",             "FX",           None,   None, None, ""),
    FutureProduct("6B",  "British Pound",            "FX",           None,   None, None, ""),
    # Ags
    FutureProduct("ZC",  "Corn",                     "Ags",          None,   None, None, ""),
    FutureProduct("ZS",  "Soybeans",                 "Ags",          None,   None, None, ""),
    FutureProduct("ZW",  "Wheat",                    "Ags",          None,   None, None, ""),
    # Crypto futures (placeholders)
    FutureProduct("BTC", "Bitcoin futures",          "Crypto",       None,   None, None, "Contract varies by venue."),
    FutureProduct("ETH", "Ether futures",            "Crypto",       None,   None, None, ""),
    FutureProduct("MBT", "Micro Bitcoin",            "Crypto",       None,   None, "BTC", ""),
    FutureProduct("MET", "Micro Ether",              "Crypto",       None,   None, "ETH", ""),
]


_BY_SYMBOL: dict[str, FutureProduct] = {p.symbol.upper(): p for p in PRODUCTS}

# Curated micros for Micro Product Pulse (Phase 4.5). Order = display order.
MICRO_PULSE_SYMBOLS: tuple[str, ...] = (
    "MES",
    "MNQ",
    "MYM",
    "M2K",
    "MGC",
    "MCL",
    "MBT",
    "MET",
)


def get_product(symbol: str) -> FutureProduct | None:
    return _BY_SYMBOL.get(symbol.strip().upper())


def asset_class_lookup() -> dict[str, str]:
    return {p.symbol.upper(): p.asset_class for p in PRODUCTS}


def symbols_for_dropdown() -> list[dict[str, str]]:
    return [{"label": f"{p.symbol} — {p.name}", "value": p.symbol} for p in PRODUCTS]


def micros_sharing_parent(parent_symbol: str) -> list[str]:
    """Root symbols of micro contracts that list ``parent_symbol`` as their micro parent."""
    p = parent_symbol.strip().upper()
    return [x.symbol for x in PRODUCTS if (x.micro_parent or "").upper() == p]


def micro_pulse_definitions() -> list[tuple[str, str | None]]:
    """``(micro_symbol, parent_symbol_or_none)`` for the pulse table."""
    out: list[tuple[str, str | None]] = []
    for sym in MICRO_PULSE_SYMBOLS:
        prod = get_product(sym)
        par = (prod.micro_parent or "").strip().upper() if prod else None
        out.append((sym, par or None))
    return out
