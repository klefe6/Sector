Futures CSV data (Phase 2)
==========================
Place vendor or CME-exported OHLCV files here.

File name pattern
-----------------
  {SYMBOL}_{TIMEFRAME}.csv

Supported timeframes: 1D, 4H, 1H, 15m, 5m

Examples: ES_1D.csv, NQ_4H.csv, CL_1H.csv

Required columns
----------------
  time, open, high, low, close, volume

Extra columns are ignored. Comment lines starting with # are skipped.

Demo / synthetic data
---------------------
Mark synthetic files with a header comment line starting with:
  # DEMO

Example: "# DEMO SYNTHETIC DATA ONLY — NOT REAL MARKET PRICES"

The dashboard will show "DEMO DATA — not real market data" in Health, Trend, and on the chart.

Import helper (CLI)
-------------------
From repo root:

  cd futures_dashboard
  python3 futures_import.py /path/to/MyNQ.csv --symbol NQ --timeframe 1D

Use --overwrite to replace an existing file.
