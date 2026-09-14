"""Provider-neutral resolution of bar feeds. There is no fallback: an order reads exactly its price_source."""

from __future__ import annotations

from collections.abc import Iterable

from virtual_orders.marketdata.sources import BarSource


class UnknownDataSource(LookupError):
    """No feed is registered for the requested source id."""


class MarketDataGateway:
    def __init__(self, bar_sources: Iterable[BarSource] = ()) -> None:
        self._bars: dict[str, BarSource] = {}
        for feed in bar_sources:
            if feed.source in self._bars:
                raise ValueError(f"duplicate bar source id: {feed.source}")
            self._bars[feed.source] = feed

    @property
    def source_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._bars))

    def bar_source(self, source_id: str) -> BarSource:
        try:
            return self._bars[source_id]
        except KeyError:
            raise UnknownDataSource(
                f"no bar source registered for {source_id!r}; fallback is not allowed"
            ) from None
