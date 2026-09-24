"""Content identity of the bars behind a reading (Plan 6, D96).

A reading's identity is the DATA it was built from, never the batch that delivered it. Re-ingesting the same
minute under a new `batch_id` is a non-event and must not look like a vendor revision: only a change in the
bars' own values, or in which minutes are present at all, is a reason to re-derive anything.

Pure: no I/O, no platform imports beyond the shared `Bar` and hashing helpers.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

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


@dataclass(frozen=True)
class BarIndex:
    """The bars in timestamp order, so a bucket's bars are found by bisection instead of by scanning them all.

    A reading's identity spans more than twenty buckets, and the scan asks for that window once per candle of
    a session. Filtering the whole list per bucket made that quadratic on the scan's hot path; building the
    order once and bisecting does not. Build it with `bar_index` and hand the same one to every lookup.
    """

    bars: tuple[Bar, ...]
    timestamps: tuple[datetime, ...]

    def between(self, start: datetime, end: datetime) -> tuple[Bar, ...]:
        """The bars with `start <= ts <= end`, in chronological order."""
        return self.bars[bisect_left(self.timestamps, start):bisect_right(self.timestamps, end)]


def bar_index(bars: Sequence[Bar]) -> BarIndex:
    """Order the bars once for repeated bucket lookups. Read order cannot change what a lookup returns."""
    ordered = tuple(sorted(bars, key=lambda item: item.ts))
    return BarIndex(ordered, tuple(item.ts for item in ordered))


def candle_input_bars(bars: Sequence[Bar], candle: Candle) -> list[Bar]:
    """The bars that fell inside `candle`'s bucket, in chronological order."""
    return list(bar_index(bars).between(candle.ts, candle.end_ts))


def candles_input_hash(index: BarIndex, candles: Sequence[Candle]) -> str:
    """Identity of the data behind a window of candles, taken together.

    A reading is never a function of one bucket. A three-candle pattern at bucket N is derived from buckets
    N-2 through N, and its context score and spliced prior-trend evidence are measured over the twenty buckets
    before those — so a correction anywhere in that span changes what the engine says at N. Hashing a narrower
    window made such a correction invisible, and because the corrected reading is stored regardless, the
    bucket was left holding two contradictory active readings with nothing linking them.

    Bucket by bucket, never one range from the first candle's start to the last one's end: a single range
    would sweep in bars that fell between buckets — extended-hours minutes and session gaps — which no candle
    was built from and whose correction changes no reading. Buckets do not overlap, so this is the identity of
    their bars taken as one set, and `bars_content_hash` sorts, so assembly order cannot change the answer.
    """
    return bars_content_hash([
        item for candle in candles for item in index.between(candle.ts, candle.end_ts)
    ])


def candle_input_hash(bars: Sequence[Bar], candle: Candle) -> str:
    """Identity of the data behind one candle."""
    return candles_input_hash(bar_index(bars), [candle])
