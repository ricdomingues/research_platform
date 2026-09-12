from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

import pytest

from core.domain.hashing import canonical_json, sha256_hex


class Color(StrEnum):
    RED = "RED"


def test_key_order_does_not_change_hash():
    assert sha256_hex({"a": 1, "b": 2}) == sha256_hex({"b": 2, "a": 1})


def test_equivalent_decimals_hash_equal():
    assert sha256_hex({"p": Decimal("1.50")}) == sha256_hex({"p": Decimal("1.5")})
    assert canonical_json(Decimal("100")) == '"100"'
    assert canonical_json(Decimal("-0.00")) == '"0"'


def test_float_is_rejected():
    with pytest.raises(TypeError):
        canonical_json({"p": 1.5})


def test_naive_datetime_is_rejected():
    with pytest.raises(ValueError):
        canonical_json(datetime(2025, 11, 25, 15, 30))


def test_same_instant_in_other_timezone_hashes_equal():
    utc = datetime(2025, 11, 25, 15, 30, tzinfo=timezone.utc)
    other = utc.astimezone(timezone(timedelta(hours=-5)))
    assert sha256_hex({"t": utc}) == sha256_hex({"t": other})


def test_supported_types_and_digest_shape():
    value = {
        "id": UUID("12345678-1234-5678-1234-567812345678"),
        "color": Color.RED,
        "items": (Decimal("1"), None, True, "x"),
    }
    assert canonical_json(value) == (
        '{"color":"RED","id":"12345678-1234-5678-1234-567812345678",'
        '"items":["1",null,true,"x"]}'
    )
    digest = sha256_hex(value)
    assert len(digest) == 64
    int(digest, 16)


def test_canonical_json_ignores_ambient_decimal_context():
    from decimal import Context, localcontext

    value = {"p": Decimal("1.234567890123456789012345")}
    expected = '{"p":"1.234567890123456789012345"}'
    assert canonical_json(value) == expected
    with localcontext(Context(prec=12)):
        assert canonical_json(value) == expected


@pytest.mark.parametrize("value", [{1: "x"}, {"outer": {UUID(int=1): "x"}}, [{None: 1}]])
def test_non_string_dict_keys_are_rejected(value):
    with pytest.raises(TypeError):
        canonical_json(value)
