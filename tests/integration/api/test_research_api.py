"""The research routes over the real app: read-only views, fixed error codes and versions that travel along.

The route set is deliberately all GET. The test at the end pins that behaviourally: every research path refuses
a write, because neither starting a scan nor promoting a candidate should follow from looking at a list.
"""

from datetime import timedelta
from decimal import Decimal

from tests.integration.support import DAY
from tests.research.support import BASE, bullish_engulfing_after_decline, zigzag
from tests.support import et
from virtual_orders.research.backtest import build_occurrences, summarize
from virtual_orders.research.models import Timeframe
from virtual_orders.research.repository import (
    ResearchRunKind,
    ResearchRunStatus,
    finish_run,
    record_backtest,
    record_candidate,
    record_detection,
    start_run,
)
from virtual_orders.research.scoring import score_setup
from virtual_orders.research.setups import build_candidate

from core.domain.models import Direction  # isort: skip

DATA_AS_OF = et(DAY, "16:30")
CONFIG = {"timeframes": ["15m"]}
LIST_PATHS = ("/research/versions", "/research/runs", "/research/detections", "/research/candidates",
              "/research/backtests", "/research/models")


def seed(engine, ticker="AAPL"):
    """One real detection and its candidate, written through the repository the scan itself uses."""
    (occurrence,) = [item for item in build_occurrences(bullish_engulfing_after_decline(), ticker=ticker,
                                                        data_as_of=DATA_AS_OF)
                     if item.pattern == "BULLISH_ENGULFING"]
    candidate = build_candidate(
        ticker=ticker, detection=occurrence.detection, snapshot=occurrence.features,
        score=score_setup(occurrence.detection, occurrence.features, Direction.LONG), data_as_of=DATA_AS_OF,
    )
    assert candidate is not None
    with engine.begin() as conn:
        run = start_run(conn, kind=ResearchRunKind.RESEARCH_SCAN, data_as_of=DATA_AS_OF, code_version="test-sha",
                        engine_version="candles-v1", configuration=CONFIG)
        detection_id, _ = record_detection(conn, run_id=run.run_id, ticker=ticker, price_source="fake_feed",
                                           detection=occurrence.detection, data_as_of=DATA_AS_OF)
        record_candidate(conn, run_id=run.run_id, detection_id=detection_id, candidate=candidate)
        finish_run(conn, run.run_id, ResearchRunStatus.COMPLETED, {"detections": 1, "candidates": 1})
    return occurrence, candidate


def seed_backtest(engine, pattern="THREE_WHITE_SOLDIERS"):
    stats = summarize(build_occurrences(zigzag(cycles=3), ticker="AAPL", data_as_of=DATA_AS_OF), horizon=10)
    with engine.begin() as conn:
        run = start_run(conn, kind=ResearchRunKind.BACKTEST, data_as_of=DATA_AS_OF, code_version="test-sha",
                        engine_version="candles-v1", configuration=CONFIG)
        record_backtest(conn, run_id=run.run_id, stats=stats, pattern=pattern, timeframe=Timeframe.M15,
                        split="TEST", period_from=DATA_AS_OF - timedelta(days=1), period_to=DATA_AS_OF,
                        data_as_of=DATA_AS_OF)
        finish_run(conn, run.run_id, ResearchRunStatus.COMPLETED)
    return stats


def test_versions_name_every_rule_behind_a_stored_observation(api):
    body = api.client.get("/research/versions").json()
    assert body["engine_version"] == "candles-v1"
    assert body["feature_version"] == "features-v1" and body["scoring_version"] == "scoring-v1"
    assert body["ambiguity_policy"] == "STOP_FIRST_ON_SAME_CANDLE_V1"
    assert len(body["supported_patterns"]) == 15
    assert body["supported_timeframes"] == ["5m", "15m", "30m", "1h", "4h", "1d"]
    assert "not a probability" in body["score_interpretation"]


def test_an_empty_database_answers_with_empty_lists_not_an_error(api):
    for path, key in (("/research/detections", "detections"), ("/research/candidates", "candidates"),
                      ("/research/backtests", "backtests"), ("/research/models", "models"),
                      ("/research/runs", "runs")):
        response = api.client.get(path)
        assert response.status_code == 200, path
        assert response.json()[key] == []


def test_the_scanner_returns_a_candidate_with_the_context_it_was_scored_on(api):
    _, candidate = seed(api.services.engine)
    body = api.client.get("/research/candidates").json()
    (row,) = body["candidates"]
    assert (row["ticker"], row["pattern"], row["timeframe"]) == ("AAPL", "BULLISH_ENGULFING", "15m")
    assert row["direction"] == "LONG"
    # Decimals cross the boundary as normalized strings (0.7, never 0.7000): compare by value, not by spelling.
    assert Decimal(row["deterministic_score"]) == candidate.deterministic_score
    assert row["ml_probability"] is None and row["model_version"] is None
    assert set(row["features"]) >= {"short_term_trend", "rsi14", "relative_volume", "pressure_side",
                                    "vwap_distance_pct", "support_distance_pct"}
    assert row["client_signal_id"] == candidate.client_signal_id
    # The meaning of the number travels with it, on every response that carries one.
    assert "not a probability" in body["score_interpretation"]


def test_the_scanner_filters_by_ticker_timeframe_pattern_and_score(api):
    seed(api.services.engine)
    assert api.client.get("/research/candidates", params={"ticker": "aapl"}).json()["candidates"]
    assert api.client.get("/research/candidates", params={"ticker": "MSFT"}).json()["candidates"] == []
    assert api.client.get("/research/candidates", params={"timeframe": "1h"}).json()["candidates"] == []
    assert api.client.get("/research/candidates",
                          params={"pattern": "BULLISH_ENGULFING"}).json()["candidates"]
    assert api.client.get("/research/candidates", params={"min_score": "0.99"}).json()["candidates"] == []
    assert api.client.get("/research/candidates", params={"direction": "SHORT"}).json()["candidates"] == []


def test_a_candidate_detail_carries_its_snapshot_thesis_and_detection(api):
    seed(api.services.engine)
    (row,) = api.client.get("/research/candidates").json()["candidates"]
    body = api.client.get(f"/research/candidates/{row['id']}").json()
    assert body["candidate"]["pattern"] == "BULLISH_ENGULFING"
    assert body["candidate"]["feature_document"]["feature_version"] == "features-v1"
    assert body["candidate"]["thesis_document"]["kind"] == "RESEARCH_CANDLESTICK_SETUP"
    assert body["detection"]["evidence"]["previous_direction"] == "BEARISH"
    assert body["detection"]["engine_version"] == "candles-v1"
    assert api.client.get("/research/candidates/999999").status_code == 404
    assert api.client.get("/research/candidates/999999").json()["error"]["code"] == "RESEARCH_CANDIDATE_NOT_FOUND"


def test_detections_are_listed_newest_first_with_their_evidence(api):
    occurrence, _ = seed(api.services.engine)
    body = api.client.get("/research/detections", params={"ticker": "AAPL", "timeframe": "15m"}).json()
    (row,) = body["detections"]
    assert row["pattern"] == "BULLISH_ENGULFING" and row["direction"] == "BULLISH"
    assert row["end_ts"] == occurrence.detection.end_ts.isoformat()
    assert Decimal(row["evidence"]["engulf_ratio"]) == occurrence.detection.evidence["engulf_ratio"]
    assert body["engine_version"] == "candles-v1"


def test_markers_come_back_in_the_platforms_own_marker_shape(api):
    occurrence, _ = seed(api.services.engine)
    start = occurrence.detection.end_ts - timedelta(days=1)
    end = occurrence.detection.end_ts + timedelta(days=1)
    body = api.client.get("/research/markers", params={
        "ticker": "AAPL", "timeframe": "15m", "from": start.isoformat(), "to": end.isoformat(),
    }).json()
    (marker,) = body["markers"]
    assert {"seq", "type", "event_key", "ts", "price"} <= set(marker)  # the existing chart contract
    assert marker["type"] == "BULLISH_ENGULFING" and marker["direction"] == "BULLISH"
    assert Decimal(marker["pattern_score"]) == occurrence.detection.overall_score
    assert body["ticker"] == "AAPL" and body["timeframe"] == "15m"


def test_backtests_report_their_sample_and_the_policy_behind_their_labels(api):
    stats = seed_backtest(api.services.engine)
    body = api.client.get("/research/backtests", params={"split": "TEST"}).json()
    (row,) = body["backtests"]
    assert (row["samples"], row["resolved"], row["wins"]) == (stats.samples, stats.resolved, stats.wins)
    assert row["ambiguity_policy"] == "STOP_FIRST_ON_SAME_CANDLE_V1"
    assert row["statistics"]["backtest_version"] == "backtest-v1"
    assert body["label_version"] == "labels-v1"
    assert api.client.get("/research/backtests", params={"pattern": "HAMMER"}).json()["backtests"] == []


def test_runs_are_listed_with_their_status_and_configuration_hash(api):
    seed(api.services.engine)
    (run,) = api.client.get("/research/runs", params={"kind": "RESEARCH_SCAN"}).json()["runs"]
    assert run["status"] == "COMPLETED" and run["kind"] == "RESEARCH_SCAN"
    assert run["config_hash"] and run["engine_version"] == "candles-v1"
    assert run["detail"]["detections"] == 1


def test_invalid_requests_answer_with_fixed_codes(api):
    for params in ({"timeframe": "7m"}, {"pattern": "NOT_A_PATTERN"}, {"direction": "SIDEWAYS"},
                   {"min_score": "5"}):
        response = api.client.get("/research/candidates", params=params)
        assert response.status_code == 422, params
        body = response.json()["error"]
        assert body["code"] == "RESEARCH_REQUEST_INVALID"
        assert body["detail"]["errors"]
    assert api.client.get("/research/runs", params={"kind": "NOPE"}).status_code == 422
    assert api.client.get("/research/backtests", params={"split": "NOPE"}).status_code == 422
    naive = api.client.get("/research/markers", params={
        "ticker": "AAPL", "timeframe": "15m", "from": "2025-11-25T09:30:00", "to": "2025-11-25T16:00:00"})
    assert naive.status_code == 422 and naive.json()["error"]["code"] == "RESEARCH_REQUEST_INVALID"
    empty = api.client.get("/research/markers", params={
        "ticker": "AAPL", "timeframe": "15m", "from": BASE.isoformat(), "to": BASE.isoformat()})
    assert empty.json()["error"]["detail"]["errors"] == ["EMPTY_WINDOW"]


def test_an_unknown_ticker_is_rejected_the_same_way_as_everywhere_else(api):
    response = api.client.get("/research/candidates", params={"ticker": "  "})
    assert response.status_code == 422 and response.json()["error"]["code"] == "TICKER_INVALID"


def test_every_research_route_refuses_a_write(api):
    """No HTTP path starts a scan or promotes a candidate: both are deliberate, separate decisions."""
    for path in LIST_PATHS:
        assert api.client.get(path).status_code == 200, path
        for method in ("post", "put", "delete", "patch"):
            response = getattr(api.client, method)(path)
            assert response.status_code == 405, (method, path)
            assert response.json()["error"]["code"] == "METHOD_NOT_ALLOWED"
    for path in ("/research/markers", "/research/candidates/1"):
        assert api.client.post(path).status_code == 405, path
