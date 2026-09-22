"""The Terminal page and its pure builders (D94). No server, no network, no browser.

Two things are pinned here because they are the ones that would quietly go wrong. The chart configuration is
asserted structurally — a Lightweight Charts series is a plain dict, so it can be checked without rendering —
and the promotion boundary is asserted from the page's side: the page never decides, it shows what the engine
answered, and a refusal arrives on screen as the policy's own reason codes.
"""

from decimal import Decimal
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from dashboard.charts import (
    lightweight_candles,
    lightweight_charts,
    lightweight_markers,
    lightweight_price_lines,
    lightweight_volume,
)
from dashboard.client import ApiRequestFailed
from dashboard.viewmodels import TIMEFRAME_MINUTES, aggregate_bars, promotion_blockers

APP = str(Path(__file__).resolve().parents[1] / "dashboard" / "app.py")
TS = "2025-11-25T14:30:00+00:00"
INTERPRETATION = "Composto determinístico em [0, 1]; não é probabilidade."


def bar(minute, open_, high, low, close, volume="1000"):
    return {"ts": f"2025-11-25T14:{minute:02d}:00+00:00", "open": open_, "high": high, "low": low,
            "close": close, "volume": volume}


BARS = [
    bar(30, "100", "101", "99.5", "100.5"),
    bar(31, "100.5", "102", "100", "101.5", "2000"),
    bar(45, "101.5", "103", "101", "102.5", "3000"),
]
CANDIDATE = {
    "id": 7, "ticker": "AAPL", "timeframe": "15m", "pattern": "BULLISH_ENGULFING", "direction": "LONG",
    "detected_at": TS, "deterministic_score": Decimal("0.72"), "entry_zone_low": Decimal("100.21"),
    "entry_zone_high": Decimal("100.70"), "stop": Decimal("98.50"), "target1": Decimal("104.63"),
    "target2": Decimal("106.60"), "risk_reward": Decimal("1.80"), "levels_valid": True, "levels_errors": [],
}
MARKER = {"ts": BARS[1]["ts"], "pattern": "BULLISH_ENGULFING", "direction": "BULLISH", "candidate_id": 7}


# --- pure builders -------------------------------------------------------------------------------------------
def test_one_minute_bars_fold_into_the_chosen_timeframe():
    candles = aggregate_bars(BARS, 15)
    assert len(candles) == 2  # 14:30 and 14:45 buckets
    first = candles[0]
    assert first["open"] == Decimal("100") and first["close"] == Decimal("101.5")
    assert first["high"] == Decimal("102") and first["low"] == Decimal("99.5")
    assert first["volume"] == Decimal("3000")  # both minutes counted


def test_a_bucket_with_no_bar_is_never_invented():
    """A gap is a gap: the chart shows what was stored, never a fabricated candle."""
    candles = aggregate_bars([BARS[0], BARS[2]], 15)
    assert [candle["ts"].minute for candle in candles] == [30, 45]


def test_every_supported_timeframe_has_a_width():
    assert set(TIMEFRAME_MINUTES) >= {"5m", "15m", "1h", "1d"}
    assert TIMEFRAME_MINUTES["1h"] == 60
    with pytest.raises(ValueError, match="minutes must be"):
        aggregate_bars(BARS, 0)


def test_the_chart_configuration_carries_candles_volume_markers_and_levels():
    candles = aggregate_bars(BARS, 15)
    config = lightweight_charts(candles, markers=[MARKER], levels=CANDIDATE)
    price, volume = config
    (series,) = price["series"]
    assert series["type"] == "Candlestick"
    assert len(series["data"]) == len(candles)
    assert series["data"][0]["time"] < series["data"][1]["time"]  # seconds, ascending
    # A bullish pattern is drawn below its bar, pointing up, so direction reads without a legend.
    (marker,) = series["markers"]
    assert (marker["position"], marker["shape"]) == ("belowBar", "arrowUp")
    titles = [line["title"] for line in series["priceLines"]]
    assert titles == ["Entrada (base)", "Entrada (topo)", "Stop", "Alvo 1", "Alvo 2"]
    assert volume["series"][0]["type"] == "Histogram"
    # TradingView's licence is satisfied on the chart itself (Apache 2.0, NOTICE attribution).
    assert price["chart"]["layout"]["attributionLogo"] is True


def test_levels_that_do_not_exist_are_not_drawn():
    assert lightweight_price_lines(None) == []
    assert [line["title"] for line in lightweight_price_lines({"stop": Decimal("98.5")})] == ["Stop"]


def test_builders_hand_floats_to_the_javascript_side():
    candles = aggregate_bars(BARS, 15)
    assert all(isinstance(item["close"], float) for item in lightweight_candles(candles))
    assert all(isinstance(item["value"], float) for item in lightweight_volume(candles))
    assert lightweight_markers([]) == []


def test_promotion_blockers_reads_the_engines_reasons():
    assert promotion_blockers({"errors": ["NOT_VALIDATED", "SCORE_BELOW_MINIMUM"]}) == [
        "NOT_VALIDATED", "SCORE_BELOW_MINIMUM"]
    assert promotion_blockers(None) == [] and promotion_blockers({}) == []


# --- the page ------------------------------------------------------------------------------------------------
class FakeClient:
    def __init__(self, promote=None):
        self.promote = promote
        self.promoted = []

    def watchlist(self):
        return [{"ticker": "AAPL", "added_at": TS, "rules": []}]

    def market_bars(self, ticker, start, end, *, source=None):
        return {"bars": BARS, "vwap": [], "data_as_of": TS, "price_source": "alpaca_iex"}

    def research_markers(self, ticker, timeframe, start, end, **kwargs):
        return [MARKER]

    def research_candidates(self, **kwargs):
        return {"candidates": [CANDIDATE], "score_interpretation": INTERPRETATION}

    def promote_candidate(self, candidate_id, *, override=False, auto_order=True):
        self.promoted.append((candidate_id, override, auto_order))
        if isinstance(self.promote, Exception):
            raise self.promote
        return self.promote


def open_terminal(fake):
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["api_client"] = fake
    at.run()
    assert not at.exception
    at.radio(key="page").set_value("Terminal").run()
    assert not at.exception
    return at


def test_the_terminal_draws_the_chosen_asset_and_its_last_price():
    at = open_terminal(FakeClient())
    assert [metric.value for metric in at.metric][:1] == ["102.5"]
    assert any("Lightweight Charts" in caption.value for caption in at.caption)


def test_choosing_a_setup_shows_the_sentence_that_travels_with_its_score():
    at = open_terminal(FakeClient())
    at.selectbox(key="terminal_candidate").select_index(1).run()
    assert any(INTERPRETATION in caption.value for caption in at.caption)


def test_a_refused_promotion_shows_the_policys_own_reasons():
    """The page never judges: it reports what the boundary answered, code by code."""
    refusal = ApiRequestFailed(409, "CANDIDATE_NOT_PROMOTABLE", None,
                               {"errors": ["NOT_VALIDATED", "SCORE_BELOW_MINIMUM"]})
    fake = FakeClient(promote=refusal)
    at = open_terminal(fake)
    at.selectbox(key="terminal_candidate").select_index(1).run()
    at.button(key="promote_7").click().run()
    assert fake.promoted == [(7, False, True)]  # the default asks the policy, never overrides it
    body = " ".join(element.value for element in at.markdown)
    assert "NOT_VALIDATED" in body and "SCORE_BELOW_MINIMUM" in body


def test_an_override_is_reported_as_an_override_not_as_a_clean_promotion():
    fake = FakeClient(promote={"signal_id": "s-1", "auto_order_id": "o-1", "overridden": True,
                               "decision": {"errors": ["NOT_VALIDATED"]}})
    at = open_terminal(fake)
    at.selectbox(key="terminal_candidate").select_index(1).run()
    at.checkbox(key="override_7").check().run()
    at.button(key="promote_7").click().run()
    assert fake.promoted[-1] == (7, True, True)
    assert any("override" in warning.value.lower() for warning in at.warning)
    assert not at.success  # a forced promotion never reads as one the policy allowed


def test_a_candidate_without_a_valid_chain_offers_no_override():
    fake = FakeClient()
    fake.research_candidates = lambda **kwargs: {
        "candidates": [{**CANDIDATE, "levels_valid": False, "levels_errors": ["NO_LEVELS"]}],
        "score_interpretation": INTERPRETATION,
    }
    at = open_terminal(fake)
    at.selectbox(key="terminal_candidate").select_index(1).run()
    assert any("não há preços" in warning.value for warning in at.warning)
    assert not at.button
