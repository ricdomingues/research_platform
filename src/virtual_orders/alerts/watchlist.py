"""Owner watchlist and alert-rule definitions (owner 2026-09-13; controller ruling for 3B; D26). Mutable by design."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Connection, delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from virtual_orders.evaluator.clock import require_aware
from virtual_orders.storage.tables import alert_rules, watchlist

DEFAULT_COOLDOWN_MINUTES = 30
MAX_COOLDOWN_MINUTES = 1440
MIN_WINDOW_BARS, MAX_WINDOW_BARS = 5, 390
RULE_FIELDS = frozenset({"kind", "level", "direction", "cmf_threshold", "window_bars", "cooldown_minutes"})
ZERO, ONE = Decimal(0), Decimal(1)


class RuleKind(StrEnum):
    PRICE_CROSS = "PRICE_CROSS"
    PRESSURE = "PRESSURE"


class CrossDirection(StrEnum):
    ABOVE = "ABOVE"
    BELOW = "BELOW"


class AlertRuleInvalid(ValueError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


class WatchlistTickerNotFound(LookupError):
    pass


class AlertRuleNotFound(LookupError):
    pass


@dataclass(frozen=True)
class RuleDraft:
    kind: RuleKind
    level: Decimal | None
    direction: CrossDirection | None
    cmf_threshold: Decimal | None
    window_bars: int | None
    cooldown_minutes: int


@dataclass(frozen=True)
class AlertRule:
    id: UUID
    ticker: str
    kind: RuleKind
    level: Decimal | None
    direction: CrossDirection | None
    cmf_threshold: Decimal | None
    window_bars: int | None
    cooldown_minutes: int
    created_at: datetime


def _decimal(body: Mapping[str, Any], name: str, errors: list[str]) -> Decimal | None:
    raw = body.get(name)
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int | Decimal):
        errors.append(f"INVALID_DECIMAL:{name}")
        return None
    value = Decimal(raw)
    if not value.is_finite():
        errors.append(f"INVALID_DECIMAL:{name}")
        return None
    return value


def _integer(body: Mapping[str, Any], name: str, errors: list[str]) -> int | None:
    raw = body.get(name)
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int):
        errors.append(f"INVALID_INTEGER:{name}")
        return None
    return int(raw)


def parse_rule_body(body: Mapping[str, Any]) -> RuleDraft:
    """Every error is reported at once, in a fixed order (API 422 ALERT_RULE_INVALID)."""
    errors = [f"UNKNOWN_FIELD:{name}" for name in sorted(set(body) - RULE_FIELDS)]
    raw_kind = body.get("kind")
    kind = RuleKind(raw_kind) if isinstance(raw_kind, str) and raw_kind in RuleKind.__members__ else None
    if kind is None:
        errors.append("INVALID_CHOICE:kind")
    level = _decimal(body, "level", errors)
    cmf_threshold = _decimal(body, "cmf_threshold", errors)
    window_bars = _integer(body, "window_bars", errors)
    cooldown = _integer(body, "cooldown_minutes", errors)
    cooldown_minutes = DEFAULT_COOLDOWN_MINUTES if cooldown is None else cooldown
    if not 0 <= cooldown_minutes <= MAX_COOLDOWN_MINUTES:
        errors.append("OUT_OF_RANGE:cooldown_minutes")
    raw_direction = body.get("direction")
    direction = (CrossDirection(raw_direction)
                 if isinstance(raw_direction, str) and raw_direction in CrossDirection.__members__ else None)
    if raw_direction is not None and direction is None:
        errors.append("INVALID_CHOICE:direction")
    if kind is RuleKind.PRICE_CROSS:
        if body.get("level") is None:
            errors.append("MISSING_FIELD:level")
        elif level is not None and level <= ZERO:
            errors.append("OUT_OF_RANGE:level")
        if raw_direction is None:
            errors.append("MISSING_FIELD:direction")
        errors.extend(f"NOT_ALLOWED:{name}" for name in ("cmf_threshold", "window_bars") if body.get(name) is not None)
    elif kind is RuleKind.PRESSURE:
        if body.get("cmf_threshold") is None:
            errors.append("MISSING_FIELD:cmf_threshold")
        elif cmf_threshold is not None and not ZERO < cmf_threshold < ONE:
            errors.append("OUT_OF_RANGE:cmf_threshold")
        if body.get("window_bars") is None:
            errors.append("MISSING_FIELD:window_bars")
        elif window_bars is not None and not MIN_WINDOW_BARS <= window_bars <= MAX_WINDOW_BARS:
            errors.append("OUT_OF_RANGE:window_bars")
        errors.extend(f"NOT_ALLOWED:{name}" for name in ("level", "direction") if body.get(name) is not None)
    if errors or kind is None:
        raise AlertRuleInvalid(errors)
    return RuleDraft(kind, level, direction, cmf_threshold, window_bars, cooldown_minutes)


def add_ticker(conn: Connection, ticker: str, *, added_at: datetime) -> bool:
    stmt = (
        pg_insert(watchlist)
        .values(ticker=ticker, added_at=require_aware(added_at, "added_at"))
        .on_conflict_do_nothing(index_elements=["ticker"])
        .returning(watchlist.c.ticker)
    )
    return conn.execute(stmt).first() is not None


def is_watched(conn: Connection, ticker: str) -> bool:
    return conn.execute(select(watchlist.c.ticker).where(watchlist.c.ticker == ticker)).first() is not None


def remove_ticker(conn: Connection, ticker: str) -> None:
    removed = conn.execute(delete(watchlist).where(watchlist.c.ticker == ticker).returning(watchlist.c.ticker))
    if removed.first() is None:
        raise WatchlistTickerNotFound(ticker)


def watchlist_tickers(conn: Connection) -> list[str]:
    return list(conn.execute(select(watchlist.c.ticker).order_by(watchlist.c.ticker)).scalars())


def create_rule(conn: Connection, ticker: str, draft: RuleDraft, *, created_at: datetime) -> AlertRule:
    if not is_watched(conn, ticker):
        raise WatchlistTickerNotFound(ticker)
    rule = AlertRule(uuid4(), ticker, draft.kind, draft.level, draft.direction, draft.cmf_threshold,
                     draft.window_bars, draft.cooldown_minutes, require_aware(created_at, "created_at"))
    conn.execute(alert_rules.insert().values(
        id=rule.id, ticker=ticker, kind=rule.kind.value, level=rule.level,
        direction=None if rule.direction is None else rule.direction.value, cmf_threshold=rule.cmf_threshold,
        window_bars=rule.window_bars, cooldown_minutes=rule.cooldown_minutes, created_at=rule.created_at,
    ))
    return rule


def delete_rule(conn: Connection, rule_id: UUID) -> None:
    removed = conn.execute(delete(alert_rules).where(alert_rules.c.id == rule_id).returning(alert_rules.c.id))
    if removed.first() is None:
        raise AlertRuleNotFound(str(rule_id))


def list_rules(conn: Connection) -> list[AlertRule]:
    rows = conn.execute(select(alert_rules).order_by(alert_rules.c.ticker, alert_rules.c.created_at, alert_rules.c.id))
    return [
        AlertRule(row.id, row.ticker, RuleKind(row.kind), row.level,
                  None if row.direction is None else CrossDirection(row.direction), row.cmf_threshold,
                  row.window_bars, row.cooldown_minutes, row.created_at)
        for row in rows
    ]


def list_watchlist(conn: Connection) -> list[dict[str, Any]]:
    by_ticker: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for rule in list_rules(conn):
        by_ticker[rule.ticker].append(asdict(rule))
    return [
        {"ticker": row.ticker, "added_at": row.added_at, "rules": by_ticker.get(row.ticker, [])}
        for row in conn.execute(select(watchlist).order_by(watchlist.c.ticker))
    ]
