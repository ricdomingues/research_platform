from core.domain.models import Direction
from core.domain.validation import validate_signal
from tests.support import D, long_signal


def short_signal(**overrides):
    values = dict(
        direction=Direction.SHORT, entry_zone_low=D(100), entry_zone_high=D(102),
        stop=D(105), target1=D(96), target2=D(92),
    )
    values.update(overrides)
    return long_signal(**values)


def test_valid_signals_have_no_errors():
    assert validate_signal(long_signal()) == []
    assert validate_signal(long_signal(target2=None)) == []
    assert validate_signal(long_signal(entry_zone_high=D(100))) == []
    assert validate_signal(short_signal()) == []
    assert validate_signal(short_signal(target2=None)) == []


def test_long_chain_violations():
    assert validate_signal(long_signal(stop=D(100))) == ["STOP_NOT_BEYOND_ZONE"]
    assert validate_signal(long_signal(entry_zone_high=D(99), stop=D(90))) == ["ZONE_INVERTED"]
    assert validate_signal(long_signal(target1=D(102))) == ["TARGET1_NOT_BEYOND_ZONE"]
    assert validate_signal(long_signal(target2=D(106))) == ["TARGET2_NOT_BEYOND_TARGET1"]


def test_short_chain_violations():
    assert validate_signal(short_signal(stop=D(102))) == ["STOP_NOT_BEYOND_ZONE"]
    assert validate_signal(short_signal(target1=D(100))) == ["TARGET1_NOT_BEYOND_ZONE"]
    assert validate_signal(short_signal(target2=D(96))) == ["TARGET2_NOT_BEYOND_TARGET1"]


def test_field_level_errors():
    assert validate_signal(long_signal(ticker="aapl")) == ["TICKER_INVALID"]
    assert validate_signal(long_signal(trigger_price=D(0))) == ["TRIGGER_PRICE_NOT_POSITIVE"]
    assert validate_signal(long_signal(valid_sessions=21)) == ["VALID_SESSIONS_OUT_OF_RANGE"]
