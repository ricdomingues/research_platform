# Virtual Order Engine — Plano 1: Núcleo Puro (domain, fills v1, actionability, qualidade, métricas)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implementar, sem nenhum I/O, todo o núcleo determinístico do Virtual Order Engine: calendário/relógio de avaliação, hashing canônico, modelos e validação de sinais, `fill_model v1` (LONG e SHORT), actionability de ordens manuais, funções de qualidade de dados e métricas com bootstrap/permutação.

**Architecture:** Pacote Python `core` com módulos puros. A máquina de estados `step(state, bar, ctx) -> StepResult` é escrita uma vez para LONG; SHORT é executado espelhando preços (`p → −p`) na entrada e na saída de `step`. Estados e eventos são dataclasses imutáveis com preços em `Decimal`. Tudo que envolve banco, APIs, worker e UI fica nos Planos 2 e 3.

**Tech Stack:** Python 3.12, `uv`, pytest, Hypothesis, NumPy, pandas_market_calendars.

**Spec:** `docs/superpowers/specs/2026-09-12-virtual-order-engine-design.md` (SPEC v1.0 FROZEN, tag `spec/virtual-order-engine-v1.0`). Seções cobertas por este plano: 3.2–3.5, 3.7, 3.8, 4.x (lógica pura), 5.4 (cálculo), 7 (unitários e propriedades).

## Sequência de planos do sub-projeto 01

| Plano | Conteúdo | Depende de |
|---|---|---|
| **1 (este)** | Núcleo puro | — |
| 2 | PostgreSQL/Alembic, tabelas append-only, ledger com idempotência estrita, leitura as-of, lotes, runs, segmentos, `selected_data_hash`, adapters Alpaca/yfinance/FMP com fixtures, evaluator, replay `REPRODUCE`/`RECALCULATE` | 1 |
| 3 | FastAPI, worker (ciclo, jobs de abertura e fim de dia), Streamlit, Docker Compose, testes ponta a ponta | 1, 2 |

Cada plano produz software funcionando e testado. Os Planos 2 e 3 serão escritos após a conclusão do Plano 1, usando as interfaces reais produzidas aqui.

## Global Constraints

- Python **3.12** (`uv python pin 3.12`); dependências via `uv`; testes com `uv run pytest`.
- Módulos `core.domain`, `core.fills`, `core.actionability`, `core.dataquality` e `core.metrics` **não fazem I/O**: sem banco, rede, arquivos ou leitura de variáveis de ambiente.
- Preços, quantidades, P&L e custos são `Decimal`. `float` é proibido em qualquer payload hasheado (`TypeError`).
- Todo `datetime` é timezone-aware; naive → `ValueError`. Armazenamento e comparação em UTC. `bar_ts` é o **início** do minuto.
- `fill_model_version` deste plano é `"v1"`. Após o primeiro resultado persistido, nenhum comportamento de `core/fills/v1/` muda (spec seção 0).
- Regras de entrada: atravessar (`low < entry_zone_high`); alvo e stop por toque (`≥`/`≤`). Stop antes de alvo no mesmo candle.
- Identificadores de código em inglês; nomes de eventos, estados e códigos de erro exatamente como na spec.
- Commits terminam com:
  ```
  Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01BKUdi9RGWWdXRXVYFWj1yp
  ```

## Extensões de detalhe decididas neste plano (não alteram semântica da spec)

1. `FillConfig` inclui `sec_fee_rate`, `taf_fee_per_share` e `taf_fee_max` (padrão `0`), usados apenas quando `sec_taf_fees_enabled=True`; entram no `config_snapshot`. A spec só define o liga/desliga; as taxas vigentes devem ser configuradas pelo operador.
2. `target1_scale_out_pct` deve estar em `(0, 100)`.
3. MFE/MAE: o candle de entrada contribui apenas para o lado adverso (`low` no LONG); candles seguintes contribuem com `high` e `low`.
4. `CANCELED` com posição aberta não gera fill de saída; a ordem sai das métricas (que usam apenas `CLOSED`).
5. Payload de eventos: chaves terminadas em `_price` ou `_level` contêm preços e são espelhadas para SHORT; `pnl` e `cost` nunca são espelhados.

## File Structure

```text
pyproject.toml                         # projeto, dependências, pytest
.python-version                        # 3.12 (gerado por uv)
src/core/__init__.py
src/core/domain/__init__.py
src/core/domain/hashing.py             # canonical_json, sha256_hex
src/core/domain/calendar.py            # Session, SessionCalendar, evaluation_start_ts, signal_valid_until_ts
src/core/domain/models.py              # enums, Bar, SignalSpec, FillConfig, GatingState, OrderContext, OrderState, Event, StepResult
src/core/domain/validation.py          # validate_signal
src/core/domain/position.py            # r_multiple, excursion_r
src/core/marketdata/__init__.py
src/core/marketdata/nyse_calendar.py   # load_nyse_calendar (pandas_market_calendars, sem rede)
src/core/fills/__init__.py             # FillModel protocol, get_fill_model
src/core/fills/v1/__init__.py          # exporta a API do modelo v1
src/core/fills/v1/mirror.py            # espelhamento SHORT ↔ LONG
src/core/fills/v1/engine.py            # new_order_state, step, run_bars, apply_validity_end, cancel, freeze, apply_dividend
src/core/actionability.py              # signal_actionability, build_manual_order
src/core/dataquality.py                # qualidade de janela, gaps, verificação de minutos ausentes, faixa diária
src/core/metrics/__init__.py
src/core/metrics/resampling.py         # max_drawdown, bootstrap_ci, drawdown_permutation_percentiles
src/core/metrics/summary.py            # TradeResult, ExecutionCounts, MetricsSummary, summarize
tests/__init__.py
tests/support.py                       # calendário de teste, et(), bar(), flat_bars(), long_signal()
tests/domain/test_hashing.py
tests/domain/test_calendar.py
tests/domain/test_models.py
tests/domain/test_validation.py
tests/marketdata/test_nyse_calendar.py
tests/fills/test_entry.py
tests/fills/test_exits.py
tests/fills/test_lifecycle.py
tests/fills/test_properties.py
tests/test_actionability.py
tests/test_dataquality.py
tests/metrics/test_summary.py
tests/metrics/test_resampling.py
```

---

### Task 1: Scaffold do projeto e hashing canônico

**Files:**
- Create: `pyproject.toml`, `src/core/__init__.py`, `src/core/domain/__init__.py`, `src/core/domain/hashing.py`, `tests/__init__.py`, `tests/domain/__init__.py`
- Test: `tests/domain/test_hashing.py`

**Interfaces:**
- Consumes: nada.
- Produces: `canonical_json(value: Any) -> str`, `sha256_hex(value: Any) -> str` em `core.domain.hashing`.

- [ ] **Step 1: Criar `pyproject.toml`**

```toml
[project]
name = "research-platform"
version = "0.1.0"
requires-python = ">=3.12,<3.13"
dependencies = [
    "numpy>=2.0",
    "pandas-market-calendars>=4.4",
]

[dependency-groups]
dev = [
    "pytest>=8.0",
    "hypothesis>=6.100",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/core"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
addopts = "-q"
```

- [ ] **Step 2: Fixar Python e instalar**

Run:
```bash
cd /Users/ricardocarneiro/Developer/research_platform
uv python pin 3.12
mkdir -p src/core/domain tests/domain
touch src/core/__init__.py src/core/domain/__init__.py tests/__init__.py tests/domain/__init__.py
uv sync
```
Expected: `.python-version` contém `3.12`; `uv sync` termina sem erro e cria `uv.lock`.

- [ ] **Step 3: Escrever os testes que falham**

`tests/domain/test_hashing.py`:
```python
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
```

- [ ] **Step 4: Rodar e ver falhar**

Run: `uv run pytest tests/domain/test_hashing.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'core.domain.hashing'`.

- [ ] **Step 5: Implementar**

`src/core/domain/hashing.py`:
```python
"""Canonical serialization used for payload hashes and idempotency (spec 3.3)."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID


def _normalize(value: Any) -> Any:
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        raise TypeError("floats are not allowed in canonical payloads; use Decimal")
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError(f"non-finite Decimal: {value}")
        if value == 0:
            return "0"
        return format(value.normalize(), "f")
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("naive datetime is not allowed")
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        return _normalize(value.value)
    if isinstance(value, dict):
        return {str(key): _normalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    raise TypeError(f"unsupported type in canonical payload: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    return json.dumps(
        _normalize(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def sha256_hex(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
```

- [ ] **Step 6: Rodar e ver passar**

Run: `uv run pytest tests/domain/test_hashing.py -v`
Expected: 6 passed.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml .python-version uv.lock src tests
git commit -m "feat(core): project scaffold and canonical hashing

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BKUdi9RGWWdXRXVYFWj1yp"
```

### Task 2: Calendário de sessões e relógio de avaliação

**Files:**
- Create: `src/core/domain/calendar.py`, `tests/support.py`
- Test: `tests/domain/test_calendar.py`

**Interfaces:**
- Consumes: nada.
- Produces (em `core.domain.calendar`):
  - `ONE_MINUTE: timedelta`
  - `class CalendarRangeError(LookupError)`
  - `@dataclass(frozen=True) Session(day: date, open_utc: datetime, close_utc: datetime)`
  - `class SessionCalendar(sessions: Sequence[Session])` com `sessions`, `session_containing(ts) -> Session | None`, `is_expected_minute(ts) -> bool`, `first_expected_minute_at_or_after(ts) -> datetime`, `next_expected_minute(ts) -> datetime` (estritamente depois), `expected_minutes(start, end) -> list[datetime]` (`[start, end)`), `nth_session_close(first: Session, n: int) -> datetime`, `last_expected_minute_before(ts) -> datetime`
  - `evaluation_start_ts(calendar, created_at) -> datetime`
  - `signal_valid_until_ts(calendar, signal_evaluation_start: datetime, valid_sessions: int) -> datetime`
- Produces (em `tests.support`): `ET`, `et(day: str, hm: str, second: int = 0) -> datetime`, `make_calendar() -> SessionCalendar`

O calendário de teste cobre 2025-11-24 a 2025-12-03: 27/11 é feriado (Thanksgiving, sem sessão) e 28/11 é meio pregão (fecha 13:00 ET). Em novembro ET = UTC−5.

- [ ] **Step 1: Criar helpers de teste**

`tests/support.py`:
```python
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from core.domain.calendar import Session, SessionCalendar

ET = ZoneInfo("America/New_York")

FULL_DAYS = ["2025-11-24", "2025-11-25", "2025-11-26", "2025-12-01", "2025-12-02", "2025-12-03"]
HALF_DAYS = ["2025-11-28"]


def et(day: str, hm: str, second: int = 0) -> datetime:
    year, month, dom = (int(part) for part in day.split("-"))
    hour, minute = (int(part) for part in hm.split(":"))
    return datetime(year, month, dom, hour, minute, second, tzinfo=ET).astimezone(timezone.utc)


def make_calendar() -> SessionCalendar:
    sessions = [Session(date.fromisoformat(d), et(d, "09:30"), et(d, "16:00")) for d in FULL_DAYS]
    sessions += [Session(date.fromisoformat(d), et(d, "09:30"), et(d, "13:00")) for d in HALF_DAYS]
    return SessionCalendar(sessions)
```

- [ ] **Step 2: Escrever os testes que falham**

`tests/domain/test_calendar.py`:
```python
from datetime import date

import pytest

from core.domain.calendar import (
    CalendarRangeError,
    Session,
    SessionCalendar,
    evaluation_start_ts,
    signal_valid_until_ts,
)
from tests.support import et, make_calendar

CAL = make_calendar()


@pytest.mark.parametrize(
    ("created", "expected"),
    [
        (et("2025-11-25", "08:00"), et("2025-11-25", "09:30")),
        (et("2025-11-25", "10:30"), et("2025-11-25", "10:30")),
        (et("2025-11-25", "10:30", 18), et("2025-11-25", "10:31")),
        (et("2025-11-26", "15:59", 30), et("2025-11-28", "09:30")),  # véspera de feriado
        (et("2025-11-28", "12:59", 30), et("2025-12-01", "09:30")),  # meio pregão
        (et("2025-11-27", "11:00"), et("2025-11-28", "09:30")),      # feriado
        (et("2025-11-25", "16:30"), et("2025-11-26", "09:30")),      # após fechamento
    ],
)
def test_evaluation_start_ts(created, expected):
    assert evaluation_start_ts(CAL, created) == expected


def test_next_expected_minute_crosses_close_holiday_and_half_day():
    assert CAL.next_expected_minute(et("2025-11-25", "10:00")) == et("2025-11-25", "10:01")
    assert CAL.next_expected_minute(et("2025-11-26", "15:59")) == et("2025-11-28", "09:30")
    assert CAL.next_expected_minute(et("2025-11-28", "12:59")) == et("2025-12-01", "09:30")
    assert CAL.next_expected_minute(et("2025-11-25", "10:00", 20)) == et("2025-11-25", "10:01")


def test_expected_minutes_and_membership():
    half_day = CAL.expected_minutes(et("2025-11-28", "00:00"), et("2025-11-28", "23:00"))
    assert len(half_day) == 210
    assert half_day[0] == et("2025-11-28", "09:30")
    assert half_day[-1] == et("2025-11-28", "12:59")
    assert CAL.is_expected_minute(et("2025-11-25", "15:59"))
    assert not CAL.is_expected_minute(et("2025-11-25", "16:00"))
    assert not CAL.is_expected_minute(et("2025-11-27", "12:30"))
    assert not CAL.is_expected_minute(et("2025-11-25", "10:00", 1))
    two_days = CAL.expected_minutes(et("2025-11-26", "15:58"), et("2025-11-28", "09:32"))
    assert two_days == [
        et("2025-11-26", "15:58"),
        et("2025-11-26", "15:59"),
        et("2025-11-28", "09:30"),
        et("2025-11-28", "09:31"),
    ]


def test_session_containing():
    session = CAL.session_containing(et("2025-11-28", "12:00"))
    assert session is not None and session.day == date(2025, 11, 28)
    assert CAL.session_containing(et("2025-11-28", "13:00")) is None


def test_signal_valid_until_counts_current_session():
    start = et("2025-11-26", "10:31")
    assert signal_valid_until_ts(CAL, start, 1) == et("2025-11-26", "16:00")
    assert signal_valid_until_ts(CAL, start, 2) == et("2025-11-28", "13:00")
    assert signal_valid_until_ts(CAL, start, 3) == et("2025-12-01", "16:00")


def test_signal_valid_until_rejects_bad_input():
    with pytest.raises(ValueError):
        signal_valid_until_ts(CAL, et("2025-11-26", "10:31"), 0)
    with pytest.raises(ValueError):
        signal_valid_until_ts(CAL, et("2025-11-26", "16:30"), 1)
    with pytest.raises(CalendarRangeError):
        signal_valid_until_ts(CAL, et("2025-12-03", "10:00"), 5)


def test_last_expected_minute_before():
    assert CAL.last_expected_minute_before(et("2025-11-28", "13:00")) == et("2025-11-28", "12:59")
    assert CAL.last_expected_minute_before(et("2025-11-25", "10:00", 30)) == et("2025-11-25", "10:00")
    assert CAL.last_expected_minute_before(et("2025-11-25", "10:00")) == et("2025-11-25", "09:59")


def test_out_of_range_raises():
    with pytest.raises(CalendarRangeError):
        CAL.first_expected_minute_at_or_after(et("2025-11-20", "10:00"))
    with pytest.raises(CalendarRangeError):
        CAL.next_expected_minute(et("2025-12-03", "15:59"))


def test_naive_datetime_and_invalid_sessions_rejected():
    from datetime import datetime

    with pytest.raises(ValueError):
        CAL.first_expected_minute_at_or_after(datetime(2025, 11, 25, 10, 0))
    with pytest.raises(ValueError):
        SessionCalendar([])
    with pytest.raises(ValueError):
        Session(date(2025, 11, 25), et("2025-11-25", "16:00"), et("2025-11-25", "09:30"))
    overlapping = [
        Session(date(2025, 11, 25), et("2025-11-25", "09:30"), et("2025-11-25", "16:00")),
        Session(date(2025, 11, 25), et("2025-11-25", "15:00"), et("2025-11-25", "17:00")),
    ]
    with pytest.raises(ValueError):
        SessionCalendar(overlapping)
```

- [ ] **Step 3: Rodar e ver falhar**

Run: `uv run pytest tests/domain/test_calendar.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'core.domain.calendar'`.

- [ ] **Step 4: Implementar**

`src/core/domain/calendar.py`:
```python
"""Expected-minute calendar and evaluation clock (spec 3.4). Pure: sessions are injected."""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Sequence

ONE_MINUTE = timedelta(minutes=1)


class CalendarRangeError(LookupError):
    """The query falls outside the loaded session range."""


def _require_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        raise ValueError("naive datetime is not allowed")
    return ts.astimezone(timezone.utc)


def _is_whole_minute(ts: datetime) -> bool:
    return ts.second == 0 and ts.microsecond == 0


@dataclass(frozen=True)
class Session:
    day: date
    open_utc: datetime
    close_utc: datetime

    def __post_init__(self) -> None:
        for ts in (self.open_utc, self.close_utc):
            if ts.tzinfo is None:
                raise ValueError("session bounds must be timezone-aware")
            if not _is_whole_minute(ts):
                raise ValueError("session bounds must be whole minutes")
        if self.close_utc <= self.open_utc:
            raise ValueError("session close must be after open")


class SessionCalendar:
    def __init__(self, sessions: Sequence[Session]) -> None:
        if not sessions:
            raise ValueError("calendar needs at least one session")
        ordered = sorted(sessions, key=lambda s: s.open_utc)
        for previous, current in zip(ordered, ordered[1:]):
            if current.open_utc < previous.close_utc:
                raise ValueError("sessions overlap")
        self._sessions = tuple(ordered)
        self._opens = [s.open_utc for s in ordered]
        self._closes = [s.close_utc for s in ordered]

    @property
    def sessions(self) -> tuple[Session, ...]:
        return self._sessions

    def _check_lower(self, ts: datetime) -> None:
        if ts < self._sessions[0].open_utc - timedelta(hours=24):
            raise CalendarRangeError(f"{ts.isoformat()} is before the loaded calendar")

    def session_containing(self, ts: datetime) -> Session | None:
        ts = _require_utc(ts)
        self._check_lower(ts)
        index = bisect.bisect_right(self._closes, ts)
        if index < len(self._sessions) and self._sessions[index].open_utc <= ts:
            return self._sessions[index]
        if index >= len(self._sessions):
            raise CalendarRangeError(f"{ts.isoformat()} is after the loaded calendar")
        return None

    def is_expected_minute(self, ts: datetime) -> bool:
        ts = _require_utc(ts)
        if not _is_whole_minute(ts):
            return False
        try:
            return self.session_containing(ts) is not None
        except CalendarRangeError:
            return False

    def first_expected_minute_at_or_after(self, ts: datetime) -> datetime:
        ts = _require_utc(ts)
        self._check_lower(ts)
        minute = ts.replace(second=0, microsecond=0)
        if minute < ts:
            minute += ONE_MINUTE
        index = bisect.bisect_right(self._closes, minute)
        if index >= len(self._sessions):
            raise CalendarRangeError(f"no session at or after {ts.isoformat()}")
        return max(minute, self._sessions[index].open_utc)

    def next_expected_minute(self, ts: datetime) -> datetime:
        ts = _require_utc(ts)
        return self.first_expected_minute_at_or_after(ts.replace(second=0, microsecond=0) + ONE_MINUTE)

    def expected_minutes(self, start: datetime, end: datetime) -> list[datetime]:
        start, end = _require_utc(start), _require_utc(end)
        minutes: list[datetime] = []
        if end <= start:
            return minutes
        try:
            minute = self.first_expected_minute_at_or_after(start)
        except CalendarRangeError:
            return minutes
        while minute < end:
            minutes.append(minute)
            try:
                minute = self.next_expected_minute(minute)
            except CalendarRangeError:
                break
        return minutes

    def nth_session_close(self, first: Session, n: int) -> datetime:
        if n < 1:
            raise ValueError("n must be >= 1")
        index = self._sessions.index(first) + n - 1
        if index >= len(self._sessions):
            raise CalendarRangeError(f"session {n} after {first.day} is not loaded")
        return self._sessions[index].close_utc

    def last_expected_minute_before(self, ts: datetime) -> datetime:
        ts = _require_utc(ts)
        index = bisect.bisect_left(self._opens, ts) - 1
        if index < 0:
            raise CalendarRangeError(f"no expected minute before {ts.isoformat()}")
        candidate = min(ts, self._sessions[index].close_utc)
        floored = candidate.replace(second=0, microsecond=0)
        return floored - ONE_MINUTE if floored == candidate else floored


def evaluation_start_ts(calendar: SessionCalendar, created_at: datetime) -> datetime:
    """First complete candle that starts at or after the decision time (spec 3.4)."""
    return calendar.first_expected_minute_at_or_after(created_at)


def signal_valid_until_ts(
    calendar: SessionCalendar, signal_evaluation_start: datetime, valid_sessions: int
) -> datetime:
    """Session 1 is the regular session containing the signal's evaluation start."""
    if not 1 <= valid_sessions <= 20:
        raise ValueError("valid_sessions must be between 1 and 20")
    session = calendar.session_containing(signal_evaluation_start)
    if session is None:
        raise ValueError("signal evaluation start must be an expected minute")
    return calendar.nth_session_close(session, valid_sessions)
```

Nota sobre `_check_lower`: consultas até 24 h antes da primeira sessão carregada são aceitas (ex.: 08:00 do primeiro dia); antes disso o resultado não é confiável e levanta `CalendarRangeError`.

- [ ] **Step 5: Rodar e ver passar**

Run: `uv run pytest tests/domain/test_calendar.py -v`
Expected: todos passam (15 casos contando a parametrização).

- [ ] **Step 6: Commit**

```bash
git add src/core/domain/calendar.py tests/support.py tests/domain/test_calendar.py
git commit -m "feat(core): session calendar and evaluation clock

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BKUdi9RGWWdXRXVYFWj1yp"
```

### Task 3: Carregador do calendário NYSE

**Files:**
- Create: `src/core/marketdata/__init__.py`, `src/core/marketdata/nyse_calendar.py`, `tests/marketdata/__init__.py`
- Test: `tests/marketdata/test_nyse_calendar.py`

**Interfaces:**
- Consumes: `Session`, `SessionCalendar` (Task 2).
- Produces: `load_nyse_calendar(start: date, end: date) -> SessionCalendar` em `core.marketdata.nyse_calendar`. Usa `pandas_market_calendars` (cálculo local, sem rede).

- [ ] **Step 1: Escrever o teste que falha**

`tests/marketdata/test_nyse_calendar.py`:
```python
from datetime import date

from core.marketdata.nyse_calendar import load_nyse_calendar
from tests.support import et


def test_matches_known_holiday_and_half_day():
    calendar = load_nyse_calendar(date(2025, 11, 24), date(2025, 12, 3))
    days = [session.day for session in calendar.sessions]
    assert date(2025, 11, 27) not in days
    by_day = {session.day: session for session in calendar.sessions}
    assert by_day[date(2025, 11, 26)].open_utc == et("2025-11-26", "09:30")
    assert by_day[date(2025, 11, 26)].close_utc == et("2025-11-26", "16:00")
    assert by_day[date(2025, 11, 28)].close_utc == et("2025-11-28", "13:00")
    assert len(days) == 7
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `mkdir -p tests/marketdata src/core/marketdata && touch tests/marketdata/__init__.py src/core/marketdata/__init__.py && uv run pytest tests/marketdata/test_nyse_calendar.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'core.marketdata.nyse_calendar'`.

- [ ] **Step 3: Implementar**

`src/core/marketdata/nyse_calendar.py`:
```python
"""Build a SessionCalendar from pandas_market_calendars (local computation, no network)."""

from __future__ import annotations

from datetime import date, timezone

import pandas_market_calendars as mcal

from core.domain.calendar import Session, SessionCalendar


def load_nyse_calendar(start: date, end: date) -> SessionCalendar:
    schedule = mcal.get_calendar("XNYS").schedule(
        start_date=start.isoformat(), end_date=end.isoformat()
    )
    sessions = [
        Session(
            day=index.date(),
            open_utc=row["market_open"].to_pydatetime().astimezone(timezone.utc),
            close_utc=row["market_close"].to_pydatetime().astimezone(timezone.utc),
        )
        for index, row in schedule.iterrows()
    ]
    return SessionCalendar(sessions)
```

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/marketdata/test_nyse_calendar.py -v`
Expected: 1 passed. Se a versão instalada da biblioteca nomear as colunas de outra forma, rodar `uv run python -c "import pandas_market_calendars as m; print(m.get_calendar('XNYS').schedule('2025-11-24','2025-11-26').columns)"` e ajustar apenas os nomes das colunas.

- [ ] **Step 5: Commit**

```bash
git add src/core/marketdata tests/marketdata
git commit -m "feat(core): NYSE calendar loader

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BKUdi9RGWWdXRXVYFWj1yp"
```

---

### Task 4: Modelos de domínio, validação de sinais e métricas de posição

**Files:**
- Create: `src/core/domain/models.py`, `src/core/domain/validation.py`, `src/core/domain/position.py`
- Modify: `tests/support.py` (acrescentar `D`, `bar`, `flat_bars`, `long_signal`)
- Test: `tests/domain/test_models.py`, `tests/domain/test_validation.py`

**Interfaces:**
- Consumes: `SessionCalendar` (Task 2), `sha256_hex` (Task 1).
- Produces (em `core.domain.models`):
  - `ZERO = Decimal("0")`
  - Enums (`StrEnum`): `Direction{LONG,SHORT}`, `Origin{AUTO_STRATEGY,MANUAL_USER}`, `OrderStatus{PENDING,OPEN,PARTIAL,CLOSED,EXPIRED,INVALIDATED,CANCELED}`, `EventType{ORDER_CREATED,TRIGGER_HIT,ZONE_LOST,ZONE_RECLAIMED,FILLED,TARGET1_HIT,TARGET2_HIT,STOPPED,TIME_EXIT,EXPIRED,INVALIDATED,CANCELED,FROZEN,DIVIDEND,DATA_QUALITY,DATA_GAP,NEEDS_REVIEW}`, `CloseReason{STOPPED,TARGET_FINAL,TIME_EXIT}`, `EntryPath{DIRECT,RECLAIMED}`, `ZoneLostPolicy{RECLAIM,CANCEL}`
  - `FINAL_STATUSES: frozenset[OrderStatus]`, `MARKET_EVENT_TYPES: frozenset[EventType]`
  - `Bar(ts, open, high, low, close, volume=ZERO, batch_id=None)`
  - `SignalSpec(ticker, direction, entry_zone_low, entry_zone_high, stop, target1, target2=None, trigger_price=None, valid_sessions=1)` com `as_payload() -> dict`
  - `FillConfig(...)` com `snapshot() -> dict`
  - `GatingState(status, zone_lost, entry_eligible_from, trigger_hit_at)` com `as_payload() -> dict`
  - `OrderContext(signal, config, calendar, evaluation_start_ts, valid_until_ts)`
  - `OrderState(...)` com `is_final`, `needs_review`, `gating_state()`
  - `Event(type, event_key, bar_ts=None, price=None, qty=None, bar_batch_id=None, payload={})` com `hash_material() -> dict` e `payload_hash: str`
  - `StepResult(state: OrderState, events: tuple[Event, ...] = ())`
- Produces (em `core.domain.validation`): `validate_signal(signal: SignalSpec) -> list[str]`
- Produces (em `core.domain.position`): `r_multiple(state, risk_amount) -> Decimal`, `excursion_r(state, direction) -> tuple[Decimal | None, Decimal | None]`
- Produces (em `tests.support`): `D(value) -> Decimal`, `bar(ts, o, h, l, c) -> Bar`, `flat_bars(calendar, start, end, price) -> list[Bar]`, `long_signal(**overrides) -> SignalSpec`

- [ ] **Step 1: Acrescentar helpers em `tests/support.py`**

Adicionar ao final do arquivo:
```python
from decimal import Decimal

from core.domain.models import Bar, Direction, SignalSpec


def D(value) -> Decimal:
    return Decimal(str(value))


def bar(ts: datetime, o, h, l, c) -> Bar:
    return Bar(ts=ts, open=D(o), high=D(h), low=D(l), close=D(c), volume=D(1000))


def flat_bars(calendar: SessionCalendar, start: datetime, end: datetime, price) -> list[Bar]:
    return [bar(minute, price, price, price, price) for minute in calendar.expected_minutes(start, end)]


def long_signal(**overrides) -> SignalSpec:
    values = dict(
        ticker="AAPL",
        direction=Direction.LONG,
        entry_zone_low=D(100),
        entry_zone_high=D(102),
        stop=D(97),
        target1=D(106),
        target2=D(110),
        trigger_price=None,
        valid_sessions=3,
    )
    values.update(overrides)
    return SignalSpec(**values)
```

- [ ] **Step 2: Escrever os testes que falham**

`tests/domain/test_models.py`:
```python
from datetime import datetime
from decimal import Decimal
from uuid import UUID

import pytest

from core.domain.models import (
    Bar,
    Direction,
    Event,
    EventType,
    FillConfig,
    OrderContext,
    OrderState,
    OrderStatus,
    SignalSpec,
)
from core.domain.position import excursion_r, r_multiple
from tests.support import D, et, long_signal, make_calendar


def test_bar_validation():
    ts = et("2025-11-25", "10:00")
    with pytest.raises(TypeError):
        Bar(ts, 1.0, D(2), D(1), D(1))
    with pytest.raises(ValueError):
        Bar(datetime(2025, 11, 25, 15, 0), D(1), D(2), D(1), D(1))
    with pytest.raises(ValueError):
        Bar(et("2025-11-25", "10:00", 5), D(1), D(2), D(1), D(1))
    with pytest.raises(ValueError):
        Bar(ts, D(1), D(2), D("1.5"), D(1))


def test_signal_coerces_direction_and_payload():
    signal = SignalSpec("AAPL", "SHORT", D(100), D(102), D(105), D(95))
    assert signal.direction is Direction.SHORT
    assert signal.as_payload()["target2"] is None


def test_fill_config_defaults_and_snapshot():
    config = FillConfig()
    snapshot = config.snapshot()
    assert snapshot["stop_slippage_bps"] == Decimal("5")
    assert snapshot["zone_lost_policy"] == "RECLAIM"
    assert set(snapshot) >= {
        "risk_amount", "entry_slippage_bps", "commission_per_execution",
        "sec_taf_fees_enabled", "target1_scale_out_pct", "data_gap_minutes",
        "crosscheck_tolerance_pct", "dividend_tolerance",
    }
    with pytest.raises(ValueError):
        FillConfig(target1_scale_out_pct=D(100))
    with pytest.raises(ValueError):
        FillConfig(risk_amount=D(0))


def test_order_context_rejects_expired_window():
    start = et("2025-11-25", "10:00")
    with pytest.raises(ValueError):
        OrderContext(long_signal(), FillConfig(), make_calendar(), start, start)


def test_event_requires_bar_ts_for_market_events_and_hashes_canonically():
    with pytest.raises(ValueError):
        Event(EventType.FILLED, "FILLED")
    ts = et("2025-11-25", "10:00")
    batch = UUID("12345678-1234-5678-1234-567812345678")
    a = Event(EventType.FILLED, "FILLED", ts, D("101.50"), D(10), batch, {"rule": "ZONE_OPEN"})
    b = Event(EventType.FILLED, "FILLED", ts, D("101.5"), D(10), batch, {"rule": "ZONE_OPEN"})
    c = Event(EventType.FILLED, "FILLED", ts, D("101.51"), D(10), batch, {"rule": "ZONE_OPEN"})
    assert a.payload_hash == b.payload_hash
    assert a.payload_hash != c.payload_hash


def test_order_state_flags_and_gating():
    state = OrderState(zone_lost=True, trigger_hit_at=et("2025-11-25", "10:00"))
    gating = state.gating_state()
    assert gating.status is OrderStatus.PENDING and gating.zone_lost
    assert gating.as_payload()["trigger_hit_at"] == et("2025-11-25", "10:00")
    assert not state.is_final and not state.needs_review
    assert OrderState(status=OrderStatus.CANCELED).is_final
    assert OrderState(review_reasons=("SPLIT",)).needs_review


def test_position_metrics_long_and_short():
    long_state = OrderState(
        avg_entry=D(101), initial_stop=D(97), best_price=D(109), worst_price=D(99),
        realized_pnl=D(200), costs=D(2), dividends=D(1),
    )
    assert r_multiple(long_state, D(100)) == D("1.99")
    assert excursion_r(long_state, Direction.LONG) == (D(2), D("-0.5"))
    short_state = OrderState(avg_entry=D(100), initial_stop=D(104), best_price=D(92), worst_price=D(102))
    assert excursion_r(short_state, Direction.SHORT) == (D(2), D("-0.5"))
    assert excursion_r(OrderState(), Direction.LONG) == (None, None)
```

`tests/domain/test_validation.py`:
```python
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
```

- [ ] **Step 3: Rodar e ver falhar**

Run: `uv run pytest tests/domain -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'core.domain.models'` (inclusive ao importar `tests.support`).

- [ ] **Step 4: Implementar `models.py`**

`src/core/domain/models.py`:
```python
"""Immutable domain types shared by fills, actionability, data quality and metrics."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

from core.domain.calendar import SessionCalendar
from core.domain.hashing import sha256_hex

ZERO = Decimal("0")


class Direction(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"


class Origin(StrEnum):
    AUTO_STRATEGY = "AUTO_STRATEGY"
    MANUAL_USER = "MANUAL_USER"


class OrderStatus(StrEnum):
    PENDING = "PENDING"
    OPEN = "OPEN"
    PARTIAL = "PARTIAL"
    CLOSED = "CLOSED"
    EXPIRED = "EXPIRED"
    INVALIDATED = "INVALIDATED"
    CANCELED = "CANCELED"


FINAL_STATUSES = frozenset(
    {OrderStatus.CLOSED, OrderStatus.EXPIRED, OrderStatus.INVALIDATED, OrderStatus.CANCELED}
)


class EventType(StrEnum):
    ORDER_CREATED = "ORDER_CREATED"
    TRIGGER_HIT = "TRIGGER_HIT"
    ZONE_LOST = "ZONE_LOST"
    ZONE_RECLAIMED = "ZONE_RECLAIMED"
    FILLED = "FILLED"
    TARGET1_HIT = "TARGET1_HIT"
    TARGET2_HIT = "TARGET2_HIT"
    STOPPED = "STOPPED"
    TIME_EXIT = "TIME_EXIT"
    EXPIRED = "EXPIRED"
    INVALIDATED = "INVALIDATED"
    CANCELED = "CANCELED"
    FROZEN = "FROZEN"
    DIVIDEND = "DIVIDEND"
    DATA_QUALITY = "DATA_QUALITY"
    DATA_GAP = "DATA_GAP"
    NEEDS_REVIEW = "NEEDS_REVIEW"


MARKET_EVENT_TYPES = frozenset(
    {
        EventType.TRIGGER_HIT,
        EventType.ZONE_LOST,
        EventType.ZONE_RECLAIMED,
        EventType.FILLED,
        EventType.TARGET1_HIT,
        EventType.TARGET2_HIT,
        EventType.STOPPED,
        EventType.TIME_EXIT,
        EventType.INVALIDATED,
    }
)


class CloseReason(StrEnum):
    STOPPED = "STOPPED"
    TARGET_FINAL = "TARGET_FINAL"
    TIME_EXIT = "TIME_EXIT"


class EntryPath(StrEnum):
    DIRECT = "DIRECT"
    RECLAIMED = "RECLAIMED"


class ZoneLostPolicy(StrEnum):
    RECLAIM = "RECLAIM"
    CANCEL = "CANCEL"


def _require_aware(value: datetime | None, name: str) -> None:
    if value is not None and value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")


@dataclass(frozen=True)
class Bar:
    ts: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal = ZERO
    batch_id: UUID | None = None

    def __post_init__(self) -> None:
        for name in ("open", "high", "low", "close", "volume"):
            if not isinstance(getattr(self, name), Decimal):
                raise TypeError(f"bar {name} must be Decimal")
        _require_aware(self.ts, "bar ts")
        if self.ts.second or self.ts.microsecond:
            raise ValueError("bar ts must be a whole minute")
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close):
            raise ValueError("inconsistent OHLC")


@dataclass(frozen=True)
class SignalSpec:
    ticker: str
    direction: Direction
    entry_zone_low: Decimal
    entry_zone_high: Decimal
    stop: Decimal
    target1: Decimal
    target2: Decimal | None = None
    trigger_price: Decimal | None = None
    valid_sessions: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "direction", Direction(self.direction))

    def as_payload(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}


@dataclass(frozen=True)
class FillConfig:
    risk_amount: Decimal = Decimal("100")
    entry_slippage_bps: Decimal = Decimal("0")
    stop_slippage_bps: Decimal = Decimal("5")
    commission_per_execution: Decimal = Decimal("0")
    sec_taf_fees_enabled: bool = False
    sec_fee_rate: Decimal = Decimal("0")
    taf_fee_per_share: Decimal = Decimal("0")
    taf_fee_max: Decimal = Decimal("0")
    zone_lost_policy: ZoneLostPolicy = ZoneLostPolicy.RECLAIM
    target1_scale_out_pct: Decimal = Decimal("50")
    data_gap_minutes: int = 30
    crosscheck_tolerance_pct: Decimal = Decimal("0.5")
    dividend_tolerance: Decimal = Decimal("0.001")

    def __post_init__(self) -> None:
        object.__setattr__(self, "zone_lost_policy", ZoneLostPolicy(self.zone_lost_policy))
        if self.risk_amount <= ZERO:
            raise ValueError("risk_amount must be positive")
        if not ZERO < self.target1_scale_out_pct < Decimal("100"):
            raise ValueError("target1_scale_out_pct must be in (0, 100)")
        for name in ("entry_slippage_bps", "stop_slippage_bps", "commission_per_execution"):
            if getattr(self, name) < ZERO:
                raise ValueError(f"{name} must be >= 0")
        if self.data_gap_minutes < 1:
            raise ValueError("data_gap_minutes must be >= 1")

    def snapshot(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}


@dataclass(frozen=True)
class GatingState:
    status: OrderStatus
    zone_lost: bool
    entry_eligible_from: datetime | None
    trigger_hit_at: datetime | None

    def as_payload(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "zone_lost": self.zone_lost,
            "entry_eligible_from": self.entry_eligible_from,
            "trigger_hit_at": self.trigger_hit_at,
        }


@dataclass(frozen=True)
class OrderContext:
    signal: SignalSpec
    config: FillConfig
    calendar: SessionCalendar
    evaluation_start_ts: datetime
    valid_until_ts: datetime

    def __post_init__(self) -> None:
        _require_aware(self.evaluation_start_ts, "evaluation_start_ts")
        _require_aware(self.valid_until_ts, "valid_until_ts")
        if self.valid_until_ts <= self.evaluation_start_ts:
            raise ValueError("valid_until_ts must be after evaluation_start_ts")


@dataclass(frozen=True)
class OrderState:
    status: OrderStatus = OrderStatus.PENDING
    zone_lost: bool = False
    zone_ever_lost: bool = False
    entry_eligible_from: datetime | None = None
    trigger_hit_at: datetime | None = None
    entry_path: EntryPath | None = None
    avg_entry: Decimal | None = None
    initial_stop: Decimal | None = None
    stop_current: Decimal | None = None
    stop_previous: Decimal | None = None
    stop_active_from: datetime | None = None
    t1_done: bool = False
    qty_total: Decimal = ZERO
    qty_open: Decimal = ZERO
    realized_pnl: Decimal = ZERO
    costs: Decimal = ZERO
    dividends: Decimal = ZERO
    best_price: Decimal | None = None
    worst_price: Decimal | None = None
    opened_at: datetime | None = None
    closed_at: datetime | None = None
    final_event_ts: datetime | None = None
    last_bar_ts: datetime | None = None
    close_reason: CloseReason | None = None
    frozen: bool = False
    review_reasons: tuple[str, ...] = ()

    @property
    def is_final(self) -> bool:
        return self.status in FINAL_STATUSES

    @property
    def needs_review(self) -> bool:
        return bool(self.review_reasons)

    def gating_state(self) -> GatingState:
        return GatingState(self.status, self.zone_lost, self.entry_eligible_from, self.trigger_hit_at)


@dataclass(frozen=True)
class Event:
    type: EventType
    event_key: str
    bar_ts: datetime | None = None
    price: Decimal | None = None
    qty: Decimal | None = None
    bar_batch_id: UUID | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_aware(self.bar_ts, "bar_ts")
        if self.type in MARKET_EVENT_TYPES and self.bar_ts is None:
            raise ValueError(f"{self.type} requires bar_ts")

    def hash_material(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "bar_ts": self.bar_ts,
            "price": self.price,
            "qty": self.qty,
            "bar_batch_id": self.bar_batch_id,
            "payload": self.payload,
        }

    @property
    def payload_hash(self) -> str:
        return sha256_hex(self.hash_material())


@dataclass(frozen=True)
class StepResult:
    state: OrderState
    events: tuple[Event, ...] = ()
```

- [ ] **Step 5: Implementar `validation.py` e `position.py`**

`src/core/domain/validation.py`:
```python
"""Signal level-chain validation (spec 3.8). Ticker tradability is checked by the API layer."""

from __future__ import annotations

from core.domain.models import ZERO, Direction, SignalSpec


def validate_signal(signal: SignalSpec) -> list[str]:
    errors: list[str] = []
    if not signal.ticker or signal.ticker != signal.ticker.strip().upper():
        errors.append("TICKER_INVALID")
    prices = {
        "ENTRY_ZONE_LOW": signal.entry_zone_low,
        "ENTRY_ZONE_HIGH": signal.entry_zone_high,
        "STOP": signal.stop,
        "TARGET1": signal.target1,
        "TARGET2": signal.target2,
        "TRIGGER_PRICE": signal.trigger_price,
    }
    for name, value in prices.items():
        if value is not None and value <= ZERO:
            errors.append(f"{name}_NOT_POSITIVE")
    if not 1 <= signal.valid_sessions <= 20:
        errors.append("VALID_SESSIONS_OUT_OF_RANGE")
    if errors:
        return errors

    s = signal
    if not s.entry_zone_low <= s.entry_zone_high:
        errors.append("ZONE_INVERTED")
    if s.direction is Direction.LONG:
        if not s.stop < s.entry_zone_low:
            errors.append("STOP_NOT_BEYOND_ZONE")
        if not s.entry_zone_high < s.target1:
            errors.append("TARGET1_NOT_BEYOND_ZONE")
        if s.target2 is not None and not s.target1 < s.target2:
            errors.append("TARGET2_NOT_BEYOND_TARGET1")
    else:
        if not s.stop > s.entry_zone_high:
            errors.append("STOP_NOT_BEYOND_ZONE")
        if not s.entry_zone_low > s.target1:
            errors.append("TARGET1_NOT_BEYOND_ZONE")
        if s.target2 is not None and not s.target1 > s.target2:
            errors.append("TARGET2_NOT_BEYOND_TARGET1")
    return errors
```

`src/core/domain/position.py`:
```python
"""Position results normalized in R (spec 3.7)."""

from __future__ import annotations

from decimal import Decimal

from core.domain.models import Direction, OrderState


def r_multiple(state: OrderState, risk_amount: Decimal) -> Decimal:
    return (state.realized_pnl - state.costs + state.dividends) / risk_amount


def excursion_r(state: OrderState, direction: Direction) -> tuple[Decimal | None, Decimal | None]:
    if state.avg_entry is None or state.initial_stop is None:
        return None, None
    risk_per_share = abs(state.avg_entry - state.initial_stop)
    sign = Decimal(1) if direction is Direction.LONG else Decimal(-1)
    mfe = None if state.best_price is None else sign * (state.best_price - state.avg_entry) / risk_per_share
    mae = None if state.worst_price is None else sign * (state.worst_price - state.avg_entry) / risk_per_share
    return mfe, mae
```

Nota: em `test_long_chain_violations`, `entry_zone_high=D(99), stop=D(90)` produz só `ZONE_INVERTED` porque `stop < entry_zone_low` e `entry_zone_high < target1` continuam válidos; a checagem de zona invertida vem primeiro na lista.

- [ ] **Step 6: Rodar e ver passar**

Run: `uv run pytest tests/domain -v`
Expected: todos os testes de `tests/domain` passam (hashing, calendar, models, validation).

- [ ] **Step 7: Commit**

```bash
git add src/core/domain tests/support.py tests/domain
git commit -m "feat(core): domain models, signal validation and position metrics

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BKUdi9RGWWdXRXVYFWj1yp"
```

### Task 5: `fill_model v1` — criação de ordem, espelhamento e fase de entrada

**Files:**
- Create: `src/core/fills/__init__.py`, `src/core/fills/v1/__init__.py`, `src/core/fills/v1/mirror.py`, `src/core/fills/v1/engine.py`, `tests/fills/__init__.py`
- Modify: `tests/support.py` (acrescentar `make_ctx`)
- Test: `tests/fills/test_entry.py`

**Interfaces:**
- Consumes: modelos da Task 4; `SessionCalendar.next_expected_minute`, `is_expected_minute`, `signal_valid_until_ts` (Task 2); `sha256_hex` (Task 1).
- Produces (em `core.fills.v1.engine`, reexportados por `core.fills.v1`):
  - `VERSION = "v1"`
  - `new_order_state(ctx: OrderContext, inherited: GatingState | None = None, extra_payload: Mapping[str, Any] | None = None) -> StepResult` — estado inicial + evento `ORDER_CREATED`
  - `step(state: OrderState, bar: Bar, ctx: OrderContext) -> StepResult`
  - `run_bars(state: OrderState, bars: Iterable[Bar], ctx: OrderContext) -> StepResult` — aplica `step` em ordem cronológica e concatena eventos
- Produces (em `core.fills`): `get_fill_model(version: str) -> ModuleType` (levanta `KeyError` para versão desconhecida)
- Produces (em `core.fills.v1.mirror`): `mirror_bar`, `mirror_signal`, `mirror_state`, `mirror_event`
- Produces (em `tests.support`): `make_ctx(signal=None, config=None, start=et("2025-11-25", "09:30")) -> OrderContext`

Códigos de `reason` em `INVALIDATED`: `OPEN_AT_OR_THROUGH_STOP` (regra 1), `STOP_TOUCHED_WITHOUT_POSITION` (regra 4), `ZONE_LOST_CANCEL` (regra 5 com política `CANCEL`). Valores de `rule` em `FILLED`: `ZONE_OPEN` (regra 2), `ZONE_CROSS` (regra 3).

- [ ] **Step 1: Acrescentar `make_ctx` em `tests/support.py`**

Adicionar ao final:
```python
from core.domain.calendar import evaluation_start_ts, signal_valid_until_ts
from core.domain.models import FillConfig, OrderContext


def make_ctx(signal: SignalSpec | None = None, config: FillConfig | None = None,
             start: datetime | None = None, calendar: SessionCalendar | None = None) -> OrderContext:
    calendar = calendar or make_calendar()
    signal = signal or long_signal()
    begin = evaluation_start_ts(calendar, start or et("2025-11-25", "09:30"))
    return OrderContext(
        signal=signal,
        config=config or FillConfig(),
        calendar=calendar,
        evaluation_start_ts=begin,
        valid_until_ts=signal_valid_until_ts(calendar, begin, signal.valid_sessions),
    )
```

- [ ] **Step 2: Escrever os testes que falham**

`tests/fills/test_entry.py`:
```python
from core.domain.models import (
    Direction, EntryPath, EventType, FillConfig, OrderStatus, ZoneLostPolicy,
)
from core.fills import get_fill_model
from core.fills.v1 import new_order_state, run_bars, step
from tests.support import D, bar, et, flat_bars, long_signal, make_ctx


def start(ctx):
    return new_order_state(ctx).state


def types(events):
    return [e.type for e in events]


def short_signal(**overrides):
    values = dict(direction=Direction.SHORT, entry_zone_low=D(100), entry_zone_high=D(102),
                  stop=D(105), target1=D(96), target2=D(92))
    values.update(overrides)
    return long_signal(**values)


def test_registry_and_order_created_event():
    assert get_fill_model("v1").VERSION == "v1"
    ctx = make_ctx()
    result = new_order_state(ctx)
    assert result.state.status is OrderStatus.PENDING
    assert result.state.stop_current == D(97)
    (created,) = result.events
    assert created.type is EventType.ORDER_CREATED and created.event_key == "ORDER_CREATED"
    assert created.payload["fill_model_version"] == "v1"
    assert created.payload["inherited_signal_state"] is None
    assert created.payload["evaluation_start_ts"] == et("2025-11-25", "09:30")


def test_rule1_open_at_stop_invalidates():
    ctx = make_ctx()
    result = step(start(ctx), bar(et("2025-11-25", "09:30"), 97, 98, 96, 97.5), ctx)
    assert result.state.status is OrderStatus.INVALIDATED
    assert types(result.events) == [EventType.INVALIDATED]
    assert result.events[0].payload["reason"] == "OPEN_AT_OR_THROUGH_STOP"


def test_rule2_open_inside_zone_fills_at_open():
    ctx = make_ctx()
    result = step(start(ctx), bar(et("2025-11-25", "09:30"), 101, 101.5, 100.5, 101.2), ctx)
    (filled,) = result.events
    assert filled.type is EventType.FILLED
    assert filled.price == D(101) and filled.qty == D(25)
    assert filled.payload["rule"] == "ZONE_OPEN"
    assert result.state.status is OrderStatus.OPEN
    assert result.state.entry_path is EntryPath.DIRECT


def test_rule3_open_above_zone_crossing_fills_at_zone_high():
    ctx = make_ctx()
    result = step(start(ctx), bar(et("2025-11-25", "09:30"), 103, 103.5, 101.5, 102.5), ctx)
    (filled,) = result.events
    assert filled.price == D(102) and filled.qty == D(20)
    assert filled.payload["rule"] == "ZONE_CROSS"


def test_touching_zone_high_does_not_fill():
    ctx = make_ctx()
    result = step(start(ctx), bar(et("2025-11-25", "09:30"), 103, 103.5, 102, 102.5), ctx)
    assert result.events == ()
    assert result.state.status is OrderStatus.PENDING


def test_ambiguous_candle_without_position_invalidates_never_fills():
    ctx = make_ctx()
    result = step(start(ctx), bar(et("2025-11-25", "09:30"), 99, 103, 96, 101), ctx)
    assert types(result.events) == [EventType.INVALIDATED]
    assert result.events[0].payload["reason"] == "STOP_TOUCHED_WITHOUT_POSITION"


def test_open_below_zone_loses_zone_without_fill():
    ctx = make_ctx()
    result = step(start(ctx), bar(et("2025-11-25", "09:30"), 99.5, 99.8, 99, 99.6), ctx)
    assert types(result.events) == [EventType.ZONE_LOST]
    assert result.state.zone_lost and result.state.status is OrderStatus.PENDING
    assert result.events[0].payload["zone_boundary_level"] == D(100)


def test_intrabar_violation_is_not_zone_loss():
    ctx = make_ctx(long_signal(trigger_price=D(104)))
    result = step(start(ctx), bar(et("2025-11-25", "09:30"), 103, 103, 99, 101), ctx)
    assert result.events == ()
    assert not result.state.zone_lost


def test_reclaim_blocks_fill_until_next_expected_minute():
    ctx = make_ctx()
    bars = [
        bar(et("2025-11-25", "09:30"), 99.5, 99.8, 99, 99.6),
        bar(et("2025-11-25", "09:31"), 99.6, 101, 99.5, 100.5),
        bar(et("2025-11-25", "09:32"), 100.5, 101, 100.2, 100.8),
    ]
    result = run_bars(start(ctx), bars, ctx)
    assert types(result.events) == [EventType.ZONE_LOST, EventType.ZONE_RECLAIMED, EventType.FILLED]
    reclaimed, filled = result.events[1], result.events[2]
    assert reclaimed.payload["entry_eligible_from"] == et("2025-11-25", "09:32")
    assert filled.bar_ts == et("2025-11-25", "09:32") and filled.price == D("100.5")
    assert result.state.entry_path is EntryPath.RECLAIMED


def test_zone_lost_and_reclaimed_in_same_candle():
    ctx = make_ctx()
    first = step(start(ctx), bar(et("2025-11-25", "09:30"), 99.5, 101, 99.2, 100.4), ctx)
    assert types(first.events) == [EventType.ZONE_LOST, EventType.ZONE_RECLAIMED]
    second = step(first.state, bar(et("2025-11-25", "09:31"), 100.4, 100.9, 100.1, 100.6), ctx)
    assert types(second.events) == [EventType.FILLED]


def test_reclaim_on_last_minute_waits_for_next_session():
    ctx = make_ctx()
    bars = [
        bar(et("2025-11-25", "15:58"), 99.5, 99.8, 99, 99.6),
        bar(et("2025-11-25", "15:59"), 99.6, 101, 99.5, 100.5),
    ]
    result = run_bars(start(ctx), bars, ctx)
    assert result.state.entry_eligible_from == et("2025-11-26", "09:30")


def test_zone_lost_cancel_policy_invalidates():
    ctx = make_ctx(config=FillConfig(zone_lost_policy=ZoneLostPolicy.CANCEL))
    result = step(start(ctx), bar(et("2025-11-25", "09:30"), 99.5, 99.8, 99, 99.6), ctx)
    assert types(result.events) == [EventType.ZONE_LOST, EventType.INVALIDATED]
    assert result.events[1].payload["reason"] == "ZONE_LOST_CANCEL"


def test_trigger_hit_takes_effect_on_next_expected_minute():
    ctx = make_ctx(long_signal(trigger_price=D("101.5")))
    first = step(start(ctx), bar(et("2025-11-25", "09:30"), 101, 101.6, 100.8, 101.2), ctx)
    assert types(first.events) == [EventType.TRIGGER_HIT]
    second = step(first.state, bar(et("2025-11-25", "09:31"), 101.2, 101.4, 101, 101.3), ctx)
    assert types(second.events) == [EventType.FILLED]
    assert second.events[0].price == D("101.2")


def test_no_market_event_before_evaluation_start():
    ctx = make_ctx(start=et("2025-11-25", "11:30"))
    early = flat_bars(ctx.calendar, et("2025-11-25", "10:00"), et("2025-11-25", "11:30"), 101)
    result = run_bars(start(ctx), early + [bar(et("2025-11-25", "11:30"), 101, 101, 101, 101)], ctx)
    assert types(result.events) == [EventType.FILLED]
    assert result.events[0].bar_ts == et("2025-11-25", "11:30")


def test_entry_slippage_is_adverse():
    ctx = make_ctx(config=FillConfig(entry_slippage_bps=D(10)))
    result = step(start(ctx), bar(et("2025-11-25", "09:30"), 101, 101.5, 100.5, 101.2), ctx)
    assert result.events[0].price == D("101.101")
    assert result.events[0].payload["raw_price"] == D(101)


def test_short_entry_rules_are_mirrored():
    ctx = make_ctx(short_signal())
    inside = step(start(ctx), bar(et("2025-11-25", "09:30"), 101, 101.5, 100.5, 101), ctx)
    assert inside.events[0].price == D(101) and inside.events[0].qty == D(25)
    crossing = step(start(ctx), bar(et("2025-11-25", "09:30"), 99, 100.5, 98.8, 100.2), ctx)
    assert crossing.events[0].price == D(100) and crossing.events[0].qty == D(20)
    assert crossing.state.avg_entry == D(100) and crossing.state.stop_current == D(105)
    through_stop = step(start(ctx), bar(et("2025-11-25", "09:30"), 105, 105.5, 104, 104.5), ctx)
    assert through_stop.state.status is OrderStatus.INVALIDATED
    lost = step(start(ctx), bar(et("2025-11-25", "09:30"), 103, 103.5, 102.5, 103), ctx)
    assert types(lost.events) == [EventType.ZONE_LOST]
    assert lost.events[0].payload["zone_boundary_level"] == D(102)


def test_ignores_non_expected_minutes_and_reprocessed_bars():
    ctx = make_ctx()
    state = start(ctx)
    assert step(state, bar(et("2025-11-25", "16:00"), 101, 101, 101, 101), ctx).events == ()
    first = step(state, bar(et("2025-11-25", "09:30"), 99.5, 99.8, 99, 99.6), ctx)
    again = step(first.state, bar(et("2025-11-25", "09:30"), 99.5, 99.8, 99, 99.6), ctx)
    assert again.events == () and again.state == first.state
```

- [ ] **Step 3: Rodar e ver falhar**

Run: `mkdir -p src/core/fills/v1 tests/fills && touch tests/fills/__init__.py && uv run pytest tests/fills/test_entry.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'core.fills'`.

- [ ] **Step 4: Implementar o registro e o espelhamento**

`src/core/fills/__init__.py`:
```python
"""Fill model registry. Each version lives in its own frozen module (spec section 0)."""

from __future__ import annotations

from importlib import import_module
from types import ModuleType

_VERSIONS = {"v1": "core.fills.v1"}


def get_fill_model(version: str) -> ModuleType:
    if version not in _VERSIONS:
        raise KeyError(f"unknown fill_model_version: {version}")
    return import_module(_VERSIONS[version])
```

`src/core/fills/v1/mirror.py`:
```python
"""SHORT orders run through the LONG rules on negated prices (p -> -p)."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from core.domain.models import Bar, Event, OrderState, SignalSpec

PRICE_SUFFIXES = ("_price", "_level")


def _neg(value: Decimal | None) -> Decimal | None:
    return None if value is None else -value


def mirror_bar(bar: Bar) -> Bar:
    return replace(bar, open=-bar.open, high=-bar.low, low=-bar.high, close=-bar.close)


def mirror_signal(signal: SignalSpec) -> SignalSpec:
    return replace(
        signal,
        entry_zone_low=-signal.entry_zone_high,
        entry_zone_high=-signal.entry_zone_low,
        stop=-signal.stop,
        target1=-signal.target1,
        target2=_neg(signal.target2),
        trigger_price=_neg(signal.trigger_price),
    )


def mirror_state(state: OrderState) -> OrderState:
    return replace(
        state,
        avg_entry=_neg(state.avg_entry),
        initial_stop=_neg(state.initial_stop),
        stop_current=_neg(state.stop_current),
        stop_previous=_neg(state.stop_previous),
        best_price=_neg(state.best_price),
        worst_price=_neg(state.worst_price),
    )


def mirror_event(event: Event) -> Event:
    payload = {
        key: (-value if key.endswith(PRICE_SUFFIXES) and isinstance(value, Decimal) else value)
        for key, value in event.payload.items()
    }
    return replace(event, price=_neg(event.price), payload=payload)
```

- [ ] **Step 5: Implementar o motor (entrada)**

`src/core/fills/v1/engine.py`:
```python
"""fill_model v1 (spec section 4). FROZEN: behavior changes require a new version module."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import Any, Iterable, Mapping

from core.domain.hashing import sha256_hex
from core.domain.models import (
    Bar,
    Direction,
    EntryPath,
    Event,
    EventType,
    GatingState,
    OrderContext,
    OrderState,
    OrderStatus,
    SignalSpec,
    StepResult,
    ZoneLostPolicy,
)
from core.fills.v1.mirror import mirror_bar, mirror_event, mirror_signal, mirror_state

VERSION = "v1"
BPS = Decimal("10000")


def _adverse_buy(price: Decimal, bps: Decimal) -> Decimal:
    return price + abs(price) * bps / BPS


def _adverse_sell(price: Decimal, bps: Decimal) -> Decimal:
    return price - abs(price) * bps / BPS


def _min(current: Decimal | None, value: Decimal) -> Decimal:
    return value if current is None else min(current, value)


def _max(current: Decimal | None, value: Decimal) -> Decimal:
    return value if current is None else max(current, value)


def _execution_cost(ctx: OrderContext, price: Decimal, qty: Decimal, leg: str) -> Decimal:
    config = ctx.config
    cost = config.commission_per_execution
    if config.sec_taf_fees_enabled:
        is_long = ctx.signal.direction is Direction.LONG
        is_sell = (leg == "exit") if is_long else (leg == "entry")
        if is_sell:
            cost += abs(price) * qty * config.sec_fee_rate
            cost += min(qty * config.taf_fee_per_share, config.taf_fee_max)
    return cost


def new_order_state(
    ctx: OrderContext,
    inherited: GatingState | None = None,
    extra_payload: Mapping[str, Any] | None = None,
) -> StepResult:
    state = OrderState(initial_stop=ctx.signal.stop, stop_current=ctx.signal.stop)
    inherited_payload = None
    inherited_hash = None
    if inherited is not None:
        if inherited.status is not OrderStatus.PENDING:
            raise ValueError("inherited signal state must be PENDING")
        state = replace(
            state,
            zone_lost=inherited.zone_lost,
            zone_ever_lost=inherited.zone_lost,
            entry_eligible_from=inherited.entry_eligible_from,
            trigger_hit_at=inherited.trigger_hit_at,
        )
        inherited_payload = inherited.as_payload()
        inherited_hash = sha256_hex(inherited_payload)
    payload: dict[str, Any] = {
        "fill_model_version": VERSION,
        "signal": ctx.signal.as_payload(),
        "config": ctx.config.snapshot(),
        "evaluation_start_ts": ctx.evaluation_start_ts,
        "valid_until_ts": ctx.valid_until_ts,
        "inherited_signal_state": inherited_payload,
        "inherited_signal_state_hash": inherited_hash,
    }
    if extra_payload:
        overlap = set(extra_payload) & set(payload)
        if overlap:
            raise ValueError(f"extra_payload overrides reserved keys: {sorted(overlap)}")
        payload.update(extra_payload)
    return StepResult(state, (Event(EventType.ORDER_CREATED, "ORDER_CREATED", payload=payload),))


def step(state: OrderState, bar: Bar, ctx: OrderContext) -> StepResult:
    if state.is_final or state.frozen:
        return StepResult(state)
    if bar.ts < ctx.evaluation_start_ts or bar.ts >= ctx.valid_until_ts:
        return StepResult(state)
    if state.last_bar_ts is not None and bar.ts <= state.last_bar_ts:
        return StepResult(state)
    if not ctx.calendar.is_expected_minute(bar.ts):
        return StepResult(state)

    is_long = ctx.signal.direction is Direction.LONG
    signal = ctx.signal if is_long else mirror_signal(ctx.signal)
    work_bar = bar if is_long else mirror_bar(bar)
    work_state = state if is_long else mirror_state(state)

    if work_state.status is OrderStatus.PENDING:
        work_state, events = _entry_phase(work_state, work_bar, signal, ctx)
    else:
        work_state, events = _exit_phase(work_state, work_bar, signal, ctx, entry_bar=False)
    work_state = replace(work_state, last_bar_ts=bar.ts)

    if not is_long:
        work_state = mirror_state(work_state)
        events = [mirror_event(event) for event in events]
    return StepResult(work_state, tuple(events))


def run_bars(state: OrderState, bars: Iterable[Bar], ctx: OrderContext) -> StepResult:
    events: list[Event] = []
    for current in sorted(bars, key=lambda b: b.ts):
        result = step(state, current, ctx)
        state = result.state
        events.extend(result.events)
    return StepResult(state, tuple(events))


def _eligible(state: OrderState, bar: Bar, signal: SignalSpec, ctx: OrderContext) -> bool:
    if state.zone_lost:
        return False
    if state.entry_eligible_from is not None and bar.ts < state.entry_eligible_from:
        return False
    if signal.trigger_price is not None:
        if state.trigger_hit_at is None:
            return False
        if bar.ts < ctx.calendar.next_expected_minute(state.trigger_hit_at):
            return False
    return True


def _invalidate(state: OrderState, bar: Bar, reason: str) -> tuple[OrderState, list[Event]]:
    event = Event(
        EventType.INVALIDATED, "INVALIDATED", bar.ts,
        bar_batch_id=bar.batch_id, payload={"reason": reason},
    )
    return replace(state, status=OrderStatus.INVALIDATED, final_event_ts=bar.ts), [event]


def _entry_phase(
    state: OrderState, bar: Bar, signal: SignalSpec, ctx: OrderContext
) -> tuple[OrderState, list[Event]]:
    # Rule 1
    if bar.open <= signal.stop:
        return _invalidate(state, bar, "OPEN_AT_OR_THROUGH_STOP")

    # Rules 2 and 3
    if _eligible(state, bar, signal, ctx):
        raw_price: Decimal | None = None
        rule = ""
        if signal.entry_zone_low <= bar.open <= signal.entry_zone_high:
            raw_price, rule = bar.open, "ZONE_OPEN"
        elif bar.open > signal.entry_zone_high and bar.low < signal.entry_zone_high:
            raw_price, rule = signal.entry_zone_high, "ZONE_CROSS"
        if raw_price is not None:
            state, fill_events = _fill(state, bar, signal, ctx, raw_price, rule)
            state, exit_events = _exit_phase(state, bar, signal, ctx, entry_bar=True)
            return state, fill_events + exit_events

    # Rule 4
    if bar.low <= signal.stop:
        return _invalidate(state, bar, "STOP_TOUCHED_WITHOUT_POSITION")

    events: list[Event] = []
    # Rule 5: support lost only by open or close below the zone
    if not state.zone_lost and (bar.open < signal.entry_zone_low or bar.close < signal.entry_zone_low):
        events.append(
            Event(
                EventType.ZONE_LOST, f"ZONE_LOST:{bar.ts.isoformat()}", bar.ts,
                bar_batch_id=bar.batch_id,
                payload={"zone_boundary_level": signal.entry_zone_low},
            )
        )
        state = replace(state, zone_lost=True, zone_ever_lost=True)
        if ctx.config.zone_lost_policy is ZoneLostPolicy.CANCEL:
            state, invalidated = _invalidate(state, bar, "ZONE_LOST_CANCEL")
            return state, events + invalidated

    # Rule 6
    if state.zone_lost and bar.close >= signal.entry_zone_low:
        eligible_from = ctx.calendar.next_expected_minute(bar.ts)
        events.append(
            Event(
                EventType.ZONE_RECLAIMED, f"ZONE_RECLAIMED:{bar.ts.isoformat()}", bar.ts,
                bar_batch_id=bar.batch_id,
                payload={"entry_eligible_from": eligible_from},
            )
        )
        state = replace(state, zone_lost=False, entry_eligible_from=eligible_from)

    # Trigger: effective from the next expected minute (spec 4.2)
    if (
        signal.trigger_price is not None
        and state.trigger_hit_at is None
        and bar.high >= signal.trigger_price
    ):
        events.append(
            Event(EventType.TRIGGER_HIT, "TRIGGER_HIT", bar.ts, price=signal.trigger_price,
                  bar_batch_id=bar.batch_id)
        )
        state = replace(state, trigger_hit_at=bar.ts)
    return state, events


def _fill(
    state: OrderState, bar: Bar, signal: SignalSpec, ctx: OrderContext, raw_price: Decimal, rule: str
) -> tuple[OrderState, list[Event]]:
    price = _adverse_buy(raw_price, ctx.config.entry_slippage_bps)
    qty = ctx.config.risk_amount / (price - signal.stop)
    cost = _execution_cost(ctx, price, qty, "entry")
    path = EntryPath.RECLAIMED if state.zone_ever_lost else EntryPath.DIRECT
    event = Event(
        EventType.FILLED, "FILLED", bar.ts, price=price, qty=qty, bar_batch_id=bar.batch_id,
        payload={"rule": rule, "entry_path": path, "raw_price": raw_price, "cost": cost},
    )
    state = replace(
        state,
        status=OrderStatus.OPEN,
        avg_entry=price,
        initial_stop=signal.stop,
        stop_current=signal.stop,
        qty_total=qty,
        qty_open=qty,
        costs=state.costs + cost,
        entry_path=path,
        opened_at=bar.ts,
        best_price=price,
    )
    return state, [event]


def _exit_phase(
    state: OrderState, bar: Bar, signal: SignalSpec, ctx: OrderContext, entry_bar: bool
) -> tuple[OrderState, list[Event]]:
    # Task 5 version: excursion tracking only. Task 6 replaces this function with stops and targets.
    best = state.best_price if entry_bar else _max(state.best_price, bar.high)
    return replace(state, best_price=best, worst_price=_min(state.worst_price, bar.low)), []
```

`src/core/fills/v1/__init__.py`:
```python
from core.fills.v1.engine import VERSION, new_order_state, run_bars, step

__all__ = ["VERSION", "new_order_state", "run_bars", "step"]
```

- [ ] **Step 6: Rodar e ver passar**

Run: `uv run pytest tests/fills/test_entry.py -v`
Expected: 17 passed.

- [ ] **Step 7: Rodar a suíte inteira**

Run: `uv run pytest`
Expected: todos passam.

- [ ] **Step 8: Commit**

```bash
git add src/core/fills tests/fills tests/support.py
git commit -m "feat(fills): v1 order creation, SHORT mirroring and entry rules

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BKUdi9RGWWdXRXVYFWj1yp"
```

### Task 6: `fill_model v1` — saídas, breakeven, custos e excursões

**Files:**
- Modify: `src/core/fills/v1/engine.py` (substituir `_exit_phase`, adicionar `_close`, ajustar imports)
- Test: `tests/fills/test_exits.py`

**Interfaces:**
- Consumes: `step`, `run_bars`, `new_order_state` (Task 5); `excursion_r`, `r_multiple` (Task 4).
- Produces: comportamento de saída em `step` (sem mudança de assinatura). Eventos: `STOPPED` (payload `stop_kind` ∈ `INITIAL|BREAKEVEN`, `stop_level`, `raw_price`, `pnl`, `cost`), `TARGET1_HIT` (payload `final: bool`, `pnl`, `cost`; se parcial também `new_stop_level`, `stop_active_from`), `TARGET2_HIT` (payload `final: True`, `pnl`, `cost`). `state.close_reason` ∈ `STOPPED|TARGET_FINAL`.

- [ ] **Step 1: Escrever os testes que falham**

`tests/fills/test_exits.py`:
```python
from core.domain.models import CloseReason, Direction, EventType, FillConfig, OrderStatus
from core.domain.position import excursion_r, r_multiple
from core.fills.v1 import new_order_state, run_bars, step
from tests.support import D, bar, et, long_signal, make_ctx

ENTRY = bar(et("2025-11-25", "09:30"), 101, 101.5, 100.5, 101.2)


def opened(ctx, entry=ENTRY):
    return step(new_order_state(ctx).state, entry, ctx)


def types(events):
    return [e.type for e in events]


def test_fill_and_stop_in_same_candle_is_worst_case():
    ctx = make_ctx()
    result = opened(ctx, bar(et("2025-11-25", "09:30"), 101, 101.5, 96.5, 97.5))
    assert types(result.events) == [EventType.FILLED, EventType.STOPPED]
    stopped = result.events[1]
    assert stopped.price == D("96.9515")
    assert stopped.payload["stop_kind"] == "INITIAL"
    assert stopped.payload["pnl"] == D("-101.2125")
    assert result.state.status is OrderStatus.CLOSED
    assert result.state.close_reason is CloseReason.STOPPED


def test_entry_candle_never_hits_target():
    ctx = make_ctx()
    first = opened(ctx, bar(et("2025-11-25", "09:30"), 101, 107, 100.5, 106.5))
    assert types(first.events) == [EventType.FILLED]
    second = step(first.state, bar(et("2025-11-25", "09:31"), 106, 106.2, 105.5, 106), ctx)
    assert types(second.events) == [EventType.TARGET1_HIT]


def test_target_by_touch_scales_out_and_schedules_breakeven():
    ctx = make_ctx()
    result = step(opened(ctx).state, bar(et("2025-11-25", "09:31"), 105, 106, 104.8, 105.5), ctx)
    (t1,) = result.events
    assert t1.type is EventType.TARGET1_HIT
    assert t1.price == D(106) and t1.qty == D("12.5") and t1.payload["pnl"] == D("62.5")
    state = result.state
    assert state.status is OrderStatus.PARTIAL and state.qty_open == D("12.5")
    assert state.stop_current == D(101) and state.stop_previous == D(97)
    assert state.stop_active_from == et("2025-11-25", "09:32")


def test_breakeven_not_active_on_target1_candle_then_active():
    ctx = make_ctx()
    t1 = step(opened(ctx).state, bar(et("2025-11-25", "09:31"), 105, 106, 100, 100.5), ctx)
    assert types(t1.events) == [EventType.TARGET1_HIT]
    after = step(t1.state, bar(et("2025-11-25", "09:32"), 101.5, 101.6, 100.9, 101), ctx)
    (stopped,) = after.events
    assert stopped.type is EventType.STOPPED
    assert stopped.payload["stop_kind"] == "BREAKEVEN"
    assert stopped.price == D("100.9495") and stopped.qty == D("12.5")


def test_stop_wins_over_target_in_same_candle():
    ctx = make_ctx()
    result = step(opened(ctx).state, bar(et("2025-11-25", "09:31"), 100, 106.5, 96, 99), ctx)
    assert types(result.events) == [EventType.STOPPED]
    assert result.events[0].payload["raw_price"] == D(97)


def test_gap_through_stop_fills_at_open_with_slippage():
    ctx = make_ctx()
    result = step(opened(ctx).state, bar(et("2025-11-25", "09:31"), 95, 95.5, 94, 95), ctx)
    assert result.events[0].price == D("94.9525")


def test_gap_above_target_gets_no_price_improvement():
    ctx = make_ctx()
    result = step(opened(ctx).state, bar(et("2025-11-25", "09:31"), 108, 108.5, 107, 108), ctx)
    assert types(result.events) == [EventType.TARGET1_HIT]
    assert result.events[0].price == D(106)


def test_target1_and_target2_in_same_candle():
    ctx = make_ctx()
    result = step(opened(ctx).state, bar(et("2025-11-25", "09:31"), 105, 111, 104, 110.5), ctx)
    assert types(result.events) == [EventType.TARGET1_HIT, EventType.TARGET2_HIT]
    assert result.events[1].price == D(110) and result.events[1].qty == D("12.5")
    assert result.state.close_reason is CloseReason.TARGET_FINAL
    assert r_multiple(result.state, D(100)) == D("1.75")


def test_without_target2_target1_closes_everything():
    ctx = make_ctx(long_signal(target2=None))
    result = step(opened(ctx).state, bar(et("2025-11-25", "09:31"), 105, 106, 104.8, 105.5), ctx)
    (t1,) = result.events
    assert t1.qty == D(25) and t1.payload["final"] is True
    assert result.state.status is OrderStatus.CLOSED
    assert result.state.close_reason is CloseReason.TARGET_FINAL


def test_commission_charged_per_execution():
    ctx = make_ctx(config=FillConfig(commission_per_execution=D(1)))
    bars = [
        ENTRY,
        bar(et("2025-11-25", "09:31"), 105, 106, 104.8, 105.5),
        bar(et("2025-11-25", "09:32"), 101.5, 101.6, 100.9, 101),
    ]
    result = run_bars(new_order_state(ctx).state, bars, ctx)
    assert types(result.events) == [EventType.FILLED, EventType.TARGET1_HIT, EventType.STOPPED]
    assert result.state.costs == D(3)
    assert all(e.payload["cost"] == D(1) for e in result.events)


def test_sec_taf_fees_apply_to_sells_only():
    config = FillConfig(sec_taf_fees_enabled=True, sec_fee_rate=D("0.0001"),
                        taf_fee_per_share=D("0.01"), taf_fee_max=D(5))
    long_ctx = make_ctx(config=config)
    first = opened(long_ctx)
    assert first.events[0].payload["cost"] == D(0)
    t1 = step(first.state, bar(et("2025-11-25", "09:31"), 105, 106, 104.8, 105.5), long_ctx)
    assert t1.events[0].payload["cost"] == D("0.2575")
    short_ctx = make_ctx(
        long_signal(direction=Direction.SHORT, stop=D(105), target1=D(96), target2=D(92)), config
    )
    short_fill = opened(short_ctx, bar(et("2025-11-25", "09:30"), 101, 101.5, 100.5, 101))
    assert short_fill.events[0].payload["cost"] == D("0.5025")


def test_excursions_ignore_entry_candle_high():
    ctx = make_ctx()
    first = opened(ctx)
    assert first.state.best_price == D(101) and first.state.worst_price == D("100.5")
    second = step(first.state, bar(et("2025-11-25", "09:31"), 101.2, 104, 99.5, 103), ctx)
    assert excursion_r(second.state, Direction.LONG) == (D("0.75"), D("-0.375"))


def test_short_exits_are_mirrored():
    ctx = make_ctx(long_signal(direction=Direction.SHORT, stop=D(105), target1=D(96), target2=D(92)))
    first = opened(ctx, bar(et("2025-11-25", "09:30"), 101, 101.5, 100.5, 101))
    t1 = step(first.state, bar(et("2025-11-25", "09:31"), 97, 97.2, 96, 96.5), ctx)
    assert types(t1.events) == [EventType.TARGET1_HIT]
    assert t1.events[0].price == D(96) and t1.events[0].payload["pnl"] == D("62.5")
    assert t1.state.stop_current == D(101)
    assert t1.events[0].payload["new_stop_level"] == D(101)
    stop = step(t1.state, bar(et("2025-11-25", "09:32"), 100, 101.2, 99.8, 101), ctx)
    (stopped,) = stop.events
    assert stopped.payload["stop_kind"] == "BREAKEVEN"
    assert stopped.price == D("101.0505") and stopped.payload["stop_level"] == D(101)
    assert stopped.payload["pnl"] == D("-0.63125")
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/fills/test_exits.py -v`
Expected: FAIL — p.ex. `test_fill_and_stop_in_same_candle_is_worst_case` com `assert [FILLED] == [FILLED, STOPPED]`.

- [ ] **Step 3: Implementar**

Em `src/core/fills/v1/engine.py`, adicionar `CloseReason` e `ZERO` ao import de `core.domain.models`:
```python
from core.domain.models import (
    ZERO,
    Bar,
    CloseReason,
    Direction,
    EntryPath,
    Event,
    EventType,
    GatingState,
    OrderContext,
    OrderState,
    OrderStatus,
    SignalSpec,
    StepResult,
    ZoneLostPolicy,
)
```

Substituir a função `_exit_phase` inteira por:
```python
def _exit_phase(
    state: OrderState, bar: Bar, signal: SignalSpec, ctx: OrderContext, entry_bar: bool
) -> tuple[OrderState, list[Event]]:
    config = ctx.config
    stop_level = state.stop_current
    if state.stop_active_from is not None and bar.ts < state.stop_active_from:
        stop_level = state.stop_previous
    best = state.best_price if entry_bar else _max(state.best_price, bar.high)
    state = replace(state, best_price=best, worst_price=_min(state.worst_price, bar.low))

    # Stop first (spec 4.4)
    if bar.low <= stop_level:
        raw_price = min(bar.open, stop_level)
        price = _adverse_sell(raw_price, config.stop_slippage_bps)
        return _close(
            state, bar, ctx, EventType.STOPPED, price, CloseReason.STOPPED,
            {
                "stop_kind": "BREAKEVEN" if state.t1_done else "INITIAL",
                "stop_level": stop_level,
                "raw_price": raw_price,
            },
        )
    if entry_bar:
        return state, []

    events: list[Event] = []
    if not state.t1_done and bar.high >= signal.target1:
        if signal.target2 is None:
            return _close(
                state, bar, ctx, EventType.TARGET1_HIT, signal.target1,
                CloseReason.TARGET_FINAL, {"final": True},
            )
        qty = state.qty_total * config.target1_scale_out_pct / Decimal(100)
        pnl = (signal.target1 - state.avg_entry) * qty
        cost = _execution_cost(ctx, signal.target1, qty, "exit")
        active_from = ctx.calendar.next_expected_minute(bar.ts)
        events.append(
            Event(
                EventType.TARGET1_HIT, "TARGET1_HIT", bar.ts, price=signal.target1, qty=qty,
                bar_batch_id=bar.batch_id,
                payload={
                    "final": False,
                    "pnl": pnl,
                    "cost": cost,
                    "new_stop_level": state.avg_entry,
                    "stop_active_from": active_from,
                },
            )
        )
        state = replace(
            state,
            status=OrderStatus.PARTIAL,
            t1_done=True,
            qty_open=state.qty_open - qty,
            realized_pnl=state.realized_pnl + pnl,
            costs=state.costs + cost,
            stop_previous=state.stop_current,
            stop_current=state.avg_entry,
            stop_active_from=active_from,
        )
    if state.t1_done and signal.target2 is not None and bar.high >= signal.target2:
        state, closing = _close(
            state, bar, ctx, EventType.TARGET2_HIT, signal.target2,
            CloseReason.TARGET_FINAL, {"final": True},
        )
        events.extend(closing)
    return state, events


def _close(
    state: OrderState,
    bar: Bar,
    ctx: OrderContext,
    event_type: EventType,
    price: Decimal,
    reason: CloseReason,
    extra: dict[str, Any],
) -> tuple[OrderState, list[Event]]:
    qty = state.qty_open
    pnl = (price - state.avg_entry) * qty
    cost = _execution_cost(ctx, price, qty, "exit")
    event = Event(
        event_type, event_type.value, bar.ts, price=price, qty=qty, bar_batch_id=bar.batch_id,
        payload={**extra, "pnl": pnl, "cost": cost},
    )
    state = replace(
        state,
        status=OrderStatus.CLOSED,
        qty_open=ZERO,
        realized_pnl=state.realized_pnl + pnl,
        costs=state.costs + cost,
        close_reason=reason,
        closed_at=bar.ts,
        final_event_ts=bar.ts,
    )
    return state, [event]
```

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/fills -v`
Expected: `test_entry.py` (17) e `test_exits.py` (13) passam.

- [ ] **Step 5: Commit**

```bash
git add src/core/fills/v1/engine.py tests/fills/test_exits.py
git commit -m "feat(fills): v1 exits, breakeven activation, costs and excursions

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BKUdi9RGWWdXRXVYFWj1yp"
```

### Task 7: `fill_model v1` — validade, cancelamento, congelamento, revisão e dividendos

**Files:**
- Modify: `src/core/fills/v1/engine.py` (validade dentro de `step`; novas funções públicas), `src/core/fills/v1/__init__.py`
- Test: `tests/fills/test_lifecycle.py`

**Interfaces:**
- Consumes: Tasks 5 e 6.
- Produces (em `core.fills.v1`):
  - `apply_validity_end(state: OrderState, ctx: OrderContext, last_bar: Bar | None, now: datetime) -> StepResult` — para o job de fim de dia quando o candle do último minuto esperado não existiu. `last_bar` deve ser o último candle já processado (`last_bar.ts == state.last_bar_ts`), senão `ValueError`.
  - `cancel(state: OrderState, at: datetime, requested_by: str = "user") -> StepResult`
  - `freeze(state: OrderState, reason: str, ref: str) -> StepResult` — eventos `FROZEN:{reason}` e `NEEDS_REVIEW:{reason}:{ref}`
  - `flag_review(state: OrderState, reason: str, ref: str) -> StepResult` — evento `NEEDS_REVIEW:{reason}:{ref}`; acrescenta `reason` a `state.review_reasons` sem duplicar
  - `apply_dividend(state: OrderState, ctx: OrderContext, ex_date: date, amount: Decimal, validated: bool) -> StepResult`
- Comportamento novo em `step`: ao processar o candle cujo `bar_ts == calendar.last_expected_minute_before(valid_until_ts)`, ordem `PENDING` → `EXPIRED` (sem `bar_ts`, `final_event_ts = valid_until_ts`); `OPEN`/`PARTIAL` → `TIME_EXIT` no `close` com slippage de stop.

- [ ] **Step 1: Escrever os testes que falham**

`tests/fills/test_lifecycle.py`:
```python
from datetime import date

import pytest

from core.domain.models import CloseReason, Direction, EventType, OrderStatus
from core.domain.position import r_multiple
from core.fills.v1 import (
    apply_dividend, apply_validity_end, cancel, flag_review, freeze, new_order_state, run_bars, step,
)
from tests.support import D, bar, et, long_signal, make_ctx

ENTRY = bar(et("2025-11-25", "09:30"), 101, 101.5, 100.5, 101.2)


def types(events):
    return [e.type for e in events]


def one_session_ctx(**signal_overrides):
    return make_ctx(long_signal(valid_sessions=1, **signal_overrides))


def test_time_exit_on_last_expected_minute():
    ctx = one_session_ctx()
    last = bar(et("2025-11-25", "15:59"), 103, 103.5, 102.5, 103)
    result = run_bars(new_order_state(ctx).state, [ENTRY, last], ctx)
    assert types(result.events) == [EventType.FILLED, EventType.TIME_EXIT]
    exit_event = result.events[1]
    assert exit_event.price == D("102.9485") and exit_event.qty == D(25)
    assert exit_event.payload["raw_price"] == D(103)
    assert result.state.close_reason is CloseReason.TIME_EXIT


def test_pending_order_expires_on_last_expected_minute():
    ctx = one_session_ctx()
    result = step(new_order_state(ctx).state, bar(et("2025-11-25", "15:59"), 104, 104, 103.5, 104), ctx)
    (expired,) = result.events
    assert expired.type is EventType.EXPIRED and expired.bar_ts is None
    assert result.state.status is OrderStatus.EXPIRED
    assert result.state.final_event_ts == et("2025-11-25", "16:00")


def test_half_day_last_minute_and_bars_after_validity_ignored():
    ctx = make_ctx(long_signal(valid_sessions=1), start=et("2025-11-28", "09:30"))
    result = step(new_order_state(ctx).state, bar(et("2025-11-28", "12:59"), 104, 104, 103.5, 104), ctx)
    assert types(result.events) == [EventType.EXPIRED]
    later = step(new_order_state(ctx).state, bar(et("2025-12-01", "09:30"), 101, 101, 101, 101), ctx)
    assert later.events == ()


def test_apply_validity_end_when_last_bar_missing():
    ctx = one_session_ctx()
    last_seen = bar(et("2025-11-25", "15:30"), 103, 103.5, 102.5, 103)
    opened = run_bars(new_order_state(ctx).state, [ENTRY, last_seen], ctx)
    assert apply_validity_end(opened.state, ctx, last_seen, et("2025-11-25", "15:45")).events == ()
    closed = apply_validity_end(opened.state, ctx, last_seen, et("2025-11-25", "16:30"))
    assert types(closed.events) == [EventType.TIME_EXIT]
    assert closed.events[0].bar_ts == et("2025-11-25", "15:30")
    with pytest.raises(ValueError):
        apply_validity_end(opened.state, ctx, ENTRY, et("2025-11-25", "16:30"))

    pending = new_order_state(ctx).state
    expired = apply_validity_end(pending, ctx, None, et("2025-11-25", "16:30"))
    assert types(expired.events) == [EventType.EXPIRED]

    no_bar = apply_validity_end(opened.state, ctx, None, et("2025-11-25", "16:30"))
    assert [e.event_key for e in no_bar.events] == [
        "NEEDS_REVIEW:NO_EXIT_BAR:2025-11-25T21:00:00+00:00"
    ]
    assert no_bar.state.review_reasons == ("NO_EXIT_BAR",)


def test_short_time_exit_slippage_is_adverse():
    ctx = make_ctx(long_signal(direction=Direction.SHORT, stop=D(105), target1=D(96),
                               target2=D(92), valid_sessions=1))
    entry = bar(et("2025-11-25", "09:30"), 101, 101.5, 100.5, 101)
    last = bar(et("2025-11-25", "15:59"), 99, 99.5, 98.5, 99)
    result = run_bars(new_order_state(ctx).state, [entry, last], ctx)
    assert result.events[-1].type is EventType.TIME_EXIT
    assert result.events[-1].price == D("99.0495")


def test_cancel():
    ctx = make_ctx()
    state = new_order_state(ctx).state
    canceled = cancel(state, et("2025-11-25", "10:00"))
    assert types(canceled.events) == [EventType.CANCELED]
    assert canceled.state.status is OrderStatus.CANCELED
    assert cancel(canceled.state, et("2025-11-25", "10:01")).events == ()
    assert step(canceled.state, ENTRY, ctx).events == ()


def test_freeze_blocks_market_events_and_flags_review():
    ctx = make_ctx()
    frozen = freeze(new_order_state(ctx).state, "SPLIT", "2025-11-26")
    assert [e.event_key for e in frozen.events] == ["FROZEN:SPLIT", "NEEDS_REVIEW:SPLIT:2025-11-26"]
    assert frozen.state.frozen and frozen.state.review_reasons == ("SPLIT",)
    assert step(frozen.state, ENTRY, ctx).events == ()


def test_flag_review_does_not_duplicate_reason():
    state = new_order_state(make_ctx()).state
    once = flag_review(state, "DAILY_RANGE_MISMATCH", "2025-11-25")
    twice = flag_review(once.state, "DAILY_RANGE_MISMATCH", "2025-11-26")
    assert twice.state.review_reasons == ("DAILY_RANGE_MISMATCH",)
    assert twice.events[0].event_key == "NEEDS_REVIEW:DAILY_RANGE_MISMATCH:2025-11-26"


def test_dividends_credit_long_debit_short_and_require_validation():
    ctx = make_ctx()
    opened = step(new_order_state(ctx).state, ENTRY, ctx).state
    credited = apply_dividend(opened, ctx, date(2025, 11, 26), D("0.24"), validated=True)
    (dividend,) = credited.events
    assert dividend.event_key == "DIVIDEND:2025-11-26" and dividend.payload["cash"] == D(6)
    assert r_multiple(credited.state, D(100)) == D("0.06")

    unverified = apply_dividend(opened, ctx, date(2025, 11, 26), D("0.24"), validated=False)
    assert [e.event_key for e in unverified.events] == ["NEEDS_REVIEW:DIVIDEND_UNVERIFIED:2025-11-26"]
    assert unverified.state.dividends == D(0)

    assert apply_dividend(new_order_state(ctx).state, ctx, date(2025, 11, 26), D("0.24"), True).events == ()

    short_ctx = make_ctx(long_signal(direction=Direction.SHORT, stop=D(105), target1=D(96), target2=D(92)))
    short_open = step(new_order_state(short_ctx).state,
                      bar(et("2025-11-25", "09:30"), 101, 101.5, 100.5, 101), short_ctx).state
    debited = apply_dividend(short_open, short_ctx, date(2025, 11, 26), D("0.24"), validated=True)
    assert debited.events[0].payload["cash"] == D(-6)
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/fills/test_lifecycle.py -v`
Expected: FAIL com `ImportError: cannot import name 'apply_dividend' from 'core.fills.v1'`.

- [ ] **Step 3: Implementar**

Em `src/core/fills/v1/engine.py`, trocar os imports do topo por:
```python
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterable, Mapping
```

Em `step`, substituir o trecho:
```python
    work_state = replace(work_state, last_bar_ts=bar.ts)

    if not is_long:
```
por:
```python
    work_state = replace(work_state, last_bar_ts=bar.ts)
    if not work_state.is_final and bar.ts == ctx.calendar.last_expected_minute_before(ctx.valid_until_ts):
        work_state, end_events = _validity_end(work_state, work_bar, ctx)
        events = events + end_events

    if not is_long:
```

Acrescentar ao final do arquivo:
```python
def _validity_end(
    state: OrderState, bar: Bar | None, ctx: OrderContext
) -> tuple[OrderState, list[Event]]:
    if state.status is OrderStatus.PENDING:
        event = Event(EventType.EXPIRED, "EXPIRED", payload={"valid_until_ts": ctx.valid_until_ts})
        return replace(state, status=OrderStatus.EXPIRED, final_event_ts=ctx.valid_until_ts), [event]
    assert bar is not None
    price = _adverse_sell(bar.close, ctx.config.stop_slippage_bps)
    return _close(state, bar, ctx, EventType.TIME_EXIT, price, CloseReason.TIME_EXIT,
                  {"raw_price": bar.close})


def apply_validity_end(
    state: OrderState, ctx: OrderContext, last_bar: Bar | None, now: datetime
) -> StepResult:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if now < ctx.valid_until_ts or state.is_final or state.frozen:
        return StepResult(state)
    if state.status is OrderStatus.PENDING:
        new_state, events = _validity_end(state, None, ctx)
        return StepResult(new_state, tuple(events))
    if last_bar is None:
        return flag_review(state, "NO_EXIT_BAR", ctx.valid_until_ts.isoformat())
    if last_bar.ts != state.last_bar_ts:
        raise ValueError("last_bar must be the latest processed bar")

    is_long = ctx.signal.direction is Direction.LONG
    work_state = state if is_long else mirror_state(state)
    work_bar = last_bar if is_long else mirror_bar(last_bar)
    work_state, events = _validity_end(work_state, work_bar, ctx)
    if not is_long:
        work_state = mirror_state(work_state)
        events = [mirror_event(event) for event in events]
    return StepResult(work_state, tuple(events))


def flag_review(state: OrderState, reason: str, ref: str) -> StepResult:
    event = Event(
        EventType.NEEDS_REVIEW, f"NEEDS_REVIEW:{reason}:{ref}",
        payload={"reason": reason, "ref": ref},
    )
    reasons = state.review_reasons if reason in state.review_reasons else state.review_reasons + (reason,)
    return StepResult(replace(state, review_reasons=reasons), (event,))


def cancel(state: OrderState, at: datetime, requested_by: str = "user") -> StepResult:
    if at.tzinfo is None:
        raise ValueError("at must be timezone-aware")
    if state.is_final:
        return StepResult(state)
    event = Event(
        EventType.CANCELED, "CANCELED",
        payload={"at": at, "requested_by": requested_by, "open_qty": state.qty_open},
    )
    return StepResult(replace(state, status=OrderStatus.CANCELED, final_event_ts=at), (event,))


def freeze(state: OrderState, reason: str, ref: str) -> StepResult:
    if state.is_final:
        return StepResult(state)
    frozen_event = Event(EventType.FROZEN, f"FROZEN:{reason}", payload={"reason": reason, "ref": ref})
    review = flag_review(replace(state, frozen=True), reason, ref)
    return StepResult(review.state, (frozen_event,) + review.events)


def apply_dividend(
    state: OrderState, ctx: OrderContext, ex_date: date, amount: Decimal, validated: bool
) -> StepResult:
    if state.is_final or state.frozen or state.qty_open <= ZERO:
        return StepResult(state)
    ref = ex_date.isoformat()
    if not validated:
        return flag_review(state, "DIVIDEND_UNVERIFIED", ref)
    sign = Decimal(1) if ctx.signal.direction is Direction.LONG else Decimal(-1)
    cash = sign * amount * state.qty_open
    event = Event(
        EventType.DIVIDEND, f"DIVIDEND:{ref}", qty=state.qty_open,
        payload={"ex_date": ex_date, "amount_per_share": amount, "cash": cash},
    )
    return StepResult(replace(state, dividends=state.dividends + cash), (event,))
```

`src/core/fills/v1/__init__.py`:
```python
from core.fills.v1.engine import (
    VERSION,
    apply_dividend,
    apply_validity_end,
    cancel,
    flag_review,
    freeze,
    new_order_state,
    run_bars,
    step,
)

__all__ = [
    "VERSION",
    "apply_dividend",
    "apply_validity_end",
    "cancel",
    "flag_review",
    "freeze",
    "new_order_state",
    "run_bars",
    "step",
]
```

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/fills -v`
Expected: `test_entry.py` (17), `test_exits.py` (13) e `test_lifecycle.py` (9) passam.

- [ ] **Step 5: Commit**

```bash
git add src/core/fills tests/fills/test_lifecycle.py
git commit -m "feat(fills): v1 validity end, cancel, freeze, review flags and dividends

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BKUdi9RGWWdXRXVYFWj1yp"
```

### Task 8: Propriedades do `fill_model v1` (Hypothesis)

**Files:**
- Test: `tests/fills/test_properties.py`

**Interfaces:**
- Consumes: `new_order_state`, `run_bars`, `step` (Tasks 5–7); `evaluation_start_ts`, `signal_valid_until_ts` (Task 2); `r_multiple` (Task 4).
- Produces: nenhuma API nova. Garante as propriedades obrigatórias da spec seção 7.

Estes testes verificam código já implementado e devem passar na primeira execução. Se algum falhar, o Hypothesis mostra um contraexemplo mínimo: ele é um bug das Tasks 5–7. Corrija o motor, adicione o contraexemplo como teste unitário no arquivo correspondente (`test_entry.py`, `test_exits.py` ou `test_lifecycle.py`) e rode a suíte inteira antes do commit.

- [ ] **Step 1: Escrever os testes de propriedade**

`tests/fills/test_properties.py`:
```python
from datetime import timedelta
from decimal import Decimal

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from core.domain.calendar import evaluation_start_ts, signal_valid_until_ts
from core.domain.models import (
    MARKET_EVENT_TYPES, Bar, Direction, EventType, FillConfig, OrderContext, OrderStatus, SignalSpec,
)
from core.domain.position import r_multiple
from core.fills.v1 import new_order_state, run_bars, step
from tests.support import et, make_calendar

CAL = make_calendar()
MINUTES = CAL.expected_minutes(et("2025-11-24", "09:30"), et("2025-12-03", "16:00"))
FIRST_THREE_SESSIONS = 390 * 3
MIRROR_K = Decimal(400)

PROPERTY_SETTINGS = settings(
    max_examples=150,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)


def cents(value: int) -> Decimal:
    return Decimal(value) / 100


@st.composite
def long_signals(draw):
    zone_low = draw(st.integers(9800, 10100))
    zone_high = zone_low + draw(st.integers(0, 150))
    stop = zone_low - draw(st.integers(30, 300))
    target1 = zone_high + draw(st.integers(30, 300))
    target2 = draw(st.one_of(st.none(), st.integers(30, 300).map(lambda d: target1 + d)))
    trigger = draw(st.one_of(st.none(), st.integers(9700, 10400)))
    return SignalSpec(
        ticker="AAPL",
        direction=Direction.LONG,
        entry_zone_low=cents(zone_low),
        entry_zone_high=cents(zone_high),
        stop=cents(stop),
        target1=cents(target1),
        target2=None if target2 is None else cents(target2),
        trigger_price=None if trigger is None else cents(trigger),
        valid_sessions=draw(st.integers(1, 3)),
    )


@st.composite
def bar_paths(draw):
    start = draw(st.integers(0, len(MINUTES) - 1))
    price = draw(st.integers(9700, 10400))
    steps = draw(
        st.lists(
            st.tuples(
                st.integers(-80, 80), st.integers(-120, 120),
                st.integers(0, 80), st.integers(0, 80), st.booleans(),
            ),
            min_size=1,
            max_size=150,
        )
    )
    bars = []
    for offset, (gap, move, up, down, keep) in enumerate(steps):
        index = start + offset
        if index >= len(MINUTES):
            break
        open_ = price + gap
        close = open_ + move
        high = max(open_, close) + up
        low = min(open_, close) - down
        price = close
        if keep:
            bars.append(Bar(MINUTES[index], cents(open_), cents(high), cents(low), cents(close), Decimal(100)))
    return bars


@st.composite
def scenarios(draw, zero_costs: bool = False):
    signal = draw(long_signals())
    created = MINUTES[draw(st.integers(0, FIRST_THREE_SESSIONS - 1))] + timedelta(
        seconds=draw(st.integers(0, 59))
    )
    begin = evaluation_start_ts(CAL, created)
    if zero_costs:
        config = FillConfig(stop_slippage_bps=Decimal(0))
    else:
        config = FillConfig(
            entry_slippage_bps=Decimal(draw(st.integers(0, 5))),
            stop_slippage_bps=Decimal(draw(st.integers(0, 10))),
            commission_per_execution=cents(draw(st.integers(0, 100))),
        )
    ctx = OrderContext(signal, config, CAL, begin, signal_valid_until_ts(CAL, begin, signal.valid_sessions))
    return ctx, draw(bar_paths())


def run(ctx, bars):
    return run_bars(new_order_state(ctx).state, bars, ctx)


@PROPERTY_SETTINGS
@given(scenarios())
def test_deterministic_events_and_hashes(scenario):
    ctx, bars = scenario
    first, second = run(ctx, bars), run(ctx, bars)
    assert first.events == second.events
    assert [e.payload_hash for e in first.events] == [e.payload_hash for e in second.events]
    assert first.state == second.state


@PROPERTY_SETTINGS
@given(scenarios(), st.data())
def test_prefix_no_look_ahead(scenario, data):
    ctx, bars = scenario
    cut = data.draw(st.integers(0, len(bars)))
    full = run(ctx, bars).events
    prefix = run(ctx, bars[:cut]).events
    assert full[: len(prefix)] == prefix


@PROPERTY_SETTINGS
@given(scenarios())
def test_market_events_inside_evaluation_window(scenario):
    ctx, bars = scenario
    for event in run(ctx, bars).events:
        if event.type in MARKET_EVENT_TYPES:
            assert ctx.evaluation_start_ts <= event.bar_ts < ctx.valid_until_ts


@PROPERTY_SETTINGS
@given(scenarios())
def test_accounting_invariants(scenario):
    ctx, bars = scenario
    state = new_order_state(ctx).state
    events = []
    for current in bars:
        result = step(state, current, ctx)
        state = result.state
        events.extend(result.events)
        assert state.qty_open >= 0
        assert state.qty_open <= state.qty_total
    assert sum((e.payload.get("pnl", Decimal(0)) for e in events), Decimal(0)) == state.realized_pnl
    assert sum((e.payload.get("cost", Decimal(0)) for e in events), Decimal(0)) == state.costs
    if state.status is OrderStatus.CLOSED:
        assert state.qty_open == 0


@PROPERTY_SETTINGS
@given(scenarios())
def test_reprocessing_is_idempotent(scenario):
    ctx, bars = scenario
    first = run(ctx, bars)
    again = run_bars(first.state, bars, ctx)
    assert again.events == ()
    assert again.state == first.state


@PROPERTY_SETTINGS
@given(scenarios())
def test_event_keys_unique_and_no_fill_on_reclaim_candle(scenario):
    ctx, bars = scenario
    events = run(ctx, bars).events
    keys = [e.event_key for e in events]
    assert len(keys) == len(set(keys))
    reclaimed = {e.bar_ts for e in events if e.type is EventType.ZONE_RECLAIMED}
    filled = {e.bar_ts for e in events if e.type is EventType.FILLED}
    assert not reclaimed & filled


def _mirror_price(value):
    return None if value is None else MIRROR_K - value


@PROPERTY_SETTINGS
@given(scenarios(zero_costs=True))
def test_short_mirror_matches_long(scenario):
    ctx, bars = scenario
    s = ctx.signal
    short_signal = SignalSpec(
        ticker=s.ticker,
        direction=Direction.SHORT,
        entry_zone_low=MIRROR_K - s.entry_zone_high,
        entry_zone_high=MIRROR_K - s.entry_zone_low,
        stop=MIRROR_K - s.stop,
        target1=MIRROR_K - s.target1,
        target2=_mirror_price(s.target2),
        trigger_price=_mirror_price(s.trigger_price),
        valid_sessions=s.valid_sessions,
    )
    short_ctx = OrderContext(short_signal, ctx.config, CAL, ctx.evaluation_start_ts, ctx.valid_until_ts)
    short_bars = [
        Bar(b.ts, MIRROR_K - b.open, MIRROR_K - b.low, MIRROR_K - b.high, MIRROR_K - b.close, b.volume)
        for b in bars
    ]
    long_result = run(ctx, bars)
    short_result = run(short_ctx, short_bars)
    assert [e.type for e in long_result.events] == [e.type for e in short_result.events]
    assert [e.bar_ts for e in long_result.events] == [e.bar_ts for e in short_result.events]
    for long_event, short_event in zip(long_result.events, short_result.events):
        assert short_event.price == _mirror_price(long_event.price)
        assert short_event.qty == long_event.qty
    risk = ctx.config.risk_amount
    assert r_multiple(long_result.state, risk) == r_multiple(short_result.state, risk)
```

- [ ] **Step 2: Rodar**

Run: `uv run pytest tests/fills/test_properties.py -v`
Expected: 7 passed. Se falhar, siga a instrução do início desta task (corrigir, fixar o contraexemplo como teste unitário, rodar tudo).

- [ ] **Step 3: Rodar a suíte inteira**

Run: `uv run pytest`
Expected: todos passam.

- [ ] **Step 4: Commit**

```bash
git add tests/fills/test_properties.py
git commit -m "test(fills): property tests for determinism, look-ahead, accounting and mirroring

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BKUdi9RGWWdXRXVYFWj1yp"
```

### Task 9: Actionability do sinal e construção de ordens manuais

**Files:**
- Create: `src/core/actionability.py`
- Test: `tests/test_actionability.py`

**Interfaces:**
- Consumes: `new_order_state`, `step` via módulo de fill model (`get_fill_model("v1")`, Task 5); `evaluation_start_ts`, `ONE_MINUTE` (Task 2); modelos (Task 4).
- Produces (em `core.actionability`):
  - `class ActionabilityReason(StrEnum)`: `ACTIONABLE`, `SIGNAL_EXPIRED`, `INVALIDATED`, `ENTRY_OPPORTUNITY_ALREADY_OCCURRED`, `STOPPED`, `TARGET_REACHED`
  - `@dataclass(frozen=True) Actionability(reason, hypothetical_status: OrderStatus, gating_state: GatingState | None)` com `actionable: bool` e `error_code: str | None` (`None`, `"SIGNAL_EXPIRED"` ou `"SIGNAL_NO_LONGER_ACTIONABLE"`)
  - `signal_actionability(fill_model: ModuleType, signal_ctx: OrderContext, bars: Iterable[Bar], as_of: datetime) -> Actionability`
  - `class ManualOrderRejected(Exception)` com atributo `actionability`
  - `@dataclass(frozen=True) ManualOrder(context: OrderContext, created: StepResult, actionability: Actionability)`
  - `build_manual_order(fill_model: ModuleType, signal_ctx: OrderContext, bars: Iterable[Bar], created_at: datetime, extra_payload: Mapping[str, Any] | None = None) -> ManualOrder` — levanta `ManualOrderRejected`
- `signal_ctx` é o contexto da ordem hipotética do sinal (`evaluation_start_ts` do sinal, `valid_until_ts` do sinal). O Plano 3 passa `extra_payload={"actionability_run_id": ..., "actionability_data_as_of": ...}`.

- [ ] **Step 1: Escrever os testes que falham**

`tests/test_actionability.py`:
```python
import pytest

from core.actionability import (
    ActionabilityReason, ManualOrderRejected, build_manual_order, signal_actionability,
)
from core.domain.hashing import sha256_hex
from core.domain.models import EventType, OrderStatus
from core.fills import get_fill_model
from core.fills.v1 import run_bars
from tests.support import D, bar, et, flat_bars, long_signal, make_ctx

V1 = get_fill_model("v1")
DAY = "2025-11-25"


def signal_ctx(**overrides):
    return make_ctx(long_signal(**overrides), start=et(DAY, "09:40"))


def flat(ctx, start, end, price):
    return flat_bars(ctx.calendar, et(DAY, start), et(DAY, end), price)


def entry_at_1005(ctx):
    return flat(ctx, "09:40", "10:05", 105) + [bar(et(DAY, "10:05"), 101, 101.5, 100.5, 101.2)]


def test_stop_touched_before_click_is_not_actionable():
    ctx = signal_ctx()
    bars = flat(ctx, "09:40", "10:20", 105) + [bar(et(DAY, "10:20"), 98, 98.5, 96, 98)]
    decision = signal_actionability(V1, ctx, bars, et(DAY, "13:00"))
    assert decision.reason is ActionabilityReason.INVALIDATED
    assert not decision.actionable
    assert decision.error_code == "SIGNAL_NO_LONGER_ACTIONABLE"
    with pytest.raises(ManualOrderRejected) as rejected:
        build_manual_order(V1, ctx, bars, et(DAY, "13:00"))
    assert rejected.value.actionability.reason is ActionabilityReason.INVALIDATED


def test_prior_hypothetical_entry_blocks_manual_order():
    ctx = signal_ctx()
    bars = (
        entry_at_1005(ctx)
        + flat(ctx, "10:06", "11:00", 103)
        + [bar(et(DAY, "11:00"), 105, 106, 104.8, 105.5)]
        + flat(ctx, "11:01", "13:00", 104)
    )
    decision = signal_actionability(V1, ctx, bars, et(DAY, "13:00"))
    assert decision.reason is ActionabilityReason.ENTRY_OPPORTUNITY_ALREADY_OCCURRED
    assert decision.hypothetical_status is OrderStatus.PARTIAL


def test_stopped_and_target_reached_reasons():
    ctx = signal_ctx()
    stopped = entry_at_1005(ctx) + [bar(et(DAY, "10:30"), 100, 100.2, 96.5, 97)]
    assert signal_actionability(V1, ctx, stopped, et(DAY, "13:00")).reason is ActionabilityReason.STOPPED
    reached = (
        entry_at_1005(ctx)
        + [bar(et(DAY, "11:00"), 105, 106, 104.8, 105.5)]
        + [bar(et(DAY, "11:30"), 109, 110.5, 108.8, 110)]
    )
    decision = signal_actionability(V1, ctx, reached, et(DAY, "13:00"))
    assert decision.reason is ActionabilityReason.TARGET_REACHED


def test_expired_signal():
    ctx = signal_ctx()
    decision = signal_actionability(V1, ctx, [], et("2025-11-28", "13:00"))
    assert decision.reason is ActionabilityReason.SIGNAL_EXPIRED
    assert decision.error_code == "SIGNAL_EXPIRED"
    with pytest.raises(ManualOrderRejected) as rejected:
        build_manual_order(V1, ctx, [], et("2025-11-28", "12:59", 30))
    assert rejected.value.actionability.reason is ActionabilityReason.SIGNAL_EXPIRED


def test_manual_order_inherits_zone_lost_and_waits_for_reclaim():
    ctx = signal_ctx()
    history = [bar(et(DAY, "10:00"), 99.5, 99.8, 99, 99.6)] + flat(ctx, "10:01", "10:30", 99.5)
    decision = signal_actionability(V1, ctx, history, et(DAY, "10:30"))
    assert decision.actionable and decision.gating_state.zone_lost

    manual = build_manual_order(V1, ctx, history, et(DAY, "10:30"),
                                extra_payload={"actionability_run_id": "run-1"})
    assert manual.context.evaluation_start_ts == et(DAY, "10:30")
    assert manual.context.valid_until_ts == ctx.valid_until_ts
    (created,) = manual.created.events
    inherited = created.payload["inherited_signal_state"]
    assert inherited["zone_lost"] is True and inherited["status"] is OrderStatus.PENDING
    assert created.payload["inherited_signal_state_hash"] == sha256_hex(inherited)
    assert created.payload["actionability_run_id"] == "run-1"

    later = [
        bar(et(DAY, "10:30"), 99.6, 101, 99.5, 100.5),
        bar(et(DAY, "10:31"), 100.5, 101, 100.2, 100.8),
    ]
    result = run_bars(manual.created.state, history + later, manual.context)
    assert [e.type for e in result.events] == [EventType.ZONE_RECLAIMED, EventType.FILLED]
    assert result.events[1].bar_ts == et(DAY, "10:31")


def test_trigger_only_counts_from_complete_candles_before_click():
    ctx = signal_ctx(trigger_price=D("101.5"))
    history = [bar(et(DAY, "09:45"), 101, 101.6, 100.8, 101.2)]
    too_early = signal_actionability(V1, ctx, history, et(DAY, "09:45", 30))
    assert too_early.actionable and too_early.gating_state.trigger_hit_at is None
    complete = signal_actionability(V1, ctx, history, et(DAY, "09:46"))
    assert complete.gating_state.trigger_hit_at == et(DAY, "09:45")


def test_manual_order_from_morning_signal_has_no_events_before_click():
    ctx = make_ctx(long_signal(), start=et(DAY, "08:00"))
    history = flat(ctx, "09:30", "14:00", 105)
    manual = build_manual_order(V1, ctx, history, et(DAY, "14:00"))
    assert manual.context.evaluation_start_ts == et(DAY, "14:00")
    assert manual.context.valid_until_ts == ctx.valid_until_ts
    entry = bar(et(DAY, "14:00"), 101, 101.5, 100.5, 101.2)
    result = run_bars(manual.created.state, history + [entry], manual.context)
    assert [e.bar_ts for e in result.events] == [et(DAY, "14:00")]


def test_actionability_is_pure():
    ctx = signal_ctx()
    bars = entry_at_1005(ctx)
    first = signal_actionability(V1, ctx, bars, et(DAY, "12:00"))
    second = signal_actionability(V1, ctx, list(reversed(bars)), et(DAY, "12:00"))
    assert first == second
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_actionability.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'core.actionability'`.

- [ ] **Step 3: Implementar**

`src/core/actionability.py`:
```python
"""Signal actionability for MANUAL_USER orders (spec 3.4.1). Pure: bars are injected."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import ModuleType
from typing import Any, Iterable, Mapping

from core.domain.calendar import ONE_MINUTE, evaluation_start_ts
from core.domain.models import Bar, CloseReason, GatingState, OrderContext, OrderStatus, StepResult


class ActionabilityReason(StrEnum):
    ACTIONABLE = "ACTIONABLE"
    SIGNAL_EXPIRED = "SIGNAL_EXPIRED"
    INVALIDATED = "INVALIDATED"
    ENTRY_OPPORTUNITY_ALREADY_OCCURRED = "ENTRY_OPPORTUNITY_ALREADY_OCCURRED"
    STOPPED = "STOPPED"
    TARGET_REACHED = "TARGET_REACHED"


@dataclass(frozen=True)
class Actionability:
    reason: ActionabilityReason
    hypothetical_status: OrderStatus
    gating_state: GatingState | None

    @property
    def actionable(self) -> bool:
        return self.reason is ActionabilityReason.ACTIONABLE

    @property
    def error_code(self) -> str | None:
        if self.actionable:
            return None
        if self.reason is ActionabilityReason.SIGNAL_EXPIRED:
            return "SIGNAL_EXPIRED"
        return "SIGNAL_NO_LONGER_ACTIONABLE"


_EXPIRED = Actionability(ActionabilityReason.SIGNAL_EXPIRED, OrderStatus.EXPIRED, None)

_CLOSE_REASONS = {
    CloseReason.STOPPED: ActionabilityReason.STOPPED,
    CloseReason.TARGET_FINAL: ActionabilityReason.TARGET_REACHED,
    CloseReason.TIME_EXIT: ActionabilityReason.SIGNAL_EXPIRED,
}


def signal_actionability(
    fill_model: ModuleType, signal_ctx: OrderContext, bars: Iterable[Bar], as_of: datetime
) -> Actionability:
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    if as_of >= signal_ctx.valid_until_ts:
        return _EXPIRED

    state = fill_model.new_order_state(signal_ctx).state
    for current in sorted(bars, key=lambda b: b.ts):
        if current.ts + ONE_MINUTE > as_of:
            break
        state = fill_model.step(state, current, signal_ctx).state

    status = state.status
    if status is OrderStatus.PENDING:
        return Actionability(ActionabilityReason.ACTIONABLE, status, state.gating_state())
    if status is OrderStatus.EXPIRED:
        return _EXPIRED
    if status is OrderStatus.INVALIDATED:
        return Actionability(ActionabilityReason.INVALIDATED, status, None)
    if status in (OrderStatus.OPEN, OrderStatus.PARTIAL):
        return Actionability(ActionabilityReason.ENTRY_OPPORTUNITY_ALREADY_OCCURRED, status, None)
    if status is OrderStatus.CLOSED and state.close_reason is not None:
        return Actionability(_CLOSE_REASONS[state.close_reason], status, None)
    raise AssertionError(f"unexpected hypothetical status: {status}")


class ManualOrderRejected(Exception):
    def __init__(self, actionability: Actionability) -> None:
        super().__init__(actionability.error_code)
        self.actionability = actionability


@dataclass(frozen=True)
class ManualOrder:
    context: OrderContext
    created: StepResult
    actionability: Actionability


def build_manual_order(
    fill_model: ModuleType,
    signal_ctx: OrderContext,
    bars: Iterable[Bar],
    created_at: datetime,
    extra_payload: Mapping[str, Any] | None = None,
) -> ManualOrder:
    decision = signal_actionability(fill_model, signal_ctx, bars, created_at)
    if not decision.actionable:
        raise ManualOrderRejected(decision)
    start = evaluation_start_ts(signal_ctx.calendar, created_at)
    if start >= signal_ctx.valid_until_ts:
        raise ManualOrderRejected(_EXPIRED)
    context = OrderContext(
        signal=signal_ctx.signal,
        config=signal_ctx.config,
        calendar=signal_ctx.calendar,
        evaluation_start_ts=start,
        valid_until_ts=signal_ctx.valid_until_ts,
    )
    created = fill_model.new_order_state(
        context, inherited=decision.gating_state, extra_payload=extra_payload
    )
    return ManualOrder(context, created, decision)
```

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/test_actionability.py -v`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add src/core/actionability.py tests/test_actionability.py
git commit -m "feat(core): signal actionability and manual order construction

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BKUdi9RGWWdXRXVYFWj1yp"
```

### Task 10: Funções puras de qualidade de dados

**Files:**
- Create: `src/core/dataquality.py`
- Test: `tests/test_dataquality.py`

**Interfaces:**
- Consumes: `SessionCalendar`, `ONE_MINUTE` (Task 2); modelos (Task 4). Não importa `core.fills` — devolve `ReviewFlag`s que o evaluator (Plano 2) aplica com `fill_model.flag_review`.
- Produces (em `core.dataquality`):
  - `@dataclass(frozen=True) ReviewFlag(reason: str, ref: str)`
  - `@dataclass(frozen=True) SessionQuality(day: date, expected: int, missing: tuple[datetime, ...])` com `coverage_pct: Decimal` (2 casas) e `event() -> Event` (`DATA_QUALITY:{day}`)
  - `quality_window(state: OrderState, ctx: OrderContext, now: datetime) -> tuple[datetime, datetime]` — intervalo `[start, end)` de minutos esperados
  - `session_quality(calendar, start, end, present: Iterable[datetime]) -> list[SessionQuality]`
  - `find_gaps(calendar, missing: Sequence[datetime], min_minutes: int) -> list[tuple[datetime, int]]`
  - `gap_events(gaps) -> list[Event]` (`DATA_GAP:{gap_start_ts}`)
  - `active_levels(signal: SignalSpec, state: OrderState, minute: datetime) -> list[Decimal]`
  - `missing_bar_reviews(missing, reference: Mapping[datetime, Bar], signal, state) -> list[ReviewFlag]`
  - `daily_range_mismatch(bars: Iterable[Bar], reference_high: Decimal, reference_low: Decimal, tolerance_pct: Decimal) -> bool`
  - `dividends_agree(first: Decimal | None, second: Decimal | None, tolerance: Decimal) -> bool`

Janela `[start, end)`: `start = evaluation_start_ts`. Se a ordem é final e `final_event_ts` é um minuto esperado (evento de candle), `end = final_event_ts + 1 min` (o candle final conta); se é final por instante não-minuto (ex.: `EXPIRED` em `valid_until_ts`), `end = final_event_ts`; se não é final, `end = min(now truncado ao minuto, valid_until_ts)` — só candles completos.

- [ ] **Step 1: Escrever os testes que falham**

`tests/test_dataquality.py`:
```python
from dataclasses import replace
from datetime import date

from core.dataquality import (
    ReviewFlag, active_levels, daily_range_mismatch, dividends_agree, find_gaps, gap_events,
    missing_bar_reviews, quality_window, session_quality,
)
from core.domain.models import EventType, OrderState, OrderStatus
from tests.support import D, bar, et, make_ctx

CTX = make_ctx(start=et("2025-11-25", "09:30"))
CAL = CTX.calendar


def test_quality_window_rules():
    open_state = OrderState(status=OrderStatus.OPEN)
    assert quality_window(open_state, CTX, et("2025-11-25", "10:05", 30)) == (
        et("2025-11-25", "09:30"), et("2025-11-25", "10:05"),
    )
    stopped = OrderState(status=OrderStatus.CLOSED, final_event_ts=et("2025-11-25", "11:00"))
    assert quality_window(stopped, CTX, et("2025-11-26", "12:00"))[1] == et("2025-11-25", "11:01")
    expired = OrderState(status=OrderStatus.EXPIRED, final_event_ts=CTX.valid_until_ts)
    assert quality_window(expired, CTX, et("2025-12-02", "12:00"))[1] == CTX.valid_until_ts


def test_session_quality_per_session_and_event():
    present = {et("2025-11-26", "15:58"), et("2025-11-28", "09:31")}
    report = session_quality(CAL, et("2025-11-26", "15:58"), et("2025-11-28", "09:32"), present)
    assert [(q.day, q.expected, q.missing) for q in report] == [
        (date(2025, 11, 26), 2, (et("2025-11-26", "15:59"),)),
        (date(2025, 11, 28), 2, (et("2025-11-28", "09:30"),)),
    ]
    event = report[0].event()
    assert event.type is EventType.DATA_QUALITY and event.event_key == "DATA_QUALITY:2025-11-26"
    assert event.payload["coverage_pct"] == D("50.00")
    assert event.payload["missing_bars"] == 1 and event.payload["expected_bars"] == 2


def test_find_gaps_follows_expected_minutes_across_sessions():
    same_day = CAL.expected_minutes(et("2025-11-25", "10:00"), et("2025-11-25", "10:30"))
    lone = [et("2025-11-25", "11:00")]
    assert find_gaps(CAL, same_day + lone, 30) == [(et("2025-11-25", "10:00"), 30)]
    across = CAL.expected_minutes(et("2025-11-26", "15:45"), et("2025-11-28", "09:45"))
    assert find_gaps(CAL, across, 30) == [(et("2025-11-26", "15:45"), 30)]
    assert find_gaps(CAL, across, 31) == []
    (gap,) = gap_events([(et("2025-11-26", "15:45"), 30)])
    assert gap.event_key == "DATA_GAP:2025-11-26T20:45:00+00:00" and gap.payload["minutes"] == 30


def test_missing_bar_reviews_touch_absent_and_clear():
    state = OrderState(
        status=OrderStatus.PARTIAL, stop_current=D(101), stop_previous=D(97),
        stop_active_from=et("2025-11-25", "10:02"),
    )
    touch_old_stop = et("2025-11-25", "10:01")
    clear = et("2025-11-25", "10:03")
    absent = et("2025-11-25", "10:04")
    reference = {
        touch_old_stop: bar(touch_old_stop, 98, 98.5, 96.8, 98),
        clear: bar(clear, 103, 104, 102.5, 103.5),
    }
    flags = missing_bar_reviews([absent, clear, touch_old_stop], reference, CTX.signal, state)
    assert flags == [
        ReviewFlag("MISSING_BAR_LEVEL_TOUCH", touch_old_stop.isoformat()),
        ReviewFlag("MISSING_BAR_UNVERIFIABLE", absent.isoformat()),
    ]
    assert D(97) in active_levels(CTX.signal, state, touch_old_stop)
    assert D(101) in active_levels(CTX.signal, state, clear)
    pending = replace(state, status=OrderStatus.PENDING, stop_current=None, stop_previous=None,
                      stop_active_from=None)
    assert D(97) in active_levels(CTX.signal, pending, clear)


def test_daily_range_mismatch_and_dividend_agreement():
    bars = [bar(et("2025-11-25", "09:30"), 100, 101, 99.5, 100.5), bar(et("2025-11-25", "09:31"), 100.5, 102, 100, 101)]
    assert not daily_range_mismatch(bars, D("102.3"), D("99.4"), D("0.5"))
    assert daily_range_mismatch(bars, D("103"), D("99.5"), D("0.5"))
    assert not daily_range_mismatch([], D(1), D(1), D("0.5"))
    assert dividends_agree(D("0.24"), D("0.2405"), D("0.001"))
    assert not dividends_agree(D("0.24"), D("0.25"), D("0.001"))
    assert not dividends_agree(D("0.24"), None, D("0.001"))
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_dataquality.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'core.dataquality'`.

- [ ] **Step 3: Implementar**

`src/core/dataquality.py`:
```python
"""Pure data-quality checks (spec 4.6). Reference data (yfinance) is injected by the caller."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Iterable, Mapping, Sequence

from core.domain.calendar import ONE_MINUTE, SessionCalendar
from core.domain.models import Bar, Event, EventType, OrderContext, OrderState, SignalSpec

HUNDRED = Decimal(100)
CENT = Decimal("0.01")


@dataclass(frozen=True)
class ReviewFlag:
    reason: str
    ref: str


@dataclass(frozen=True)
class SessionQuality:
    day: date
    expected: int
    missing: tuple[datetime, ...]

    @property
    def coverage_pct(self) -> Decimal:
        present = self.expected - len(self.missing)
        return (Decimal(present) * HUNDRED / Decimal(self.expected)).quantize(CENT)

    def event(self) -> Event:
        return Event(
            EventType.DATA_QUALITY,
            f"DATA_QUALITY:{self.day.isoformat()}",
            payload={
                "session_date": self.day,
                "expected_bars": self.expected,
                "missing_bars": len(self.missing),
                "coverage_pct": self.coverage_pct,
                "missing_minutes": list(self.missing),
            },
        )


def quality_window(state: OrderState, ctx: OrderContext, now: datetime) -> tuple[datetime, datetime]:
    start = ctx.evaluation_start_ts
    if state.final_event_ts is not None:
        final = state.final_event_ts
        end = final + ONE_MINUTE if ctx.calendar.is_expected_minute(final) else final
    else:
        end = min(now.replace(second=0, microsecond=0), ctx.valid_until_ts)
    return start, max(start, end)


def session_quality(
    calendar: SessionCalendar, start: datetime, end: datetime, present: Iterable[datetime]
) -> list[SessionQuality]:
    present_set = set(present)
    expected: dict[date, int] = {}
    missing: dict[date, list[datetime]] = {}
    for minute in calendar.expected_minutes(start, end):
        session = calendar.session_containing(minute)
        assert session is not None
        expected[session.day] = expected.get(session.day, 0) + 1
        bucket = missing.setdefault(session.day, [])
        if minute not in present_set:
            bucket.append(minute)
    return [SessionQuality(day, count, tuple(missing[day])) for day, count in expected.items()]


def find_gaps(
    calendar: SessionCalendar, missing: Sequence[datetime], min_minutes: int
) -> list[tuple[datetime, int]]:
    gaps: list[tuple[datetime, int]] = []
    run_start: datetime | None = None
    run_length = 0
    previous: datetime | None = None
    for minute in sorted(missing):
        if previous is not None and calendar.next_expected_minute(previous) == minute:
            run_length += 1
        else:
            if run_start is not None and run_length >= min_minutes:
                gaps.append((run_start, run_length))
            run_start, run_length = minute, 1
        previous = minute
    if run_start is not None and run_length >= min_minutes:
        gaps.append((run_start, run_length))
    return gaps


def gap_events(gaps: Iterable[tuple[datetime, int]]) -> list[Event]:
    return [
        Event(
            EventType.DATA_GAP,
            f"DATA_GAP:{start.isoformat()}",
            payload={"gap_start_ts": start, "minutes": minutes},
        )
        for start, minutes in gaps
    ]


def active_levels(signal: SignalSpec, state: OrderState, minute: datetime) -> list[Decimal]:
    stop = state.stop_current if state.stop_current is not None else signal.stop
    if state.stop_active_from is not None and minute < state.stop_active_from and state.stop_previous is not None:
        stop = state.stop_previous
    levels = [
        signal.entry_zone_low,
        signal.entry_zone_high,
        stop,
        signal.target1,
        signal.target2,
        signal.trigger_price,
    ]
    return [level for level in levels if level is not None]


def missing_bar_reviews(
    missing: Iterable[datetime],
    reference: Mapping[datetime, Bar],
    signal: SignalSpec,
    state: OrderState,
) -> list[ReviewFlag]:
    flags: list[ReviewFlag] = []
    for minute in sorted(missing):
        reference_bar = reference.get(minute)
        if reference_bar is None:
            flags.append(ReviewFlag("MISSING_BAR_UNVERIFIABLE", minute.isoformat()))
        elif any(reference_bar.low <= level <= reference_bar.high
                 for level in active_levels(signal, state, minute)):
            flags.append(ReviewFlag("MISSING_BAR_LEVEL_TOUCH", minute.isoformat()))
    return flags


def daily_range_mismatch(
    bars: Iterable[Bar], reference_high: Decimal, reference_low: Decimal, tolerance_pct: Decimal
) -> bool:
    collected = list(bars)
    if not collected:
        return False
    high = max(b.high for b in collected)
    low = min(b.low for b in collected)
    tolerance = tolerance_pct / HUNDRED
    return (
        abs(high - reference_high) > reference_high * tolerance
        or abs(low - reference_low) > reference_low * tolerance
    )


def dividends_agree(first: Decimal | None, second: Decimal | None, tolerance: Decimal) -> bool:
    return first is not None and second is not None and abs(first - second) <= tolerance
```

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/test_dataquality.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/core/dataquality.py tests/test_dataquality.py
git commit -m "feat(core): pure data-quality checks

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BKUdi9RGWWdXRXVYFWj1yp"
```

### Task 11: Reamostragem — drawdown, bootstrap e permutação

**Files:**
- Create: `src/core/metrics/__init__.py`, `src/core/metrics/resampling.py`, `tests/metrics/__init__.py`
- Test: `tests/metrics/test_resampling.py`

**Interfaces:**
- Consumes: NumPy.
- Produces (em `core.metrics.resampling`):
  - `max_drawdown(values: Sequence[float]) -> float` — maior queda pico→vale da curva acumulada iniciando em 0 (valor positivo em R)
  - `win_rate_stat(samples: np.ndarray) -> np.ndarray`, `mean_stat(samples: np.ndarray) -> np.ndarray` (estatísticas vetorizadas por linha)
  - `bootstrap_ci(values: Sequence[float], statistic, resamples: int, seed: int, level: float = 0.95) -> tuple[float, float] | None` (`None` se `len < 2`)
  - `drawdown_permutation_percentiles(values: Sequence[float], resamples: int, seed: int) -> dict[str, float] | None` — chaves `p5`, `p50`, `p95` (`None` se `len < 2`). Rótulo de negócio: **risco de sequência**, não intervalo de confiança do drawdown histórico.

- [ ] **Step 1: Escrever os testes que falham**

`tests/metrics/test_resampling.py`:
```python
import pytest

from core.metrics.resampling import (
    bootstrap_ci, drawdown_permutation_percentiles, max_drawdown, mean_stat, win_rate_stat,
)

SAMPLE = [1.0, -1.0, 2.0, -0.5]


def test_max_drawdown():
    assert max_drawdown(SAMPLE) == pytest.approx(1.0)
    assert max_drawdown([]) == 0.0
    assert max_drawdown([-1.0, -2.0]) == pytest.approx(3.0)
    assert max_drawdown([1.0, 2.0]) == 0.0


def test_bootstrap_ci_is_reproducible_and_bounded():
    first = bootstrap_ci(SAMPLE, mean_stat, 2000, 42)
    assert first == bootstrap_ci(SAMPLE, mean_stat, 2000, 42)
    low, high = first
    assert -1.0 <= low <= 0.375 <= high <= 2.0
    assert bootstrap_ci([0.5] * 10, mean_stat, 500, 1) == (pytest.approx(0.5), pytest.approx(0.5))
    assert bootstrap_ci([1.0, 2.0, 3.0], win_rate_stat, 500, 1) == (1.0, 1.0)
    assert bootstrap_ci([1.0], mean_stat, 500, 1) is None


def test_drawdown_permutation_percentiles():
    result = drawdown_permutation_percentiles(SAMPLE, 2000, 42)
    assert result == drawdown_permutation_percentiles(SAMPLE, 2000, 42)
    assert set(result) == {"p5", "p50", "p95"}
    assert 0.0 <= result["p5"] <= result["p50"] <= result["p95"] <= 1.5
    assert drawdown_permutation_percentiles([1.0, 2.0, 3.0], 200, 7) == {"p5": 0.0, "p50": 0.0, "p95": 0.0}
    assert drawdown_permutation_percentiles([1.0], 200, 7) is None
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `mkdir -p src/core/metrics tests/metrics && touch src/core/metrics/__init__.py tests/metrics/__init__.py && uv run pytest tests/metrics/test_resampling.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'core.metrics.resampling'`.

- [ ] **Step 3: Implementar**

`src/core/metrics/resampling.py`:
```python
"""Resampling statistics for strategy metrics (spec 5.4). Seeds make results reproducible."""

from __future__ import annotations

from typing import Callable, Sequence

import numpy as np

Statistic = Callable[[np.ndarray], np.ndarray]


def max_drawdown(values: Sequence[float]) -> float:
    equity = 0.0
    peak = 0.0
    worst = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        worst = max(worst, peak - equity)
    return worst


def win_rate_stat(samples: np.ndarray) -> np.ndarray:
    return (samples > 0).mean(axis=1)


def mean_stat(samples: np.ndarray) -> np.ndarray:
    return samples.mean(axis=1)


def bootstrap_ci(
    values: Sequence[float], statistic: Statistic, resamples: int, seed: int, level: float = 0.95
) -> tuple[float, float] | None:
    if len(values) < 2:
        return None
    data = np.asarray(values, dtype=float)
    rng = np.random.default_rng(seed)
    indexes = rng.integers(0, len(data), size=(resamples, len(data)))
    stats = statistic(data[indexes])
    tail = (1.0 - level) / 2.0 * 100.0
    low, high = np.percentile(stats, [tail, 100.0 - tail])
    return float(low), float(high)


def drawdown_permutation_percentiles(
    values: Sequence[float], resamples: int, seed: int
) -> dict[str, float] | None:
    if len(values) < 2:
        return None
    data = np.asarray(values, dtype=float)
    rng = np.random.default_rng(seed)
    permutations = rng.permuted(np.tile(data, (resamples, 1)), axis=1)
    equity = np.concatenate([np.zeros((resamples, 1)), np.cumsum(permutations, axis=1)], axis=1)
    drawdowns = (np.maximum.accumulate(equity, axis=1) - equity).max(axis=1)
    p5, p50, p95 = np.percentile(drawdowns, [5, 50, 95])
    return {"p5": float(p5), "p50": float(p50), "p95": float(p95)}
```

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/metrics/test_resampling.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/core/metrics tests/metrics
git commit -m "feat(metrics): drawdown, bootstrap confidence intervals and sequence-risk permutations

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BKUdi9RGWWdXRXVYFWj1yp"
```

---

### Task 12: Resumo de métricas, política de revisão e taxa de execução

**Files:**
- Create: `src/core/metrics/summary.py`
- Test: `tests/metrics/test_summary.py`

**Interfaces:**
- Consumes: `resampling` (Task 11); `Direction`, `OrderState`, `OrderStatus` (Task 4).
- Produces (em `core.metrics.summary`):
  - `MIN_SAMPLE = 30`, `INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"`, `SHORT_BORROW_NOT_SIMULATED = "SHORT_BORROW_NOT_SIMULATED"`
  - `@dataclass(frozen=True) TradeResult(order_id: str, direction: Direction, opened_at: datetime, closed_at: datetime, r_multiple: Decimal, mfe_r: Decimal | None = None, mae_r: Decimal | None = None, review_reasons: tuple[str, ...] = ())` com `needs_review`
  - `@dataclass(frozen=True) ExecutionCounts(filled: int, resolved: int)` com `rate: float | None`
  - `execution_counts(orders: Iterable[OrderState]) -> ExecutionCounts`
  - `@dataclass(frozen=True) ReviewTally(count: int, reasons: dict[str, int])`
  - `@dataclass(frozen=True) MetricsSummary(...)` (campos abaixo)
  - `summarize(trades: Iterable[TradeResult], counts: ExecutionCounts, include_needs_review: bool = False, resamples: int = 2000, seed: int = 42) -> MetricsSummary`
- O Plano 3 converte `MetricsSummary` para JSON na rota `GET /metrics`.

Definições: trades ordenados por `(closed_at, order_id)`; win = `r_multiple > 0`; `expectancy_r` = média de R (igual a `avg_r` por definição: `p·média_ganhos + (1−p)·média_perdas`); `profit_factor = Σ ganhos / |Σ perdas|` (`None` sem perdas); `execution_counts`: `filled` = ordens com `opened_at` definido; `resolved` = `filled` + ordens nunca preenchidas em `EXPIRED`/`INVALIDATED` e não `frozen`.

- [ ] **Step 1: Escrever os testes que falham**

`tests/metrics/test_summary.py`:
```python
from datetime import timedelta

import pytest

from core.domain.models import Direction, OrderState, OrderStatus
from core.metrics.summary import (
    INSUFFICIENT_SAMPLE, SHORT_BORROW_NOT_SIMULATED, ExecutionCounts, ReviewTally, TradeResult,
    execution_counts, summarize,
)
from tests.support import D, et

BASE = et("2025-11-25", "10:00")


def trade(i, r, direction=Direction.LONG, reasons=(), mfe=None, mae=None):
    closed = BASE + timedelta(hours=i + 1)
    return TradeResult(f"o{i}", direction, closed - timedelta(hours=1), closed, D(r),
                       None if mfe is None else D(mfe), None if mae is None else D(mae), reasons)


SAMPLE = [trade(0, 1, mfe=1.5, mae=-0.2), trade(1, -1, mfe=0.3, mae=-1), trade(2, 2, mfe=2.5, mae=-0.4),
          trade(3, -0.5, mfe=0.1, mae=-0.6)]


def test_hand_computed_summary():
    summary = summarize(SAMPLE, ExecutionCounts(filled=4, resolved=5))
    assert summary.trades == 4
    assert summary.win_rate == pytest.approx(0.5)
    assert summary.avg_r == pytest.approx(0.375)
    assert summary.expectancy_r == pytest.approx(0.375)
    assert summary.profit_factor == pytest.approx(2.0)
    assert summary.max_drawdown_r == pytest.approx(1.0)
    assert summary.avg_duration_seconds == pytest.approx(3600.0)
    assert summary.avg_mfe_r == pytest.approx(1.1)
    assert summary.avg_mae_r == pytest.approx(-0.55)
    assert summary.execution_rate == pytest.approx(0.8)
    assert summary.warnings == (INSUFFICIENT_SAMPLE,)
    assert summary.win_rate_ci is not None and summary.expectancy_ci is not None
    assert set(summary.drawdown_sequence_risk) == {"p5", "p50", "p95"}


def test_order_of_input_does_not_matter_and_is_reproducible():
    counts = ExecutionCounts(4, 4)
    assert summarize(list(reversed(SAMPLE)), counts) == summarize(SAMPLE, counts)


def test_needs_review_excluded_by_default_and_reported():
    flagged = trade(4, 5, reasons=("MISSING_BAR_LEVEL_TOUCH", "SPLIT"))
    default = summarize(SAMPLE + [flagged], ExecutionCounts(5, 5))
    assert default.trades == 4
    assert default.excluded_needs_review == ReviewTally(1, {"MISSING_BAR_LEVEL_TOUCH": 1, "SPLIT": 1})
    assert default.included_needs_review == ReviewTally(0, {})
    included = summarize(SAMPLE + [flagged], ExecutionCounts(5, 5), include_needs_review=True)
    assert included.trades == 5
    assert included.included_needs_review.count == 1
    assert included.excluded_needs_review == ReviewTally(0, {})


def test_short_warning_and_empty_summary():
    with_short = summarize([trade(0, 1, Direction.SHORT)], ExecutionCounts(1, 1))
    assert SHORT_BORROW_NOT_SIMULATED in with_short.warnings
    empty = summarize([], ExecutionCounts(0, 0))
    assert empty.trades == 0 and empty.win_rate is None and empty.profit_factor is None
    assert empty.max_drawdown_r == 0.0 and empty.execution_rate is None
    assert empty.win_rate_ci is None and empty.drawdown_sequence_risk is None
    assert empty.warnings == (INSUFFICIENT_SAMPLE,)


def test_execution_counts():
    filled_at = et("2025-11-25", "10:00")
    orders = [
        OrderState(status=OrderStatus.CLOSED, opened_at=filled_at),
        OrderState(status=OrderStatus.CANCELED, opened_at=filled_at),
        OrderState(status=OrderStatus.EXPIRED),
        OrderState(status=OrderStatus.INVALIDATED),
        OrderState(status=OrderStatus.PENDING),
        OrderState(status=OrderStatus.CANCELED),
        OrderState(status=OrderStatus.PENDING, frozen=True),
        OrderState(status=OrderStatus.EXPIRED, frozen=True),
    ]
    counts = execution_counts(orders)
    assert counts == ExecutionCounts(filled=2, resolved=4)
    assert counts.rate == pytest.approx(0.5)
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/metrics/test_summary.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'core.metrics.summary'`.

- [ ] **Step 3: Implementar**

`src/core/metrics/summary.py`:
```python
"""Strategy metrics over closed orders (spec 5.4). Review policy never mixes flagged trades silently."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Iterable

from core.domain.models import Direction, OrderState, OrderStatus
from core.metrics.resampling import (
    bootstrap_ci, drawdown_permutation_percentiles, max_drawdown, mean_stat, win_rate_stat,
)

MIN_SAMPLE = 30
INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"
SHORT_BORROW_NOT_SIMULATED = "SHORT_BORROW_NOT_SIMULATED"


@dataclass(frozen=True)
class TradeResult:
    order_id: str
    direction: Direction
    opened_at: datetime
    closed_at: datetime
    r_multiple: Decimal
    mfe_r: Decimal | None = None
    mae_r: Decimal | None = None
    review_reasons: tuple[str, ...] = ()

    @property
    def needs_review(self) -> bool:
        return bool(self.review_reasons)


@dataclass(frozen=True)
class ExecutionCounts:
    filled: int
    resolved: int

    @property
    def rate(self) -> float | None:
        return None if self.resolved == 0 else self.filled / self.resolved


def execution_counts(orders: Iterable[OrderState]) -> ExecutionCounts:
    filled = 0
    resolved = 0
    for state in orders:
        if state.opened_at is not None:
            filled += 1
            resolved += 1
        elif not state.frozen and state.status in (OrderStatus.EXPIRED, OrderStatus.INVALIDATED):
            resolved += 1
    return ExecutionCounts(filled, resolved)


@dataclass(frozen=True)
class ReviewTally:
    count: int
    reasons: dict[str, int]


@dataclass(frozen=True)
class MetricsSummary:
    trades: int
    win_rate: float | None
    avg_r: float | None
    expectancy_r: float | None
    profit_factor: float | None
    max_drawdown_r: float
    avg_duration_seconds: float | None
    avg_mfe_r: float | None
    avg_mae_r: float | None
    execution_rate: float | None
    win_rate_ci: tuple[float, float] | None
    expectancy_ci: tuple[float, float] | None
    drawdown_sequence_risk: dict[str, float] | None
    excluded_needs_review: ReviewTally
    included_needs_review: ReviewTally
    warnings: tuple[str, ...]


def _tally(trades: list[TradeResult]) -> ReviewTally:
    counter = Counter(reason for t in trades for reason in t.review_reasons)
    return ReviewTally(len(trades), dict(sorted(counter.items())))


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def summarize(
    trades: Iterable[TradeResult],
    counts: ExecutionCounts,
    include_needs_review: bool = False,
    resamples: int = 2000,
    seed: int = 42,
) -> MetricsSummary:
    ordered = sorted(trades, key=lambda t: (t.closed_at, t.order_id))
    flagged = [t for t in ordered if t.needs_review]
    used = ordered if include_needs_review else [t for t in ordered if not t.needs_review]
    empty = ReviewTally(0, {})

    rs = [float(t.r_multiple) for t in used]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r < 0]
    avg_r = _mean(rs)

    warnings: list[str] = []
    if len(rs) < MIN_SAMPLE:
        warnings.append(INSUFFICIENT_SAMPLE)
    if any(t.direction is Direction.SHORT for t in used):
        warnings.append(SHORT_BORROW_NOT_SIMULATED)

    return MetricsSummary(
        trades=len(rs),
        win_rate=len(wins) / len(rs) if rs else None,
        avg_r=avg_r,
        expectancy_r=avg_r,
        profit_factor=sum(wins) / abs(sum(losses)) if losses else None,
        max_drawdown_r=max_drawdown(rs),
        avg_duration_seconds=_mean([(t.closed_at - t.opened_at).total_seconds() for t in used]),
        avg_mfe_r=_mean([float(t.mfe_r) for t in used if t.mfe_r is not None]),
        avg_mae_r=_mean([float(t.mae_r) for t in used if t.mae_r is not None]),
        execution_rate=counts.rate,
        win_rate_ci=bootstrap_ci(rs, win_rate_stat, resamples, seed),
        expectancy_ci=bootstrap_ci(rs, mean_stat, resamples, seed),
        drawdown_sequence_risk=drawdown_permutation_percentiles(rs, resamples, seed),
        excluded_needs_review=empty if include_needs_review else _tally(flagged),
        included_needs_review=_tally(flagged) if include_needs_review else empty,
        warnings=tuple(warnings),
    )
```

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/metrics -v`
Expected: `test_resampling.py` (3) e `test_summary.py` (5) passam.

- [ ] **Step 5: Rodar a suíte inteira**

Run: `uv run pytest`
Expected: todos passam (Tasks 1–12).

- [ ] **Step 6: Commit**

```bash
git add src/core/metrics/summary.py tests/metrics/test_summary.py
git commit -m "feat(metrics): summary with review policy, execution rate and warnings

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BKUdi9RGWWdXRXVYFWj1yp"
```

---

## Autorrevisão do plano

### Cobertura da spec (Plano 1)

| Spec | Onde |
|---|---|
| 3.2 eventos, `event_key`, eventos de mercado com `bar_ts` | Task 4 (`EventType`, `MARKET_EVENT_TYPES`, validação em `Event`), Tasks 5–7 (chaves) |
| 3.3 hash canônico de payload | Tasks 1 e 4 (`payload_hash`); verificação no banco → Plano 2 |
| 3.4 minutos esperados, próximo minuto, `evaluation_start_ts`, validade, invariante | Task 2; invariante aplicada em `step` (Task 5) e propriedade (Task 8); rejeição no ledger → Plano 2 |
| 3.4.1 actionability, estado herdado, auditoria | Task 9 |
| 3.5 estados, `frozen` | Tasks 5–7 |
| 3.6 as-of, runs, snapshots, replay | Plano 2 |
| 3.7 tamanho em R, T1/breakeven, `r_multiple`, `entry_path` | Tasks 4–6 |
| 3.8 validação de sinais | Task 4 (negociabilidade do ticker → Plano 3) |
| 4.1–4.5 regras de fill e custos | Tasks 5–7 |
| 4.6 qualidade de dados (lógica) | Task 10; agendamento e fontes → Planos 2 e 3 |
| 4.7 dividendos, split (congelamento), aviso SHORT | Task 7 (`apply_dividend`, `freeze`), Task 10 (`dividends_agree`), Task 12 (aviso) |
| 5.4 métricas | Tasks 11–12 |
| 7 casos obrigatórios e propriedades | Tasks 2, 5–10 (unitários), Task 8 (propriedades) |

### Verificações feitas

- **Placeholders:** nenhum passo sem código ou comando concreto.
- **Consistência de nomes:** `new_order_state`, `step`, `run_bars`, `apply_validity_end`, `cancel`, `freeze`, `flag_review`, `apply_dividend`, `signal_actionability`, `build_manual_order`, `ReviewFlag`, `TradeResult`, `ExecutionCounts` usados com as mesmas assinaturas em todas as tasks.
- **Casos obrigatórios da spec seção 7:** todos mapeados para testes nomeados nas Tasks 2, 5, 6, 7 e 9.

## Execution Handoff

Plano salvo em `docs/superpowers/plans/2026-09-12-virtual-order-engine-core.md`. Duas opções de execução:

1. **Subagent-Driven (recomendado)** — um subagente novo por task, revisão entre tasks, iteração rápida. Sub-skill: `superpowers:subagent-driven-development`.
2. **Inline Execution** — executar as tasks nesta sessão com checkpoints. Sub-skill: `superpowers:executing-plans`.
