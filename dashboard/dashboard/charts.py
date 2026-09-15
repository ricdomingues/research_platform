"""Pure Plotly figure builders (D40, D43). Floats appear only here, to hand values to Plotly for drawing."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from dashboard.viewmodels import RPoint, pressure_bucket_label

ET = ZoneInfo("America/New_York")
LEVELS = (("stop", "Stop"), ("target1", "Alvo 1"), ("target2", "Alvo 2"), ("trigger_price", "Gatilho"),
          ("avg_entry", "Entrada média"), ("stop_current", "Stop vigente"))
MARKER_SYMBOLS = {
    "FILLED": "triangle-up", "TARGET1_HIT": "star", "TARGET2_HIT": "star", "STOPPED": "x", "TIME_EXIT": "square",
    "INVALIDATED": "x-open", "ZONE_LOST": "triangle-down-open", "ZONE_RECLAIMED": "triangle-up-open",
    "TRIGGER_HIT": "diamond",
}


def _et_label(value: Any) -> str:
    moment = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return moment.astimezone(ET).strftime("%Y-%m-%d %H:%M")


def _number(value: Any) -> float:
    return float(value if isinstance(value, Decimal) else Decimal(str(value)))


def candlestick_figure(
    bars: Sequence[Mapping[str, Any]],
    vwap: Sequence[Mapping[str, Any]],
    *,
    title: str,
    levels: Mapping[str, Any] | None = None,
    markers: Sequence[Mapping[str, Any]] = (),
    evaluation_start_ts: str | None = None,
) -> go.Figure:
    figure = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.75, 0.25], vertical_spacing=0.03)
    x = [_et_label(bar["ts"]) for bar in bars]
    figure.add_trace(go.Candlestick(
        x=x, open=[_number(b["open"]) for b in bars], high=[_number(b["high"]) for b in bars],
        low=[_number(b["low"]) for b in bars], close=[_number(b["close"]) for b in bars], name="Candles",
    ), row=1, col=1)
    figure.add_trace(go.Scatter(x=[_et_label(p["ts"]) for p in vwap], y=[_number(p["value"]) for p in vwap],
                                mode="lines", name="VWAP (sessão)"), row=1, col=1)
    figure.add_trace(go.Bar(x=x, y=[_number(b["volume"]) for b in bars], name="Volume"), row=2, col=1)
    levels = levels or {}
    if levels.get("entry_zone_low") is not None and levels.get("entry_zone_high") is not None:
        figure.add_shape(type="rect", xref="x domain", x0=0, x1=1, yref="y", y0=_number(levels["entry_zone_low"]),
                         y1=_number(levels["entry_zone_high"]), name="Zona de entrada",
                         fillcolor="rgba(46, 125, 50, 0.12)", line={"width": 0}, layer="below")
    for key, label in LEVELS:
        if levels.get(key) is None:
            continue
        value = _number(levels[key])
        figure.add_shape(type="line", xref="x domain", x0=0, x1=1, yref="y", y0=value, y1=value, name=label,
                         line={"dash": "dash", "width": 1})
        figure.add_annotation(xref="x domain", x=1, yref="y", y=value, text=label, showarrow=False, xanchor="left")
    if evaluation_start_ts is not None:
        start = _et_label(evaluation_start_ts)
        figure.add_shape(type="line", xref="x", x0=start, x1=start, yref="y domain", y0=0, y1=1,
                         name="evaluation_start_ts", line={"dash": "dot", "width": 1})
    priced: dict[str, list[Mapping[str, Any]]] = {}
    for marker in markers:
        if marker.get("price") is None:
            at = _et_label(marker["ts"])
            figure.add_shape(type="line", xref="x", x0=at, x1=at, yref="y domain", y0=0, y1=1,
                             name=str(marker["type"]), line={"dash": "dot", "width": 1, "color": "gray"})
        else:
            priced.setdefault(str(marker["type"]), []).append(marker)
    for kind, items in priced.items():
        figure.add_trace(go.Scatter(
            x=[_et_label(m["ts"]) for m in items], y=[_number(m["price"]) for m in items], mode="markers", name=kind,
            marker={"symbol": MARKER_SYMBOLS.get(kind, "circle"), "size": 11},
        ), row=1, col=1)
    figure.update_layout(title={"text": title}, height=640, legend={"orientation": "h"},
                         xaxis_rangeslider_visible=False)
    figure.update_xaxes(rangebreaks=[{"bounds": ["sat", "mon"]}, {"bounds": [16, 9.5], "pattern": "hour"}])
    return figure


def cumulative_r_figure(points: Sequence[RPoint]) -> go.Figure:
    figure = go.Figure(go.Scatter(x=[_et_label(p.closed_at) for p in points],
                                  y=[float(p.cumulative_r) for p in points], mode="lines+markers", name="R acumulado"))
    figure.update_layout(title={"text": "R acumulado (ordens fechadas)"}, yaxis_title="R", height=360)
    return figure


def daily_r_figure(days: Sequence[Mapping[str, Any]]) -> go.Figure:
    figure = go.Figure(go.Bar(x=[str(day["session_day"]) for day in days], y=[_number(day["sum_r"]) for day in days],
                              name="R do pregão"))
    figure.update_layout(title={"text": "R somado por pregão (trades sem revisão)"}, yaxis_title="R", height=320)
    return figure


def pressure_buckets_figure(buckets: Sequence[Mapping[str, Any]], *, method: str) -> go.Figure:
    figure = go.Figure(go.Bar(
        x=[pressure_bucket_label(bucket) for bucket in buckets],
        # D66: a bucket without a mean (n < 5) gets no bar (a gap, never 0) and the label "n<5".
        y=[None if bucket.get("mean_r") is None else _number(bucket["mean_r"]) for bucket in buckets],
        text=["n<5" if bucket.get("mean_r") is None else f"n={bucket['trades']}" for bucket in buckets],
        name="R médio",
    ))
    figure.update_layout(title={"text": f"R médio por pressão estimada ({method}) — não é fluxo de ordens"},
                         yaxis_title="R", height=320)
    return figure
