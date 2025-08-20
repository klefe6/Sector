#!/usr/bin/env python3
# app_rrg.py

from functools import lru_cache
import numpy as np
import pandas as pd
import yfinance as yf

import dash
from dash import Dash, dcc, html, Input, Output, State
from dash import dash_table

import plotly.graph_objects as go
import plotly.express as px

# ───────── CONFIG ─────────
BENCHMARK = "SPY"
SECTORS   = ["XLV","XLRE","XLB","XLU","XLP","XLF","XLY","XLE","XLI","XLC","XLK"]
SECTOR_NAMES = {
    "XLV":"Health Care","XLRE":"Real Estate","XLB":"Materials","XLU":"Utilities",
    "XLP":"Consumer Staples","XLF":"Financials","XLY":"Consumer Discretionary",
    "XLE":"Energy","XLI":"Industrials","XLC":"Communications","XLK":"Technology"
}
START_DEFAULT      = "2015-01-01"
DEFAULT_FREQ       = "W"          # 'D' or 'W'
DEFAULT_SHORT_WMA  = 10
DEFAULT_LONG_WMA   = 30

SEASONALITY_DEFAULT_START = 2000   # shown & editable
SEASONALITY_DEFAULT_AGG   = "mean" # 'mean' or 'median'

COLOR_SEQ = px.colors.qualitative.Plotly
COLOR_MAP = {t: COLOR_SEQ[i % len(COLOR_SEQ)] for i, t in enumerate(SECTORS)}

# ───────── HELPERS ─────────
def wma(series, n):
    w = np.arange(1, n + 1)
    return series.rolling(n).apply(lambda x: np.dot(x, w) / w.sum(), raw=True)

@lru_cache(maxsize=16)
def load_prices(freq, start=START_DEFAULT, end=None,
                tickers=tuple(SECTORS+[BENCHMARK])):
    raw = yf.download(list(tickers), start=start, end=end,
                      progress=False, auto_adjust=False, actions=False)
    df = raw['Adj Close'] if isinstance(raw.columns, pd.MultiIndex) else raw
    if freq.upper() == "W":
        df = df.resample("W-FRI").last()
    return df.dropna(how="all")

def calc_rrg(px, benchmark, short_len, long_len):
    rs = px.div(px[benchmark], axis=0)
    rs_s = rs.apply(lambda s: wma(s, short_len))
    rs_l = rs.apply(lambda s: wma(s, long_len))
    rs_ratio = 100 + 100 * (rs_s / rs_l - 1)
    mom_wma  = rs_ratio.apply(lambda s: wma(s, short_len))
    rs_mom   = 100 + 100 * (rs_ratio / mom_wma - 1)
    return rs_ratio.drop(columns=[benchmark]), rs_mom.drop(columns=[benchmark])

@lru_cache(maxsize=64)
def seasonality_table(tickers_tuple, start_year=2000, agg="mean"):
    start = f"{start_year}-01-01"
    px_daily = load_prices("D", start=start, tickers=tickers_tuple)
    rets = px_daily.pct_change().dropna()
    rets["Month"] = rets.index.month
    func = np.nanmean if agg == "mean" else np.nanmedian
    monthly = rets.groupby("Month").agg(func) * 100
    monthly.index = ["Jan","Feb","Mar","Apr","May","Jun",
                     "Jul","Aug","Sep","Oct","Nov","Dec"]
    return monthly

def make_marks(idx, step):
    return {i: idx[i].strftime("%Y-%m-%d") for i in range(0, len(idx), step)}

def quadrant(rr, rm):
    if rr >= 100 and rm >= 100: return "Leading"
    if rr >= 100 and rm < 100:  return "Weakening"
    if rr < 100  and rm < 100:  return "Lagging"
    return "Improving"

def safe_bounds(arr, center=100, span=10):
    arr = np.asarray(arr, dtype=float)
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return center - span, center + span
    low, high = float(finite.min()), float(finite.max())
    if low == high: low -= 1; high += 1
    return low, high

# ───────── PRELOAD ─────────
_init_prices = load_prices(DEFAULT_FREQ)
RSR_INIT, RSM_INIT = calc_rrg(_init_prices, BENCHMARK,
                              DEFAULT_SHORT_WMA, DEFAULT_LONG_WMA)
DATES_INIT    = RSR_INIT.index
LAST_IDX_INIT = len(DATES_INIT) - 1

check_opts = [{
    "label": html.Span(f"{t} – {SECTOR_NAMES[t]}", style={"color": COLOR_MAP[t]}),
    "value": t
} for t in SECTORS]

# ───────── APP ─────────
app = Dash(__name__, title="Sector RRG")
server = app.server

LEFT_STYLE = {
    "width":"430px","minWidth":"220px","maxWidth":"65%",
    "padding":"12px 14px","overflowY":"auto",
    "borderRight":"1px solid #ddd",
    "resize":"horizontal"          # CSS-only drag
}

app.layout = html.Div(
    style={"display":"flex","height":"100vh","fontFamily":"sans-serif","width":"100%"},
    children=[
        # LEFT PANEL
        html.Div(
            id="left-panel",
            style=LEFT_STYLE,
            children=[
                html.H4("Sectors", style={"marginTop":"0"}),
                dcc.Checklist(id="tickers", options=check_opts, value=SECTORS,
                              labelStyle={"display":"block","marginBottom":"4px"}),

                html.Hr(),

                html.Label("Data frequency"),
                dcc.RadioItems(id="freq",
                    options=[{"label":"Weekly","value":"W"},
                             {"label":"Daily","value":"D"}],
                    value=DEFAULT_FREQ, inline=True),

                html.Label("Smoothing (short / long)"),
                html.Div([
                    dcc.Input(id="short_w", type="number", value=DEFAULT_SHORT_WMA,
                              min=2, max=50, step=1,
                              style={"width":"60px","marginRight":"8px"}),
                    dcc.Input(id="long_w", type="number", value=DEFAULT_LONG_WMA,
                              min=3, max=100, step=1, style={"width":"60px"})
                ], style={"marginBottom":"8px"}),

                html.H5("Quadrants (current bar)"),
                dash_table.DataTable(
                    id="quad_table",
                    columns=[{"name":"Ticker","id":"Ticker"},
                             {"name":"Name","id":"Name"},
                             {"name":"Quadrant","id":"Quadrant"},
                             {"name":"RS-Ratio","id":"RSR"},
                             {"name":"RS-Mom.","id":"RSM"}],
                    data=[],
                    style_table={"maxHeight":"45vh","overflowY":"auto"},
                    style_cell={"padding":"4px","fontSize":"12px"},
                    style_header={"fontWeight":"600","backgroundColor":"#f5f5f5"},
                    style_data_conditional=[
                        {"if":{"filter_query":"{Quadrant} = 'Leading'"},   "backgroundColor":"#e8f8e8"},
                        {"if":{"filter_query":"{Quadrant} = 'Lagging'"},   "backgroundColor":"#fdecea"},
                        {"if":{"filter_query":"{Quadrant} = 'Improving'"}, "backgroundColor":"#e8f2ff"},
                        {"if":{"filter_query":"{Quadrant} = 'Weakening'"}, "backgroundColor":"#fff7e6"},
                    ]
                ),

                html.Div(id="current_period_left",
                         style={"fontSize":"22px","fontWeight":"700","marginTop":"12px"}),

                html.Hr(),

                html.Label("Label at arrow"),
                dcc.RadioItems(id="label_toggle",
                    options=[{"label":"Hide","value":"hide"},
                             {"label":"Show","value":"show"}],
                    value="show", inline=True),

                html.Hr(),

                html.Label("Seasonality"),
                dcc.RadioItems(id="seasonality_toggle",
                    options=[{"label":"Hide","value":"hide"},
                             {"label":"Show","value":"show"}],
                    value="show", inline=True),

                html.Div([
                    html.Label("Aggregation"),
                    dcc.RadioItems(id="seas_agg",
                        options=[{"label":"Average","value":"mean"},
                                 {"label":"Median","value":"median"}],
                        value=SEASONALITY_DEFAULT_AGG, inline=True),
                    html.Label("Start year"),
                    dcc.Input(id="seas_start", type="number",
                              value=SEASONALITY_DEFAULT_START,
                              min=1980, max=pd.Timestamp.today().year,
                              step=1, style={"width":"90px","marginLeft":"6px"})
                ], style={"marginTop":"6px"})
            ]
        ),

        # RIGHT PANEL
        html.Div(
            style={"flex":"1","padding":"12px 16px","display":"flex","flexDirection":"column","minWidth":0},
            children=[
                html.H3("Sector RRG (DIY clone)", style={"marginTop":"0"}),

                html.Div([
                    html.Div([
                        html.Label("Time slider"),
                        dcc.Slider(id="time_idx", min=0, max=LAST_IDX_INIT, value=LAST_IDX_INIT,
                                   step=1, marks=make_marks(DATES_INIT, max(1,len(DATES_INIT)//10)),
                                   tooltip={"placement":"bottom"})
                    ], style={"flex":"3","paddingRight":"16px"}),

                    html.Div([
                        html.Label("Tail length"),
                        dcc.Slider(id="tail_len", min=1, max=26, step=1, value=8,
                                   tooltip={"placement":"bottom"})
                    ], style={"flex":"1","paddingRight":"16px"}),

                    html.Div([
                        html.Button("▶ Play", id="play_btn", n_clicks=0, style={"width":"80px"}),
                        dcc.Interval(id="play_timer", interval=800, disabled=True)
                    ], style={"display":"flex","flexDirection":"column","justifyContent":"flex-end"})
                ], style={"display":"flex","alignItems":"flex-end","marginBottom":"8px"}),

                html.Div(id="current_period", style={"fontWeight":"600","marginBottom":"6px"}),

                dcc.Graph(id="rrg_graph", style={"flex":"1"}),

                dcc.Graph(id="seasonality_graph", style={"height":"280px","display":"none"}),

                html.Div(id="dbg", style={"fontSize":"11px","color":"#666","marginTop":"4px"})
            ]
        )
    ]
)

# ───────── CALLBACKS ─────────
@app.callback(
    Output("play_timer","disabled"),
    Output("play_btn","children"),
    Input("play_btn","n_clicks"),
    State("play_timer","disabled"),
    prevent_initial_call=True
)
def toggle_play(n, disabled):
    new_state = not disabled
    return new_state, ("❚❚ Pause" if not new_state else "▶ Play")

@app.callback(
    Output("time_idx","value"),
    Input("play_timer","n_intervals"),
    State("time_idx","value"),
    State("time_idx","max"),
    prevent_initial_call=True
)
def step_slider(_, cur, mx):
    return 0 if cur >= mx else cur + 1

@app.callback(
    Output("rrg_graph","figure"),
    Output("quad_table","data"),
    Output("current_period","children"),
    Output("current_period_left","children"),
    Output("dbg","children"),
    Output("time_idx","max"),
    Output("time_idx","marks"),
    Output("seasonality_graph","figure"),
    Output("seasonality_graph","style"),
    Input("tickers","value"),
    Input("tail_len","value"),
    Input("time_idx","value"),
    Input("freq","value"),
    Input("short_w","value"),
    Input("long_w","value"),
    Input("seasonality_toggle","value"),
    Input("label_toggle","value"),
    Input("seas_agg","value"),
    Input("seas_start","value")
)
def update(sel_tickers, tail_len, t_idx, freq, sw, lw,
           seas_tog, label_tog, seas_agg, seas_start):

    if not sel_tickers:
        empty = go.Figure()
        return (empty, [], "", "", "No tickers selected.",
                0, {}, empty, {"display":"none"})

    sw = int(sw) if sw else DEFAULT_SHORT_WMA
    lw = int(lw) if lw else DEFAULT_LONG_WMA
    if lw <= sw: lw = sw + 1

    prices = load_prices(freq)
    rsr_full, rsm_full = calc_rrg(prices, BENCHMARK, sw, lw)
    dates   = rsr_full.index
    max_idx = len(dates) - 1

    t_idx = min(t_idx, max_idx)

    rratio = rsr_full.iloc[:t_idx+1][sel_tickers]
    rmom   = rsm_full.iloc[:t_idx+1][sel_tickers]
    idx    = rratio.index
    tail_len = max(1, min(tail_len, len(idx)))

    fig = go.Figure()
    vx, vy = [], []

    for t in sel_tickers:
        tx = rratio[t].values[-tail_len:]
        ty = rmom[t].values[-tail_len:]
        td = idx[-tail_len:]
        c  = COLOR_MAP[t]

        vx.extend(tx); vy.extend(ty)

        alphas = np.linspace(0.25,1.0,len(tx))
        widths = np.linspace(1.0,4.0,len(tx))
        sizes  = np.linspace(4,10,len(tx))

        fig.add_trace(go.Scatter(
            x=tx, y=ty, mode="markers", name=t,
            marker=dict(color=c, size=sizes, opacity=alphas),
            text=[d.strftime("%Y-%m-%d") for d in td],
            hovertemplate=(f"<b>{t} - {SECTOR_NAMES[t]}</b><br>"
                           "Date: %{text}<br>RS-Ratio: %{x:.2f}<br>"
                           "RS-Momentum: %{y:.2f}<extra></extra>")
        ))
        for i in range(1,len(tx)):
            fig.add_trace(go.Scatter(
                x=tx[i-1:i+1], y=ty[i-1:i+1], mode="lines",
                line=dict(color=c, width=widths[i]),
                opacity=float(alphas[i]), showlegend=False, hoverinfo="skip"
            ))
        fig.add_trace(go.Scatter(
            x=[tx[0]], y=[ty[0]], mode="markers",
            marker=dict(size=6, symbol="circle-open", line=dict(color=c)),
            showlegend=False, hoverinfo="skip", opacity=alphas[0]
        ))
        if len(tx) > 1:
            fig.add_annotation(
                x=tx[-1], y=ty[-1], ax=tx[-2], ay=ty[-2],
                xref="x", yref="y", axref="x", ayref="y",
                showarrow=True, arrowhead=2, arrowsize=2,
                arrowwidth=2, arrowcolor=c
            )
            if label_tog == "show":
                fig.add_annotation(
                    x=tx[-1], y=ty[-1], xshift=6, yshift=6,
                    text=SECTOR_NAMES[t], showarrow=False,
                    font=dict(size=11, color=c),
                    bgcolor="rgba(255,255,255,0.6)",
                    bordercolor=c, borderwidth=0.5,
                    opacity=0.9
                )

    x_min, x_max = safe_bounds(vx)
    y_min, y_max = safe_bounds(vy)
    pad_x = (x_max - x_min)*0.08
    pad_y = (y_max - y_min)*0.08
    x_min = min(x_min,100)-pad_x; x_max = max(x_max,100)+pad_x
    y_min = min(y_min,100)-pad_y; y_max = max(y_max,100)+pad_y

    shapes = [
        dict(type="rect",xref="x",yref="y",x0=100,x1=x_max,y0=100,y1=y_max,
             fillcolor="rgba(0,153,0,0.08)",line_width=0,layer="below"),
        dict(type="rect",xref="x",yref="y",x0=x_min,x1=100,y0=100,y1=y_max,
             fillcolor="rgba(255,165,0,0.08)",line_width=0,layer="below"),
        dict(type="rect",xref="x",yref="y",x0=x_min,x1=100,y0=y_min,y1=100,
             fillcolor="rgba(220,20,60,0.08)",line_width=0,layer="below"),
        dict(type="rect",xref="x",yref="y",x0=100,x1=x_max,y0=y_min,y1=100,
             fillcolor="rgba(0,102,204,0.08)",line_width=0,layer="below"),
    ]
    fig.update_layout(shapes=shapes)
    fig.add_hline(y=100, line_width=1, line_dash="dot", line_color="#777")
    fig.add_vline(x=100, line_width=1, line_dash="dot", line_color="#777")
    fig.update_xaxes(range=[x_min,x_max])
    fig.update_yaxes(range=[y_min,y_max])
    fig.update_layout(
        xaxis_title="RS-Ratio", yaxis_title="RS-Momentum",
        template="plotly_white",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        margin=dict(l=50,r=20,t=40,b=40)
    )

    last_rr = rratio.iloc[-1].round(2)
    last_rm = rmom.iloc[-1].round(2)
    rows = [{"Ticker":t,"Name":SECTOR_NAMES[t],
             "Quadrant":quadrant(last_rr[t], last_rm[t]),
             "RSR":float(last_rr[t]), "RSM":float(last_rm[t])}
            for t in sel_tickers]

    current_date = idx[-1]
    period_txt = f"Period: {current_date.strftime('%B %Y')}"
    dbg = (f"Bars: {len(idx)} | Last date: {current_date.date()} | "
           f"Tail={tail_len} | Freq={freq}")

    marks = make_marks(dates, max(1,len(dates)//10))

    # ---- Seasonality ----
    if seas_tog == "show":
        seas_year  = int(seas_start or SEASONALITY_DEFAULT_START)
        seas_agg   = seas_agg or SEASONALITY_DEFAULT_AGG
        seas_df    = seasonality_table(tuple(sel_tickers), start_year=seas_year, agg=seas_agg)

        seas_fig = go.Figure()
        for t in sel_tickers:
            if t in seas_df.columns:
                seas_fig.add_trace(go.Bar(
                    x=seas_df.index, y=seas_df[t],
                    name=t, marker_color=COLOR_MAP[t],
                    hovertemplate=("<b>"+t+"</b><br>"
                                   "Month: %{x}<br>"
                                   f"{'Avg' if seas_agg=='mean' else 'Median'}: "
                                   "%{y:.2f}%<extra></extra>")
                ))
        seas_fig.update_layout(
            barmode="group", template="plotly_white",
            title=f"Monthly {'Average' if seas_agg=='mean' else 'Median'} Returns (%) – Since {seas_year}",
            xaxis_title="Month", yaxis_title="% Return",
            margin=dict(l=40,r=20,t=40,b=40),
            legend=dict(orientation="h", y=1.08)
        )
        seas_style = {"height":"280px","display":"block"}
    else:
        seas_fig   = go.Figure()
        seas_style = {"display":"none"}

    return (fig, rows, period_txt, period_txt, dbg,
            max_idx, marks, seas_fig, seas_style)

# ───────── MAIN ─────────
if __name__ == "__main__":
    app.run(debug=True, port=8059)
