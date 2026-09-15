from decimal import Decimal

from dashboard.charts import candlestick_figure, cumulative_r_figure, daily_r_figure, pressure_buckets_figure
from dashboard.viewmodels import RPoint

BARS = [
    {"ts": "2025-11-25T14:30:00+00:00", "open": "100", "high": "101", "low": "99.5", "close": "100.5",
     "volume": "1000"},
    {"ts": "2025-11-25T14:31:00+00:00", "open": "100.5", "high": "102", "low": "100", "close": "101.5",
     "volume": "3000"},
]
VWAP = [{"ts": BARS[0]["ts"], "value": "100.3333"}, {"ts": BARS[1]["ts"], "value": "100.9583"}]


def test_candlestick_figure_has_price_vwap_and_volume_rows_in_et():
    figure = candlestick_figure(BARS, VWAP, title="AAPL")
    assert [(trace.type, trace.name) for trace in figure.data] == [
        ("candlestick", "Candles"), ("scatter", "VWAP (sessão)"), ("bar", "Volume")]
    assert list(figure.data[0].open) == [100.0, 100.5] and list(figure.data[2].y) == [1000.0, 3000.0]
    assert list(figure.data[0].x) == ["2025-11-25 09:30", "2025-11-25 09:31"]
    assert figure.data[2].yaxis == "y2"
    assert figure.layout.xaxis.rangeslider.visible is False
    assert figure.layout.title.text == "AAPL"


def test_levels_zone_evaluation_start_and_markers_are_overlaid():
    figure = candlestick_figure(
        BARS, VWAP, title="AAPL",
        levels={"entry_zone_low": "100", "entry_zone_high": "102", "stop": "97", "target1": "106", "target2": None,
                "trigger_price": None, "avg_entry": None, "stop_current": None},
        markers=[{"type": "FILLED", "ts": BARS[1]["ts"], "price": "101"},
                 {"type": "DATA_GAP", "ts": BARS[0]["ts"], "price": None}],
        evaluation_start_ts=BARS[0]["ts"],
    )
    assert [shape.name for shape in figure.layout.shapes] == [
        "Zona de entrada", "Stop", "Alvo 1", "evaluation_start_ts", "DATA_GAP"]
    zone, stop = figure.layout.shapes[0], figure.layout.shapes[1]
    assert (zone.y0, zone.y1, stop.y0, stop.y1) == (100.0, 102.0, 97.0, 97.0)
    assert figure.layout.shapes[3].x0 == "2025-11-25 09:30"
    markers = [(trace.name, list(trace.y)) for trace in figure.data if getattr(trace, "mode", None) == "markers"]
    assert markers == [("FILLED", [101.0])]


def test_cumulative_r_figure():
    figure = cumulative_r_figure([RPoint("2025-11-25T17:00:00+00:00", Decimal("-1"), "b"),
                                  RPoint("2025-11-25T18:00:00+00:00", Decimal("0.75"), "a")])
    (trace,) = figure.data
    assert (trace.name, list(trace.x), list(trace.y)) == (
        "R acumulado", ["2025-11-25 12:00", "2025-11-25 13:00"], [-1.0, 0.75])


def test_daily_r_and_pressure_bucket_figures():
    daily = daily_r_figure([{"session_day": "2025-11-25", "sum_r": "1.75"},
                            {"session_day": "2025-11-26", "sum_r": "-1"}])
    (bars,) = daily.data
    assert (bars.type, list(bars.x), list(bars.y)) == ("bar", ["2025-11-25", "2025-11-26"], [1.75, -1.0])
    buckets = [{"alignment": "ALIGNED", "strength": "STRONG", "trades": 5, "mean_r": "1.75"},
               {"alignment": "UNAVAILABLE", "strength": None, "trades": 2, "mean_r": None}]
    figure = pressure_buckets_figure(buckets, method="OHLCV_PRESSURE_ESTIMATE_V1")
    (trace,) = figure.data
    assert (list(trace.x), list(trace.y), list(trace.text)) == (
        ["a favor · forte", "indisponível"], [1.75, None], ["n=5", "n<5"])  # no bar drawn at 0 for n < 5
    assert "OHLCV_PRESSURE_ESTIMATE_V1" in figure.layout.title.text
    assert "não é fluxo de ordens" in figure.layout.title.text
