"""Content identity of the bars behind a reading (Plan 6, D96).

A reading's identity is the DATA it was built from, never the batch that delivered it. Re-ingesting the same
minute under a new `batch_id` is a non-event and must not look like a vendor revision: only a change in the
bars' own values, or in which minutes are present at all, is a reason to re-derive anything.

Pure: no I/O, no platform imports beyond the shared `Bar` and hashing helpers.
"""

from __future__ import annotations

from collections.abc import Sequence

from core.domain.hashing import sha256_hex
from core.domain.models import Bar
from virtual_orders.research.models import Candle


def bars_content_hash(bars: Sequence[Bar]) -> str:
    """Identity of a set of bars: their timestamps and values, sorted, with provenance deliberately excluded.

    `batch_id` is absent on purpose. Absence of a minute is itself part of the identity, so a candle built
    from fourteen minutes never hashes like the same candle built from fifteen.
    """
    return sha256_hex([
        {"ts": item.ts, "open": item.open, "high": item.high, "low": item.low,
         "close": item.close, "volume": item.volume}
        for item in sorted(bars, key=lambda item: item.ts)
    ])


def candle_input_bars(bars: Sequence[Bar], candle: Candle) -> list[Bar]:
    """The bars that fell inside `candle`'s bucket, in chronological order."""
    return sorted(
        (item for item in bars if candle.ts <= item.ts <= candle.end_ts),
        key=lambda item: item.ts,
    )


def candles_input_hash(bars: Sequence[Bar], candles: Sequence[Candle]) -> str:
    """Identity of the data behind a window of candles, taken together.

    A reading is rarely a function of one bucket: a three-candle pattern at bucket N is derived from buckets
    N-2 through N, so a vendor correction to any of them changes what the engine says at N. Hashing only the
    bucket's own bars made such a correction invisible, and the corrected reading was then stored beside the
    stale one with nothing marking either as retired.

    Buckets do not overlap, so this is the identity of their bars taken as one set; `bars_content_hash` sorts,
    so the order the windows are assembled in cannot change the answer.
    """
    return bars_content_hash([
        item for candle in candles for item in candle_input_bars(bars, candle)
    ])


def candle_input_hash(bars: Sequence[Bar], candle: Candle) -> str:
    """Identity of the data behind one candle."""
    return candles_input_hash(bars, [candle])
