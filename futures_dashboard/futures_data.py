"""CSV market data adapter — Phase 1–3 (import, coverage, demo detection, vendor aliases).

Expected files: ``data/{SYMBOL}_{TF}.csv`` e.g. ``data/ES_1D.csv``.

Canonical columns after normalize: time, open, high, low, close, volume
Vendor aliases (case-insensitive column names) are mapped before load.

Demo files: first non-empty lines may be ``# DEMO ...`` (peeked before pandas strips comments).
"""
from __future__ import annotations

import io
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

log = logging.getLogger(__name__)

_BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(_BASE, "data")

# Supported bar files for coverage + import naming
SUPPORTED_TIMEFRAMES: tuple[str, ...] = ("1D", "4H", "1H", "15m", "5m")

# Nominal bar length in seconds for timeframe sanity checks (non-blocking warning)
TIMEFRAME_EXPECTED_SECONDS: dict[str, float] = {
    "5m": 300.0,
    "15m": 900.0,
    "1H": 3600.0,
    "4H": 14400.0,
    "1D": 86400.0,
}

# Column aliases: canonical -> accepted lowercase names (first match wins per canonical)
_TIME_ALIASES: tuple[str, ...] = ("datetime", "timestamp", "date", "time")
_OHLC_ALIASES: dict[str, tuple[str, ...]] = {
    "open": ("open", "o"),
    "high": ("high", "h"),
    "low": ("low", "l"),
    "close": ("close", "c"),
    "volume": ("volume", "vol"),
}


@dataclass
class LoadResult:
    df: pd.DataFrame
    path: str
    warning: str = ""
    mtime: Optional[datetime] = None
    rows: int = 0
    is_demo: bool = False
    tf_warning: str = ""
    #: ``csv`` | ``yahoo`` | ``none`` (nothing loaded after attempts)
    resolved_source: str = "csv"
    #: Shown when Yahoo data is actually charted (not CME / not vendor CSV).
    yahoo_note: str = ""
    #: True when local CSV last bar is stale for Auto-mode rules (1D: >7 calendar days).
    csv_stale: bool = False
    #: Short human reason for Auto resolution (System Health).
    auto_reason: str = ""
    #: Last bar timestamp label for chart status (UTC).
    last_bar_display: str = ""
    #: Multiline debug for Auto source decision.
    source_decision_log: str = ""


def last_bar_timestamp_utc(df: pd.DataFrame | None) -> Optional[pd.Timestamp]:
    """Last row ``dt`` as UTC pandas Timestamp, or None."""
    if df is None or df.empty or "dt" not in df.columns:
        return None
    ts = pd.Timestamp(df["dt"].iloc[-1])
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    return ts


def format_last_bar_utc(df: pd.DataFrame | None) -> str:
    """Compact UTC label for chart status / health."""
    ts = last_bar_timestamp_utc(df)
    if ts is None:
        return "—"
    return ts.strftime("%Y-%m-%d %H:%M UTC")


def csv_stale_for_auto(df: pd.DataFrame | None, timeframe: str) -> tuple[bool, str]:
    """Stale means Auto mode should prefer Yahoo when local CSV is otherwise usable.

    Rules:
    - ``1D``: stale if last bar date is more than 7 calendar days behind UTC today.
    - Intraday: not evaluated here (returns ``(False, note)``) so real non-demo CSV is not
      auto-demoted by age until a dedicated intraday rule exists.
    """
    if timeframe != "1D":
        return False, "stale check: 1D only (intraday not evaluated)"
    ts = last_bar_timestamp_utc(df)
    if ts is None:
        return False, "no last bar"
    today = datetime.now(timezone.utc).date()
    last_d = ts.date()
    days = (today - last_d).days
    stale = days > 7
    note = f"last bar {last_d.isoformat()} ({days} calendar day(s) behind UTC today)"
    return stale, note


def csv_pulse_tier(csv_res: LoadResult) -> str:
    """Classify local CSV for Micro Product Pulse ordering (always 1D context)."""
    ok = csv_res.rows > 0 and csv_res.df is not None and not csv_res.df.empty
    if not ok:
        return "empty"
    if csv_res.is_demo:
        return "demo"
    stale, _note = csv_stale_for_auto(csv_res.df, "1D")
    if stale:
        return "stale_real"
    return "fresh_real"


@dataclass
class CoverageRow:
    symbol: str
    timeframe: str
    filename: str
    rows: int
    start: str
    end: str
    mtime: str
    demo_or_real: str
    status: str
    asset_class: str


def csv_path(symbol: str, timeframe: str) -> str:
    safe_sym = symbol.strip().upper().replace("/", "-")
    safe_tf = timeframe.strip().replace(" ", "")
    return os.path.join(DATA_DIR, f"{safe_sym}_{safe_tf}.csv")


def file_is_demo(path: str) -> bool:
    """True if file header comments mark synthetic demo data."""
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            for _ in range(12):
                line = f.readline()
                if not line:
                    break
                s = line.strip()
                if not s:
                    continue
                if not s.startswith("#"):
                    break
                up = s.upper()
                if up.startswith("# DEMO") or "DEMO SYNTHETIC" in up or "NOT REAL MARKET" in up:
                    return True
    except OSError as exc:
        log.debug("demo peek %s: %s", path, exc)
    return False


def validate_data_folder() -> tuple[bool, list[str]]:
    """Return (ok, human-readable notes)."""
    notes: list[str] = []
    if not os.path.isdir(DATA_DIR):
        notes.append(f"Data folder missing: {DATA_DIR}")
        return False, notes
    notes.append(f"Data folder OK: {DATA_DIR}")
    try:
        names = [n for n in os.listdir(DATA_DIR) if n.lower().endswith(".csv")]
        notes.append(f"CSV files found: {len(names)}")
    except OSError as exc:
        notes.append(f"Cannot list data folder: {exc}")
        return False, notes
    return True, notes


def parse_symbol_tf_filename(name: str) -> tuple[str, str] | None:
    """Parse ``NAME`` like ``ES_1D.csv`` into ``("ES", "1D")`` if valid."""
    if not name.lower().endswith(".csv"):
        return None
    stem = name[:-4]
    if "_" not in stem:
        return None
    sym, tf = stem.rsplit("_", 1)
    sym = sym.strip().upper()
    tf = tf.strip()
    if tf not in SUPPORTED_TIMEFRAMES:
        return None
    if not re.match(r"^[A-Z0-9]{1,12}$", sym):
        return None
    return sym, tf


def _lower_col_index(raw: pd.DataFrame) -> dict[str, str]:
    """Map stripped lower-case name -> original column string (last wins on duplicate lowers)."""
    return {str(c).strip().lower(): str(c).strip() for c in raw.columns}


def _pick_original_column(lower_index: dict[str, str], aliases: tuple[str, ...]) -> str | None:
    for a in aliases:
        if a in lower_index:
            return lower_index[a]
    return None


def normalize_vendor_columns(raw: pd.DataFrame) -> tuple[pd.DataFrame | None, str]:
    """Map vendor columns to canonical names: time, open, high, low, close, volume.

    Returns ``(frame, error_message)``. On success error is empty string.
    """
    if raw.empty:
        return None, "Empty CSV"
    li = _lower_col_index(raw)
    tcol = _pick_original_column(li, _TIME_ALIASES)
    if not tcol:
        return None, f"Missing time column (need one of {_TIME_ALIASES}). Found: {list(raw.columns)}"

    out = pd.DataFrame()
    out["time"] = raw[tcol]

    need_ohlc = ("open", "high", "low", "close")
    missing: list[str] = []
    for canon in need_ohlc:
        orig = _pick_original_column(li, _OHLC_ALIASES[canon])
        if not orig:
            missing.append(canon)
        else:
            out[canon] = raw[orig]
    if missing:
        return None, f"Missing columns: {missing}. Found: {list(raw.columns)}"

    vorig = _pick_original_column(li, _OHLC_ALIASES["volume"])
    if vorig is not None:
        out["volume"] = raw[vorig]
    else:
        out["volume"] = 0.0

    return out, ""


def _parse_time_series(series: pd.Series) -> pd.Series:
    """Parse to UTC (naive inputs are treated as UTC by pandas)."""
    return pd.to_datetime(series, utc=True, errors="coerce")


def time_column_timezone_note(ser: pd.Series) -> str:
    """Short human note for import summary (expects parsed UTC datetimes)."""
    sub = pd.to_datetime(ser, utc=True, errors="coerce").dropna()
    if sub.empty:
        return "no valid timestamps"
    try:
        tz = sub.dt.tz
        if tz is not None:
            return str(tz)
    except (TypeError, AttributeError):
        pass
    return "UTC"


def finalize_ohlcv_frame(
    norm: pd.DataFrame,
    *,
    drop_duplicate_times: bool = True,
) -> tuple[pd.DataFrame | None, str, int]:
    """Parse times, coerce OHLCV, sort, optional drop duplicate timestamps.

    Returns ``(df_with_dt_column_renamed_to_dt_for_internal_use, error, dups_dropped)``.
    Actually returns columns: dt, open, high, low, close, volume, vol — same as load_ohlcv output.
    """
    df = norm.copy()
    df["time"] = _parse_time_series(df["time"])
    for k in ("open", "high", "low", "close"):
        df[k] = pd.to_numeric(df[k], errors="coerce")
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0.0)

    df.dropna(subset=["time", "open", "high", "low", "close"], inplace=True)
    if df.empty:
        return None, "No valid OHLCV rows after parse", 0

    df.sort_values("time", inplace=True)
    before = len(df)
    if drop_duplicate_times:
        df.drop_duplicates(subset=["time"], keep="last", inplace=True)
    dups = before - len(df)

    # Basic numeric sanity: OHLC finite
    for k in ("open", "high", "low", "close"):
        if not df[k].replace([float("inf"), float("-inf")], pd.NA).notna().all():
            return None, f"Non-finite values in column {k}", dups
    df.reset_index(drop=True, inplace=True)

    df.rename(columns={"time": "dt"}, inplace=True)
    df["vol"] = df["volume"]
    return df, "", dups


def check_timeframe_alignment(dt: pd.Series, timeframe: str) -> tuple[bool, str]:
    """Return (ok, warning_message). Warning empty if OK or insufficient bars."""
    if timeframe not in TIMEFRAME_EXPECTED_SECONDS:
        return True, ""
    exp = TIMEFRAME_EXPECTED_SECONDS[timeframe]
    s = pd.to_datetime(dt, utc=True, errors="coerce").dropna().sort_values()
    if len(s) < 3:
        return True, ""
    deltas = s.diff().dt.total_seconds().iloc[1:]
    deltas = deltas[deltas > 60]  # ignore sub-minute noise
    if deltas.empty:
        return True, ""
    if timeframe == "1D":
        capped = deltas[deltas <= 14 * 86400]
        med = float(capped.median()) if len(capped) > 0 else float(deltas.median())
        low, high = 0.28 * exp, 4.5 * exp
    else:
        med = float(deltas.median())
        low, high = 0.42 * exp, 2.35 * exp
    if low <= med <= high:
        return True, ""
    return False, "File timestamps do not appear to match selected timeframe."


def import_summary_lines(
    df_final: pd.DataFrame,
    *,
    dup_count: int,
    tz_note: str,
) -> list[str]:
    """Lines for CLI after successful normalize."""
    lines: list[str] = []
    if df_final.empty:
        return ["Rows: 0"]
    tcol = df_final["dt"]
    start = pd.Timestamp(tcol.iloc[0]).strftime("%Y-%m-%d %H:%M:%S UTC")
    end = pd.Timestamp(tcol.iloc[-1]).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines.append(f"Rows: {len(df_final)}")
    lines.append(f"Start: {start}")
    lines.append(f"End: {end}")
    lines.append(f"Timezone note: {tz_note}")
    if dup_count:
        lines.append(f"Duplicate timestamps dropped: {dup_count}")
    return lines


def _validate_and_build_frame(raw: pd.DataFrame) -> tuple[pd.DataFrame | None, str, int, str]:
    """Returns (df with dt/open/high/low/close/volume/vol, err, dups_dropped, tz_note)."""
    norm, err = normalize_vendor_columns(raw)
    if err or norm is None:
        return None, err, 0, ""
    fin, err2, dups = finalize_ohlcv_frame(norm, drop_duplicate_times=True)
    if err2 or fin is None:
        return None, err2, dups, ""
    tz_note = time_column_timezone_note(fin["dt"])
    return fin, "", dups, tz_note


def import_ohlcv_csv(
    source_path: str,
    symbol: str,
    timeframe: str,
    *,
    overwrite: bool = False,
) -> tuple[str, str, list[str]]:
    """Validate and write normalized OHLCV to ``data/{SYMBOL}_{TF}.csv``.

    Returns ``(dest_path, short_message, summary_lines)``.
    Raises ``ValueError`` on validation failure.
    """
    if timeframe.strip() not in SUPPORTED_TIMEFRAMES:
        raise ValueError(f"Unsupported timeframe {timeframe!r}. Use one of {SUPPORTED_TIMEFRAMES}")

    src = os.path.abspath(os.path.expanduser(source_path))
    if not os.path.isfile(src):
        raise ValueError(f"Source file not found: {src}")

    os.makedirs(DATA_DIR, exist_ok=True)
    dest = csv_path(symbol, timeframe)
    if os.path.exists(dest) and not overwrite:
        raise ValueError(f"Destination exists (use --overwrite): {dest}")

    try:
        raw = pd.read_csv(src, comment="#")
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Cannot read CSV: {exc}") from exc

    df, err, dups, tz_note = _validate_and_build_frame(raw)
    if err or df is None:
        raise ValueError(err)

    out = df[["dt", "open", "high", "low", "close", "volume"]].copy()
    out.rename(columns={"dt": "time"}, inplace=True)
    out["time"] = pd.to_datetime(out["time"], utc=True)
    try:
        buf = io.StringIO()
        out.to_csv(buf, index=False, date_format="%Y-%m-%d %H:%M:%S")
        body = buf.getvalue()
        with open(dest, "w", encoding="utf-8", newline="") as f:
            if file_is_demo(src):
                f.write("# DEMO SYNTHETIC DATA ONLY — NOT REAL MARKET PRICES\n")
            f.write(body)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Cannot write {dest}: {exc}") from exc

    summary = import_summary_lines(df, dup_count=dups, tz_note=tz_note)
    msg = f"Imported → {dest}"
    log.info("futures_data: %s rows=%s", msg, len(df))
    return dest, msg, summary


def load_ohlcv(symbol: str, timeframe: str) -> LoadResult:
    """Load OHLCV from CSV. Returns empty DataFrame if missing or invalid."""
    path = csv_path(symbol, timeframe)
    is_demo = file_is_demo(path) if os.path.exists(path) else False

    if not os.path.exists(path):
        return LoadResult(
            df=pd.DataFrame(),
            path=path,
            warning=f"No file: {path}",
            is_demo=False,
        )

    try:
        raw = pd.read_csv(path, comment="#")
    except Exception as exc:  # noqa: BLE001
        log.warning("CSV read failed %s: %s", path, exc)
        return LoadResult(df=pd.DataFrame(), path=path, warning=str(exc), is_demo=is_demo)

    if raw.empty:
        return LoadResult(df=pd.DataFrame(), path=path, warning="Empty CSV", is_demo=is_demo)

    df, err, _dups, _tz = _validate_and_build_frame(raw)
    if err or df is None:
        return LoadResult(
            df=pd.DataFrame(),
            path=path,
            warning=err,
            is_demo=is_demo,
        )

    tf_ok, tf_warn = check_timeframe_alignment(df["dt"], timeframe)
    tf_warning = "" if tf_ok else tf_warn

    mtime = None
    try:
        ts = os.path.getmtime(path)
        mtime = datetime.fromtimestamp(ts, tz=timezone.utc)
    except OSError:
        pass

    return LoadResult(
        df=df,
        path=path,
        warning="",
        mtime=mtime,
        rows=len(df),
        is_demo=is_demo,
        tf_warning=tf_warning,
        csv_stale=csv_stale_for_auto(df, timeframe)[0],
        last_bar_display=format_last_bar_utc(df),
    )


def scan_data_coverage(asset_class_lookup: dict[str, str] | None = None) -> list[CoverageRow]:
    """Scan ``data/`` for ``{SYMBOL}_{TIMEFRAME}.csv`` files.

    ``asset_class_lookup``: symbol upper -> asset class label (from universe).
    """
    rows: list[CoverageRow] = []
    lookup = asset_class_lookup or {}

    if not os.path.isdir(DATA_DIR):
        return rows

    for fn in sorted(os.listdir(DATA_DIR)):
        parsed = parse_symbol_tf_filename(fn)
        if not parsed:
            continue
        sym, tf = parsed
        path = os.path.join(DATA_DIR, fn)
        demo = file_is_demo(path)
        mtime_str = "—"
        try:
            ts = os.path.getmtime(path)
            mtime_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        except OSError:
            pass

        ac = lookup.get(sym, "Unknown")

        res = load_ohlcv(sym, tf)
        if res.rows > 0 and res.df is not None and not res.df.empty:
            start = res.df["dt"].iloc[0]
            end = res.df["dt"].iloc[-1]
            st_s = pd.Timestamp(start).strftime("%Y-%m-%d")
            en_s = pd.Timestamp(end).strftime("%Y-%m-%d")
            status = "OK"
            nrows = res.rows
        else:
            st_s = en_s = "—"
            nrows = 0
            w = (res.warning or "").lower()
            if (
                "missing columns" in w
                or "missing time column" in w
                or "cannot read" in w
                or "empty csv" in w
            ):
                status = "Invalid"
            elif "no valid" in w:
                status = "Invalid"
            else:
                status = "Empty"

        rows.append(
            CoverageRow(
                symbol=sym,
                timeframe=tf,
                filename=fn,
                rows=nrows,
                start=st_s,
                end=en_s,
                mtime=mtime_str,
                demo_or_real="Demo" if demo else "Real",
                status=status,
                asset_class=ac,
            )
        )
    return rows
