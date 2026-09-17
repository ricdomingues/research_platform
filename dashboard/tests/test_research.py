"""The Research page, its view-models and its chart markers (Plan 5, D93). No server, no network.

The page is read-only by construction, and these tests pin the three things that must never drift: a score is
never shown without the sentence saying what it is not, a percentage is never shown without its sample count,
and an unscored candidate reads as "no model yet" rather than as a low probability.
"""

from decimal import Decimal
from pathlib import Path

from streamlit.testing.v1 import AppTest

from dashboard.charts import add_pattern_markers, candlestick_figure
from dashboard.viewmodels import (
    EMPTY,
    RESEARCH_ISOLATION_NOTICE,
    RESEARCH_NO_MODEL,
    backtest_rows,
    candidate_view,
    fmt_probability,
    levels_rows,
    marker_label,
    model_rows,
    pattern_label,
    research_run_rows,
    scanner_rows,
)

APP = str(Path(__file__).resolve().parents[1] / "dashboard" / "app.py")
TS = "2025-11-25T20:45:00+00:00"
INTERPRETATION = "Composto determinístico em [0, 1]; não é probabilidade."
BARS = [
    {"ts": "2025-11-25T14:30:00+00:00", "open": "100", "high": "101", "low": "99.5", "close": "100.5",
     "volume": "1000"},
    {"ts": "2025-11-25T14:45:00+00:00", "open": "100.5", "high": "102", "low": "100", "close": "101.5",
     "volume": "3000"},
]
VWAP = [{"ts": BARS[0]["ts"], "value": "100.3333"}, {"ts": BARS[1]["ts"], "value": "100.9583"}]
FEATURES = {
    "short_term_trend": "UP", "medium_term_trend": "DOWN", "rsi14": Decimal("58.2"),
    "relative_volume": Decimal("1.8"), "pressure_side": "BUY", "cmf": Decimal("0.12"),
    "vwap_distance_pct": Decimal("0.45"), "support_distance_pct": Decimal("-1.20"),
    "resistance_distance_pct": Decimal("2.40"), "atr14": Decimal("1.05"), "breakout": "INSIDE",
    "pressure_disclaimer": "Estimativa de OHLCV; não é fluxo de ordens.",
}
CANDIDATE = {
    "id": 7, "ticker": "AAPL", "timeframe": "15m", "pattern": "BULLISH_ENGULFING", "direction": "LONG",
    "detected_at": TS, "deterministic_score": Decimal("0.72"), "ml_probability": None, "model_version": None,
    "entry_zone_low": Decimal("100.21"), "entry_zone_high": Decimal("100.70"), "stop": Decimal("98.50"),
    "target1": Decimal("104.63"), "target2": Decimal("106.60"), "risk_reward": Decimal("1.80"),
    "levels_valid": True, "levels_errors": [], "features": FEATURES,
}
MARKER = {
    "seq": 1, "type": "BULLISH_ENGULFING", "ts": BARS[1]["ts"], "price": Decimal("100.70"),
    "pattern": "BULLISH_ENGULFING", "direction": "BULLISH", "timeframe": "15m",
    "pattern_score": Decimal("0.70"), "deterministic_score": Decimal("0.72"), "ml_probability": None,
    "candidate_id": 7,
}
BACKTEST = {
    "pattern": "BULLISH_ENGULFING", "timeframe": "15m", "ticker": None, "split": "TEST", "samples": 120,
    "resolved": 100, "wins": 58, "losses": 42, "timeouts": 20, "ambiguous": 3, "win_rate": Decimal("0.58"),
    "expectancy_r": Decimal("0.21"), "profit_factor": Decimal("1.4"), "max_drawdown_r": Decimal("4.2"),
    "avg_mfe_r": Decimal("1.1"), "avg_mae_r": Decimal("-0.6"), "period_from": TS, "period_to": TS,
}
RUN = {
    "kind": "RESEARCH_SCAN", "status": "COMPLETED", "started_at": TS, "completed_at": TS, "data_as_of": TS,
    "engine_version": "candles-v1", "detail": {"detections": 12, "candidates": 9, "failures": {"MSFT:15m": "x"}},
}


class FakeResearchApi:
    """Only the calls the Research page makes. A page that asked for anything else would fail loudly here."""

    def __init__(self) -> None:
        self.markers_calls: list[tuple[str, str]] = []

    def research_versions(self):
        return {"engine_version": "candles-v1", "feature_version": "features-v1", "scoring_version": "scoring-v1",
                "label_version": "labels-v1", "ambiguity_policy": "STOP_FIRST_ON_SAME_CANDLE_V1",
                "supported_patterns": ["BULLISH_ENGULFING", "HAMMER"],
                "supported_timeframes": ["15m", "1h", "1d"]}

    def research_candidates(self, **kwargs):
        return {"candidates": [CANDIDATE], "score_interpretation": INTERPRETATION,
                "feature_version": "features-v1", "scoring_version": "scoring-v1"}

    def research_candidate(self, candidate_id):
        return {"candidate": {**CANDIDATE, "feature_document": FEATURES,
                              "thesis_document": {"levels": {"entry_zone_low": Decimal("100.21"),
                                                             "entry_zone_high": Decimal("100.70"),
                                                             "stop": Decimal("98.50"),
                                                             "target1": Decimal("104.63"),
                                                             "target2": Decimal("106.60"),
                                                             "risk_reward": Decimal("1.80"),
                                                             "valid": True, "errors": []}}},
                "detection": {"evidence": {"engulf_ratio": Decimal("1.5"), "previous_direction": "BEARISH"}},
                "score_interpretation": INTERPRETATION}

    def research_markers(self, ticker, timeframe, start, end, **kwargs):
        self.markers_calls.append((ticker, timeframe))
        return [MARKER]

    def research_backtests(self, **kwargs):
        return [BACKTEST]

    def research_models(self, **kwargs):
        return []

    def research_runs(self, **kwargs):
        return [RUN]

    def market_bars(self, ticker, start, end, *, source=None):
        return {"bars": BARS, "vwap": VWAP, "data_as_of": TS, "price_source": "alpaca_iex"}


def open_research(fake):
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["api_client"] = fake
    at.run()
    assert not at.exception
    at.radio(key="page").set_value("Pesquisa").run()
    assert not at.exception
    return at


def values(elements):
    return [element.value for element in elements]


def test_scanner_rows_carry_the_columns_the_brief_asks_for():
    (row,) = scanner_rows([CANDIDATE])
    assert list(row) == ["Ticker", "Timeframe", "Padrão", "Direção", "Score do padrão", "Tendência", "RSI",
                         "Volume relativo", "Pressão (estimativa)", "Distância VWAP", "Distância suporte",
                         "Detectado em", "Probabilidade ML"]
    assert (row["Ticker"], row["Padrão"], row["Score do padrão"]) == ("AAPL", "Engolfo de alta", "0.72")
    assert (row["Tendência"], row["RSI"], row["Volume relativo"]) == ("alta", "58.2", "1.80")
    assert (row["Distância VWAP"], row["Distância suporte"]) == ("+0.45%", "-1.20%")
    assert row["Probabilidade ML"] == EMPTY  # no approved model: empty, never a made-up number


def test_an_unscored_candidate_never_reads_as_a_low_probability():
    assert fmt_probability(None) == EMPTY
    assert fmt_probability(Decimal("0.634")) == "63.4%"


def test_levels_rows_show_an_invalid_chain_with_its_reason():
    rejected = {**CANDIDATE, "levels_valid": False, "levels_errors": ["RISK_REWARD_BELOW_MINIMUM"]}
    valid, invalid = levels_rows([CANDIDATE, rejected])
    assert (valid["Zona"], valid["Válido"], valid["Motivos"]) == ("100.21–100.70", "sim", EMPTY)
    assert (invalid["Válido"], invalid["Motivos"]) == ("não", "RISK_REWARD_BELOW_MINIMUM")


def test_backtest_rows_never_show_a_rate_without_its_sample():
    (row,) = backtest_rows([BACKTEST])
    assert row["Win rate (n)"] == "58.0% (n=100)"
    assert (row["Amostras"], row["Resolvidos"], row["Timeouts"]) == ("120", "100", "20")
    assert (row["Expectância (R)"], row["Profit factor"]) == ("+0.21R", "1.40")
    assert row["Padrão"] == "Engolfo de alta"


def test_candidate_view_splits_evidence_from_context():
    view = candidate_view(FakeResearchApi().research_candidate(7))
    assert view.title == "AAPL · Engolfo de alta · 15m"
    assert (view.score, view.probability) == ("0.72", EMPTY)
    assert {"Evidência": "engulf_ratio", "Valor": "1.5"} in view.evidence
    context = {row["Métrica"]: row["Valor"] for row in view.context}
    assert (context["Tendência curta"], context["RSI 14"], context["Rompimento"]) == ("alta", "58.2",
                                                                                      "dentro da faixa")
    assert view.levels and view.levels[0]["R:R"] == "1.80"
    assert view.interpretation == INTERPRETATION


def test_run_and_model_rows():
    (row,) = research_run_rows([RUN])
    assert (row["Tipo"], row["Status"], row["Detecções"], row["Falhas"]) == ("RESEARCH_SCAN", "COMPLETED", "12", "1")
    assert model_rows([]) == []
    (model,) = model_rows([{
        "model_name": "candle-setup-target-first", "model_version": "m-1", "model_kind": "GBDT_LOGISTIC_V1",
        "framework": "numpy", "feature_version": "features-v1", "label_version": "labels-v1",
        "training_from": TS, "training_to": TS, "training_rows": 300,
        "validation_metrics": {"auc": Decimal("0.61"), "brier": Decimal("0.2103")}, "created_at": TS,
    }])
    assert (model["Versão"], model["Linhas"], model["AUC (validação)"]) == ("m-1", "300", "0.610")


def test_pattern_markers_are_drawn_on_the_existing_candlestick_figure():
    figure = add_pattern_markers(candlestick_figure(BARS, VWAP, title="AAPL"), [MARKER])
    drawn = [trace for trace in figure.data if getattr(trace, "mode", None) == "markers+text"]
    assert [trace.name for trace in drawn] == ["Padrão BULLISH"]
    assert list(drawn[0].y) == [100.7]
    assert list(drawn[0].text) == ["Engolfo de alta · 0.70 · 15m"]
    assert marker_label(MARKER) == "Engolfo de alta · 0.70 · 15m"
    # A detection without a usable price becomes a vertical line, never a point at zero.
    unpriced = add_pattern_markers(candlestick_figure(BARS, VWAP, title="AAPL"),
                                   [{**MARKER, "price": None}])
    assert [shape.name for shape in unpriced.layout.shapes] == ["BULLISH_ENGULFING"]
    assert not [trace for trace in unpriced.data if getattr(trace, "mode", None) == "markers+text"]


def test_the_research_page_shows_the_scanner_and_labels_its_score():
    at = open_research(FakeResearchApi())
    assert at.title[0].value == "Pesquisa"
    captions = values(at.caption)
    assert RESEARCH_ISOLATION_NOTICE in captions
    assert INTERPRETATION in captions
    assert RESEARCH_NO_MODEL in captions  # no model registered, and no candidate carries a probability
    rows = [row for frame in at.dataframe for row in frame.value.to_dict(orient="records")]
    assert any(row.get("Padrão") == pattern_label("BULLISH_ENGULFING") for row in rows)
    assert any(row.get("Win rate (n)") == "58.0% (n=100)" for row in rows)


def test_the_asset_chart_asks_for_markers_of_the_chosen_timeframe():
    fake = FakeResearchApi()
    at = open_research(fake)
    assert "Informe um ticker para ver o gráfico com os padrões detectados." in values(at.caption)
    at.text_input(key="research-asset").set_value("aapl").run()
    assert not at.exception
    assert fake.markers_calls == [("AAPL", "15m")]
    assert any("padrão(ões) marcado(s)" in caption for caption in values(at.caption))


def test_a_failing_research_call_shows_a_message_instead_of_a_traceback():
    from dashboard.client import ApiUnreachable

    class Failing(FakeResearchApi):
        def research_candidates(self, **kwargs):
            raise ApiUnreachable("ConnectError")

    at = open_research(Failing())
    assert not at.exception
    assert "API indisponível (ConnectError)." in values(at.error)
