#!/usr/bin/env python3
"""Import vendor OHLCV CSVs into ``futures_dashboard/data/``.

Single file (symbol and timeframe explicit):
  cd futures_dashboard && python3 futures_import.py ~/Downloads/vendor.csv --symbol ES --timeframe 1D

Batch (each file named ``SYMBOL_TIMEFRAME.csv`` in a folder):
  python3 futures_import.py /path/to/folder --batch

READ ONLY — normalizes and writes CSV only; no trading.
"""
from __future__ import annotations

import argparse
import os
import sys

_BASE = os.path.dirname(os.path.abspath(__file__))
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)

import futures_data as fd  # noqa: E402


def _print_import_result(dest: str, msg: str, summary: list[str]) -> None:
    print(msg)
    for line in summary:
        print(line)
    print(dest)


def main() -> int:
    ap = argparse.ArgumentParser(description="Import OHLCV CSV into futures data folder")
    ap.add_argument("src", help="Source CSV file, or folder with --batch")
    ap.add_argument("--symbol", help="Root symbol e.g. ES, NQ (not used with --batch)")
    ap.add_argument("--timeframe", help=f"One of {fd.SUPPORTED_TIMEFRAMES} (not used with --batch)")
    ap.add_argument("--batch", action="store_true", help="Import every SYMBOL_TF.csv from a directory")
    ap.add_argument("--overwrite", action="store_true", help="Replace existing destination files")
    args = ap.parse_args()

    if args.batch:
        folder = os.path.abspath(os.path.expanduser(args.src))
        if not os.path.isdir(folder):
            print(f"Error: batch source must be a directory: {folder}", file=sys.stderr)
            return 1
        if args.symbol or args.timeframe:
            print("Note: --symbol and --timeframe are ignored in --batch mode.", file=sys.stderr)
        err_count = 0
        ok_count = 0
        for fn in sorted(os.listdir(folder)):
            if not fn.lower().endswith(".csv"):
                continue
            parsed = fd.parse_symbol_tf_filename(fn)
            if not parsed:
                print(
                    f"skip {fn}: expected name like ES_1D.csv (symbol one of supported roots, "
                    f"timeframe in {fd.SUPPORTED_TIMEFRAMES})",
                    file=sys.stderr,
                )
                continue
            sym, tf = parsed
            path = os.path.join(folder, fn)
            try:
                dest, msg, summary = fd.import_ohlcv_csv(path, sym, tf, overwrite=args.overwrite)
                print(f"\n=== {fn} → {sym} {tf} ===")
                _print_import_result(dest, msg, summary)
                ok_count += 1
            except ValueError as exc:
                print(f"Error {fn}: {exc}", file=sys.stderr)
                err_count += 1
        print(f"\nBatch done: {ok_count} imported, {err_count} failed.", file=sys.stderr)
        return 1 if err_count else 0

    if not args.symbol or not args.timeframe:
        print("Error: --symbol and --timeframe are required (unless using --batch).", file=sys.stderr)
        return 1

    try:
        dest, msg, summary = fd.import_ohlcv_csv(
            args.src, args.symbol, args.timeframe, overwrite=args.overwrite
        )
        _print_import_result(dest, msg, summary)
        return 0
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
