"""D57: the dashboard's view of the API. A recorded session is walked through the real routes, and the JSON shape of
every response the dashboard reads is compared with dashboard/tests/fixtures/api/<name>.json. With
DASHBOARD_CONTRACT_RECORD=1 the responses are written as those fixtures instead; the dashboard project then asserts
its client, view-models and figures against them. The engine never imports the dashboard (D56).

Fixtures are normalized before writing (and the same normalization is applied before comparing): real run ids,
batch ids and other server-generated UUIDs are replaced by a stable placeholder numbered in first-seen order (the
mapping is shared across every file recorded in one run, so joins such as replay_pairs still line up); any
timestamp the API stamped with the real wall clock (data_as_of, started_at, latest_ingested_at, observed_at,
recorded_at, and the alert outbox's created_at/last_attempted_at) is replaced by a fixed sentinel, while timestamps
that come from the fake clock or the scenario itself (bar ts, order/signal created_at, valid_until_ts, ...) all fall
on the scenario's own calendar months and are left untouched; and every `*_hash` field (payload_hash,
selected_data_hash, ...) is pinned to a fixed placeholder too, because those hashes are computed server-side over
material that includes a bar batch id (marketdata/asof.py's selected_data_hash, core/domain/models.py's
Event.hash_material) which is itself a random id never echoed back verbatim in any response we can normalize by
value — they are only ever shape-checked (a fixed-length hex string), never compared by value. This keeps two
consecutive recordings byte-identical."""

import json
import os
import re
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select

from tests.integration.support import DAY, ROOT, flat_raw, post_json, scenario_bars, signal_body
from tests.support import et
from virtual_orders.alerts.outbox import AlertKind, enqueue_alert
from virtual_orders.storage import tables
from virtual_orders.worker.jobs import JobResult, WorkerJobs

FIXTURES = ROOT / "dashboard" / "tests" / "fixtures" / "api"
RECORD = os.environ.get("DASHBOARD_CONTRACT_RECORD") == "1"
NEXT = "2025-11-26"
# Subtrees whose keys depend on the event type, cause code or review reason by design: compared as opaque values.
OPAQUE = frozenset({"detail", "payload", "config_snapshot", "reasons", "needs_review", "missing_runs"})
WILDCARDS = frozenset({"null", "opaque"})

_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
# Any ISO date/timestamp, with the year-month captured so it can be checked against the scenario's own calendar.
_TIMESTAMP_RE = re.compile(r"^(?P<month>\d{4}-\d{2})-\d{2}(T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2}))?$")
# The recorded scenario only ever produces business dates in these months (DAY/NEXT plus the valid_until horizon a
# few trading sessions later); anything outside them is the real wall clock the test happened to run on.
_SCENARIO_MONTHS = frozenset({"2025-11", "2025-12"})
_TIMESTAMP_SENTINEL = "2000-01-01T00:00:00+00:00"
_HASH_SENTINEL = "0" * 64


class Normalizer:
    """Replaces server-generated UUIDs, wall-clock timestamps and non-reproducible hashes with stable placeholders."""

    def __init__(self) -> None:
        self.uuid_map: dict[str, str] = {}

    def _uuid_placeholder(self, value: str) -> str:
        if value not in self.uuid_map:
            self.uuid_map[value] = f"00000000-0000-4000-8000-{len(self.uuid_map) + 1:012d}"
        return self.uuid_map[value]

    def _string(self, value: str) -> str:
        if _UUID_RE.match(value):
            return self._uuid_placeholder(value)
        match = _TIMESTAMP_RE.match(value)
        if match and match.group("month") not in _SCENARIO_MONTHS:
            return _TIMESTAMP_SENTINEL
        return value

    def apply(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: (_HASH_SENTINEL if key.endswith("_hash") and isinstance(item, str) else self.apply(item))
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self.apply(item) for item in value]
        if isinstance(value, str):
            return self._string(value)
        return value


def _canonicalize(name: str, body: Any) -> Any:
    """Two of the scenario's positions tie on GET /portfolio/virtual's primary sort key (both AAPL), and the
    readmodel breaks the tie with the row's own random order_id (readmodels/portfolio.py: `ORDER BY g.ticker,
    o.id`), so the API itself may list them in either order from one run to the next. Re-sort here by a business
    key that IS stable across runs (ticker, then origin), so the fixture -- and the first-seen numbering the
    Normalizer assigns to the UUIDs inside it -- does not depend on that random tiebreak. This does not touch
    what the test asserts against the live API: only what gets written to/compared against the fixture file."""
    if name == "portfolio_virtual":
        positions = sorted(body["portfolio"]["positions"], key=lambda p: (p["position"]["ticker"], p["position"]["origin"]))
        return {**body, "portfolio": {**body["portfolio"], "positions": positions}}
    return body


def _dump_json(value: Any) -> str:
    """Pretty JSON that keeps Decimal values as the exact number text the API sent (never rounded through float)."""
    markers: dict[str, str] = {}

    def default(obj: Any) -> str:
        if isinstance(obj, Decimal):
            marker = f"__decimal_{len(markers)}__"
            markers[marker] = format(obj, "f")
            return marker
        raise TypeError(f"not JSON serializable: {type(obj).__name__}")

    text = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, default=default)
    for marker, number in markers.items():
        text = text.replace(f'"{marker}"', number)
    return text


def shape(value: Any, key: str | None = None) -> Any:
    if key in OPAQUE:
        return "opaque"
    if isinstance(value, dict):
        return {name: shape(item, name) for name, item in value.items()}
    if isinstance(value, list):
        return [shape(item) for item in value[:1]]
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int | Decimal):
        return "number"
    return type(value).__name__


def _wildcard(value: Any) -> bool:
    return isinstance(value, str) and value in WILDCARDS


def differences(expected: Any, actual: Any, path: str = "$") -> list[str]:
    """Key renames, removals and type changes. Empty lists and nulls match anything: they carry no shape."""
    if _wildcard(expected) or _wildcard(actual):
        return []
    if isinstance(expected, dict) and isinstance(actual, dict):
        found = [f"{path}: missing key {name}" for name in sorted(expected.keys() - actual.keys())]
        found += [f"{path}: new key {name}" for name in sorted(actual.keys() - expected.keys())]
        for name in sorted(expected.keys() & actual.keys()):
            found += differences(expected[name], actual[name], f"{path}.{name}")
        return found
    if isinstance(expected, list) and isinstance(actual, list):
        return [] if not expected or not actual else differences(expected[0], actual[0], f"{path}[0]")
    return [] if expected == actual else [f"{path}: {expected} != {actual}"]


class Recorder:
    def __init__(self) -> None:
        self.names: list[str] = []
        # One Normalizer for the whole recording run: the uuid_map is shared across every fixture file, so the
        # same server-generated id (e.g. an order_id joined between orders.json and orders_replay.json) always
        # gets the same placeholder no matter which response it first turns up in.
        self.normalizer = Normalizer()

    def check(self, name: str, response: Any) -> Any:
        assert response.status_code in (200, 503), (name, response.status_code, response.text)
        self.names.append(name)
        body = json.loads(response.text, parse_float=Decimal)
        path = FIXTURES / f"{name}.json"
        if RECORD:
            FIXTURES.mkdir(parents=True, exist_ok=True)
            normalized = self.normalizer.apply(_canonicalize(name, body))
            path.write_text(_dump_json(normalized) + "\n", encoding="utf-8")
            return body
        assert path.exists(), f"missing {path}: record it with DASHBOARD_CONTRACT_RECORD=1 (D57)"
        expected = shape(json.loads(path.read_text(encoding="utf-8"), parse_float=Decimal))
        actual = shape(self.normalizer.apply(_canonicalize(name, body)))
        assert differences(expected, actual) == [], name
        return body


def test_dashboard_contract_matches_the_recorded_api_responses(api):
    recorder = Recorder()

    def get(name: str, path: str, **params: Any) -> Any:
        return recorder.check(name, api.client.get(path, params=params))

    jobs = WorkerJobs(api.services)
    api.bars.load(scenario_bars())  # fill 10:05 @101, TARGET1 11:00 @106, TARGET2 12:50 @110 -> r = 1.75
    created = post_json(api.client, "/signals", signal_body())
    assert created.status_code == 201, created.text
    signal_id, auto_id = created.json()["signal_id"], UUID(created.json()["auto_order_id"])
    api.clock.set(et(DAY, "10:00", 30))
    manual = api.client.post(f"/signals/{signal_id}/orders")
    assert manual.status_code == 201, manual.text
    api.clock.set(et(DAY, "10:30"))
    assert jobs.live_cycle() == JobResult("live_cycle", True, "COMPLETED")
    get("portfolio_virtual", "/portfolio/virtual")  # two open positions marked at the 10:29 close (103)
    for hm in ("11:30", "13:00"):
        api.clock.set(et(DAY, hm))
        assert jobs.live_cycle() == JobResult("live_cycle", True, "COMPLETED")
    get("pressure", "/market/pressure", ticker="AAPL", cmf_threshold="0.01")
    api.clock.set(et(DAY, "16:30"))
    assert jobs.end_of_day() == JobResult("end_of_day", True, "COMPLETED")
    replay = post_json(api.client, "/replay", {"mode": "REPRODUCE", "from": et(DAY, "08:00").isoformat(),
                                               "to": et(DAY, "23:00").isoformat()})
    assert replay.status_code == 200, replay.text

    api.clock.set(et(NEXT, "09:00"))  # second session: MSFT never fills and misses 10:10-10:45 (DATA_GAP)
    gap = post_json(api.client, "/signals", signal_body(client_signal_id="msft-gap", ticker="MSFT"))
    assert gap.status_code == 201, gap.text
    api.bars.load([bar for bar in flat_raw(NEXT, "09:30", "16:00", 105, "MSFT")
                   if not et(NEXT, "10:10") <= bar.ts < et(NEXT, "10:45")])
    api.clock.set(et(NEXT, "16:30"))
    assert jobs.end_of_day() == JobResult("end_of_day", True, "COMPLETED")

    assert api.client.put("/watchlist/MSFT").status_code == 201
    rule = post_json(api.client, "/watchlist/MSFT/alerts",
                     {"kind": "PRICE_CROSS", "level": Decimal("101.50"), "direction": "ABOVE"})
    assert rule.status_code == 201, rule.text
    with api.services.engine.begin() as conn:
        conn.execute(tables.health_state_log.insert().values(state="DEGRADED", cause_codes=["LIVE_CYCLE_STALE"]))
        enqueue_alert(conn, alert_key="contract-alert", kind=AlertKind.PRICE_CROSS, document={"rule": "contract"},
                      subject="rule")
        alert_id = conn.execute(select(tables.alert_outbox.c.id)
                                .where(tables.alert_outbox.c.alert_key == "contract-alert")).scalar_one()
        conn.execute(tables.alert_delivery_attempts.insert(), [
            {"alert_id": alert_id, "outcome": "FAILED", "status_code": None, "error_type": "ConnectTimeout"},
            {"alert_id": alert_id, "outcome": "EXPIRED", "status_code": None, "error_type": None},
        ])

    get("metrics", "/metrics")
    get("metrics_by_origin", "/metrics", group_by="origin")
    get("signals", "/signals", date=DAY)
    get("orders", "/orders", limit=1000)
    get("orders_closed", "/orders", status="CLOSED", limit=1000)
    get("orders_replay", "/orders", replay="true", limit=1000)
    get("order_detail", f"/orders/{auto_id}")
    chart = get("order_chart", f"/orders/{auto_id}/chart")
    get("market_bars", "/market/bars", ticker=chart["ticker"], source=chart["price_source"],
        **{"from": chart["window"]["from"], "to": chart["window"]["to"]})
    get("portfolio_real", "/portfolio/real")
    get("watchlist", "/watchlist")
    get("health", "/health")
    get("health_log", "/health/log", limit=20)
    get("alert_outbox", "/alert-outbox", outcome="EXPIRED")
    get("quality_overview", "/quality/overview")

    if not RECORD:  # no stale fixture: every file in the folder comes from this walk
        assert sorted(path.stem for path in FIXTURES.glob("*.json")) == sorted(recorder.names)
