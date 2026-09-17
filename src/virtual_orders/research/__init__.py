"""Candlestick, technical-context, backtest and model research over the bars the platform already stores.

Nothing here creates market data, orders or signals of its own: the pure engines read `core.domain.models.Bar`
values handed to them, and the persistence layer reads the stored `bars_1m` through the existing `data_as_of`
watermark. Research observations live in their own tables and only ever become platform signals through the
existing signal intake (spec 3.3, 5.1).
"""
