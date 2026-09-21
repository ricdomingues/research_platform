"""Scoring, level proposal, candidates and the promotion boundary (spec 12, 13, 17, 18).

The boundary is the subject: a detected pattern is an observation, and the only way it becomes a platform
signal is through the existing intake, with a body the existing parser accepts unchanged.
"""

from dataclasses import replace
from datetime import timedelta

import pytest

from core.domain.models import Direction
from core.domain.validation import validate_signal
from tests.research.support import BASE, D, bullish_engulfing_after_decline, candle, zigzag
from virtual_orders.evaluator.signals import parse_signal_body
from virtual_orders.research.backtest import BacktestStats, build_occurrences, summarize
from virtual_orders.research.candlesticks import ENGINE_VERSION
from virtual_orders.research.indicators import compute_series
from virtual_orders.research.levels import (
    DEFAULT_POLICY,
    RISK_REWARD_BELOW_MINIMUM,
    TARGET_BLOCKED_BY_STRUCTURE,
    LevelPolicy,
    RiskLevels,
    propose_levels,
    to_tick,
)
from virtual_orders.research.market_structure import (
    BreakoutState,
    GapState,
    MarketStructure,
    structure_at,
    swing_points,
)
from virtual_orders.research.models import PatternDirection, TrendState
from virtual_orders.research.promotion import (
    EXPECTANCY_BELOW_MINIMUM,
    INSUFFICIENT_BACKTEST_SAMPLES,
    INVALID_LEVELS,
    ML_PROBABILITY_REQUIRED,
    NO_LEVELS,
    NOT_VALIDATED,
    SCORE_BELOW_MINIMUM,
    PromotionPolicy,
    decide,
    is_promotable,
    promotion_errors,
    signal_body,
)
from virtual_orders.research.scoring import SCORE_INTERPRETATION, ScoreWeights, score_setup
from virtual_orders.research.setups import RESEARCH_STRATEGY, build_candidate

VALID_LEVELS = RiskLevels(
    levels_version="levels-v1", policy={}, direction=Direction.LONG, entry_zone_low=D(100),
    entry_zone_high=D(101), stop=D(98), target1=D(105), target2=D(108), risk=D(3), risk_reward=D(2),
    basis={}, valid=True, errors=(),
)
EVIDENCE = BacktestStats(
    backtest_version="backtest-v1", samples=120, resolved=100, wins=58, losses=42, timeouts=20, ambiguous=3,
    gap_through=2, win_rate=D("0.58"), avg_return_pct=D("0.5"), median_return_pct=D("0.4"),
    expectancy_r=D("0.21"), avg_r=D("0.21"), profit_factor=D("1.4"), max_drawdown_r=4.2, avg_mfe_r=D("1.1"),
    avg_mae_r=D("-0.6"), win_rate_ci=(0.48, 0.67), expectancy_ci=(0.02, 0.4), warnings=(),
)


def engulfing():
    (found,) = [item for item in build_occurrences(bullish_engulfing_after_decline(), ticker="AAPL",
                                                   data_as_of=BASE)
                if item.pattern == "BULLISH_ENGULFING"]
    return found


def candidate(levels=VALID_LEVELS, **overrides):
    found = engulfing()
    built = build_candidate(
        ticker="AAPL", detection=found.detection, snapshot=found.features,
        score=score_setup(found.detection, found.features, Direction.LONG), data_as_of=BASE, levels=levels,
    )
    assert built is not None
    return replace(built, **overrides) if overrides else built


# --- scoring -----------------------------------------------------------------------------------------------
def test_every_component_and_the_composite_stay_inside_zero_and_one():
    found = engulfing()
    score = score_setup(found.detection, found.features, Direction.LONG)
    assert set(score.components) == {"pattern", "trend", "volume", "pressure", "structure"}
    assert all(D(0) <= value <= D(1) for value in score.components.values())
    assert D(0) <= score.deterministic_score <= D(1)
    assert score.interpretation == SCORE_INTERPRETATION
    assert "not a probability" in score.interpretation


def test_an_unavailable_input_scores_neutral_rather_than_zero_or_one():
    found = engulfing()
    blank = replace(found.features, relative_volume=None, cmf=None, support_distance_pct=None,
                    resistance_distance_pct=None)
    score = score_setup(found.detection, blank, Direction.LONG)
    assert score.components["volume"] == D("0.5000")
    assert score.components["pressure"] == D("0.5000")


def test_weights_must_sum_to_one():
    assert sum(ScoreWeights().snapshot().values()) == D(1)
    with pytest.raises(ValueError, match="must sum to 1"):
        ScoreWeights(pattern=D("0.9"))


def test_a_stronger_pattern_scores_higher_with_everything_else_equal():
    found = engulfing()
    weak = replace(found.detection, overall_score=D("0.10"))
    strong = replace(found.detection, overall_score=D("0.90"))
    assert (score_setup(strong, found.features, Direction.LONG).deterministic_score
            > score_setup(weak, found.features, Direction.LONG).deterministic_score)


# --- levels ------------------------------------------------------------------------------------------------
def levels_for(direction=PatternDirection.BULLISH, policy=DEFAULT_POLICY):
    series = zigzag(cycles=3)
    indicators = compute_series(series)
    index = len(series) - 5
    atr = indicators.atr_at(index)
    assert atr is not None
    structure = structure_at(series, index, indicators, swings=swing_points(series))
    return propose_levels(series[index - 1 : index + 1], direction=direction, atr=atr, structure=structure,
                          policy=policy)


def test_a_long_chain_is_ordered_and_quantized_to_a_real_tick():
    levels = levels_for()
    assert levels.direction is Direction.LONG
    assert levels.stop < levels.entry_zone_low <= levels.entry_zone_high < levels.target1
    for price in (levels.entry_zone_low, levels.entry_zone_high, levels.stop, levels.target1):
        assert price == to_tick(price)
    assert levels.basis["atr"] is not None and levels.levels_version == "levels-v1"


def test_a_short_chain_is_the_mirror():
    levels = levels_for(PatternDirection.BEARISH)
    assert levels.direction is Direction.SHORT
    assert levels.stop > levels.entry_zone_high >= levels.entry_zone_low > levels.target1


def test_a_chain_is_validated_by_the_platforms_own_rule_and_never_repaired():
    levels = levels_for()
    if levels.valid:
        spec = levels.signal_spec("AAPL", valid_sessions=3)
        assert validate_signal(spec) == []
    else:
        assert levels.errors  # reported, never silently adjusted
        with pytest.raises(ValueError, match="invalid level chain"):
            levels.signal_spec("AAPL", valid_sessions=3)


def test_a_poor_reward_to_risk_is_reported_rather_than_proposed():
    demanding = levels_for(policy=LevelPolicy(min_risk_reward=D(99)))
    assert demanding.valid is False and "RISK_REWARD_BELOW_MINIMUM" in demanding.errors


def test_level_inputs_are_validated():
    series = zigzag(cycles=2)
    indicators = compute_series(series)
    structure = structure_at(series, 10, indicators, swings=swing_points(series))
    with pytest.raises(ValueError, match="atr must be positive"):
        propose_levels(series[:2], direction=PatternDirection.BULLISH, atr=D(0), structure=structure)
    with pytest.raises(ValueError, match="neutral pattern"):
        propose_levels(series[:2], direction=PatternDirection.NEUTRAL, atr=D(1), structure=structure)
    with pytest.raises(ValueError, match="at least one candle"):
        propose_levels([], direction=PatternDirection.BULLISH, atr=D(1), structure=structure)
    with pytest.raises(ValueError, match="must be positive"):
        LevelPolicy(target1_atr=D(0))


def structure_with(support=None, resistance=None):
    """A structure whose only meaningful content is the two confirmed levels the clamp actually reads."""
    return MarketStructure(
        context_version="context-v1", short_term_trend=TrendState.SIDEWAYS,
        medium_term_trend=TrendState.SIDEWAYS, ema20_slope_pct=None, support=support, resistance=resistance,
        support_distance_pct=None, resistance_distance_pct=None, breakout=BreakoutState.INSIDE,
        relative_atr_pct=None, gap=GapState.NONE, gap_pct=None, swings_confirmed=2, swing_left=2,
        swing_right=2,
    )


LONG_PATTERN = [candle(0, 100, 101, 99, 99.5), candle(1, 99.5, 105, 99, 104.5)]
SHORT_PATTERN = [candle(0, 105, 106, 104, 104.5), candle(1, 104.5, 105, 100, 100.5)]


def test_a_long_records_the_resistance_that_capped_it_and_the_support_that_widened_its_stop():
    levels = propose_levels(LONG_PATTERN, direction=PatternDirection.BULLISH, atr=D(2),
                            structure=structure_with(support=D(97), resistance=D(107)))
    assert levels.direction is Direction.LONG
    # target1 would have been 105 + 2xATR = 109; resistance sits in front of it.
    assert levels.target1 == to_tick(D(107))
    assert levels.basis["target1_capped_by_structure"] is True
    assert levels.basis["capping_level"] == to_tick(D(107))
    # the stop would have been 99 - 0.25xATR = 98.50; support pushes it below 97 instead.
    assert levels.stop == to_tick(D("96.5"))
    assert levels.basis["stop_widened_by_structure"] is True
    assert levels.basis["widening_level"] == to_tick(D(97))


def test_a_short_records_the_support_that_capped_it_and_the_resistance_that_widened_its_stop():
    """The mirror of the long case, and the one that was structurally unrecordable.

    Both flags used to be long-side by construction — `resistance_capped_target1` compared target1 against
    resistance, and `support_widened_stop` was hard-coded `and long` — so a short recorded `false` however
    hard structure had moved its prices. The clamp that decides reward was invisible in the stored evidence
    for exactly half the candidates.
    """
    levels = propose_levels(SHORT_PATTERN, direction=PatternDirection.BEARISH, atr=D(2),
                            structure=structure_with(support=D(98), resistance=D(107)))
    assert levels.direction is Direction.SHORT
    # target1 would have been 100 - 2xATR = 96; support sits in front of it.
    assert levels.target1 == to_tick(D(98))
    assert levels.basis["target1_capped_by_structure"] is True
    assert levels.basis["capping_level"] == to_tick(D(98))
    # the stop would have been 106 + 0.25xATR = 106.50; resistance pushes it above 107 instead.
    assert levels.stop == to_tick(D("107.5"))
    assert levels.basis["stop_widened_by_structure"] is True
    assert levels.basis["widening_level"] == to_tick(D(107))


@pytest.mark.parametrize("direction,pattern", [
    (PatternDirection.BULLISH, LONG_PATTERN),
    (PatternDirection.BEARISH, SHORT_PATTERN),
])
def test_a_chain_structure_never_touched_records_neither_a_cap_nor_a_widening(direction, pattern):
    levels = propose_levels(pattern, direction=direction, atr=D(2), structure=structure_with())
    assert levels.basis["target1_capped_by_structure"] is False
    assert levels.basis["capping_level"] is None
    assert levels.basis["stop_widened_by_structure"] is False
    assert levels.basis["widening_level"] is None


@pytest.mark.parametrize("direction,pattern,level,entry", [
    (PatternDirection.BEARISH, SHORT_PATTERN, {"support": D("99.96")}, D(100)),
    (PatternDirection.BULLISH, LONG_PATTERN, {"resistance": D("105.04")}, D(105)),
])
def test_a_level_within_cents_of_entry_blocks_the_setup_instead_of_becoming_its_target(
    direction, pattern, level, entry
):
    """The funnel's dominant rejection, named for what is actually wrong with it.

    A confirmed level sitting just in front of entry used to pull target1 onto itself, so reward became the
    distance to that level however small — four cents against 7.50 of risk in the worst case observed live,
    reported as RISK_REWARD_BELOW_MINIMUM as though the floor were the problem. No floor rescues four cents.
    A level closer than `min_target_atr` x ATR means the path to a first objective is blocked, and that is
    the reason the chain now carries.
    """
    levels = propose_levels(pattern, direction=direction, atr=D(2), structure=structure_with(**level))
    (blocking,) = level.values()
    assert levels.target1 == to_tick(blocking)  # what blocked it is still recorded, at its real price
    assert levels.basis["target1_capped_by_structure"] is True
    assert levels.basis["target1_blocked_by_structure"] is True
    assert levels.valid is False
    assert levels.errors == (TARGET_BLOCKED_BY_STRUCTURE,)
    assert RISK_REWARD_BELOW_MINIMUM not in levels.errors  # the symptom never stands in for the cause
    assert abs(levels.target1 - entry) < D(2) * DEFAULT_POLICY.min_target_atr


def test_a_level_with_genuine_room_still_caps_the_target_and_is_judged_on_reward():
    """The clamp is not the defect and is not removed: structure still decides where the first objective is."""
    levels = propose_levels(SHORT_PATTERN, direction=PatternDirection.BEARISH, atr=D(2),
                            structure=structure_with(support=D(97)))
    assert levels.target1 == to_tick(D(97))
    assert levels.basis["target1_capped_by_structure"] is True
    assert levels.basis["target1_blocked_by_structure"] is False
    # 3.00 of reward against 6.50 of risk: a real objective, honestly short of the reward-to-risk floor.
    assert levels.risk_reward == D("0.4615")
    assert levels.errors == (RISK_REWARD_BELOW_MINIMUM,)


def test_a_target_floor_beyond_the_target_itself_is_refused():
    with pytest.raises(ValueError, match="min_target_atr must not exceed target1_atr"):
        LevelPolicy(target1_atr=D(2), min_target_atr=D(3))


# --- candidates --------------------------------------------------------------------------------------------
def test_a_candidate_is_identified_the_same_way_on_every_rescan():
    first = candidate()
    later = build_candidate(
        ticker="AAPL", detection=engulfing().detection, snapshot=engulfing().features,
        score=score_setup(engulfing().detection, engulfing().features, Direction.LONG),
        data_as_of=BASE + timedelta(hours=6), levels=VALID_LEVELS,
    )
    assert later is not None
    # The scan's watermark is recorded but is not part of the identity: re-scanning is not a second signal.
    assert first.client_signal_id == later.client_signal_id
    assert first.candidate_key == later.candidate_key
    assert first.strategy == RESEARCH_STRATEGY and ENGINE_VERSION in first.strategy_version


def test_a_different_reading_of_the_same_candle_is_a_different_candidate_hash():
    first = candidate()
    altered = replace(first, deterministic_score=D("0.99"))
    assert altered.candidate_hash != first.candidate_hash
    assert altered.client_signal_id == first.client_signal_id  # same occurrence, different reading


def test_two_patterns_detected_on_one_bar_never_share_a_client_signal_id():
    """One bar can satisfy several detectors, and the id has to separate them.

    Live during the canary, BEARISH_ENGULFING and EVENING_STAR fired on the same AAPL 1h candle. The id used
    to be `strategy-version:ticker:timeframe:detected_at`, so both rows carried it identically — and because
    `signals.client_signal_id` is UNIQUE, intake would keep whichever was promoted first and drop the rest.
    For two same-direction readings that is arguably right; for two families disagreeing on direction it
    picks a side by iteration order. `candidate_key` already separated them; the id now agrees with it.
    """
    bullish = candidate()
    opposite = replace(bullish, pattern="EVENING_STAR", direction=Direction.SHORT)
    same_side = replace(bullish, pattern="THREE_WHITE_SOLDIERS")
    assert bullish.detected_at == opposite.detected_at == same_side.detected_at
    ids = {bullish.client_signal_id, opposite.client_signal_id, same_side.client_signal_id}
    assert len(ids) == 3
    keys = {bullish.candidate_key, opposite.candidate_key, same_side.candidate_key}
    assert len(keys) == 3  # the id and the research key now separate the same occurrences


def test_a_neutral_pattern_never_becomes_a_candidate():
    found = engulfing()
    neutral = replace(found.detection, direction=PatternDirection.NEUTRAL)
    assert build_candidate(ticker="AAPL", detection=neutral, snapshot=found.features,
                           score=score_setup(found.detection, found.features, Direction.LONG),
                           data_as_of=BASE, levels=None) is None


def test_the_thesis_is_structured_evidence_not_prose():
    thesis = candidate().thesis()
    assert thesis["kind"] == "RESEARCH_CANDLESTICK_SETUP"
    assert set(thesis) >= {"pattern", "direction", "score_components", "pattern_evidence", "context",
                           "pressure_estimate", "levels", "feature_hash", "score_interpretation"}
    assert thesis["pressure_estimate"]["disclaimer"]  # the estimate always travels with its disclaimer
    assert isinstance(thesis["score_components"], dict)


def test_a_model_observes_a_candidate_it_never_rewrites_one():
    original = candidate()
    scored = original.with_prediction(probability=D("0.63"), model_version="m-1", label_version="labels-v1")
    assert (original.ml_probability, original.model_version) == (None, None)
    assert (scored.ml_probability, scored.model_version) == (D("0.63"), "m-1")
    assert scored.thesis()["ml"]["probability_target_first"] == D("0.63")
    assert scored.deterministic_score == original.deterministic_score


# --- promotion ---------------------------------------------------------------------------------------------
def test_an_unvalidated_candidate_is_never_promotable():
    errors = promotion_errors(candidate(levels=None, deterministic_score=D("0.20")))
    assert NOT_VALIDATED in errors and NO_LEVELS in errors and SCORE_BELOW_MINIMUM in errors
    assert is_promotable(candidate(levels=None)) is False


def test_thin_or_unprofitable_evidence_blocks_promotion():
    strong = candidate(deterministic_score=D("0.72"))
    assert promotion_errors(strong, evidence=replace(EVIDENCE, samples=12)) == (INSUFFICIENT_BACKTEST_SAMPLES,)
    assert promotion_errors(strong, evidence=replace(EVIDENCE, expectancy_r=D("-0.1"))) == (
        EXPECTANCY_BELOW_MINIMUM,)
    assert promotion_errors(strong, evidence=replace(EVIDENCE, expectancy_r=None)) == (EXPECTANCY_BELOW_MINIMUM,)


def test_an_invalid_level_chain_blocks_promotion():
    broken = candidate(levels=replace(VALID_LEVELS, valid=False, errors=("STOP_NOT_BEYOND_ZONE",)),
                       deterministic_score=D("0.72"))
    assert promotion_errors(broken, evidence=EVIDENCE) == (INVALID_LEVELS,)
    with pytest.raises(ValueError, match="no signal body"):
        signal_body(broken)


def test_a_policy_can_require_a_model_probability():
    strong = candidate(deterministic_score=D("0.72"))
    policy = PromotionPolicy(require_ml_probability=True)
    assert promotion_errors(strong, evidence=EVIDENCE, policy=policy) == (ML_PROBABILITY_REQUIRED,)
    scored = strong.with_prediction(probability=D("0.80"), model_version="m-1", label_version="labels-v1")
    assert promotion_errors(scored, evidence=EVIDENCE, policy=policy) == ()


def test_a_promoted_body_is_accepted_by_the_existing_intake_unchanged():
    decision = decide(candidate(deterministic_score=D("0.72")), evidence=EVIDENCE)
    assert decision.promotable is True and decision.errors == ()
    body = decision.body
    assert body is not None
    parsed = parse_signal_body(body)
    assert parsed.spec.ticker == "AAPL" and parsed.spec.direction is Direction.LONG
    assert (parsed.spec.entry_zone_low, parsed.spec.stop, parsed.spec.target1) == (D(100), D(98), D(105))
    assert parsed.score == D("0.72")
    assert parsed.client_signal_id == decision.client_signal_id
    assert isinstance(body["thesis"], str) and '"kind":"RESEARCH_CANDLESTICK_SETUP"' in body["thesis"]
    assert body["source"] == "research_scan"


def test_promotion_policy_values_are_validated():
    for bad in ({"valid_sessions": 0}, {"valid_sessions": 21}, {"min_deterministic_score": D(2)},
                {"min_ml_probability": D(-1)}, {"min_backtest_samples": 0}):
        with pytest.raises(ValueError):
            PromotionPolicy(**bad)


def test_real_statistics_can_be_used_as_promotion_evidence():
    stats = summarize(build_occurrences(zigzag(cycles=4), ticker="AAPL", data_as_of=BASE))
    errors = promotion_errors(candidate(deterministic_score=D("0.72")), evidence=stats)
    assert NOT_VALIDATED not in errors  # evidence was supplied; whether it passes depends on the numbers
