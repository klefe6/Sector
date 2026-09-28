"""Futures Decision Cockpit — Phase 4.5 (Micro Product Pulse + UI refresh).

Run:
  cd futures_dashboard && source ../.venv/bin/activate && python3 app_futures.py

READ ONLY. No broker APIs. No orders.

Port: FUTURES_PORT (default 8072).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
from dash import Dash, Input, Output, State, ctx, dcc, html, no_update

import futures_charting as fc
import futures_data as fd
import futures_micro_pulse as fmp
import futures_providers as fp
import futures_universe as fu
import futures_yfinance as fy
from futures_formatting import format_price

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("app_futures")

PORT = int(os.environ.get("FUTURES_PORT", "8072"))

DEFAULT_SYMBOL    = "ES"
DEFAULT_TIMEFRAME = "1D"
DEFAULT_LOOKBACK  = 180
DEFAULT_DATA_SRC  = "auto"

COV_FLT_ALL = "__ALL__"

DEMO_BANNER = "DEMO DATA — not real market data"

DATA_SRC_OPTIONS = fp.DROPDOWN_OPTIONS
DATA_SRC_LABELS = fp.DROPDOWN_LABELS
RESOLVED_LABELS = fp.RESOLVED_PROVIDER_LABELS

_ASSETS = os.path.join(os.path.dirname(__file__), "assets")

app = Dash(__name__, title="Futures Decision Cockpit", update_title=None, assets_folder=_ASSETS)
app.index_string = """<!DOCTYPE html>
<html>
    <head>
        {%metas%}
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>{%title%}</title>
        {%favicon%}
        {%css%}
    </head>
    <body>
        {%app_entry%}
        <footer>
            {%config%}
            {%scripts%}
            {%renderer%}
        </footer>
    </body>
</html>"""

_acs = sorted({p.asset_class for p in fu.PRODUCTS})
_cov_asset_options = [{"label": "All", "value": COV_FLT_ALL}] + [{"label": ac, "value": ac} for ac in _acs]
if "Unknown" not in _acs:
    _cov_asset_options.append({"label": "Unknown", "value": "Unknown"})
_cov_sym_options = [{"label": "All", "value": COV_FLT_ALL}] + [
    {"label": p.symbol, "value": p.symbol} for p in sorted(fu.PRODUCTS, key=lambda x: x.symbol)
]
_cov_tf_options = [{"label": "All", "value": COV_FLT_ALL}] + [
    {"label": x, "value": x} for x in fd.SUPPORTED_TIMEFRAMES
]
_cov_demo_options = [
    {"label": "All", "value": COV_FLT_ALL},
    {"label": "Demo", "value": "Demo"},
    {"label": "Real", "value": "Real"},
]


def _resolve_chart(sym: str, tf: str, lb: int, src_pref: str) -> fd.LoadResult:
    """Resolve OHLCV via ``futures_providers`` preference ids (``auto`` | ``csv`` | ``yahoo`` | placeholders).

    Auto priority (swing-friendly):
    1) Real, non-demo CSV with rows and not stale (1D: last bar ≤7 calendar days behind UTC today)
    2) Yahoo when available and supported (``futures_yfinance`` adapter)
    3) Local CSV fallback (demo, stale real, or empty-file recovery paths as applicable)
    4) Missing if nothing usable

    Placeholder providers (Databento / IBKR) return an empty frame and a clean message.
    """
    src_pref = fp.normalize_data_src_preference(src_pref)
    if fp.is_placeholder(src_pref):
        log_lines = [
            f"Provider preference: {src_pref}",
            "CSV exists: (not evaluated for placeholder)",
            "Yahoo attempted: no",
            f"Message: {fp.PROVIDER_NOT_CONFIGURED_MSG}",
            "Final source: none (placeholder)",
        ]
        return fd.LoadResult(
            df=pd.DataFrame(),
            path=f"provider:{src_pref}",
            warning=fp.PROVIDER_NOT_CONFIGURED_MSG,
            mtime=None,
            rows=0,
            is_demo=False,
            tf_warning="",
            resolved_source="none",
            yahoo_note="",
            csv_stale=False,
            auto_reason=fp.PROVIDER_NOT_CONFIGURED_MSG,
            last_bar_display="—",
            source_decision_log="\n".join(log_lines),
        )

    csv_res = fd.load_ohlcv(sym, tf)
    csv_ok = csv_res.rows > 0 and not csv_res.df.empty
    csv_stale, stale_note = fd.csv_stale_for_auto(csv_res.df if csv_ok else None, tf)
    ytick = fy.yahoo_ticker_for_symbol(sym) or f"{sym.strip().upper()}=?"

    def _yf_success(df: pd.DataFrame, ytick_s: str, *, auto_reason: str, log_lines: list[str]) -> fd.LoadResult:
        tf_ok, tf_warn = fd.check_timeframe_alignment(df["dt"], tf)
        lines = list(log_lines) + ["Final source: yahoo"]
        return fd.LoadResult(
            df=df,
            path=f"yahoo:{ytick_s}",
            warning="",
            mtime=datetime.now(timezone.utc),
            rows=len(df),
            is_demo=False,
            tf_warning="" if tf_ok else tf_warn,
            resolved_source="yahoo",
            yahoo_note=fy.YFINANCE_UNOFFICIAL,
            csv_stale=csv_stale if csv_ok else False,
            auto_reason=auto_reason,
            last_bar_display=fd.format_last_bar_utc(df),
            source_decision_log="\n".join(lines),
        )

    def _csv_as_chart_result(
        base: fd.LoadResult,
        *,
        auto_reason: str,
        log_lines: list[str],
        extra_warning: str = "",
        final_log: str | None = None,
    ) -> fd.LoadResult:
        w = (base.warning or "").strip()
        if extra_warning:
            w = f"{w} {extra_warning}".strip() if w else extra_warning
        lines = list(log_lines)
        if final_log:
            lines.append(final_log)
        return fd.LoadResult(
            df=base.df,
            path=base.path,
            warning=w,
            mtime=base.mtime,
            rows=base.rows,
            is_demo=base.is_demo,
            tf_warning=base.tf_warning,
            resolved_source="csv",
            yahoo_note="",
            csv_stale=csv_stale if csv_ok else False,
            auto_reason=auto_reason,
            last_bar_display=fd.format_last_bar_utc(base.df) if csv_ok else "—",
            source_decision_log="\n".join(lines),
        )

    ydf = pd.DataFrame()
    yerr = ""
    if src_pref in ("auto", str(fp.DataProviderId.YAHOO)):
        ydf, yerr = fy.load_yfinance_ohlcv(sym, tf, lb)
    yahoo_ok = (not yerr) and ydf is not None and not ydf.empty

    log_lines: list[str] = [
        f"Auto source decision (symbol={sym} tf={tf} preference={src_pref})",
        f"CSV exists: {'yes' if os.path.isfile(csv_res.path) else 'no'}",
        f"CSV path: {csv_res.path}",
        f"CSV rows: {csv_res.rows}",
        f"CSV demo: {'yes' if csv_res.is_demo else 'no'}",
        f"CSV last bar: {fd.format_last_bar_utc(csv_res.df) if csv_ok else '—'}",
        f"CSV stale (1D rule): {'yes' if csv_stale else 'no'} ({stale_note})",
        f"Yahoo attempted: {'yes' if src_pref in ('auto', str(fp.DataProviderId.YAHOO)) else 'no'}",
        f"Yahoo symbol: {ytick}",
        f"Yahoo rows: {len(ydf) if yahoo_ok else 0}",
        f"Yahoo error: {(yerr or '').strip() or ('not attempted' if src_pref == 'csv' else '—')}",
    ]

    if src_pref == "csv":
        ar = ""
        extra = ""
        if csv_ok and csv_res.is_demo:
            ar = "Demo CSV (explicit CSV mode)."
            if csv_stale and tf == "1D":
                extra = (
                    "This DEMO file is also STALE for 1D (last bar >7 calendar days behind UTC today: "
                    f"{stale_note}). Use Auto or Yahoo for current data."
                )
        elif csv_ok and csv_stale and tf == "1D":
            extra = (
                "CSV STALE: last daily bar is more than 7 calendar days behind UTC today "
                f"({stale_note}). Switch to Auto or Yahoo for current data."
            )
            ar = "Stale CSV (explicit CSV mode — Yahoo not used)."
        elif csv_ok:
            ar = "Local CSV (explicit CSV mode)."
        else:
            ar = "CSV explicit: file missing, empty, or invalid."
        return _csv_as_chart_result(
            csv_res,
            auto_reason=ar,
            log_lines=log_lines,
            extra_warning=extra,
            final_log="Final source: CSV (explicit)",
        )

    if src_pref == str(fp.DataProviderId.YAHOO):
        if yahoo_ok:
            return _yf_success(
                ydf,
                ytick,
                auto_reason="Yahoo Finance unofficial (explicit selection).",
                log_lines=log_lines,
            )
        return fd.LoadResult(
            df=pd.DataFrame(),
            path=f"yahoo:{ytick}",
            warning=yerr or "No Yahoo data.",
            mtime=csv_res.mtime,
            rows=0,
            is_demo=False,
            tf_warning="",
            resolved_source="none",
            yahoo_note="",
            csv_stale=csv_stale if csv_ok else False,
            auto_reason="Yahoo explicit selection failed.",
            last_bar_display="—",
            source_decision_log="\n".join(log_lines + ["Final source: none"]),
        )

    # --- auto ---
    fresh_real_csv = csv_ok and (not csv_res.is_demo) and (not csv_stale)
    if fresh_real_csv:
        return _csv_as_chart_result(
            csv_res,
            auto_reason="Real fresh CSV preferred in Auto mode.",
            log_lines=log_lines,
            final_log="Final source: csv (fresh real local file)",
        )

    if yahoo_ok:
        if csv_ok and csv_res.is_demo:
            ar = "Demo CSV available; using Yahoo fallback in Auto mode."
        elif csv_ok and csv_stale:
            ar = "CSV stale; using Yahoo fallback in Auto mode."
        elif not csv_ok:
            ar = "No usable CSV; using Yahoo in Auto mode."
        else:
            ar = "Using Yahoo in Auto mode."
        return _yf_success(ydf, ytick, auto_reason=ar, log_lines=log_lines)

    # Yahoo failed or unavailable — fall back to local CSV if any rows exist
    if csv_ok:
        extra = ""
        if csv_res.is_demo:
            ar = "Yahoo unavailable; using demo CSV as last resort in Auto mode."
        elif csv_stale:
            ar = "Yahoo unavailable; using stale local CSV in Auto mode."
            extra = (
                "CSV STALE: last daily bar is more than 7 calendar days behind UTC today "
                f"({stale_note}). Yahoo failed — showing local data anyway."
            )
        else:
            ar = "Yahoo unavailable; using local CSV in Auto mode."
        return _csv_as_chart_result(
            csv_res,
            auto_reason=ar,
            log_lines=log_lines,
            extra_warning=extra,
            final_log="Final source: csv (Yahoo unavailable — local CSV fallback)",
        )

    cw = (csv_res.warning or "").strip()
    ye = (yerr or "no data").strip()
    log_lines.append("Final source: none")
    return fd.LoadResult(
        df=pd.DataFrame(),
        path=csv_res.path,
        warning=f"CSV: {cw}. Yahoo: {ye}.",
        mtime=csv_res.mtime,
        rows=0,
        is_demo=False,
        tf_warning="",
        resolved_source="none",
        yahoo_note="",
        csv_stale=False,
        auto_reason="No chart data (CSV empty/invalid and Yahoo failed).",
        last_bar_display="—",
        source_decision_log="\n".join(log_lines),
    )


def _row(label: str, value, *, val_class: str = "") -> html.Div:
    return html.Div(
        [
            html.Span(label, style={"color": "#6b7280", "width": "200px", "display": "inline-block"}),
            html.Span(str(value), className=val_class or None, style={"fontWeight": "600"}),
        ],
        style={"marginBottom": "6px"},
    )


def _bool_txt(v: bool | None) -> str:
    if v is None:
        return "—"
    return "Above" if v else "Below"


def _pct(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:+.2f}%"


def _fmt_meta_num(v: float | None) -> str:
    if v is None:
        return "Unknown"
    try:
        f = float(v)
        if f == int(f):
            return str(int(f))
        return str(f)
    except (TypeError, ValueError):
        return "Unknown"


def _filter_coverage_rows(
    rows: list[fd.CoverageRow],
    flt_asset: str | None,
    flt_symbol: str | None,
    flt_tf: str | None,
    flt_demo: str | None,
) -> list[fd.CoverageRow]:
    fa = flt_asset or COV_FLT_ALL
    fs = flt_symbol or COV_FLT_ALL
    ft = flt_tf or COV_FLT_ALL
    fdemo = flt_demo or COV_FLT_ALL
    out: list[fd.CoverageRow] = []
    for r in rows:
        if fa != COV_FLT_ALL and r.asset_class != fa:
            continue
        if fs != COV_FLT_ALL and r.symbol != fs:
            continue
        if ft != COV_FLT_ALL and r.timeframe != ft:
            continue
        if fdemo != COV_FLT_ALL:
            want_demo = fdemo == "Demo"
            is_demo = r.demo_or_real == "Demo"
            if is_demo != want_demo:
                continue
        out.append(r)
    return out


def _coverage_table(rows: list[fd.CoverageRow]) -> html.Table:
    th = {"padding": "6px 8px", "fontSize": "0.68rem", "color": "#9ca3af", "textAlign": "left", "borderBottom": "1px solid #374151"}
    td = {"padding": "6px 8px", "fontSize": "0.78rem", "borderBottom": "1px solid #1f2937", "whiteSpace": "nowrap"}
    if not rows:
        return html.Table(
            [html.Tr([html.Td("No matching CSV files in data/.", style=td)])],
            style={"width": "100%", "marginTop": "8px"},
        )
    hdr = html.Tr([
        html.Th("Class", style=th),
        html.Th("Symbol", style=th),
        html.Th("TF", style=th),
        html.Th("Rows", style=th),
        html.Th("Start", style=th),
        html.Th("End", style=th),
        html.Th("Modified", style=th),
        html.Th("Demo / Real", style=th),
        html.Th("Status", style=th),
    ])
    body = []
    for r in rows:
        demo_style = {"color": "#f87171", "fontWeight": "600"} if r.demo_or_real == "Demo" else {"color": "#86efac"}
        body.append(html.Tr([
            html.Td(r.asset_class, style=td),
            html.Td(r.symbol, style=td),
            html.Td(r.timeframe, style=td),
            html.Td(str(r.rows), style=td),
            html.Td(r.start, style=td),
            html.Td(r.end, style=td),
            html.Td(r.mtime, style=td),
            html.Td(r.demo_or_real, style={**td, **demo_style}),
            html.Td(r.status, style=td),
        ]))
    return html.Table([html.Thead(hdr), html.Tbody(body)], style={"width": "100%", "marginTop": "8px"})


def _fbadge(text: str, kind: str) -> html.Span:
    return html.Span(text, className=f"fbadge fbadge--{kind}")


def _fmt_pulse_pct(v: Optional[float]) -> tuple[str, str]:
    if v is None:
        return "—", "pulse-muted"
    s = f"{v:+.2f}%"
    if v > 0:
        return s, "pulse-pos"
    if v < 0:
        return s, "pulse-neg"
    return s, "pulse-flat"


def _resolved_quality(res: fd.LoadResult, loaded: bool) -> tuple[str, str]:
    if not loaded:
        return "Missing", "bad"
    if res.resolved_source == "yahoo":
        return "Yahoo unofficial", "warn"
    if res.is_demo:
        return "CSV demo", "demo"
    return "CSV real", "ok"


def _next_action_text(res: fd.LoadResult, loaded: bool, data_src: str) -> str:
    pref = fp.normalize_data_src_preference(data_src)
    if fp.is_placeholder(pref):
        return fp.PROVIDER_NOT_CONFIGURED_MSG
    if loaded and res.resolved_source == "yahoo":
        return "Chart ready (unofficial Yahoo). Prefer CSV for production-style work."
    if loaded and res.is_demo:
        return "Replace demo CSV with vendor data, or pick Yahoo for a live proxy."
    if loaded:
        return "Chart ready."
    return "Import CSV or switch Data source to Yahoo if supported."


def _header_freshness(sym: str, tf: str, res: fd.LoadResult, loaded: bool) -> html.Div:
    """Compact freshness line under the site header (visible before scrolling to the chart)."""
    if not loaded:
        warn = (res.warning or "No OHLCV loaded").strip()
        return html.Div(
            [html.Span(f"{sym} · {tf}", className="hf-pill"), html.Span(warn, className="hf-warn")],
            className="header-freshness",
        )
    lb = getattr(res, "last_bar_display", "") or "—"
    src = RESOLVED_LABELS.get(res.resolved_source, res.resolved_source)
    pills = [
        html.Span(f"{sym} · {tf}", className="hf-pill"),
        html.Span(f"Last bar {lb}", className="hf-pill hf-pill--time"),
        html.Span(src, className="hf-pill"),
    ]
    if res.is_demo:
        pills.append(html.Span("Demo data", className="hf-pill hf-pill--demo"))
    elif res.resolved_source == "yahoo":
        pills.append(html.Span("Unofficial Yahoo", className="hf-pill hf-pill--warn"))
    return html.Div(pills, className="header-freshness")


def _top_summary_strip(sym: str, tf: str, data_src: str, res: fd.LoadResult, loaded: bool, prod) -> html.Div:
    pref = DATA_SRC_LABELS.get(data_src, data_src)
    qtxt, qkind = _resolved_quality(res, loaded)
    name = prod.name if prod else "Unknown"
    ac = prod.asset_class if prod else "Unknown"
    reason_el = None
    ar_txt = (getattr(res, "auto_reason", "") or "").strip()
    if ar_txt:
        reason_el = html.Div(ar_txt, className="sc-sub", style={"marginTop": "4px", "color": "#d1d5db"})
    return html.Div(
        [
            html.Div(
                [
                    html.Div("Data preference", className="sc-label"),
                    html.Div(pref, className="sc-value"),
                ],
                className="summary-card",
            ),
            html.Div(
                [
                    html.Div("Resolved source", className="sc-label"),
                    html.Div([_fbadge(qtxt, qkind)], className="sc-value"),
                    reason_el,
                ],
                className="summary-card",
            ),
            html.Div(
                [
                    html.Div("Selected product", className="sc-label"),
                    html.Div(f"{sym} · {tf}", className="sc-value"),
                    html.Div(f"{name} · {ac}", className="sc-sub"),
                ],
                className="summary-card",
            ),
            html.Div(
                [
                    html.Div("Next action", className="sc-label"),
                    html.Div(_next_action_text(res, loaded, data_src), className="sc-value"),
                ],
                className="summary-card",
            ),
        ],
        className="summary-strip",
    )


def _health_compact(
    sym: str,
    data_src: str,
    res: fd.LoadResult,
    loaded: bool,
    mtime_str: str,
    ok_dir: bool,
    dir_notes: list[str],
) -> html.Div:
    rs = RESOLVED_LABELS.get(res.resolved_source, res.resolved_source)
    dtype = "—"
    if loaded and res.resolved_source == "yahoo":
        dtype = "Yahoo (unofficial)"
    elif loaded and res.is_demo:
        dtype = "Demo CSV"
    elif loaded:
        dtype = "Vendor CSV"
    rows = [
        ("Mode", "Read-only charting (CSV + optional Yahoo)"),
        ("Data preference", DATA_SRC_LABELS.get(data_src, data_src)),
        ("Resolved source", rs),
        ("Reason", getattr(res, "auto_reason", "") or "—"),
        ("Selected symbol", sym),
        ("Last bar (UTC)", getattr(res, "last_bar_display", "") or "—"),
        ("Chart data", "Loaded" if loaded else "Missing"),
        ("Rows loaded", str(res.rows if loaded else 0)),
        ("Last file modified", mtime_str),
        ("Dataset type", dtype),
        ("Path", res.path),
    ]
    grid = []
    for lab, val in rows:
        cls = "ok" if lab == "Chart data" and loaded else ("bad" if lab == "Chart data" and not loaded else "")
        vcls = f"health-value {cls}".strip() if cls else "health-value"
        grid.append(html.Div(lab, className="health-label"))
        grid.append(html.Div(str(val), className=vcls))
    badges: list = []
    if loaded:
        badges.append(_fbadge("Loaded", "ok"))
    else:
        badges.append(_fbadge("Missing", "bad"))
    if loaded and res.is_demo:
        badges.append(_fbadge("Demo", "demo"))
    if loaded and res.resolved_source == "yahoo":
        badges.append(_fbadge("Yahoo unofficial", "warn"))
    notes = [html.Div(n, className="muted", style={"fontSize": "0.75rem", "marginTop": "4px"}) for n in dir_notes]
    warn_blocks: list = []
    if loaded and res.yahoo_note:
        warn_blocks.append(
            html.Div(
                res.yahoo_note,
                style={
                    "marginTop": "10px",
                    "padding": "8px 10px",
                    "borderRadius": "8px",
                    "background": "#422006",
                    "border": "1px solid #b45309",
                    "color": "#fde68a",
                    "fontSize": "0.78rem",
                    "fontWeight": "600",
                },
            )
        )
    if loaded and res.tf_warning:
        warn_blocks.append(
            html.Div(
                res.tf_warning,
                style={
                    "marginTop": "8px",
                    "padding": "6px 10px",
                    "borderRadius": "8px",
                    "background": "#422006",
                    "border": "1px solid #a16207",
                    "color": "#fde047",
                    "fontSize": "0.76rem",
                },
            )
        )
    if loaded and res.is_demo:
        warn_blocks.append(
            html.Div(
                DEMO_BANNER,
                style={
                    "marginTop": "8px",
                    "padding": "8px 10px",
                    "borderRadius": "8px",
                    "background": "#450a0a",
                    "border": "1px solid #991b1b",
                    "color": "#fecaca",
                    "fontSize": "0.8rem",
                    "fontWeight": "600",
                },
            )
        )
    return html.Div(
        [
            html.Div(grid, className="health-grid"),
            html.Div(
                [
                    _fbadge("Data folder OK", "ok") if ok_dir else _fbadge("Data folder issue", "bad"),
                ],
                className="health-badges",
            ),
            html.Div(badges, className="health-badges"),
            *notes,
            *warn_blocks,
        ]
    )


def _metadata_card(sym: str) -> html.Div:
    p = fu.get_product(sym)
    if not p:
        return html.Div(
            [
                html.Div("Name", className="meta-label"),
                html.Div("Unknown", className="meta-value"),
                html.Div("Asset class", className="meta-label"),
                html.Div("Unknown", className="meta-value"),
                html.Div("Point value ($/pt)", className="meta-label"),
                html.Div("Unknown", className="meta-value"),
                html.Div("Tick size", className="meta-label"),
                html.Div("Unknown", className="meta-value"),
                html.Div("Micro / parent", className="meta-label"),
                html.Div("Unknown", className="meta-value"),
            ],
            className="meta-grid",
        )
    par = (p.micro_parent or "").strip()
    micros = fu.micros_sharing_parent(p.symbol)
    micro_line = ", ".join(micros) if micros else "—"
    if par:
        rel = f"Parent product: {par}"
    else:
        rel = f"Micro equivalent(s): {micro_line}"
    return html.Div(
        [
            html.Div("Name", className="meta-label"),
            html.Div(p.name or "Unknown", className="meta-value"),
            html.Div("Asset class", className="meta-label"),
            html.Div(p.asset_class or "Unknown", className="meta-value"),
            html.Div("Point value ($/pt)", className="meta-label"),
            html.Div(_fmt_meta_num(p.point_value), className="meta-value"),
            html.Div("Tick size", className="meta-label"),
            html.Div(_fmt_meta_num(p.tick_size), className="meta-value"),
            html.Div("Micro / parent", className="meta-label"),
            html.Div(rel, className="meta-value"),
        ],
        className="meta-grid",
    )


def _micro_pulse_section() -> html.Div:
    rows = fmp.compute_micro_pulse_rows()
    ex = fmp.pulse_extreme_lines(rows)
    th = {"padding": "6px 8px"}
    hdr = html.Tr(
        [
            html.Th("Micro", style=th),
            html.Th("Parent", style=th),
            html.Th("Name", style=th),
            html.Th("Class", style=th),
            html.Th("1D %", style=th),
            html.Th("7D %", style=th),
            html.Th("30D %", style=th),
            html.Th("Source", style=th),
            html.Th("Status", style=th),
        ]
    )
    body: list = []
    for r in rows:
        c1, k1 = _fmt_pulse_pct(r.r1)
        c7, k7 = _fmt_pulse_pct(r.r7)
        c30, k30 = _fmt_pulse_pct(r.r30)
        st_cls = "pulse-muted"
        if r.status == "Demo":
            st_cls = "pulse-neg"
        elif r.status in ("Proxy parent", "Stale parent"):
            st_cls = "pulse-proxy"
        elif r.status in ("Stale local",):
            st_cls = "pulse-proxy"
        elif r.status == "Loaded":
            st_cls = "pulse-flat"
        elif r.status == "Unsupported":
            st_cls = "pulse-proxy"
        body.append(
            html.Tr(
                [
                    html.Td(r.micro, style=th),
                    html.Td(r.parent, style=th),
                    html.Td(r.name, style={**th, "maxWidth": "200px", "whiteSpace": "normal"}),
                    html.Td(r.asset_class, style=th),
                    html.Td(c1, className=k1, style=th),
                    html.Td(c7, className=k7, style=th),
                    html.Td(c30, className=k30, style=th),
                    html.Td(r.source, style=th),
                    html.Td(r.status, className=st_cls, style=th),
                ]
            )
        )
    table = html.Table([html.Thead(hdr), html.Tbody(body)], className="pulse-table")
    lead = html.Div(
        [
            html.Span([html.B("Strongest 7D: "), " ", ex["strong_7"]]),
            html.Span([html.B("Weakest 7D: "), " ", ex["weak_7"]]),
            html.Span([html.B("Strongest 30D: "), " ", ex["strong_30"]]),
            html.Span([html.B("Weakest 30D: "), " ", ex["weak_30"]]),
        ],
        className="pulse-lead",
    )
    return html.Div(
        [
            lead,
            html.Div(table, className="pulse-table-wrap"),
        ]
    )


def _coverage_summary_block(cov_full: list[fd.CoverageRow]) -> html.Div:
    n = len(cov_full)
    real = sum(1 for r in cov_full if r.demo_or_real == "Real")
    demo = sum(1 for r in cov_full if r.demo_or_real == "Demo")
    tfs = sorted({r.timeframe for r in cov_full})
    syms = sorted({r.symbol for r in cov_full})
    return html.Div(
        [
            html.Span([html.B("Files: "), str(n)]),
            html.Span([html.B("Real: "), str(real)]),
            html.Span([html.B("Demo: "), str(demo)]),
            html.Span([html.B("TFs: "), ", ".join(tfs) if tfs else "—"]),
            html.Span([html.B("Symbols: "), ", ".join(syms) if syms else "—"]),
        ],
        className="cov-summary",
    )


def _chart_slot_children(
    loaded: bool,
    fig,
    sym: str,
    tf: str,
    res: fd.LoadResult,
) -> list:
    if loaded:
        return [
            dcc.Graph(
                id="chart-futures",
                figure=fig,
                config={"displayModeBar": True, "scrollZoom": True},
                style={"height": "560px"},
            )
        ]
    rel = f"futures_dashboard/data/{sym}_{tf}.csv"
    return [
        html.Div(
            [
                html.Div(f"No chart data for {sym} {tf}", className="empty-chart-title"),
                html.Div("Expected CSV path:", className="muted", style={"fontSize": "0.8rem", "marginTop": "6px"}),
                html.Code(rel, className="empty-chart-code"),
                html.Div(
                    "Expected columns: time, open, high, low, close, volume",
                    className="muted",
                    style={"marginTop": "8px", "fontSize": "0.82rem"},
                ),
                html.Div("Suggested next actions:", className="muted", style={"marginTop": "10px", "fontSize": "0.8rem"}),
                html.Ol(
                    [
                        html.Li("Import CSV (futures_import.py)", style={"fontSize": "0.8rem"}),
                        html.Li("Switch Data source to Yahoo Finance if supported for this symbol.", style={"fontSize": "0.8rem"}),
                    ],
                    style={"marginTop": "6px", "paddingLeft": "18px", "color": "#9ca3af", "fontSize": "0.8rem"},
                ),
                html.Div(
                    html.Code(res.warning or "—", style={"whiteSpace": "pre-wrap", "fontSize": "0.75rem"}),
                    className="warn",
                    style={"marginTop": "10px"},
                ),
            ],
            className="empty-chart-card",
        )
    ]


app.layout = html.Div(
    [
        html.Div(
            [
                html.H1("Futures Decision Cockpit", className="site-title"),
                html.Div(
                    [
                        html.Span("READ ONLY / NO EXECUTION", className="badge-paper"),
                        html.Span(
                            "Phase 4.5 — Micro Product Pulse + UI refresh. No broker connection.",
                            className="badge-sub",
                        ),
                    ],
                    className="paper-banner",
                ),
                html.Div(id="header-freshness", className="header-freshness", children="Loading chart status…"),
            ],
            className="header",
        ),
        html.Div(
            [
                html.Div(id="section-top-summary", className="card"),
                html.Div(
                    [
                        html.Div("System Health", className="section-title"),
                        html.Div(id="section-health"),
                    ],
                    className="card health-card",
                ),
                html.Div(
                    [
                        html.Div("Selected contract", className="section-title"),
                        html.Div(id="section-metadata"),
                    ],
                    className="card",
                ),
                html.Div(
                    [
                        html.Div("Micro Product Pulse", className="section-title"),
                        html.P(
                            "Market overview only, not a trade signal. Uses CSV when present, else Yahoo "
                            "(parent proxy when micro data is unavailable). Refreshes on load, Reload, and every 5 minutes.",
                            className="muted",
                            style={"marginBottom": "12px", "fontSize": "0.82rem"},
                        ),
                        dcc.Loading(
                            html.Div(id="section-micro-pulse"),
                            type="circle",
                            color="#f59e0b",
                        ),
                        dcc.Interval(
                            id="pulse-interval",
                            interval=5 * 60 * 1000,  # refresh every 5 min
                            n_intervals=0,
                        ),
                    ],
                    className="card",
                ),
                html.Details(
                    [
                        html.Summary("Data coverage", className="details-summary"),
                        html.P(
                            "Files in futures_dashboard/data/ matching {SYMBOL}_{TIMEFRAME}.csv "
                            f"({', '.join(fd.SUPPORTED_TIMEFRAMES)}).",
                            className="muted",
                            style={"marginBottom": "8px"},
                        ),
                        html.Div(
                            [
                                html.Div(
                                    [
                                        html.Div(
                                            [
                                                html.Label("Class", className="input-label"),
                                                dcc.Dropdown(
                                                    id="cov-flt-asset",
                                                    options=_cov_asset_options,
                                                    value=COV_FLT_ALL,
                                                    clearable=False,
                                                    className="dd-dark dd-cov",
                                                ),
                                            ],
                                            className="ctrl-group",
                                        ),
                                        html.Div(
                                            [
                                                html.Label("Symbol", className="input-label"),
                                                dcc.Dropdown(
                                                    id="cov-flt-symbol",
                                                    options=_cov_sym_options,
                                                    value=COV_FLT_ALL,
                                                    clearable=False,
                                                    className="dd-dark dd-cov",
                                                ),
                                            ],
                                            className="ctrl-group",
                                        ),
                                        html.Div(
                                            [
                                                html.Label("TF", className="input-label"),
                                                dcc.Dropdown(
                                                    id="cov-flt-tf",
                                                    options=_cov_tf_options,
                                                    value=COV_FLT_ALL,
                                                    clearable=False,
                                                    className="dd-dark dd-cov",
                                                ),
                                            ],
                                            className="ctrl-group",
                                        ),
                                        html.Div(
                                            [
                                                html.Label("Data", className="input-label"),
                                                dcc.Dropdown(
                                                    id="cov-flt-demo",
                                                    options=_cov_demo_options,
                                                    value=COV_FLT_ALL,
                                                    clearable=False,
                                                    className="dd-dark dd-cov",
                                                ),
                                            ],
                                            className="ctrl-group",
                                        ),
                                    ],
                                    className="controls-row",
                                    style={"flexWrap": "wrap", "marginBottom": "6px"},
                                ),
                                html.Div(id="section-coverage"),
                            ],
                        ),
                    ],
                    className="card",
                    open=False,
                ),
                html.Div(
                    [
                        html.Div("Trend context", className="section-title"),
                        html.Div(
                            "Trend context only, not a trade signal.",
                            className="muted",
                            style={"marginBottom": "10px"},
                        ),
                        html.Div(id="section-trend"),
                    ],
                    className="card",
                ),
                html.Div(
                    [
                        html.H2("Futures Chart Review", className="section-title"),
                        html.P(
                            "CSV is the preferred path for serious work. Yahoo Finance is unofficial—use for "
                            "charting and prototyping only. Rollover / continuous contract logic not implemented for CSV.",
                            className="muted",
                            style={"marginBottom": "12px"},
                        ),
                        html.Div(
                            [
                                html.Div(
                                    [
                                        html.Label("Symbol", className="input-label"),
                                        dcc.Dropdown(
                                            id="dd-symbol",
                                            options=fu.symbols_for_dropdown(),
                                            value=DEFAULT_SYMBOL,
                                            clearable=False,
                                            className="dd-dark",
                                        ),
                                    ],
                                    className="ctrl-group",
                                ),
                                html.Div(
                                    [
                                        html.Label("Timeframe", className="input-label"),
                                        dcc.Dropdown(
                                            id="dd-timeframe",
                                            options=[{"label": x, "value": x} for x in fd.SUPPORTED_TIMEFRAMES],
                                            value=DEFAULT_TIMEFRAME,
                                            clearable=False,
                                            className="dd-dark",
                                        ),
                                    ],
                                    className="ctrl-group",
                                ),
                                html.Div(
                                    [
                                        html.Label("Data source", className="input-label"),
                                        dcc.Dropdown(
                                            id="dd-data-src",
                                            options=DATA_SRC_OPTIONS,
                                            value=DEFAULT_DATA_SRC,
                                            clearable=False,
                                            className="dd-dark",
                                        ),
                                    ],
                                    className="ctrl-group",
                                ),
                                html.Div(
                                    [
                                        html.Label("Lookback (bars)", className="input-label"),
                                        dcc.Dropdown(
                                            id="dd-lookback",
                                            options=[
                                                {"label": "90 bars", "value": 90},
                                                {"label": "180 bars", "value": 180},
                                                {"label": "300 bars", "value": 300},
                                            ],
                                            value=DEFAULT_LOOKBACK,
                                            clearable=False,
                                            className="dd-dark",
                                        ),
                                    ],
                                    className="ctrl-group",
                                ),
                                html.Div(
                                    [
                                        html.Label("\u00a0", className="input-label"),
                                        html.Button("Reload", id="btn-reload", className="btn-secondary", n_clicks=0),
                                    ],
                                    className="ctrl-group",
                                ),
                            ],
                            className="controls-row",
                        ),
                        html.Details(
                            [
                                html.Summary("Data provider notes", className="details-summary"),
                                html.Ul(
                                    [
                                        html.Li("CSV = serious local data path"),
                                        html.Li("Yahoo = unofficial prototype data"),
                                        html.Li(
                                            "IBKR = possible cheap live data if account/subscription is configured later"
                                        ),
                                        html.Li("Databento = possible research-grade data provider later"),
                                    ],
                                    className="muted",
                                    style={
                                        "marginTop": "8px",
                                        "marginBottom": "4px",
                                        "fontSize": "0.82rem",
                                        "paddingLeft": "18px",
                                    },
                                ),
                            ],
                            open=False,
                            style={"marginTop": "10px", "marginBottom": "8px"},
                        ),
                        dcc.Loading(
                            html.Div(
                                id="chart-slot",
                                children=[
                                    html.Div(
                                        "Chart loads after the first refresh.",
                                        className="muted",
                                        style={"padding": "24px"},
                                    )
                                ],
                            ),
                            type="circle",
                            color="#3b82f6",
                        ),
                        html.Div(id="div-chart-status", className="muted", style={"marginTop": "8px"}),
                        html.Details(
                            [
                                html.Summary("Manual trade plan overlay", className="details-summary"),
                                html.P(
                                    "Display only. Usable for long or short review. Not a recommendation or signal.",
                                    className="muted",
                                ),
                                html.P(
                                    "Optional horizontal lines on the chart when data is loaded.",
                                    className="muted",
                                    style={"fontSize": "0.78rem", "marginBottom": "10px"},
                                ),
                                html.Div(
                                    [
                                        html.Div(
                                            [
                                                html.Label("Entry", className="input-label"),
                                                dcc.Input(id="inp-entry", type="number", className="inp-dark", debounce=True),
                                            ],
                                            className="ctrl-group",
                                        ),
                                        html.Div(
                                            [
                                                html.Label("Stop", className="input-label"),
                                                dcc.Input(id="inp-stop", type="number", className="inp-dark", debounce=True),
                                            ],
                                            className="ctrl-group",
                                        ),
                                        html.Div(
                                            [
                                                html.Label("Target", className="input-label"),
                                                dcc.Input(id="inp-target", type="number", className="inp-dark", debounce=True),
                                            ],
                                            className="ctrl-group",
                                        ),
                                        html.Div(
                                            [
                                                html.Label("\u00a0", className="input-label"),
                                                html.Button("Apply overlay", id="btn-overlay", className="btn-secondary", n_clicks=0),
                                            ],
                                            className="ctrl-group",
                                        ),
                                        html.Div(
                                            [
                                                html.Label("\u00a0", className="input-label"),
                                                html.Button("Clear overlay", id="btn-clear", className="btn-secondary", n_clicks=0),
                                            ],
                                            className="ctrl-group",
                                        ),
                                    ],
                                    className="controls-row",
                                ),
                            ],
                            className="details-block",
                        ),
                    ],
                    className="card",
                ),
                html.Details(
                    [
                        html.Summary("Raw API / Debug", className="details-summary"),
                        html.Pre(id="debug-raw", className="muted", style={"fontSize": "0.78rem", "whiteSpace": "pre-wrap"}),
                    ],
                    className="card",
                ),
            ],
            className="main-content",
        ),
    ],
    className="page-root",
)


@app.callback(
    Output("section-micro-pulse", "children"),
    Input("pulse-interval", "n_intervals"),
    Input("btn-reload", "n_clicks"),
    prevent_initial_call=False,
)
def refresh_pulse(_n_intervals, _reload):
    try:
        return _micro_pulse_section()
    except Exception as exc:  # noqa: BLE001
        return html.Div(f"Pulse error: {exc}", className="warn", style={"fontSize": "0.8rem"})


@app.callback(
    Output("chart-slot", "children"),
    Output("section-top-summary", "children"),
    Output("header-freshness", "children"),
    Output("section-health", "children"),
    Output("section-metadata", "children"),
    Output("section-trend", "children"),
    Output("div-chart-status", "children"),
    Output("debug-raw", "children"),
    Output("inp-entry", "value"),
    Output("inp-stop", "value"),
    Output("inp-target", "value"),
    Output("section-coverage", "children"),
    Input("dd-symbol", "value"),
    Input("dd-timeframe", "value"),
    Input("dd-data-src", "value"),
    Input("dd-lookback", "value"),
    Input("btn-reload", "n_clicks"),
    Input("btn-overlay", "n_clicks"),
    Input("btn-clear", "n_clicks"),
    Input("cov-flt-asset", "value"),
    Input("cov-flt-symbol", "value"),
    Input("cov-flt-tf", "value"),
    Input("cov-flt-demo", "value"),
    State("inp-entry", "value"),
    State("inp-stop", "value"),
    State("inp-target", "value"),
)
def refresh_chart(
    sym,
    tf,
    data_src,
    lookback,
    _reload,
    _overlay,
    _clear,
    cov_a,
    cov_s,
    cov_tf,
    cov_demo,
    inp_e,
    inp_s,
    inp_t,
):
    sym = sym or DEFAULT_SYMBOL
    tf  = tf or DEFAULT_TIMEFRAME
    lb  = int(lookback or DEFAULT_LOOKBACK)
    data_src = fp.normalize_data_src_preference(data_src or DEFAULT_DATA_SRC)

    trig = ctx.triggered_id
    if trig == "btn-clear":
        inp_e = inp_s = inp_t = None
        out_e, out_s, out_t = None, None, None
    else:
        out_e, out_s, out_t = no_update, no_update, no_update

    cov_full = fd.scan_data_coverage(fu.asset_class_lookup())
    cov_rows = _filter_coverage_rows(cov_full, cov_a, cov_s, cov_tf, cov_demo)
    coverage = html.Div([_coverage_summary_block(cov_full), _coverage_table(cov_rows)])

    ok_dir, dir_notes = fd.validate_data_folder()

    res = _resolve_chart(sym, tf, lb, data_src)
    df  = res.df
    prod = fu.get_product(sym)

    mtime_str = res.mtime.strftime("%Y-%m-%d %H:%M UTC") if res.mtime else "—"
    loaded    = not df.empty and res.rows > 0

    top = _top_summary_strip(sym, tf, data_src, res, loaded, prod)
    health = _health_compact(sym, data_src, res, loaded, mtime_str, ok_dir, dir_notes)
    meta = _metadata_card(sym)

    demo_note = None
    if loaded and res.is_demo:
        demo_note = html.Div(
            DEMO_BANNER + " — indicators below are for layout testing only.",
            style={
                "marginBottom": "12px",
                "padding": "8px 12px",
                "background": "#451a03",
                "border": "1px solid #92400e",
                "borderRadius": "6px",
                "color": "#fcd34d",
                "fontSize": "0.82rem",
            },
        )

    if not loaded:
        chart_children = _chart_slot_children(False, None, sym, tf, res)
        trend_inner = html.Div("No data for trend context.", className="muted")
        trend = html.Div([demo_note, trend_inner] if demo_note else [trend_inner])
        msg = html.Div(
            [
                html.Span(f"No chart data · {sym} {tf}. ", style={"fontWeight": "600"}),
                html.Span("See the empty-state card above for CSV path and next steps.", className="muted"),
                html.Br(),
                html.Span(res.warning or "", className="warn", style={"fontSize": "0.82rem"}),
            ]
        )
        dec = (getattr(res, "source_decision_log", "") or "").strip()
        dbg = (
            (dec + "\n---\n" if dec else "")
            + f"symbol={sym} tf={tf} data_src={data_src}\nresolved={res.resolved_source}\n"
            f"path={res.path}\ntrigger={trig}\nwarning={res.warning}\n"
            f"auto_reason={getattr(res, 'auto_reason', '')}\nlast_bar={getattr(res, 'last_bar_display', '')}\n"
            f"yahoo_note={res.yahoo_note}\ntf_warning={res.tf_warning}\n"
            f"overlay entry={inp_e} stop={inp_s} target={inp_t}"
        )
        hf = _header_freshness(sym, tf, res, loaded)
        return chart_children, top, hf, health, meta, trend, msg, dbg, out_e, out_s, out_t, coverage

    metrics = fc.trend_metrics(df)
    last    = metrics.get("last_close")
    trend_inner = html.Div(
        [
            _row("Last close", format_price(last)),
            _row("vs 20 EMA", _bool_txt(metrics.get("above_ema20"))),
            _row("vs 50 EMA", _bool_txt(metrics.get("above_ema50"))),
            _row("vs 200 SMA", _bool_txt(metrics.get("above_sma200"))),
            _row("1-bar return (~1 session)", _pct(metrics.get("ret_1d"))),
            _row("5-bar return", _pct(metrics.get("ret_5d"))),
            _row("20-bar return", _pct(metrics.get("ret_20d"))),
            _row("ATR(14) % of price", f"{metrics['atr_pct']:.2f}%" if metrics.get("atr_pct") else "—"),
        ]
    )
    trend = html.Div([demo_note, trend_inner] if demo_note else [trend_inner])

    entry  = float(inp_e) if inp_e not in (None, "") else None
    stop   = float(inp_s) if inp_s not in (None, "") else None
    target = float(inp_t) if inp_t not in (None, "") else None
    cur    = float(df["close"].iloc[-1]) if not df.empty else None

    banner = DEMO_BANNER if res.is_demo else None
    ybanner = res.yahoo_note if res.yahoo_note else None
    fig = fc.build_futures_chart(
        df, sym, tf,
        entry_price=entry,
        stop_price=stop,
        target_price=target,
        current_price=cur,
        display_last=lb,
        demo_banner=banner,
        unofficial_banner=ybanner,
    )
    chart_children = _chart_slot_children(True, fig, sym, tf, res)

    lb_txt = getattr(res, "last_bar_display", "") or "—"
    src_lbl = RESOLVED_LABELS.get(res.resolved_source, res.resolved_source)
    msg_parts: list = [
        html.Span(
            f"Loaded {res.rows} rows · last bar {lb_txt} · source: {src_lbl} · showing last {lb} bars.",
            style={"marginRight": "12px"},
        )
    ]
    if res.yahoo_note:
        msg_parts.append(
            html.Span(" " + res.yahoo_note, style={"color": "#fbbf24", "fontWeight": "700"}),
        )
    if res.is_demo:
        msg_parts.append(
            html.Span(" " + DEMO_BANNER, style={"color": "#f87171", "fontWeight": "700"}),
        )
    if res.tf_warning:
        msg_parts.append(
            html.Span(
                " " + res.tf_warning,
                style={"marginLeft": "8px", "color": "#facc15", "fontWeight": "600"},
            )
        )
    if (res.warning or "").strip():
        msg_parts.append(html.Br())
        msg_parts.append(
            html.Span(res.warning.strip(), className="warn", style={"fontSize": "0.82rem"}),
        )
    msg = html.Div(msg_parts)
    dec = (getattr(res, "source_decision_log", "") or "").strip()
    dbg = (
        (dec + "\n---\n" if dec else "")
        + f"symbol={sym} tf={tf} data_src={data_src}\nresolved={res.resolved_source}\n"
        f"path={res.path}\nrows={res.rows}\nis_demo={res.is_demo}\ntrigger={trig}\n"
        f"auto_reason={getattr(res, 'auto_reason', '')}\nlast_bar={getattr(res, 'last_bar_display', '')}\n"
        f"yahoo_note={res.yahoo_note}\ntf_warning={res.tf_warning}\n"
        f"warning={res.warning}\n"
        f"overlay entry={inp_e} stop={inp_s} target={inp_t}"
    )
    hf = _header_freshness(sym, tf, res, loaded)
    return chart_children, top, hf, health, meta, trend, msg, dbg, out_e, out_s, out_t, coverage


if __name__ == "__main__":
    import futures_yfinance as _fy_check
    log.info("=== Futures Decision Cockpit startup ===")
    log.info("CWD: %s", os.getcwd())
    log.info("Data dir: %s", fd.DATA_DIR)
    log.info("ES_1D.csv exists: %s", os.path.exists(fd.csv_path("ES", "1D")))
    log.info("yfinance available: %s", _fy_check.yfinance_installed())
    bind_host = os.environ.get("RRG_BIND_HOST", "127.0.0.1")
    log.info("Listening on http://%s:%s", bind_host, PORT)
    app.run(host=bind_host, port=PORT, debug=False)
