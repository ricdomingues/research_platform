# Virtual Order Engine — Plano 2: Persistência, Ledger, Dados de Mercado, Evaluator e Replay

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persistir o Virtual Order Engine em PostgreSQL com histórico append-only, idempotência estrita, leitura as-of reproduzível, runs e segmentos de avaliação, camada de dados de mercado **neutra em relação a provider** (adapters Alpaca/FMP/yfinance substituíveis, testados com fixtures), evaluator (ciclo live, comandos, ordens manuais, dividendos, splits, qualidade de fim de dia), replay `REPRODUCE`/`RECALCULATE` e reconstrução da projeção (D2) com o teste `A == B`.

**Architecture:** O núcleo puro do Plano 1 (`src/core/`) é consumido sem nenhuma alteração e sem arquivos novos. Todo o código do Plano 2 fica no pacote novo `src/virtual_orders/`: `storage` (schema SQLAlchemy Core, codecs), `marketdata` (contratos neutros, `MarketDataGateway`, ingestão, leitura as-of, snapshots; adapters Alpaca/FMP/yfinance isolados em módulos próprios), `ledger` (linhas, eventos com idempotência estrita, runs/segmentos, quarentena) e `evaluator` (serviços que orquestram núcleo + ledger e dependem apenas de contratos de dados). Toda escrita de uma ordem acontece numa transação que começa com `SELECT … FOR UPDATE`. A projeção `order_state` é um cache: `regenerate_history` reexecuta o `fill_model` sobre os segmentos lidos as-of e reaplica comandos na posição do `seq`.

**Tech Stack:** Python 3.12, `uv`, PostgreSQL 16 (Docker), SQLAlchemy 2 (Core) + psycopg 3, Alembic, httpx, yfinance, pandas_market_calendars, pytest, Hypothesis, ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-09-12-virtual-order-engine-design.md` (SPEC v1.2 FROZEN, tag `spec/virtual-order-engine-v1.2`). Entradas: `docs/superpowers/notes/2026-09-12-plan1-core-closeout.md` (seção "Entradas obrigatórias para o Plano 2"). Seções cobertas: 2.2 (marketdata, ledger, evaluator — ver desvio de layout abaixo), 3.1–3.3, 3.4 (invariante no ledger), 3.4.1 (serviço), 3.6, 4.6 (job), 4.7 (jobs), 5.2, 5.3 (ciclo e jobs como funções chamáveis), 6, 7 (integração e dados de mercado), 10 (D2, D4).

## Sequência de planos do sub-projeto 01

| Plano | Conteúdo | Estado |
|---|---|---|
| 1 | Núcleo puro | concluído (`plan/virtual-order-engine-core-complete`) |
| **2 (este)** | Postgres/Alembic, ledger, as-of, runs/segmentos, market data neutra com adapters e fixtures, evaluator, dividendos/splits, qualidade de fim de dia, replay, rebuild D2 | — |
| 3 | FastAPI (rotas, `X-API-Key`, `/health`, métricas), worker APScheduler, webhook n8n, Streamlit, Docker Compose de produção, verificação de ticker negociável, composição do `MarketDataGateway` a partir da configuração | depende de 1 e 2 |
| Próximo sub-projeto (registrado na Task 17) | Robinhood MCP Read-Only Market Data Integration — começa por feasibility, sem código | depois do 2 |

## Global Constraints

- Python **3.12**; dependências via `uv add`; testes com `uv run pytest`.
- **`src/core/` está congelado (Plano 1 fechado e tagueado).** Nenhum arquivo em `src/core/` é criado, editado ou removido. Todo código novo vai para `src/virtual_orders/`. Se um teste do Plano 2 exigir mudar a semântica ou o código do `fill_model v1` ou de qualquer módulo de `src/core/`, **pare a execução e reporte ao responsável** — nunca corrija silenciosamente. O encerramento exige `git diff plan/virtual-order-engine-core-complete -- src/core` vazio.
- `src/core/` nunca importa `virtual_orders` nem bibliotecas de I/O (a única exceção pré-existente é `core/marketdata/nyse_calendar.py` → `pandas_market_calendars`, cálculo local). Verificado por teste (Task 1).
- **Market data neutra (D8, seções 3–5 do responsável):** `virtual_orders.storage`, `virtual_orders.ledger`, `virtual_orders.evaluator` e os módulos neutros de `virtual_orders.marketdata` (`sources`, `gateway`, `calendars`, `asof`, `ingest`, `snapshots`) nunca importam adapters (`alpaca`, `fmp`, `yfinance_source`, `http`) nem `httpx`/`yfinance`/`pandas`. Payloads de provider terminam dentro do adapter. Verificado por teste (Task 1).
- **Proveniência sem enum de provider:** `provider`, `provider_version`, `source` e `price_source` são `text` livres; `data_tier` é o único conjunto fechado (`RESEARCH`/`PRODUCTION`). Um provider novo (ex.: Robinhood) entra sem migration estrutural.
- **Sem troca silenciosa de provider:** cada ordem grava `orders.price_source`; toda leitura de preços da ordem usa exatamente essa fonte, resolvida por `MarketDataGateway.bar_source(price_source)`, que **não tem fallback**. Fonte ausente ou indisponível → falha registrada no run, sem avaliação. Todo fallback futuro precisa ser explícito, registrado no run e incluído no `selected_data_hash` (que já contém a fonte em cada entrada).
- Todo `datetime` é timezone-aware e armazenado em UTC (`timestamptz`); a engine SQLAlchemy usa `timezone=UTC` na sessão.
- Preços, quantidades e dinheiro são `Decimal` em Python e `numeric` sem typmod no banco. JSON de fornecedores é lido com `parse_float=Decimal`. `float` nunca entra em payload hasheado.
- `payload_hash` é calculado **uma vez** a partir de `canonical_json(event.hash_material())`; a string canônica é persistida em `order_events.hash_material` e o hash **nunca** é recalculado a partir do `jsonb` relido (notas, entrada 6).
- `fill_model_version` persistido é `"v1"`.
- Nenhum teste acessa a rede. Testes de integração usam Postgres real (`docker-compose.test.yml`, porta `127.0.0.1:55432`) e são marcados `integration`. Fixtures de provider são **synthetic/documentation-derived fixture** e estão catalogadas em `tests/fixtures/README.md`; nunca são descritas como captura real.
- Nomes de eventos, estados, códigos de erro e chaves exatamente como na spec. Identificadores de código em inglês; prosa do plano em português.
- Commits terminam com:
  ```
  Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01BKUdi9RGWWdXRXVYFWj1yp
  ```

## Decisões do responsável (registradas neste plano; a spec v1.2 não é alterada)

- **D5 — `data_as_of` depois da ingestão.** A spec 5.3 cria o run antes de ingerir (passo 1) e lê as-of `data_as_of` (passo 4), o que tornaria invisíveis os candles recém-ingeridos. Decisão: toda ingestão grava `ingested_at = clock_timestamp()` (relógio do banco) sob o advisory lock `INGEST_LOCK_KEY`; `acquire_data_as_of()` obtém `clock_timestamp()` sob o mesmo lock **após** a ingestão do ciclo, e o run é inserido com esse valor. `data_as_of` é o limite superior de conhecimento do run: versão selecionada = `max(ingested_at) where ingested_at <= evaluation_run.data_as_of` (desempate por `batch_id`); nenhum candle com `ingested_at > data_as_of` é usado; `REPRODUCE` e o rebuild usam exatamente esse limite. Não existe leitura "current" em avaliação histórica. `started_at` continua sendo o início do ciclo.
- **D6 — ausência é imutável dentro do run, não no banco.** Um minuto esperado sem candle as-of o `data_as_of` de um run fica `MISSING` naquele run/segmento (entra no `selected_data_hash` como `MISSING`). Se o provider entregar o candle depois, isso cria uma nova versão: `REPRODUCE` do run original continua vendo `MISSING`; `RECALCULATE` (novo `data_as_of`) enxerga o candle. Runs antigos nunca são reescritos. Um candle **não fechado** (`bar_ts + 1min > market_now`) não é lido nem conta como ausente. Um candle fechado ainda não recebido **no fim** do intervalo não é consumido pelo ciclo live (o próximo ciclo o lê se chegar); o ciclo de fim de dia (`close_trailing_gap=True`) fecha o intervalo e o registra como ausente naquele run e em `DATA_QUALITY`. Como o cursor live de uma ordem avança por segmento, runs live posteriores não reprocessam minutos já cobertos; a versão tardia aparece em `RECALCULATE`, em snapshots novos e em ordens criadas depois.
- **D7 — actionability com minutos ausentes é política.** Mantida a política conservadora: dados insuficientes para provar actionability → `503 ACTIONABILITY_UNVERIFIABLE`. A decisão é tomada por um `CoveragePolicy` injetável (padrão `STRICT_PRIMARY`: qualquer minuto ausente no `price_source` é não resolvido); nome da política e evidências ficam no run `ACTIONABILITY` e no payload de `ORDER_CREATED`. Uma política futura com provider secundário (ex.: Robinhood) poderá resolver minutos **se** registrar fontes e versões usadas como evidência — não é implementada agora. Observabilidade: `actionability_outcomes(conn, since=…)` conta resultados por tipo (inclusive quantos 503) a partir dos runs.
- **D8 — adapters via `httpx`**, fora de `src/core`, com fixtures no nível HTTP (`httpx.MockTransport`); yfinance via função `history` injetável.
- **D9 — colunas de infraestrutura/auditoria aprovadas** (lista em "Decisões de detalhe", item 1), sem alterar modelos de `src/core`.
- **D10 — `DATA_QUALITY_RECHECK` fora deste plano.** `DATA_QUALITY` é a fotografia do `data_as_of` original; `EventType` do núcleo não muda. Reavaliações futuras ficam fora do `fill_model v1`; hoje usam `RECALCULATE`.
- **D11 — fixtures synthetic/documentation-derived**, rotuladas como tais em `tests/fixtures/README.md` (verificado por teste); fixtures reais sanitizadas podem ser adicionadas em outra etapa.
- **D12 — Falha de provider ≠ falha de qualidade de dados** (decidida após a execução, antes da publicação). `DATA_QUALITY` só é emitido quando houve uma ingestão suficientemente válida para medir cobertura. Falha global de provider/run não gera artificialmente uma sessão com 100% de candles ausentes. Concretamente, o comando de qualidade não emite nada para aquela ordem naquele pregão (nem `DATA_QUALITY`, nem `DATA_GAP`, nem `NEEDS_REVIEW:MISSING_BAR_*`, nem `DAILY_RANGE_MISMATCH`) e registra o motivo quando: (a) `PROVIDER_FAILURE` — a chave de feed da ordem (`price_source:ticker`) está entre as falhas de ingestão do próprio ciclo (`unavailable_feeds`, propagado por `run_end_of_day` a partir de `cycle.ingest_failures`, os mesmos que já excluem `expire_due_orders`); ou (b) `NO_OBSERVATIONS` — a janela da ordem naquele pregão tem ao menos um minuto esperado mas nenhum candle foi observado as-of o `data_as_of` do run, para seu `price_source`/ticker. A falha permanece registrada como falha operacional: os motivos entram em `not_evaluated: {"<order_id>": "<reason>"}` no detail `COMPLETED` do run `END_OF_DAY`, sem incidente de integridade (é operacional, não de integridade). Cobertura parcial continua emitindo `DATA_QUALITY` normalmente com os minutos ausentes medidos; a checagem de imutabilidade da D4 (já emitido → nunca reemitido) continua sendo a primeira verificação. Reavaliação posterior de uma sessão não avaliada por D12 pertence a `DATA_QUALITY_RECHECK` ou mecanismo equivalente futuro (fora deste plano, como já registrado em D10).

## Decisões de detalhe deste plano (não alteram regras de fill)

1. **Colunas extras:** `signals.evaluation_start_ts`; `orders.price_source`; `order_events.hash_material`; `order_state.state_document` (todos os campos de `OrderState`, notas entrada 2) e `order_state.next_seq`; `order_eval_segments.first_seq` e `event_count` (posição do segmento no `seq`, necessária para intercalar comandos no rebuild); `evaluation_run_status.id` (PK substituta). `orders` e `signals` também recebem trigger append-only.
2. **`evaluation_runs.kind` ganha `END_OF_DAY`** para o job de fim de dia, cujo `run_id` a D4 exige no payload de `DATA_QUALITY`.
3. **Relógio de mercado × relógio de conhecimento.** Serviços recebem `market_now`/`created_at` (quais minutos estão fechados; instante da decisão) separado de `data_as_of` (versão dos dados, D5). Em produção coincidem; nos testes, pregões de 2025 são avaliados com watermark real.
4. **Ingestão deduplicada.** Um lote grava apenas candles novos ou com OHLCV diferente da versão as-of vigente **da mesma fonte**; lote sem mudanças não gera `bar_batches`. A leitura as-of é idêntica com ou sem essa otimização.
5. **`selected_data_hash`** = SHA-256 canônico da lista, por minuto esperado, de `(source, ticker, ts, batch_id, OHLCV)` ou `(source, ticker, ts, MISSING)` — a fonte faz parte da identidade dos dados usados.
6. **`selected_data_hash` da actionability** fica no `detail` do status do run `ACTIONABILITY` e no payload de `ORDER_CREATED` (`actionability_selected_data_hash`), porque o run é anterior à ordem.
7. **Qualidade de fim de dia:** `DATA_GAP` calculado dentro do pregão; conferência diária só quando a janela da ordem cobre o pregão inteiro; yfinance indisponível → registrado no `detail` do run. Ordens replay não recebem `DATA_QUALITY` live.
8. **REPRODUCE** regenera o histórico da ordem de origem (mesmo algoritmo do rebuild), exige identidade de `(event_key, payload_hash)` e grava a nova ordem com os eventos regenerados e segmentos que apontam para os **runs originais**. Divergência → `LedgerIntegrityError(REPRODUCE_DIVERGENCE)` com diff, incidente e ordem de origem `frozen`.
9. **RECALCULATE** usa o `price_source` da ordem de origem (nunca troca de fonte), lê candles as-of o `data_as_of` do snapshot, intercala dividendos já registrados em `dividends` na abertura do pregão da ex-date (um run `REPLAY` por trecho) e aplica validade se `data_as_of ≥ valid_until_ts`. Não copia `CANCELED`/`FROZEN`/`NEEDS_REVIEW` da origem.
10. **Dividendos:** o job de abertura exige `now` anterior à abertura do pregão da ex-date; o valor creditado é o da fonte primária quando validado.
11. **Contratos síncronos.** Os protocolos de dados (`BarSource`, `DividendSource`, `SplitSource`, `ReferenceSource`) são síncronos, como todo o Plano 2 (SQLAlchemy síncrono, worker sem event loop); o requisito "conceitualmente equivalente a `async def get_bars`" é atendido pela fronteira, não pela assincronia. Interfaces pequenas por capacidade; não há `FundamentalsProvider` porque nada no Plano 2 o consome.
12. **Layout fora de `core/`.** A spec 2.2 lista `marketdata/`, `ledger/` e `evaluator/` dentro de `core/`; por causa do congelamento e da D8, eles vivem em `virtual_orders/`. As responsabilidades e dependências da tabela 2.2 são preservadas.
13. **Baseline de lint/tipos do Plano 1:** as violações existentes em arquivos do Plano 1 são silenciadas por `per-file-ignores`/overrides de mypy, nunca corrigidas.

## Critérios de aceite adicionais (provados na Task 17)

| Critério | Prova |
|---|---|
| nenhuma dependência de I/O entrou em `src/core` | `tests/test_import_boundaries.py` + `git diff … -- src/core` vazio |
| os adapters são substituíveis | suíte de integração roda com `FakeBarSource` (`fake_feed`); ponta a ponta roda com `AlpacaBars` pelo mesmo `MarketDataGateway` |
| payloads Alpaca/FMP/yfinance não vazam para domínio/evaluator | teste de fronteira dos módulos neutros |
| proveniência não está hard-coded a providers atuais | `test_provenance_accepts_any_provider_without_migration` |
| leitura as-of funciona independentemente do provider | `test_as_of_reads_are_isolated_by_source` |
| `selected_data_hash` identifica os dados efetivamente usados | testes de hash (fonte, batch, preço, ausência) e `SELECTED_DATA_HASH_MISMATCH` |
| REPRODUCE preserva missing bars do run original | `test_missing_minute_is_missing_for_reproduce_but_present_for_recalculate` |
| RECALCULATE pode usar dados ingeridos depois | idem + `test_recalculate_uses_corrected_history_without_touching_the_source` |
| nenhuma troca de provider acontece silenciosamente | `test_no_silent_provider_switch` |
| todo o Plano 1 continua verde | suíte completa |

## Processo de execução (subagent-driven)

Por task: subagente novo com contexto mínimo (a task, as Global Constraints e as decisões) → testes que falham → implementação mínima → suíte da task → regressão relevante (`uv run pytest -m "not slow"` + ruff + mypy) → revisão independente → correções → commit próprio. Tasks são sequenciais (compartilham schema, ledger e semântica as-of); nenhuma alteração fora dos arquivos listados na task. Depois das 17 tasks: seção "Encerramento do controlador" ao final do plano. **Nenhuma tag é criada enquanto houver revisão aberta.**


## File Structure

```text
pyproject.toml                               # + deps, ruff, mypy, marcadores pytest, pacote virtual_orders
docker-compose.test.yml                      # Postgres 16 de teste (127.0.0.1:55432, tmpfs)
alembic.ini
migrations/env.py
migrations/script.py.mako
migrations/versions/0001_initial_schema.py   # tabelas, triggers append-only, role vo_app

src/core/                                    # CONGELADO (Plano 1) — nada é criado ou alterado aqui

src/virtual_orders/__init__.py
src/virtual_orders/storage/__init__.py
src/virtual_orders/storage/database.py       # make_engine, chaves de advisory lock
src/virtual_orders/storage/tables.py         # SQLAlchemy Core Table para cada tabela
src/virtual_orders/storage/codec.py          # OrderState/FillConfig <-> documento; PreparedEvent

src/virtual_orders/marketdata/__init__.py
# -- neutros (nunca importam adapters) --
src/virtual_orders/marketdata/sources.py     # RawBar, protocolos BarSource/DividendSource/SplitSource/ReferenceSource, DataTier, erros
src/virtual_orders/marketdata/gateway.py     # MarketDataGateway (resolução exata por source id, sem fallback)
src/virtual_orders/marketdata/calendars.py   # calendar_for_window (NYSE com folga)
src/virtual_orders/marketdata/asof.py        # floor_minute, acquire_data_as_of, read_bars_as_of, read_bar_version, selected_data_hash
src/virtual_orders/marketdata/ingest.py      # store_batch, ingest_bars
src/virtual_orders/marketdata/snapshots.py   # manifest_hash, create_or_reuse_snapshot
# -- adapters (payloads de provider terminam aqui) --
src/virtual_orders/marketdata/http.py        # get_json com retry/backoff e parse_float=Decimal
src/virtual_orders/marketdata/alpaca.py      # AlpacaBars, AlpacaSplits, ALPACA_IEX_SOURCE
src/virtual_orders/marketdata/fmp.py         # FmpDividends
src/virtual_orders/marketdata/yfinance_source.py  # YFinanceSource (minuto, diário, dividendos)

src/virtual_orders/ledger/__init__.py
src/virtual_orders/ledger/errors.py          # LedgerIntegrityError, ProjectionIntegrityError, HistoryDivergence, NotFound
src/virtual_orders/ledger/orders.py          # SignalRow, OrderRow, Projection; repositório de signals/orders/order_state
src/virtual_orders/ledger/events.py          # dedupe_events, append_events, stored_events
src/virtual_orders/ledger/runs.py            # RunKind, start_run, finish_run, SegmentRow, record_segment, list_segments
src/virtual_orders/ledger/writes.py          # apply_result, persist_new_order
src/virtual_orders/ledger/quarantine.py      # record_incident, quarantine

src/virtual_orders/evaluator/__init__.py
src/virtual_orders/evaluator/outcomes.py     # OrderOutcome
src/virtual_orders/evaluator/context.py      # build_order_context, load_order_context, signal_context
src/virtual_orders/evaluator/signals.py      # parse_signal_body, submit_signal
src/virtual_orders/evaluator/cycle.py        # evaluate_order, run_live_cycle, lock do ciclo
src/virtual_orders/evaluator/commands.py     # cancel_order, finalize_validity, freeze_order, flag_order_review
src/virtual_orders/evaluator/coverage.py     # CoveragePolicy, CoverageDecision, STRICT_PRIMARY_COVERAGE (D7)
src/virtual_orders/evaluator/manual.py       # create_manual_order, actionability_outcomes
src/virtual_orders/evaluator/history.py      # regenerate_history (D2)
src/virtual_orders/evaluator/rebuild.py      # rebuild_projection, rebuild_all_projections
src/virtual_orders/evaluator/corporate.py    # apply_dividends, freeze_for_splits
src/virtual_orders/evaluator/quality.py      # run_session_quality (D4), run_end_of_day
src/virtual_orders/evaluator/replay.py       # reproduce_orders, recalculate_orders

tests/test_import_boundaries.py
tests/storage/__init__.py
tests/storage/test_codec.py
tests/marketdata/test_http_sources.py
tests/marketdata/test_yfinance_source.py
tests/marketdata/test_asof_hash.py
tests/fixtures/README.md                     # catálogo: toda fixture é synthetic/documentation-derived
tests/fixtures/alpaca/bars_page1.json
tests/fixtures/alpaca/bars_page2.json
tests/fixtures/alpaca/corporate_actions.json
tests/fixtures/fmp/dividends.json
tests/fixtures/yfinance/aapl_1m_2025-11-25.csv
tests/fixtures/yfinance/aapl_1d_2025-11.csv
tests/integration/__init__.py
tests/integration/conftest.py                # banco novo por teste a partir de template migrado
tests/integration/support.py                 # fontes falsas (fake_feed), candles do cenário, corpo de sinal
tests/integration/test_schema.py
tests/integration/test_ingest_asof.py
tests/integration/test_ledger.py
tests/integration/test_signals.py
tests/integration/test_cycle.py
tests/integration/test_commands.py
tests/integration/test_manual_orders.py
tests/integration/test_rebuild.py
tests/integration/test_corporate.py
tests/integration/test_quality.py
tests/integration/test_reproduce.py
tests/integration/test_recalculate.py
tests/integration/test_end_to_end.py
docs/superpowers/notes/2026-09-12-plan2-closeout.md
docs/superpowers/roadmap/2026-09-12-robinhood-mcp-read-only.md
```

Dependências (unidirecionais): `virtual_orders.storage` → `core`; `virtual_orders.marketdata` (neutros) → `storage`, `core`; adapters → neutros; `virtual_orders.ledger` → `storage`, `core`; `virtual_orders.evaluator` → `ledger`, neutros de `marketdata`, `storage`, `core`. Somente testes e a composição do Plano 3 instanciam adapters.

---

### Task 1: Ferramentas (ruff, mypy) e testes de fronteira de imports

Notas, entrada 8; Global Constraints (congelamento de `src/core`, market data neutra). Nenhum arquivo do Plano 1 é editado; o baseline medido em `deec356` é silenciado por configuração.

**Files:**
- Modify: `pyproject.toml`
- Create: `tests/test_import_boundaries.py`

**Interfaces:**
- Consumes: nada.
- Produces: `uv run ruff check src tests` e `uv run mypy` verdes; os testes de fronteira passam a proteger `src/core` e, conforme os pacotes forem criados, os módulos neutros de `virtual_orders`.

- [ ] **Step 1: Escrever os testes de fronteira**

`tests/test_import_boundaries.py`:
```python
"""Import boundaries: frozen pure core, and provider-neutral infrastructure (Plan 2 Global Constraints)."""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"

PURE_PACKAGES = ["core/domain", "core/fills", "core/metrics"]
PURE_MODULES = ["core/actionability.py", "core/dataquality.py"]
IO_LIBRARIES = (
    "sqlalchemy", "psycopg", "alembic", "httpx", "yfinance", "pandas", "pandas_market_calendars",
    "requests", "urllib", "socket", "os", "pathlib",
)
CORE_ALLOWED = {"core/marketdata/nyse_calendar.py": {"pandas_market_calendars"}}

NEUTRAL_PACKAGES = ["virtual_orders/storage", "virtual_orders/ledger", "virtual_orders/evaluator"]
NEUTRAL_MARKETDATA = [
    "virtual_orders/marketdata/sources.py", "virtual_orders/marketdata/gateway.py",
    "virtual_orders/marketdata/calendars.py", "virtual_orders/marketdata/asof.py",
    "virtual_orders/marketdata/ingest.py", "virtual_orders/marketdata/snapshots.py",
]
PROVIDER_ADAPTERS = (
    "virtual_orders.marketdata.alpaca", "virtual_orders.marketdata.fmp",
    "virtual_orders.marketdata.yfinance_source", "virtual_orders.marketdata.http",
)
PROVIDER_LIBRARIES = ("httpx", "yfinance", "pandas")


def _rel(path: Path) -> str:
    return str(path.relative_to(SRC))


def _pure_files() -> list[Path]:
    files = [SRC / module for module in PURE_MODULES]
    for package in PURE_PACKAGES:
        files.extend(sorted((SRC / package).rglob("*.py")))
    return files


def _core_files() -> list[Path]:
    return sorted((SRC / "core").rglob("*.py"))


def _neutral_files() -> list[Path]:
    files = [SRC / module for module in NEUTRAL_MARKETDATA if (SRC / module).exists()]
    for package in NEUTRAL_PACKAGES:
        if (SRC / package).exists():
            files.extend(sorted((SRC / package).rglob("*.py")))
    return files


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def _matches(name: str, prefix: str) -> bool:
    return name == prefix or name.startswith(prefix + ".")


def _offending(path: Path, prefixes: tuple[str, ...], allowed: set[str] | None = None) -> list[str]:
    allowed = allowed or set()
    return sorted(
        name for name in _imported_modules(path)
        if any(_matches(name, prefix) for prefix in prefixes) and not any(_matches(name, a) for a in allowed)
    )


@pytest.mark.parametrize("path", _pure_files(), ids=_rel)
def test_pure_core_module_has_no_io_or_platform_imports(path: Path) -> None:
    assert _offending(path, IO_LIBRARIES + ("virtual_orders",)) == []


@pytest.mark.parametrize("path", _core_files(), ids=_rel)
def test_frozen_core_never_imports_platform_or_io(path: Path) -> None:
    allowed = CORE_ALLOWED.get(_rel(path), set())
    assert _offending(path, IO_LIBRARIES + ("virtual_orders",), allowed) == []


@pytest.mark.parametrize("path", _neutral_files(), ids=_rel)
def test_neutral_infrastructure_never_imports_provider_adapters(path: Path) -> None:
    assert _offending(path, PROVIDER_ADAPTERS + PROVIDER_LIBRARIES) == []


def test_dataquality_does_not_import_fills() -> None:
    assert not any(_matches(name, "core.fills") for name in _imported_modules(SRC / "core/dataquality.py"))


def test_boundary_scan_sees_every_pure_file() -> None:
    assert len(_pure_files()) >= 12
    assert "core.domain.models" in _imported_modules(SRC / "core/fills/v1/engine.py")
```

- [ ] **Step 2: Rodar os testes**

Run: `uv run pytest tests/test_import_boundaries.py -v`
Expected: PASS nos testes de `core` (o núcleo já respeita as fronteiras). O teste parametrizado dos módulos neutros aparece como `SKIPPED [got empty parameter set]` até a Task 2 criar `virtual_orders` — esperado. Se algum arquivo de `core` falhar, **pare e reporte** (congelamento).

- [ ] **Step 3: Adicionar ferramentas e configuração**

Run: `uv add --dev ruff==0.8.6 mypy==1.13.0 pandas-stubs`

Acrescentar ao `pyproject.toml` (mantendo o que existe):
```toml
[tool.ruff]
target-version = "py312"
line-length = 120
src = ["src", "."]

[tool.ruff.lint]
select = ["E4", "E7", "E9", "F", "B", "I", "UP"]

[tool.ruff.lint.isort]
known-first-party = ["core", "virtual_orders", "tests"]

[tool.ruff.lint.per-file-ignores]
# Baseline do Plano 1 (congelado em deec356): silenciado, nunca corrigido.
"src/core/actionability.py" = ["I001", "UP035", "UP017"]
"src/core/dataquality.py" = ["I001", "UP035"]
"src/core/domain/calendar.py" = ["UP035", "UP017", "B905"]
"src/core/domain/hashing.py" = ["UP038", "UP017"]
"src/core/domain/models.py" = ["UP017"]
"src/core/fills/v1/engine.py" = ["UP035"]
"src/core/marketdata/nyse_calendar.py" = ["UP017"]
"src/core/metrics/resampling.py" = ["UP035"]
"src/core/metrics/summary.py" = ["I001", "UP035"]
"tests/domain/test_hashing.py" = ["UP017"]
"tests/domain/test_models.py" = ["UP017"]
"tests/fills/test_entry.py" = ["I001", "B011", "UP017"]
"tests/fills/test_exits.py" = ["I001"]
"tests/fills/test_lifecycle.py" = ["I001"]
"tests/fills/test_properties.py" = ["I001", "B905", "B904"]
"tests/metrics/test_resampling.py" = ["I001"]
"tests/metrics/test_summary.py" = ["I001"]
"tests/test_actionability.py" = ["I001"]
"tests/test_dataquality.py" = ["I001"]
"tests/support.py" = ["UP017", "E402", "E741"]

[tool.mypy]
python_version = "3.12"
strict = true
mypy_path = "src"
packages = ["core"]
explicit_package_bases = true

[[tool.mypy.overrides]]
# Baseline do Plano 1: 6 erros de Optional em engine.py (inalcançáveis por caminhos validados).
module = "core.fills.v1.engine"
disable_error_code = ["operator", "type-var", "arg-type", "assignment"]

[[tool.mypy.overrides]]
module = ["pandas_market_calendars", "pandas_market_calendars.*", "yfinance", "yfinance.*"]
ignore_missing_imports = true
```

- [ ] **Step 4: Rodar lint e tipos**

Run: `uv run ruff check src tests && uv run mypy`
Expected: `All checks passed!` e `Success: no issues found in 18 source files`.
Se o ruff apontar um código a mais **em arquivo do Plano 1** (a detecção de first-party pode reclassificar `I001`), acrescente apenas esse código ao `per-file-ignores` do arquivo. Nunca rode `--fix` em arquivos do Plano 1.

- [ ] **Step 5: Rodar a suíte inteira**

Run: `uv run pytest -m "not slow"`
Expected: todos os testes do Plano 1 + os novos passam.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock tests/test_import_boundaries.py
git commit -m "chore: ruff, mypy and import boundary tests for frozen core and neutral infrastructure

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BKUdi9RGWWdXRXVYFWj1yp"
```

---

### Task 2: Postgres de teste, Alembic e schema append-only

Spec 3.1, 6 ("Alteração de histórico"), decisões de detalhe 1 e 2.

**Files:**
- Modify: `pyproject.toml`
- Create: `docker-compose.test.yml`, `alembic.ini`, `migrations/env.py`, `migrations/script.py.mako`, `migrations/versions/0001_initial_schema.py`
- Create: `src/virtual_orders/__init__.py`, `src/virtual_orders/storage/__init__.py`, `src/virtual_orders/storage/database.py`, `src/virtual_orders/storage/tables.py`
- Create: `tests/integration/__init__.py`, `tests/integration/conftest.py`, `tests/integration/test_schema.py`

**Interfaces:**
- Consumes: nada do Plano 2.
- Produces:
  - `virtual_orders.storage.database.make_engine(url: str) -> sqlalchemy.Engine` (sessão em UTC)
  - `virtual_orders.storage.database.CYCLE_LOCK_KEY: int`, `INGEST_LOCK_KEY: int`
  - `virtual_orders.storage.tables`: `metadata` e objetos `Table` `signals`, `orders`, `order_events`, `order_state`, `evaluation_runs`, `evaluation_run_status`, `order_eval_segments`, `bar_batches`, `bars_1m`, `market_data_snapshots`, `dividends`, `integrity_incidents`; `APPEND_ONLY_TABLES: tuple[str, ...]`
  - fixture pytest `engine` (banco novo migrado por teste) e `database_url`; helper `alembic_config(url) -> alembic.config.Config` em `tests/integration/conftest.py`
  - marcador `integration` aplicado a tudo em `tests/integration/`

- [ ] **Step 1: Dependências, marcador e Postgres de teste**

Run: `uv add "sqlalchemy>=2.0.36" "psycopg[binary]>=3.2" "alembic>=1.14"`

Em `pyproject.toml`, trocar `packages = ["src/core"]` (em `[tool.hatch.build.targets.wheel]`) por `packages = ["src/core", "src/virtual_orders"]`, trocar `packages = ["core"]` (em `[tool.mypy]`) por `packages = ["core", "virtual_orders"]` e substituir o bloco `[tool.pytest.ini_options]` por:
```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = [".", "src"]
addopts = "-q"
markers = [
    "integration: requires the Postgres test container (docker compose -f docker-compose.test.yml up -d)",
    "slow: long-running property search",
]
```

`docker-compose.test.yml`:
```yaml
services:
  postgres-test:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: vo
      POSTGRES_PASSWORD: vo
      POSTGRES_DB: postgres
    ports:
      - "127.0.0.1:55432:5432"
    tmpfs:
      - /var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U vo"]
      interval: 2s
      timeout: 2s
      retries: 30
```

Run: `docker compose -f docker-compose.test.yml up -d --wait`
Expected: container `postgres-test` healthy.

- [ ] **Step 2: Escrever o harness e os testes de schema que falham**

`tests/integration/__init__.py`: vazio.

`tests/integration/conftest.py`:
```python
"""Real Postgres per test: a migrated template database cloned for every test."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

from virtual_orders.storage.database import make_engine

ROOT = Path(__file__).resolve().parents[2]
ADMIN_URL = os.environ.get(
    "TEST_DATABASE_ADMIN_URL", "postgresql+psycopg://vo:vo@127.0.0.1:55432/postgres"
)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    here = Path(__file__).parent
    for item in items:
        if here in Path(str(item.fspath)).parents:
            item.add_marker(pytest.mark.integration)


def _admin() -> Engine:
    return create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")


def alembic_config(url: str) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    return config


@pytest.fixture(scope="session")
def template_database() -> Iterator[str]:
    admin = _admin()
    name = f"vo_template_{uuid4().hex[:8]}"
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
    except Exception as exc:  # noqa: BLE001 - explicit operator message
        pytest.fail(
            f"Postgres de teste indisponível em {ADMIN_URL}: {exc}. "
            "Rode: docker compose -f docker-compose.test.yml up -d --wait"
        )
    url = make_url(ADMIN_URL).set(database=name).render_as_string(hide_password=False)
    command.upgrade(alembic_config(url), "head")
    yield name
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


@pytest.fixture
def database_url(template_database: str) -> Iterator[str]:
    admin = _admin()
    name = f"vo_test_{uuid4().hex[:12]}"
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}" TEMPLATE "{template_database}"'))
    yield make_url(ADMIN_URL).set(database=name).render_as_string(hide_password=False)
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


@pytest.fixture
def engine(database_url: str) -> Iterator[Engine]:
    eng = make_engine(database_url)
    yield eng
    eng.dispose()
```

`tests/integration/test_schema.py`:
```python
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from tests.integration.conftest import alembic_config
from virtual_orders.storage import tables
from virtual_orders.storage.tables import APPEND_ONLY_TABLES


def _insert_batch(conn):
    batch_id = uuid4()
    conn.execute(
        tables.bar_batches.insert().values(
            batch_id=batch_id, provider="alpaca", provider_version="t", data_tier="RESEARCH",
            request={}, content_hash="h", ingested_at=datetime(2025, 11, 25, tzinfo=UTC),
        )
    )
    return batch_id


def test_metadata_matches_migrated_columns(engine):
    inspector = inspect(engine)
    assert set(inspector.get_table_names()) >= set(tables.metadata.tables)
    for name, table in tables.metadata.tables.items():
        db_columns = {column["name"] for column in inspector.get_columns(name)}
        assert db_columns == {column.name for column in table.columns}, name


@pytest.mark.parametrize("statement", [
    "UPDATE bar_batches SET provider = 'x'",
    "DELETE FROM bar_batches",
    "TRUNCATE bar_batches CASCADE",
])
def test_append_only_rejects_mutation(engine, statement):
    with engine.begin() as conn:
        _insert_batch(conn)
    with pytest.raises(DBAPIError, match="append-only"):
        with engine.begin() as conn:
            conn.execute(text(statement))


def test_every_append_only_table_has_triggers(engine):
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT event_object_table, event_manipulation FROM information_schema.triggers"
        )).all()
    covered = {(table, op) for table, op in rows}
    for table in APPEND_ONLY_TABLES:
        assert (table, "UPDATE") in covered, table
        assert (table, "DELETE") in covered, table
    with engine.connect() as conn:
        truncate = conn.execute(text(
            "SELECT c.relname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
            "WHERE (t.tgtype & 32) = 32 AND NOT t.tgisinternal"
        )).scalars().all()
    assert set(APPEND_ONLY_TABLES) <= set(truncate)


def test_projection_table_is_mutable(engine):
    assert "order_state" not in APPEND_ONLY_TABLES
    assert "dividends" not in APPEND_ONLY_TABLES


def test_app_role_cannot_update_history(engine):
    with engine.begin() as conn:
        _insert_batch(conn)
    with pytest.raises(DBAPIError, match="permission denied"):
        with engine.begin() as conn:
            conn.execute(text("SET LOCAL ROLE vo_app"))
            conn.execute(text("UPDATE bar_batches SET provider = 'x'"))
    with engine.begin() as conn:
        conn.execute(text("SET LOCAL ROLE vo_app"))
        _insert_batch(conn)


def test_bar_primary_key_allows_new_versions_only(engine):
    with engine.begin() as conn:
        first, second = _insert_batch(conn), _insert_batch(conn)
        row = dict(ticker="AAPL", ts=datetime(2025, 11, 25, 14, 30, tzinfo=UTC), open=1, high=1,
                   low=1, close=1, volume=1, source="alpaca_iex")
        conn.execute(tables.bars_1m.insert().values(batch_id=first, **row))
        conn.execute(tables.bars_1m.insert().values(batch_id=second, **row))
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(tables.bars_1m.insert().values(batch_id=first, **row))


def test_session_timezone_is_utc(engine):
    with engine.connect() as conn:
        assert conn.execute(text("SHOW timezone")).scalar_one() == "UTC"


def test_provenance_accepts_any_provider_without_migration(engine):
    with engine.begin() as conn:
        batch_id = uuid4()
        conn.execute(tables.bar_batches.insert().values(
            batch_id=batch_id, provider="robinhood", provider_version="mcp-2026-09", data_tier="RESEARCH",
            request={"tool": "bars", "timeframe": "1m"}, content_hash="h",
            ingested_at=datetime(2025, 11, 25, tzinfo=UTC),
        ))
        conn.execute(tables.bars_1m.insert().values(
            ticker="AAPL", ts=datetime(2025, 11, 25, 14, 30, tzinfo=UTC), open=1, high=1, low=1, close=1,
            volume=1, source="robinhood_realtime", batch_id=batch_id,
        ))
        constrained = conn.execute(text(
            "SELECT column_name FROM information_schema.constraint_column_usage u "
            "JOIN information_schema.check_constraints c ON c.constraint_name = u.constraint_name "
            "WHERE u.column_name IN ('provider', 'provider_version', 'source', 'price_source')"
        )).scalars().all()
    assert constrained == []


def test_migrations_run_from_zero_and_back(database_url):
    config = alembic_config(database_url)
    command.downgrade(config, "base")
    probe = create_engine(database_url)
    try:
        assert set(inspect(probe).get_table_names()) <= {"alembic_version"}
        command.upgrade(config, "head")
        assert set(inspect(probe).get_table_names()) >= set(tables.metadata.tables)
    finally:
        probe.dispose()
```

- [ ] **Step 3: Rodar para ver falhar**

Run: `uv run pytest tests/integration/test_schema.py -v`
Expected: FAIL/ERROR com `ModuleNotFoundError: No module named 'virtual_orders.storage'`.

- [ ] **Step 4: Implementar storage e migração**

`src/virtual_orders/__init__.py`: vazio.

`src/virtual_orders/storage/__init__.py`: vazio.

`src/virtual_orders/storage/database.py`:
```python
"""Engine construction and advisory-lock keys (Postgres only)."""

from __future__ import annotations

from sqlalchemy import Engine, create_engine

CYCLE_LOCK_KEY = 0x564F0001  # global evaluator cycle lock (spec 5.2)
INGEST_LOCK_KEY = 0x564F0002  # serializes ingestion and data_as_of watermarks (decision 3)


def make_engine(url: str) -> Engine:
    return create_engine(url, pool_pre_ping=True, connect_args={"options": "-c timezone=UTC"})
```

`src/virtual_orders/storage/tables.py`:
```python
"""SQLAlchemy Core mirror of migrations/versions/0001_initial_schema.py (checked by test_schema)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import (
    BigInteger, Boolean, Column, Date, DateTime, Integer, MetaData, Numeric, Table, Text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

metadata = MetaData()


def _ts(name: str, nullable: bool = False) -> Column[Any]:
    return Column(name, DateTime(timezone=True), nullable=nullable)


def _num(name: str, nullable: bool = False) -> Column[Any]:
    return Column(name, Numeric(asdecimal=True), nullable=nullable)


signals = Table(
    "signals", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("client_signal_id", Text, nullable=False, unique=True),
    Column("payload_hash", Text, nullable=False),
    _ts("created_at"),
    Column("strategy", Text, nullable=False),
    Column("strategy_version", Text, nullable=False),
    Column("source", Text, nullable=False),
    Column("ticker", Text, nullable=False),
    Column("direction", Text, nullable=False),
    _num("entry_zone_low"), _num("entry_zone_high"), _num("trigger_price", True),
    Column("confirmation_note", Text),
    _num("target1"), _num("target2", True), _num("stop"),
    Column("valid_sessions", Integer, nullable=False),
    _ts("evaluation_start_ts"), _ts("valid_until_ts"),
    _num("score", True),
    Column("thesis", Text),
    Column("raw_payload", JSONB, nullable=False),
)

market_data_snapshots = Table(
    "market_data_snapshots", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    _ts("created_at"),
    Column("source", Text, nullable=False),
    _ts("data_as_of"),
    Column("tickers", ARRAY(Text), nullable=False),
    _ts("range_from"), _ts("range_to"),
    Column("content_manifest_hash", Text, nullable=False),
)

orders = Table(
    "orders", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("signal_id", UUID(as_uuid=True), nullable=False),
    Column("origin", Text, nullable=False),
    _ts("created_at"), _ts("evaluation_start_ts"), _ts("valid_until_ts"),
    Column("fill_model_version", Text, nullable=False),
    Column("config_snapshot", JSONB, nullable=False),
    Column("code_version", Text, nullable=False),
    Column("replay", Boolean, nullable=False),
    Column("replay_mode", Text),
    Column("replay_of_order_id", UUID(as_uuid=True)),
    Column("market_data_snapshot_id", UUID(as_uuid=True)),
    _num("risk_amount"),
    Column("price_source", Text, nullable=False),
)

bar_batches = Table(
    "bar_batches", metadata,
    Column("batch_id", UUID(as_uuid=True), primary_key=True),
    Column("provider", Text, nullable=False),
    Column("provider_version", Text, nullable=False),
    Column("data_tier", Text, nullable=False),
    Column("request", JSONB, nullable=False),
    Column("content_hash", Text, nullable=False),
    _ts("ingested_at"),
)

bars_1m = Table(
    "bars_1m", metadata,
    Column("ticker", Text, primary_key=True),
    Column("ts", DateTime(timezone=True), primary_key=True),
    _num("open"), _num("high"), _num("low"), _num("close"), _num("volume"),
    Column("source", Text, primary_key=True),
    Column("batch_id", UUID(as_uuid=True), primary_key=True),
)

order_events = Table(
    "order_events", metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("order_id", UUID(as_uuid=True), nullable=False),
    Column("seq", Integer, nullable=False),
    Column("event_key", Text, nullable=False),
    Column("payload_hash", Text, nullable=False),
    Column("hash_material", Text, nullable=False),
    Column("type", Text, nullable=False),
    _ts("bar_ts", True),
    _num("price", True), _num("qty", True),
    Column("bar_batch_id", UUID(as_uuid=True)),
    Column("payload", JSONB, nullable=False),
    _ts("recorded_at"),
)

order_state = Table(
    "order_state", metadata,
    Column("order_id", UUID(as_uuid=True), primary_key=True),
    Column("status", Text, nullable=False),
    Column("zone_lost", Boolean, nullable=False),
    _ts("entry_eligible_from", True), _ts("trigger_hit_at", True),
    Column("entry_path", Text),
    _num("avg_entry", True), _num("initial_stop", True), _num("stop_current", True),
    _ts("stop_active_from", True),
    _num("qty_total"), _num("qty_open"), _num("realized_pnl"), _num("costs"), _num("r_multiple"),
    _num("mfe_r", True), _num("mae_r", True),
    _ts("opened_at", True), _ts("closed_at", True), _ts("final_event_ts", True), _ts("last_bar_ts", True),
    Column("expected_bars", Integer, nullable=False),
    Column("missing_bars", Integer, nullable=False),
    Column("needs_review", Boolean, nullable=False),
    Column("frozen", Boolean, nullable=False),
    Column("state_document", JSONB, nullable=False),
    Column("next_seq", Integer, nullable=False),
    _ts("updated_at"),
)

evaluation_runs = Table(
    "evaluation_runs", metadata,
    Column("run_id", UUID(as_uuid=True), primary_key=True),
    Column("kind", Text, nullable=False),
    _ts("data_as_of"),
    Column("code_version", Text, nullable=False),
    _ts("started_at"),
)

evaluation_run_status = Table(
    "evaluation_run_status", metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("run_id", UUID(as_uuid=True), nullable=False),
    Column("status", Text, nullable=False),
    _ts("recorded_at"),
    Column("detail", JSONB, nullable=False),
)

order_eval_segments = Table(
    "order_eval_segments", metadata,
    Column("order_id", UUID(as_uuid=True), primary_key=True),
    Column("run_id", UUID(as_uuid=True), primary_key=True),
    _ts("bar_from"), _ts("bar_to"),
    Column("selected_data_hash", Text, nullable=False),
    Column("first_seq", Integer, nullable=False),
    Column("event_count", Integer, nullable=False),
)

dividends = Table(
    "dividends", metadata,
    Column("ticker", Text, primary_key=True),
    Column("ex_date", Date, primary_key=True),
    _num("amount"),
    Column("pay_date", Date),
    Column("sources", ARRAY(Text), nullable=False),
    Column("validated", Boolean, nullable=False),
    _ts("checked_at"),
)

integrity_incidents = Table(
    "integrity_incidents", metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("kind", Text, nullable=False),
    Column("order_id", UUID(as_uuid=True)),
    Column("event_key", Text),
    Column("existing_hash", Text),
    Column("attempted_hash", Text),
    Column("detail", JSONB, nullable=False),
    _ts("recorded_at"),
)

APPEND_ONLY_TABLES = (
    "signals", "orders", "order_events", "evaluation_runs", "evaluation_run_status",
    "order_eval_segments", "bar_batches", "bars_1m", "market_data_snapshots", "integrity_incidents",
)
```

`alembic.ini`:
```ini
[alembic]
script_location = migrations
sqlalchemy.url =

[loggers]
keys = root

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARNING
handlers = console

[handler_console]
class = StreamHandler
args = (sys.stderr,)
formatter = generic

[formatter_generic]
format = %(levelname)s %(name)s %(message)s
```

`migrations/env.py`:
```python
"""Alembic environment: URL from -x/config or DATABASE_URL. Migrations are explicit SQL."""

from __future__ import annotations

import os

from alembic import context
from sqlalchemy import create_engine

config = context.config
url = config.get_main_option("sqlalchemy.url") or os.environ["DATABASE_URL"]

connectable = create_engine(url)
with connectable.connect() as connection:
    context.configure(connection=connection, target_metadata=None, transactional_ddl=True)
    with context.begin_transaction():
        context.run_migrations()
connectable.dispose()
```

`migrations/script.py.mako`:
```mako
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
"""

from alembic import op

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = None
depends_on = None


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```

`migrations/versions/0001_initial_schema.py`:
```python
"""Initial schema: append-only history, projections and market data (spec 3.1).

Revision ID: 0001
Revises:
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

APPEND_ONLY = (
    "signals", "orders", "order_events", "evaluation_runs", "evaluation_run_status",
    "order_eval_segments", "bar_batches", "bars_1m", "market_data_snapshots", "integrity_incidents",
)
MUTABLE = ("order_state", "dividends")

SCHEMA = """
CREATE TABLE signals (
  id uuid PRIMARY KEY,
  client_signal_id text NOT NULL UNIQUE,
  payload_hash text NOT NULL,
  created_at timestamptz NOT NULL,
  strategy text NOT NULL,
  strategy_version text NOT NULL,
  source text NOT NULL,
  ticker text NOT NULL,
  direction text NOT NULL CHECK (direction IN ('LONG', 'SHORT')),
  entry_zone_low numeric NOT NULL,
  entry_zone_high numeric NOT NULL,
  trigger_price numeric NULL,
  confirmation_note text NULL,
  target1 numeric NOT NULL,
  target2 numeric NULL,
  stop numeric NOT NULL,
  valid_sessions int NOT NULL CHECK (valid_sessions BETWEEN 1 AND 20),
  evaluation_start_ts timestamptz NOT NULL,
  valid_until_ts timestamptz NOT NULL,
  score numeric NULL,
  thesis text NULL,
  raw_payload jsonb NOT NULL
);

CREATE TABLE market_data_snapshots (
  id uuid PRIMARY KEY,
  created_at timestamptz NOT NULL,
  source text NOT NULL,
  data_as_of timestamptz NOT NULL,
  tickers text[] NOT NULL,
  range_from timestamptz NOT NULL,
  range_to timestamptz NOT NULL,
  content_manifest_hash text NOT NULL
);

CREATE TABLE orders (
  id uuid PRIMARY KEY,
  signal_id uuid NOT NULL REFERENCES signals(id),
  origin text NOT NULL CHECK (origin IN ('AUTO_STRATEGY', 'MANUAL_USER')),
  created_at timestamptz NOT NULL,
  evaluation_start_ts timestamptz NOT NULL,
  valid_until_ts timestamptz NOT NULL,
  fill_model_version text NOT NULL,
  config_snapshot jsonb NOT NULL,
  code_version text NOT NULL,
  replay boolean NOT NULL DEFAULT false,
  replay_mode text NULL CHECK (replay_mode IN ('REPRODUCE', 'RECALCULATE')),
  replay_of_order_id uuid NULL REFERENCES orders(id),
  market_data_snapshot_id uuid NULL REFERENCES market_data_snapshots(id),
  risk_amount numeric NOT NULL,
  price_source text NOT NULL,
  CHECK (
    (replay AND replay_mode IS NOT NULL AND replay_of_order_id IS NOT NULL)
    OR (NOT replay AND replay_mode IS NULL AND replay_of_order_id IS NULL
        AND market_data_snapshot_id IS NULL)
  )
);
CREATE INDEX orders_signal_idx ON orders (signal_id);

CREATE TABLE bar_batches (
  batch_id uuid PRIMARY KEY,
  provider text NOT NULL,
  provider_version text NOT NULL,
  data_tier text NOT NULL CHECK (data_tier IN ('RESEARCH', 'PRODUCTION')),
  request jsonb NOT NULL,
  content_hash text NOT NULL,
  ingested_at timestamptz NOT NULL
);

CREATE TABLE bars_1m (
  ticker text NOT NULL,
  ts timestamptz NOT NULL,
  open numeric NOT NULL,
  high numeric NOT NULL,
  low numeric NOT NULL,
  close numeric NOT NULL,
  volume numeric NOT NULL,
  source text NOT NULL,
  batch_id uuid NOT NULL REFERENCES bar_batches(batch_id),
  PRIMARY KEY (ticker, ts, source, batch_id)
);
CREATE INDEX bars_1m_lookup_idx ON bars_1m (ticker, source, ts);

CREATE TABLE order_events (
  id bigserial PRIMARY KEY,
  order_id uuid NOT NULL REFERENCES orders(id),
  seq int NOT NULL CHECK (seq >= 1),
  event_key text NOT NULL,
  payload_hash text NOT NULL,
  hash_material text NOT NULL,
  type text NOT NULL,
  bar_ts timestamptz NULL,
  price numeric NULL,
  qty numeric NULL,
  bar_batch_id uuid NULL REFERENCES bar_batches(batch_id),
  payload jsonb NOT NULL,
  recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (order_id, seq),
  UNIQUE (order_id, event_key)
);

CREATE TABLE order_state (
  order_id uuid PRIMARY KEY REFERENCES orders(id),
  status text NOT NULL,
  zone_lost boolean NOT NULL,
  entry_eligible_from timestamptz NULL,
  trigger_hit_at timestamptz NULL,
  entry_path text NULL CHECK (entry_path IN ('DIRECT', 'RECLAIMED')),
  avg_entry numeric NULL,
  initial_stop numeric NULL,
  stop_current numeric NULL,
  stop_active_from timestamptz NULL,
  qty_total numeric NOT NULL,
  qty_open numeric NOT NULL,
  realized_pnl numeric NOT NULL,
  costs numeric NOT NULL,
  r_multiple numeric NOT NULL,
  mfe_r numeric NULL,
  mae_r numeric NULL,
  opened_at timestamptz NULL,
  closed_at timestamptz NULL,
  final_event_ts timestamptz NULL,
  last_bar_ts timestamptz NULL,
  expected_bars int NOT NULL,
  missing_bars int NOT NULL,
  needs_review boolean NOT NULL,
  frozen boolean NOT NULL,
  state_document jsonb NOT NULL,
  next_seq int NOT NULL CHECK (next_seq >= 1),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE evaluation_runs (
  run_id uuid PRIMARY KEY,
  kind text NOT NULL CHECK (kind IN ('LIVE', 'REPLAY', 'ACTIONABILITY', 'END_OF_DAY')),
  data_as_of timestamptz NOT NULL,
  code_version text NOT NULL,
  started_at timestamptz NOT NULL
);

CREATE TABLE evaluation_run_status (
  id bigserial PRIMARY KEY,
  run_id uuid NOT NULL REFERENCES evaluation_runs(run_id),
  status text NOT NULL CHECK (status IN ('RUNNING', 'COMPLETED', 'FAILED')),
  recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  detail jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE order_eval_segments (
  order_id uuid NOT NULL REFERENCES orders(id),
  run_id uuid NOT NULL REFERENCES evaluation_runs(run_id),
  bar_from timestamptz NOT NULL,
  bar_to timestamptz NOT NULL,
  selected_data_hash text NOT NULL,
  first_seq int NOT NULL CHECK (first_seq >= 1),
  event_count int NOT NULL CHECK (event_count >= 0),
  PRIMARY KEY (order_id, run_id),
  CHECK (bar_to >= bar_from)
);

CREATE TABLE dividends (
  ticker text NOT NULL,
  ex_date date NOT NULL,
  amount numeric NOT NULL,
  pay_date date NULL,
  sources text[] NOT NULL,
  validated boolean NOT NULL,
  checked_at timestamptz NOT NULL,
  PRIMARY KEY (ticker, ex_date)
);

CREATE TABLE integrity_incidents (
  id bigserial PRIMARY KEY,
  kind text NOT NULL,
  order_id uuid NULL REFERENCES orders(id),
  event_key text NULL,
  existing_hash text NULL,
  attempted_hash text NULL,
  detail jsonb NOT NULL DEFAULT '{}'::jsonb,
  recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE FUNCTION reject_history_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'append-only table %: % rejected', TG_TABLE_NAME, TG_OP
    USING ERRCODE = 'restrict_violation';
END;
$$;
"""


def upgrade() -> None:
    op.execute(SCHEMA)
    for table in APPEND_ONLY:
        op.execute(
            f"CREATE TRIGGER {table}_no_update_delete BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_history_mutation()"
        )
        op.execute(
            f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION reject_history_mutation()"
        )
    op.execute(
        "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'vo_app') "
        "THEN CREATE ROLE vo_app NOLOGIN; END IF; END $$"
    )
    op.execute("GRANT USAGE ON SCHEMA public TO vo_app")
    op.execute(f"GRANT SELECT, INSERT ON {', '.join(APPEND_ONLY)} TO vo_app")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {', '.join(MUTABLE)} TO vo_app")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO vo_app")


def downgrade() -> None:
    for table in reversed(APPEND_ONLY + MUTABLE):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute("DROP FUNCTION IF EXISTS reject_history_mutation()")
```

- [ ] **Step 5: Rodar os testes de schema**

Run: `uv run pytest tests/integration/test_schema.py -v`
Expected: PASS (11 testes, contando os 3 parametrizados).

- [ ] **Step 6: Lint, tipos e suíte**

Run: `uv run ruff check src tests migrations && uv run mypy && uv run pytest -m "not slow"`
Expected: tudo verde; `tests/test_import_boundaries.py` agora coleta os arquivos de `virtual_orders/storage`.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock docker-compose.test.yml alembic.ini migrations src/virtual_orders tests/integration
git commit -m "feat(storage): Postgres schema with append-only triggers and per-test databases

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BKUdi9RGWWdXRXVYFWj1yp"
```

---

### Task 3: Codecs sem perda (`OrderState`, `FillConfig`, eventos preparados)

Notas, entradas 2 e 6. Módulo sem banco; testes unitários.

**Files:**
- Create: `src/virtual_orders/storage/codec.py`
- Create: `tests/storage/__init__.py`, `tests/storage/test_codec.py`

**Interfaces:**
- Consumes: `core.domain.hashing.canonical_json`, `core.domain.models.{OrderState, FillConfig, Event}`.
- Produces:
  - `to_document(value: Any) -> Any` — `json.loads(canonical_json(value))` (decimais viram strings, datetimes ISO UTC)
  - `parse_ts(value: str | None) -> datetime | None`
  - `state_to_document(state: OrderState) -> dict[str, Any]`; `state_from_document(doc: Mapping[str, Any]) -> OrderState`
  - `config_to_snapshot(config: FillConfig) -> dict[str, Any]`; `config_from_snapshot(doc: Mapping[str, Any]) -> FillConfig`
  - `@dataclass(frozen=True) PreparedEvent(type: str, event_key: str, bar_ts: datetime | None, price: Decimal | None, qty: Decimal | None, bar_batch_id: UUID | None, payload: dict[str, Any], hash_material: str, payload_hash: str)` com `identity() -> tuple[str, str]`
  - `material_hash(material: str) -> str`; `prepare_event(event: Event) -> PreparedEvent`

- [ ] **Step 1: Escrever os testes que falham**

`tests/storage/__init__.py`: vazio.

`tests/storage/test_codec.py`:
```python
import json
from dataclasses import fields, replace
from uuid import uuid4

import pytest

from core.domain.models import (
    CloseReason, Direction, EntryPath, Event, EventType, FillConfig, OrderState, OrderStatus,
    ZoneLostPolicy,
)
from core.fills import get_fill_model
from virtual_orders.storage import codec
from tests.support import D, bar, et, flat_bars, long_signal, make_ctx

V1 = get_fill_model("v1")
DAY = "2025-11-25"


def _partial_state() -> OrderState:
    ctx = make_ctx()
    bars = (
        flat_bars(ctx.calendar, et(DAY, "09:30"), et(DAY, "10:05"), 105)
        + [bar(et(DAY, "10:05"), 101, 101.5, 100.5, 101.2)]
        + flat_bars(ctx.calendar, et(DAY, "10:06"), et(DAY, "11:00"), 103)
        + [bar(et(DAY, "11:00"), 105, 106, 104.8, 105.5)]
    )
    state = V1.run_bars(V1.new_order_state(ctx).state, bars, ctx).state
    assert state.status is OrderStatus.PARTIAL
    return V1.freeze(state, "SPLIT", "2025-11-26").state


def _roundtrip(state: OrderState) -> OrderState:
    stored = json.loads(json.dumps(codec.state_to_document(state)))  # simulates jsonb
    return codec.state_from_document(stored)


def test_engine_state_roundtrips_exactly():
    state = _partial_state()
    assert state.stop_previous is not None and state.stop_active_from is not None
    assert state.frozen_reasons == ("SPLIT",) and state.review_reasons == ("SPLIT",)
    restored = _roundtrip(state)
    assert restored == state
    assert codec.state_to_document(restored) == codec.state_to_document(state)


def test_every_field_populated_roundtrips():
    ts = et(DAY, "12:00")
    state = replace(
        _partial_state(),
        status=OrderStatus.CLOSED, zone_lost=True, zone_ever_lost=True, entry_eligible_from=ts,
        trigger_hit_at=ts, entry_path=EntryPath.RECLAIMED, t1_done=True, dividends=D("0.26"),
        worst_price=D("99.5"), closed_at=ts, final_event_ts=ts, last_bar_ts=ts,
        close_reason=CloseReason.TARGET_FINAL, review_reasons=("B", "A"),
    )
    document = codec.state_to_document(state)
    assert all(value is not None for value in document.values())
    assert _roundtrip(state) == state
    assert _roundtrip(state).review_reasons == ("B", "A")


def test_every_order_state_field_is_mapped():
    document = codec.state_to_document(OrderState())
    assert set(document) == {f.name for f in fields(OrderState)}
    assert codec.state_from_document(document) == OrderState()


def test_document_with_missing_or_extra_field_is_rejected():
    document = codec.state_to_document(OrderState())
    missing = {k: v for k, v in document.items() if k != "t1_done"}
    with pytest.raises(ValueError, match="t1_done"):
        codec.state_from_document(missing)
    with pytest.raises(ValueError, match="bogus"):
        codec.state_from_document({**document, "bogus": 1})


def test_naive_timestamp_is_rejected():
    with pytest.raises(ValueError):
        codec.parse_ts("2025-11-25T15:00:00")


def test_config_snapshot_roundtrips_through_json():
    config = FillConfig(
        risk_amount=D("250.50"), stop_slippage_bps=D(7), commission_per_execution=D("1.25"),
        sec_taf_fees_enabled=True, sec_fee_rate=D("0.0000278"), zone_lost_policy=ZoneLostPolicy.CANCEL,
        target1_scale_out_pct=D(40), data_gap_minutes=15,
    )
    snapshot = json.loads(json.dumps(codec.config_to_snapshot(config)))
    restored = codec.config_from_snapshot(snapshot)
    assert restored == config
    assert codec.config_to_snapshot(restored) == codec.config_to_snapshot(config)


def test_prepared_event_keeps_hash_and_string_payload():
    batch = uuid4()
    event = Event(
        EventType.FILLED, "FILLED", et(DAY, "10:05"), price=D("101.00"), qty=D("25"),
        bar_batch_id=batch, payload={"rule": "ZONE_OPEN", "raw_price": D("101"), "cost": D(0)},
    )
    prepared = codec.prepare_event(event)
    assert prepared.payload_hash == event.payload_hash
    assert prepared.identity() == ("FILLED", event.payload_hash)
    assert codec.material_hash(prepared.hash_material) == event.payload_hash
    assert prepared.payload == {"rule": "ZONE_OPEN", "raw_price": "101", "cost": "0"}
    assert prepared.type == "FILLED" and prepared.bar_batch_id == batch


def test_short_signal_payload_document_is_float_free():
    document = codec.to_document(long_signal(direction=Direction.SHORT, entry_zone_low=D(100),
                                             entry_zone_high=D(102), stop=D(105), target1=D(95),
                                             target2=D(90)).as_payload())
    assert document["stop"] == "105"
    assert not any(isinstance(value, float) for value in document.values())
```

- [ ] **Step 2: Rodar para ver falhar**

Run: `uv run pytest tests/storage/test_codec.py -v`
Expected: FAIL com `ImportError: cannot import name 'codec'`.

- [ ] **Step 3: Implementar**

`src/virtual_orders/storage/codec.py`:
```python
"""Lossless documents for OrderState, FillConfig and prepared events.

Plan 1 close-out entries 2 (projection keeps every OrderState field) and 6 (hash computed once
from the canonical material, never from re-read jsonb).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, fields
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

from core.domain.hashing import canonical_json
from core.domain.models import CloseReason, EntryPath, Event, FillConfig, OrderState, OrderStatus

_DATETIME_FIELDS = frozenset({
    "entry_eligible_from", "trigger_hit_at", "stop_active_from", "opened_at", "closed_at",
    "final_event_ts", "last_bar_ts",
})
_DECIMAL_FIELDS = frozenset({
    "avg_entry", "initial_stop", "stop_current", "stop_previous", "qty_total", "qty_open",
    "realized_pnl", "costs", "dividends", "best_price", "worst_price",
})
_BOOL_FIELDS = frozenset({"zone_lost", "zone_ever_lost", "t1_done", "frozen"})
_TUPLE_FIELDS = frozenset({"frozen_reasons", "review_reasons"})
_ENUM_FIELDS: dict[str, type[StrEnum]] = {
    "status": OrderStatus, "entry_path": EntryPath, "close_reason": CloseReason,
}


def to_document(value: Any) -> Any:
    return json.loads(canonical_json(value))


def parse_ts(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp without timezone: {value!r}")
    return parsed


def state_to_document(state: OrderState) -> dict[str, Any]:
    document: dict[str, Any] = to_document({f.name: getattr(state, f.name) for f in fields(state)})
    return document


def state_from_document(doc: Mapping[str, Any]) -> OrderState:
    expected = {f.name for f in fields(OrderState)}
    if set(doc) != expected:
        raise ValueError(
            f"state document fields mismatch: missing={sorted(expected - set(doc))} "
            f"extra={sorted(set(doc) - expected)}"
        )
    values: dict[str, Any] = {}
    for name, raw in doc.items():
        if name in _TUPLE_FIELDS:
            values[name] = tuple(raw)
        elif raw is None:
            values[name] = None
        elif name in _DATETIME_FIELDS:
            values[name] = parse_ts(raw)
        elif name in _DECIMAL_FIELDS:
            values[name] = Decimal(raw)
        elif name in _ENUM_FIELDS:
            values[name] = _ENUM_FIELDS[name](raw)
        elif name in _BOOL_FIELDS:
            if not isinstance(raw, bool):
                raise TypeError(f"{name} must be bool, got {type(raw).__name__}")
            values[name] = raw
        else:
            raise ValueError(f"unmapped OrderState field: {name}")
    return OrderState(**values)


def config_to_snapshot(config: FillConfig) -> dict[str, Any]:
    snapshot: dict[str, Any] = to_document(config.snapshot())
    return snapshot


def config_from_snapshot(doc: Mapping[str, Any]) -> FillConfig:
    return FillConfig(**dict(doc))


@dataclass(frozen=True)
class PreparedEvent:
    type: str
    event_key: str
    bar_ts: datetime | None
    price: Decimal | None
    qty: Decimal | None
    bar_batch_id: UUID | None
    payload: dict[str, Any]
    hash_material: str
    payload_hash: str

    def identity(self) -> tuple[str, str]:
        return (self.event_key, self.payload_hash)


def material_hash(material: str) -> str:
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def prepare_event(event: Event) -> PreparedEvent:
    material = canonical_json(event.hash_material())
    return PreparedEvent(
        type=event.type.value,
        event_key=event.event_key,
        bar_ts=event.bar_ts,
        price=event.price,
        qty=event.qty,
        bar_batch_id=event.bar_batch_id,
        payload=json.loads(material)["payload"],
        hash_material=material,
        payload_hash=material_hash(material),
    )
```

- [ ] **Step 4: Rodar os testes**

Run: `uv run pytest tests/storage/test_codec.py -v`
Expected: PASS (8 testes).

- [ ] **Step 5: Lint, tipos e commit**

Run: `uv run ruff check src tests && uv run mypy`
Expected: verde.

```bash
git add src/virtual_orders/storage/codec.py tests/storage
git commit -m "feat(storage): lossless OrderState/FillConfig documents and prepared events

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BKUdi9RGWWdXRXVYFWj1yp"
```

---

### Task 4: Contratos neutros, `MarketDataGateway` e adapters HTTP (Alpaca, FMP) com fixtures

Spec 4.1, 4.7, 6 (retry), 7 ("Dados de mercado"); D8, D11; Global Constraints (market data neutra, sem troca silenciosa). Nenhum teste acessa a rede: `httpx.MockTransport` serve arquivos de `tests/fixtures/`.

> As fixtures abaixo são **synthetic/documentation-derived fixture**: construídas a partir do formato documentado das APIs, não são capturas reais. Ficam catalogadas em `tests/fixtures/README.md` (verificado por teste). Fixtures reais sanitizadas podem ser acrescentadas em outra etapa, sem substituir estas.

**Files:**
- Create: `src/virtual_orders/marketdata/__init__.py`, `src/virtual_orders/marketdata/sources.py`, `src/virtual_orders/marketdata/gateway.py`, `src/virtual_orders/marketdata/http.py`, `src/virtual_orders/marketdata/alpaca.py`, `src/virtual_orders/marketdata/fmp.py`
- Create: `tests/fixtures/README.md`, `tests/fixtures/alpaca/bars_page1.json`, `tests/fixtures/alpaca/bars_page2.json`, `tests/fixtures/alpaca/corporate_actions.json`, `tests/fixtures/fmp/dividends.json`
- Create: `tests/marketdata/test_http_sources.py`

**Interfaces:**
- Consumes: `core.domain.models.Bar`.
- Produces (`virtual_orders.marketdata.sources`):
  - `class DataTier(StrEnum)`: `RESEARCH`, `PRODUCTION` (único conjunto fechado; providers e fontes são `str` livres)
  - `class SourceUnavailable(Exception)`; `class SourceDataError(Exception)`
  - `@dataclass(frozen=True) RawBar(ticker: str, ts: datetime, open: Decimal, high: Decimal, low: Decimal, close: Decimal, volume: Decimal)` com `to_bar(batch_id: UUID | None = None) -> Bar`
  - `Protocol BarSource`: atributos `provider: str`, `provider_version: str`, `data_tier: DataTier`, `source: str` (id estável do feed de preços, ex. `alpaca_iex`); `fetch_bars(ticker: str, start: datetime, end: datetime) -> list[RawBar]` (candles com `start ≤ ts < end`)
  - `@dataclass(frozen=True) DividendRecord(ticker: str, ex_date: date, amount: Decimal, pay_date: date | None)`; `Protocol DividendSource`: `name: str`; `fetch_dividends(ticker: str, start: date, end: date) -> list[DividendRecord]` (ex-date inclusiva nas duas pontas)
  - `@dataclass(frozen=True) SplitRecord(ticker: str, ex_date: date, old_rate: Decimal, new_rate: Decimal)`; `Protocol SplitSource`: `fetch_splits(tickers: Sequence[str], start: date, end: date) -> list[SplitRecord]`
  - `Protocol ReferenceSource`: `fetch_minute_bars(ticker: str, day: date) -> dict[datetime, Bar]`; `fetch_daily_range(ticker: str, day: date) -> tuple[Decimal, Decimal] | None` (`(high, low)`)
- Produces (`virtual_orders.marketdata.gateway`): `UnknownDataSource(LookupError)`; `MarketDataGateway(bar_sources: Iterable[BarSource] = ())` com `source_ids -> tuple[str, ...]` e `bar_source(source_id: str) -> BarSource` (resolução exata; **sem fallback**; id duplicado → `ValueError`)
- Produces (`virtual_orders.marketdata.http`): `get_json(client, url, *, params, headers=None, attempts=3, backoff_seconds=1.0, sleep=time.sleep) -> Any`; `vendor_decimal(value: Any, name: str) -> Decimal`
- Produces: `virtual_orders.marketdata.alpaca.ALPACA_IEX_SOURCE = "alpaca_iex"`; `AlpacaBars(client, key_id, secret_key, *, base_url=ALPACA_DATA_URL, attempts=3, backoff_seconds=1.0, sleep=time.sleep)` (implementa `BarSource`, `source = ALPACA_IEX_SOURCE`), `AlpacaSplits(...)` mesma assinatura (implementa `SplitSource`); `virtual_orders.marketdata.fmp.FmpDividends(client, api_key, *, base_url=FMP_URL, attempts=3, backoff_seconds=1.0, sleep=time.sleep)` (implementa `DividendSource`, `name = "fmp"`)

- [ ] **Step 1: Criar as fixtures**

Run: `uv add "httpx>=0.28" && mkdir -p tests/fixtures/alpaca tests/fixtures/fmp tests/fixtures/yfinance`

`tests/fixtures/README.md`:
```markdown
# Fixtures de providers

Toda fixture deste diretório é **synthetic/documentation-derived fixture**: foi construída a partir do formato
documentado da API do provider e **não** é captura real de rede. Testes normais nunca acessam a rede.
Fixtures reais sanitizadas, quando existirem, entram com rótulo próprio e não substituem estas.

| Arquivo | Rótulo | Formato de referência |
|---|---|---|
| `alpaca/bars_page1.json` | synthetic/documentation-derived fixture | Alpaca Market Data v2 `GET /v2/stocks/{symbol}/bars` (página com `next_page_token`) |
| `alpaca/bars_page2.json` | synthetic/documentation-derived fixture | Alpaca Market Data v2 `GET /v2/stocks/{symbol}/bars` (última página) |
| `alpaca/corporate_actions.json` | synthetic/documentation-derived fixture | Alpaca `GET /v1/corporate-actions` (forward/reverse splits) |
| `fmp/dividends.json` | synthetic/documentation-derived fixture | FMP `GET /stable/dividends` |
```

`tests/fixtures/alpaca/bars_page1.json`:
```json
{"bars":[{"t":"2025-11-25T14:30:00Z","o":101.25,"h":101.5,"l":101.1,"c":101.4,"v":1200,"n":15,"vw":101.3},{"t":"2025-11-25T14:31:00Z","o":101.4,"h":101.6,"l":101.35,"c":101.55,"v":900,"n":11,"vw":101.5}],"symbol":"AAPL","next_page_token":"QUFQTHxNfDIwMjUtMTEtMjVUMTQ6MzI6MDBa"}
```

`tests/fixtures/alpaca/bars_page2.json`:
```json
{"bars":[{"t":"2025-11-25T14:32:00Z","o":101.55,"h":101.7,"l":101.5,"c":101.65,"v":700,"n":9,"vw":101.6},{"t":"2025-11-25T14:40:00Z","o":102.0,"h":102.1,"l":101.9,"c":102.05,"v":500,"n":6,"vw":102.0}],"symbol":"AAPL","next_page_token":null}
```

`tests/fixtures/alpaca/corporate_actions.json`:
```json
{"corporate_actions":{"forward_splits":[{"id":"fs1","symbol":"NVDA","cusip":"67066G104","new_rate":10,"old_rate":1,"process_date":"2024-06-10","ex_date":"2024-06-10","record_date":"2024-06-06","payable_date":"2024-06-07"}],"reverse_splits":[{"id":"rs1","symbol":"XYZ","cusip":"000000000","new_rate":1,"old_rate":20,"process_date":"2025-11-26","ex_date":"2025-11-26","record_date":"2025-11-25","payable_date":"2025-11-25"}]},"next_page_token":null}
```

`tests/fixtures/fmp/dividends.json`:
```json
[{"symbol":"AAPL","date":"2025-11-10","recordDate":"2025-11-10","paymentDate":"2025-11-13","declarationDate":"2025-10-30","adjDividend":0.26,"dividend":0.26,"yield":0.39,"frequency":"Quarterly"},{"symbol":"AAPL","date":"2025-08-11","recordDate":"2025-08-11","paymentDate":"","declarationDate":"2025-07-31","adjDividend":0.26,"dividend":0.26,"yield":0.44,"frequency":"Quarterly"}]
```

- [ ] **Step 2: Escrever os testes que falham**

`tests/marketdata/test_http_sources.py`:
```python
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from virtual_orders.marketdata.alpaca import ALPACA_IEX_SOURCE, AlpacaBars, AlpacaSplits
from virtual_orders.marketdata.fmp import FmpDividends
from virtual_orders.marketdata.gateway import MarketDataGateway, UnknownDataSource
from virtual_orders.marketdata.http import get_json
from virtual_orders.marketdata.sources import DataTier, SourceDataError, SourceUnavailable

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def utc(hour: int, minute: int) -> datetime:
    return datetime(2025, 11, 25, hour, minute, tzinfo=UTC)


def test_alpaca_bars_paginates_parses_decimals_and_filters_end():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        page = "bars_page2.json" if request.url.params.get("page_token") else "bars_page1.json"
        return httpx.Response(200, text=fixture(f"alpaca/{page}"))

    source = AlpacaBars(client(handler), "key", "secret")
    bars = source.fetch_bars("AAPL", utc(14, 30), utc(14, 40))

    assert [b.ts for b in bars] == [utc(14, 30), utc(14, 31), utc(14, 32)]
    assert bars[0].open == Decimal("101.25") and isinstance(bars[0].volume, Decimal)
    assert bars[0].to_bar().low == Decimal("101.1")
    first = seen[0]
    assert first.url.path == "/v2/stocks/AAPL/bars"
    assert first.headers["APCA-API-KEY-ID"] == "key"
    assert first.url.params["adjustment"] == "raw"
    assert first.url.params["feed"] == "iex"
    assert first.url.params["timeframe"] == "1Min"
    assert first.url.params["start"] == "2025-11-25T14:30:00Z"
    assert seen[1].url.params["page_token"] == "QUFQTHxNfDIwMjUtMTEtMjVUMTQ6MzI6MDBa"
    assert (source.provider, source.source, source.data_tier) == ("alpaca", ALPACA_IEX_SOURCE, DataTier.RESEARCH)


def test_retry_with_backoff_then_success():
    sleeps: list[float] = []
    responses = iter([httpx.Response(503), httpx.Response(429), httpx.Response(200, text='{"ok": 1.5}')])
    body = get_json(client(lambda request: next(responses)), "https://x.test/a", params={},
                    sleep=sleeps.append)
    assert body == {"ok": Decimal("1.5")}
    assert sleeps == [1.0, 2.0]


def test_retry_exhaustion_raises_unavailable():
    sleeps: list[float] = []
    with pytest.raises(SourceUnavailable, match="after 3 attempts"):
        get_json(client(lambda request: httpx.Response(500)), "https://x.test/a", params={},
                 sleep=sleeps.append)
    assert sleeps == [1.0, 2.0]


def test_transport_errors_are_retried():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ConnectError("boom", request=request)
        return httpx.Response(200, text="[]")

    assert get_json(client(handler), "https://x.test/a", params={}, sleep=lambda s: None) == []
    assert len(calls) == 2


def test_non_retryable_status_fails_immediately():
    sleeps: list[float] = []
    with pytest.raises(SourceUnavailable, match="HTTP 403"):
        get_json(client(lambda request: httpx.Response(403)), "https://x.test/a", params={},
                 sleep=sleeps.append)
    assert sleeps == []


def test_invalid_vendor_values_raise_data_error():
    bad = '{"bars":[{"t":"2025-11-25T14:30:00Z","o":"abc","h":1,"l":1,"c":1,"v":1}],"next_page_token":null}'
    source = AlpacaBars(client(lambda request: httpx.Response(200, text=bad)), "k", "s")
    with pytest.raises(SourceDataError):
        source.fetch_bars("AAPL", utc(14, 30), utc(14, 40))


def test_alpaca_splits_parses_forward_and_reverse():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text=fixture("alpaca/corporate_actions.json"))

    splits = AlpacaSplits(client(handler), "k", "s").fetch_splits(["XYZ", "NVDA"], date(2024, 1, 1), date(2025, 12, 31))
    assert [(s.ticker, s.ex_date, s.old_rate, s.new_rate) for s in splits] == [
        ("NVDA", date(2024, 6, 10), Decimal(1), Decimal(10)),
        ("XYZ", date(2025, 11, 26), Decimal(20), Decimal(1)),
    ]
    assert seen[0].url.path == "/v1/corporate-actions"
    assert seen[0].url.params["symbols"] == "NVDA,XYZ"
    assert seen[0].url.params["types"] == "forward_split,reverse_split"
    assert AlpacaSplits(client(handler), "k", "s").fetch_splits([], date(2024, 1, 1), date(2024, 1, 2)) == []


def test_fmp_dividends_filters_by_ex_date_and_parses_optional_pay_date():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text=fixture("fmp/dividends.json"))

    source = FmpDividends(client(handler), "fmp-key")
    records = source.fetch_dividends("AAPL", date(2025, 8, 11), date(2025, 11, 10))
    assert [(r.ex_date, r.amount, r.pay_date) for r in records] == [
        (date(2025, 8, 11), Decimal("0.26"), None),
        (date(2025, 11, 10), Decimal("0.26"), date(2025, 11, 13)),
    ]
    assert seen[0].url.path == "/stable/dividends"
    assert seen[0].url.params["symbol"] == "AAPL" and seen[0].url.params["apikey"] == "fmp-key"
    assert source.name == "fmp"


class _Feed:
    provider, provider_version, data_tier = "any", "t", DataTier.RESEARCH

    def __init__(self, source: str) -> None:
        self.source = source

    def fetch_bars(self, ticker, start, end):
        return []


def test_gateway_resolves_exact_source_without_fallback():
    primary, backup = _Feed("primary_feed"), _Feed("backup_feed")
    gateway = MarketDataGateway([primary, backup])
    assert gateway.bar_source("primary_feed") is primary
    assert gateway.source_ids == ("backup_feed", "primary_feed")
    with pytest.raises(UnknownDataSource, match="fallback is not allowed"):
        MarketDataGateway([backup]).bar_source("primary_feed")


def test_gateway_rejects_duplicate_source_ids():
    with pytest.raises(ValueError, match="duplicate"):
        MarketDataGateway([_Feed("same"), _Feed("same")])


def test_fixture_manifest_labels_every_fixture_as_synthetic():
    manifest = (FIXTURES / "README.md").read_text()
    labelled = {
        line.split("`")[1] for line in manifest.splitlines()
        if line.startswith("| `") and "synthetic/documentation-derived fixture" in line
    }
    files = {str(p.relative_to(FIXTURES)) for p in FIXTURES.rglob("*") if p.is_file() and p.name != "README.md"}
    assert files and files <= labelled
```

- [ ] **Step 3: Rodar para ver falhar**

Run: `uv run pytest tests/marketdata/test_http_sources.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'virtual_orders.marketdata.alpaca'`.

- [ ] **Step 4: Implementar**

`src/virtual_orders/marketdata/sources.py`:
```python
"""Provider-neutral market-data contracts. Adapters do network I/O; everything returned is Decimal and UTC.

Small capability interfaces: a provider implements only what it offers. Provider payloads never cross
these types; `provider`, `provider_version` and `source` are free text so new providers need no migration.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from core.domain.models import Bar


class DataTier(StrEnum):
    RESEARCH = "RESEARCH"
    PRODUCTION = "PRODUCTION"


class SourceUnavailable(Exception):
    """Provider unreachable, rate limited or refusing the request."""


class SourceDataError(Exception):
    """Provider answered with data that cannot be trusted (malformed, inconsistent)."""


@dataclass(frozen=True)
class RawBar:
    ticker: str
    ts: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    def to_bar(self, batch_id: UUID | None = None) -> Bar:
        return Bar(ts=self.ts, open=self.open, high=self.high, low=self.low, close=self.close,
                   volume=self.volume, batch_id=batch_id)


class BarSource(Protocol):
    provider: str
    provider_version: str
    data_tier: DataTier
    source: str

    def fetch_bars(self, ticker: str, start: datetime, end: datetime) -> list[RawBar]: ...


@dataclass(frozen=True)
class DividendRecord:
    ticker: str
    ex_date: date
    amount: Decimal
    pay_date: date | None


class DividendSource(Protocol):
    name: str

    def fetch_dividends(self, ticker: str, start: date, end: date) -> list[DividendRecord]: ...


@dataclass(frozen=True)
class SplitRecord:
    ticker: str
    ex_date: date
    old_rate: Decimal
    new_rate: Decimal


class SplitSource(Protocol):
    def fetch_splits(self, tickers: Sequence[str], start: date, end: date) -> list[SplitRecord]: ...


class ReferenceSource(Protocol):
    def fetch_minute_bars(self, ticker: str, day: date) -> dict[datetime, Bar]: ...

    def fetch_daily_range(self, ticker: str, day: date) -> tuple[Decimal, Decimal] | None: ...
```

`src/virtual_orders/marketdata/__init__.py`: vazio.

`src/virtual_orders/marketdata/gateway.py`:
```python
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
```

`src/virtual_orders/marketdata/http.py`:
```python
"""JSON GET with bounded retries (spec 6: 3 attempts with backoff) and Decimal parsing."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from decimal import Decimal
from typing import Any

import httpx

from virtual_orders.marketdata.sources import SourceDataError, SourceUnavailable

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


def get_json(
    client: httpx.Client,
    url: str,
    *,
    params: Mapping[str, str | int],
    headers: Mapping[str, str] | None = None,
    attempts: int = 3,
    backoff_seconds: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
) -> Any:
    last_error = ""
    for attempt in range(attempts):
        try:
            response = client.get(url, params=dict(params), headers=dict(headers or {}))
        except httpx.TransportError as exc:
            last_error = f"transport error: {exc}"
        else:
            if response.status_code == 200:
                try:
                    return json.loads(response.text, parse_float=Decimal)
                except ValueError as exc:
                    raise SourceDataError(f"invalid JSON from {url}") from exc
            if response.status_code not in RETRYABLE_STATUS:
                raise SourceUnavailable(f"{url} returned HTTP {response.status_code}")
            last_error = f"HTTP {response.status_code}"
        if attempt + 1 < attempts:
            sleep(backoff_seconds * 2**attempt)
    raise SourceUnavailable(f"{url} failed after {attempts} attempts: {last_error}")


def vendor_decimal(value: Any, name: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, int | Decimal):
        raise SourceDataError(f"{name} is not numeric: {value!r}")
    result = Decimal(value)
    if not result.is_finite():
        raise SourceDataError(f"{name} is not finite: {value!r}")
    return result
```

`src/virtual_orders/marketdata/alpaca.py`:
```python
"""Alpaca Market Data v2 minute bars (IEX, raw) and corporate-action splits over HTTP."""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
from typing import Any

import httpx

from virtual_orders.marketdata.http import get_json, vendor_decimal
from virtual_orders.marketdata.sources import DataTier, RawBar, SourceDataError, SplitRecord

ALPACA_DATA_URL = "https://data.alpaca.markets"
ALPACA_IEX_SOURCE = "alpaca_iex"


def _iso_z(ts: datetime) -> str:
    return ts.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_ts(value: Any) -> datetime:
    if not isinstance(value, str):
        raise SourceDataError(f"timestamp is not a string: {value!r}")
    ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if ts.tzinfo is None:
        raise SourceDataError(f"timestamp without timezone: {value!r}")
    return ts.astimezone(UTC)


class _AlpacaClient:
    def __init__(
        self,
        client: httpx.Client,
        key_id: str,
        secret_key: str,
        *,
        base_url: str = ALPACA_DATA_URL,
        attempts: int = 3,
        backoff_seconds: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = client
        self._headers = {"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret_key}
        self._base_url = base_url.rstrip("/")
        self._attempts = attempts
        self._backoff = backoff_seconds
        self._sleep = sleep

    def _pages(self, path: str, params: dict[str, str | int]) -> list[Any]:
        pages: list[Any] = []
        token: str | None = None
        while True:
            page_params = dict(params)
            if token:
                page_params["page_token"] = token
            body = get_json(
                self._client, f"{self._base_url}{path}", params=page_params, headers=self._headers,
                attempts=self._attempts, backoff_seconds=self._backoff, sleep=self._sleep,
            )
            if not isinstance(body, dict):
                raise SourceDataError(f"unexpected Alpaca body for {path}")
            pages.append(body)
            token = body.get("next_page_token")
            if not token:
                return pages


class AlpacaBars(_AlpacaClient):
    provider = "alpaca"
    provider_version = "market-data-v2/bars/iex/raw"
    data_tier = DataTier.RESEARCH
    source = ALPACA_IEX_SOURCE

    def fetch_bars(self, ticker: str, start: datetime, end: datetime) -> list[RawBar]:
        params: dict[str, str | int] = {
            "timeframe": "1Min", "start": _iso_z(start), "end": _iso_z(end), "adjustment": "raw",
            "feed": "iex", "limit": 10000, "sort": "asc",
        }
        bars: list[RawBar] = []
        for page in self._pages(f"/v2/stocks/{ticker}/bars", params):
            for item in page.get("bars") or []:
                try:
                    ts = _parse_ts(item["t"])
                    raw = RawBar(
                        ticker=ticker, ts=ts,
                        open=vendor_decimal(item["o"], "o"), high=vendor_decimal(item["h"], "h"),
                        low=vendor_decimal(item["l"], "l"), close=vendor_decimal(item["c"], "c"),
                        volume=vendor_decimal(item["v"], "v"),
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    raise SourceDataError(f"malformed Alpaca bar for {ticker}: {item!r}") from exc
                if start <= ts < end:
                    bars.append(raw)
        return bars


class AlpacaSplits(_AlpacaClient):
    def fetch_splits(self, tickers: Sequence[str], start: date, end: date) -> list[SplitRecord]:
        if not tickers:
            return []
        params: dict[str, str | int] = {
            "symbols": ",".join(sorted(set(tickers))), "types": "forward_split,reverse_split",
            "start": start.isoformat(), "end": end.isoformat(), "limit": 1000,
        }
        records: list[SplitRecord] = []
        for page in self._pages("/v1/corporate-actions", params):
            actions = page.get("corporate_actions") or {}
            for kind in ("forward_splits", "reverse_splits"):
                for item in actions.get(kind) or []:
                    try:
                        records.append(SplitRecord(
                            ticker=item["symbol"], ex_date=date.fromisoformat(item["ex_date"]),
                            old_rate=vendor_decimal(item["old_rate"], "old_rate"),
                            new_rate=vendor_decimal(item["new_rate"], "new_rate"),
                        ))
                    except (KeyError, TypeError, ValueError) as exc:
                        raise SourceDataError(f"malformed Alpaca split: {item!r}") from exc
        return sorted(records, key=lambda r: (r.ex_date, r.ticker))
```

`src/virtual_orders/marketdata/fmp.py`:
```python
"""FMP dividends (free tier `stable/dividends`)."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import date

import httpx

from virtual_orders.marketdata.http import get_json, vendor_decimal
from virtual_orders.marketdata.sources import DividendRecord, SourceDataError

FMP_URL = "https://financialmodelingprep.com"


class FmpDividends:
    name = "fmp"

    def __init__(
        self,
        client: httpx.Client,
        api_key: str,
        *,
        base_url: str = FMP_URL,
        attempts: int = 3,
        backoff_seconds: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = client
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._attempts = attempts
        self._backoff = backoff_seconds
        self._sleep = sleep

    def fetch_dividends(self, ticker: str, start: date, end: date) -> list[DividendRecord]:
        body = get_json(
            self._client, f"{self._base_url}/stable/dividends",
            params={"symbol": ticker, "apikey": self._api_key},
            attempts=self._attempts, backoff_seconds=self._backoff, sleep=self._sleep,
        )
        if not isinstance(body, list):
            raise SourceDataError(f"unexpected FMP dividends body for {ticker}")
        records: list[DividendRecord] = []
        for item in body:
            try:
                ex_date = date.fromisoformat(item["date"])
                pay_raw = item.get("paymentDate")
                record = DividendRecord(
                    ticker=ticker, ex_date=ex_date,
                    amount=vendor_decimal(item["dividend"], "dividend"),
                    pay_date=date.fromisoformat(pay_raw) if pay_raw else None,
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise SourceDataError(f"malformed FMP dividend for {ticker}: {item!r}") from exc
            if start <= ex_date <= end:
                records.append(record)
        return sorted(records, key=lambda r: r.ex_date)
```

- [ ] **Step 5: Rodar os testes**

Run: `uv run pytest tests/marketdata/test_http_sources.py -v`
Expected: PASS (11 testes).

- [ ] **Step 6: Lint, tipos e commit**

Run: `uv run ruff check src tests && uv run mypy && uv run pytest tests/test_import_boundaries.py`
Expected: verde.

```bash
git add pyproject.toml uv.lock src/virtual_orders/marketdata/__init__.py src/virtual_orders/marketdata/sources.py src/virtual_orders/marketdata/gateway.py src/virtual_orders/marketdata/http.py tests/fixtures/README.md src/virtual_orders/marketdata/alpaca.py src/virtual_orders/marketdata/fmp.py tests/fixtures/alpaca tests/fixtures/fmp tests/marketdata/test_http_sources.py
git commit -m "feat(marketdata): provider-neutral contracts, gateway without fallback, Alpaca/FMP adapters

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BKUdi9RGWWdXRXVYFWj1yp"
```

---

### Task 5: Adapter yfinance (referência de minuto, faixa diária e dividendos)

Spec 4.6 (verificação de minutos ausentes, conferência diária), 4.7 (segunda fonte de dividendos). yfinance nunca fornece preço de fill (4.1).

**Files:**
- Create: `src/virtual_orders/marketdata/yfinance_source.py`
- Modify: `tests/fixtures/README.md` (catalogar as fixtures novas)
- Create: `tests/fixtures/yfinance/aapl_1m_2025-11-25.csv`, `tests/fixtures/yfinance/aapl_1d_2025-11.csv`
- Create: `tests/marketdata/test_yfinance_source.py`

**Interfaces:**
- Consumes: `virtual_orders.marketdata.sources.{DividendRecord, SourceDataError, SourceUnavailable}`, `core.domain.models.Bar`.
- Produces:
  - `HistoryFn = Callable[[str, date, date, str], pandas.DataFrame]` — `(ticker, start, end_exclusive, interval)`; colunas `Open, High, Low, Close, Volume, Dividends`; índice tz-aware
  - `download_history(ticker, start, end, interval) -> DataFrame` (única função que chama a rede)
  - `YFinanceSource(history: HistoryFn = download_history)` com `name = "yfinance"`, `fetch_minute_bars(ticker, day) -> dict[datetime, Bar]`, `fetch_daily_range(ticker, day) -> tuple[Decimal, Decimal] | None`, `fetch_dividends(ticker, start, end) -> list[DividendRecord]` — implementa `ReferenceSource` e `DividendSource`
  - Preços quantizados em `Decimal("0.0001")`; dividendos em `Decimal("0.000001")`

- [ ] **Step 1: Criar as fixtures**

Run: `uv add "yfinance>=0.2.50"`

Acrescentar à tabela de `tests/fixtures/README.md`:
```markdown
| `yfinance/aapl_1m_2025-11-25.csv` | synthetic/documentation-derived fixture | `yfinance.Ticker.history(interval="1m", auto_adjust=False, actions=True)` |
| `yfinance/aapl_1d_2025-11.csv` | synthetic/documentation-derived fixture | `yfinance.Ticker.history(interval="1d", auto_adjust=False, actions=True)` |
```

`tests/fixtures/yfinance/aapl_1m_2025-11-25.csv`:
```csv
Datetime,Open,High,Low,Close,Volume,Dividends,Stock Splits
2025-11-25 09:30:00-05:00,101.25,101.5,101.0999984741211,101.4000015258789,120000,0.0,0.0
2025-11-25 09:31:00-05:00,101.4000015258789,101.5999984741211,101.3499984741211,101.55000305175781,90000,0.0,0.0
2025-11-25 09:32:00-05:00,,,,,,0.0,0.0
2025-11-25 09:33:00-05:00,101.6,101.8,101.5,101.7,80000,0.0,0.0
```

`tests/fixtures/yfinance/aapl_1d_2025-11.csv`:
```csv
Date,Open,High,Low,Close,Volume,Dividends,Stock Splits
2025-11-07 00:00:00-05:00,269.79,272.29,266.77,268.47,48227400,0.0,0.0
2025-11-10 00:00:00-05:00,268.96,273.73,267.46,269.43,41312400,0.26,0.0
2025-11-25 00:00:00-05:00,275.27,280.3800048828125,275.25,276.97,46914200,0.0,0.0
```

- [ ] **Step 2: Escrever os testes que falham**

`tests/marketdata/test_yfinance_source.py`:
```python
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from virtual_orders.marketdata.sources import SourceUnavailable
from virtual_orders.marketdata.yfinance_source import YFinanceSource

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "yfinance"


def load(name: str) -> pd.DataFrame:
    frame = pd.read_csv(FIXTURES / name)
    stamps = pd.to_datetime(frame.pop(frame.columns[0]), utc=True)
    frame.index = pd.DatetimeIndex(stamps).tz_convert("America/New_York")
    return frame


def recorded_history(calls: list[tuple]):
    minute, daily = load("aapl_1m_2025-11-25.csv"), load("aapl_1d_2025-11.csv")

    def history(ticker: str, start: date, end: date, interval: str) -> pd.DataFrame:
        calls.append((ticker, start, end, interval))
        frame = minute if interval == "1m" else daily
        days = frame.index.date
        return frame[(days >= start) & (days < end)]

    return history


def test_minute_bars_are_utc_decimal_and_skip_empty_rows():
    calls: list[tuple] = []
    bars = YFinanceSource(recorded_history(calls)).fetch_minute_bars("AAPL", date(2025, 11, 25))
    open_utc = datetime(2025, 11, 25, 14, 30, tzinfo=UTC)
    assert sorted(bars) == [open_utc, open_utc.replace(minute=31), open_utc.replace(minute=33)]
    first = bars[open_utc]
    assert first.low == Decimal("101.1000") and first.close == Decimal("101.4000")
    assert first.volume == Decimal(120000) and first.ts == open_utc
    assert calls == [("AAPL", date(2025, 11, 25), date(2025, 11, 26), "1m")]


def test_daily_range_for_session_or_none():
    source = YFinanceSource(recorded_history([]))
    assert source.fetch_daily_range("AAPL", date(2025, 11, 25)) == (Decimal("280.38"), Decimal("275.25"))
    assert source.fetch_daily_range("AAPL", date(2025, 11, 26)) is None


def test_dividends_from_daily_actions():
    source = YFinanceSource(recorded_history([]))
    records = source.fetch_dividends("AAPL", date(2025, 11, 1), date(2025, 11, 30))
    assert [(r.ticker, r.ex_date, r.amount, r.pay_date) for r in records] == [
        ("AAPL", date(2025, 11, 10), Decimal("0.26"), None)
    ]
    assert source.name == "yfinance"


def test_provider_failure_becomes_unavailable():
    def broken(ticker, start, end, interval):
        raise RuntimeError("Too Many Requests")

    with pytest.raises(SourceUnavailable, match="Too Many Requests"):
        YFinanceSource(broken).fetch_minute_bars("AAPL", date(2025, 11, 25))
```

- [ ] **Step 3: Rodar para ver falhar**

Run: `uv run pytest tests/marketdata/test_yfinance_source.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'virtual_orders.marketdata.yfinance_source'`.

- [ ] **Step 4: Implementar**

`src/virtual_orders/marketdata/yfinance_source.py`:
```python
"""yfinance as cross-check reference (spec 4.6) and second dividend source (spec 4.7)."""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

import pandas as pd

from core.domain.models import Bar
from virtual_orders.marketdata.sources import DividendRecord, SourceDataError, SourceUnavailable

HistoryFn = Callable[[str, date, date, str], pd.DataFrame]
PRICE_QUANTUM = Decimal("0.0001")
DIVIDEND_QUANTUM = Decimal("0.000001")
_PRICE_COLUMNS = ("Open", "High", "Low", "Close")


def download_history(ticker: str, start: date, end: date, interval: str) -> pd.DataFrame:
    import yfinance as yf

    frame = yf.Ticker(ticker).history(
        start=start.isoformat(), end=end.isoformat(), interval=interval,
        auto_adjust=False, actions=True, prepost=False, raise_errors=True,
    )
    return cast(pd.DataFrame, frame)


def _is_missing(value: Any) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))


def _quantize(value: Any, quantum: Decimal) -> Decimal:
    if _is_missing(value):
        raise SourceDataError("missing numeric value")
    return Decimal(repr(float(value))).quantize(quantum)


class YFinanceSource:
    name = "yfinance"

    def __init__(self, history: HistoryFn = download_history) -> None:
        self._history_fn = history

    def _history(self, ticker: str, start: date, end: date, interval: str) -> pd.DataFrame:
        try:
            return self._history_fn(ticker, start, end, interval)
        except SourceDataError:
            raise
        except Exception as exc:  # yfinance raises many unrelated exception types
            raise SourceUnavailable(f"yfinance {ticker} {interval}: {exc}") from exc

    def fetch_minute_bars(self, ticker: str, day: date) -> dict[datetime, Bar]:
        frame = self._history(ticker, day, day + timedelta(days=1), "1m")
        bars: dict[datetime, Bar] = {}
        for index, row in frame.iterrows():
            ts = cast(pd.Timestamp, index).to_pydatetime().astimezone(UTC)
            if ts.second or ts.microsecond:
                continue
            if any(_is_missing(row[column]) for column in (*_PRICE_COLUMNS, "Volume")):
                continue
            bars[ts] = Bar(
                ts=ts,
                open=_quantize(row["Open"], PRICE_QUANTUM),
                high=_quantize(row["High"], PRICE_QUANTUM),
                low=_quantize(row["Low"], PRICE_QUANTUM),
                close=_quantize(row["Close"], PRICE_QUANTUM),
                volume=Decimal(int(row["Volume"])),
            )
        return bars

    def fetch_daily_range(self, ticker: str, day: date) -> tuple[Decimal, Decimal] | None:
        frame = self._history(ticker, day, day + timedelta(days=1), "1d")
        for index, row in frame.iterrows():
            if cast(pd.Timestamp, index).date() == day:
                if _is_missing(row["High"]) or _is_missing(row["Low"]):
                    return None
                return _quantize(row["High"], PRICE_QUANTUM), _quantize(row["Low"], PRICE_QUANTUM)
        return None

    def fetch_dividends(self, ticker: str, start: date, end: date) -> list[DividendRecord]:
        frame = self._history(ticker, start, end + timedelta(days=1), "1d")
        records: list[DividendRecord] = []
        for index, row in frame.iterrows():
            value = row["Dividends"]
            if _is_missing(value) or float(value) <= 0:
                continue
            ex_date = cast(pd.Timestamp, index).date()
            if start <= ex_date <= end:
                records.append(DividendRecord(ticker, ex_date, _quantize(value, DIVIDEND_QUANTUM), None))
        return records
```

- [ ] **Step 5: Rodar os testes**

Run: `uv run pytest tests/marketdata/test_yfinance_source.py -v`
Expected: PASS (4 testes).

- [ ] **Step 6: Lint, tipos e commit**

Run: `uv run ruff check src tests && uv run mypy && uv run pytest tests/marketdata`
Expected: verde, inclusive `test_fixture_manifest_labels_every_fixture_as_synthetic`. (Se `pandas-stubs` reclamar de `row[...]`, anote o tipo como `Any` localmente — nunca desligue o strict do módulo.)

```bash
git add pyproject.toml uv.lock src/virtual_orders/marketdata/yfinance_source.py tests/fixtures/README.md tests/fixtures/yfinance tests/marketdata/test_yfinance_source.py
git commit -m "feat(marketdata): yfinance reference bars, daily range and dividends

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BKUdi9RGWWdXRXVYFWj1yp"
```

---

### Task 6: Calendário com folga, ingestão versionada, leitura as-of e `selected_data_hash`

Spec 3.6; notas, entradas 5 e 7; D5, D6; decisões de detalhe 4 e 5.

**Files:**
- Create: `src/virtual_orders/marketdata/calendars.py`, `src/virtual_orders/marketdata/asof.py`, `src/virtual_orders/marketdata/ingest.py`
- Create: `tests/marketdata/test_asof_hash.py`, `tests/integration/support.py`, `tests/integration/test_ingest_asof.py`

**Interfaces:**
- Consumes: `core.marketdata.nyse_calendar.load_nyse_calendar`; `virtual_orders.marketdata.sources.{BarSource, RawBar, SourceDataError, DataTier}`; `virtual_orders.marketdata.gateway.MarketDataGateway`; `virtual_orders.storage.tables.{bar_batches, bars_1m}`; `virtual_orders.storage.database.INGEST_LOCK_KEY`; `virtual_orders.storage.codec.to_document`.
- Produces:
  - `virtual_orders.marketdata.calendars.calendar_for_window(earliest: datetime, latest: datetime) -> SessionCalendar` (carrega de `earliest − 10 dias` a `latest + 10 dias`, em cache)
  - `virtual_orders.marketdata.asof.floor_minute(ts: datetime) -> datetime`
  - `virtual_orders.marketdata.asof.acquire_data_as_of(engine: Engine) -> datetime`
  - `virtual_orders.marketdata.asof.read_bars_as_of(conn: Connection, ticker: str, source: str, start: datetime, end: datetime, as_of: datetime) -> list[Bar]` — um `Bar` por minuto, `start ≤ ts < end`, com `batch_id`
  - `virtual_orders.marketdata.asof.read_bar_version(conn, ticker, source, ts, batch_id) -> Bar` (`LookupError` se ausente)
  - `virtual_orders.marketdata.asof.bars_in_minutes(bars: Iterable[Bar], minutes: Sequence[datetime]) -> list[Bar]`
  - `virtual_orders.marketdata.asof.selected_data_hash(source: str, ticker: str, minutes: Sequence[datetime], bars: Sequence[Bar]) -> str` — entradas `(source, ticker, ts, batch_id, OHLCV)` ou `(source, ticker, ts, MISSING)`
  - `virtual_orders.marketdata.ingest.IngestResult(batch_id: UUID | None, fetched: int, stored: int)`
  - `virtual_orders.marketdata.ingest.store_batch(engine, source: BarSource, ticker, start, end, bars: Sequence[RawBar]) -> IngestResult`; `ingest_bars(engine, source, ticker, start, end) -> IngestResult`
  - `tests.integration.support`: `TICKER`, `DAY`, `PRICE_SOURCE = "fake_feed"`, `feeds(*sources) -> MarketDataGateway`, `raw(day, hm, o, h, l, c, volume=1000, ticker=TICKER) -> RawBar`, `flat_raw(day, start_hm, end_hm, price, ticker=TICKER) -> list[RawBar]`, `FakeBarSource(bars=(), source=PRICE_SOURCE)`, `backdated_batch(engine, ticker, bars, ingested_at, batch_id=None, source=PRICE_SOURCE) -> UUID`, `count(engine, table_name) -> int`

- [ ] **Step 1: Escrever os testes unitários que falham**

`tests/marketdata/test_asof_hash.py`:
```python
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest

from virtual_orders.marketdata.asof import bars_in_minutes, floor_minute, selected_data_hash
from tests.support import bar, et

DAY = "2025-11-25"
B1, B2 = UUID(int=1), UUID(int=2)


def versioned(hm: str, price, batch: UUID):
    return replace(bar(et(DAY, hm), price, price, price, price), batch_id=batch)


MINUTES = [et(DAY, "10:00"), et(DAY, "10:01"), et(DAY, "10:02")]


def test_floor_minute_is_utc_and_rejects_naive():
    assert floor_minute(et(DAY, "10:00", 59)) == et(DAY, "10:00")
    assert floor_minute(et(DAY, "10:00")).tzinfo is UTC
    with pytest.raises(ValueError):
        floor_minute(datetime(2025, 11, 25, 10, 0))


def test_hash_is_deterministic_and_marks_missing_minutes():
    bars = [versioned("10:00", 100, B1), versioned("10:02", 101, B1)]
    first = selected_data_hash("feed", "AAPL", MINUTES, bars)
    assert first == selected_data_hash("feed", "AAPL", MINUTES, list(reversed(bars)))
    filled = selected_data_hash("feed", "AAPL", MINUTES, bars + [versioned("10:01", 100, B1)])
    assert filled != first


def test_hash_changes_with_source_batch_price_and_ticker():
    base = selected_data_hash("feed", "AAPL", MINUTES[:1], [versioned("10:00", 100, B1)])
    assert base != selected_data_hash("other_feed", "AAPL", MINUTES[:1], [versioned("10:00", 100, B1)])
    assert base != selected_data_hash("feed", "AAPL", MINUTES[:1], [versioned("10:00", 100, B2)])
    assert base != selected_data_hash("feed", "AAPL", MINUTES[:1], [versioned("10:00", 100.01, B1)])
    assert base != selected_data_hash("feed", "MSFT", MINUTES[:1], [versioned("10:00", 100, B1)])
    assert selected_data_hash("feed", "AAPL", MINUTES[:1], []) != selected_data_hash("other_feed", "AAPL", MINUTES[:1], [])


def test_hash_rejects_bars_outside_minutes_duplicates_and_unversioned_bars():
    with pytest.raises(ValueError, match="outside"):
        selected_data_hash("feed", "AAPL", MINUTES[:1], [versioned("10:05", 100, B1)])
    with pytest.raises(ValueError, match="duplicate"):
        selected_data_hash("feed", "AAPL", MINUTES, [versioned("10:00", 100, B1), versioned("10:00", 100, B2)])
    with pytest.raises(ValueError, match="batch_id"):
        selected_data_hash("feed", "AAPL", MINUTES[:1], [bar(et(DAY, "10:00"), 100, 100, 100, 100)])


def test_bars_in_minutes_filters_non_expected_timestamps():
    bars = [versioned("10:00", 100, B1), versioned("10:05", 100, B1)]
    assert [b.ts for b in bars_in_minutes(bars, MINUTES)] == [et(DAY, "10:00")]
```

- [ ] **Step 2: Escrever o suporte e os testes de integração que falham**

`tests/integration/support.py`:
```python
"""Shared integration helpers: fake providers and scenario bars. Never touches the network."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Engine, func, select

from virtual_orders.marketdata.calendars import calendar_for_window
from virtual_orders.marketdata.gateway import MarketDataGateway
from virtual_orders.marketdata.sources import DataTier, RawBar, SourceUnavailable
from virtual_orders.storage import tables
from tests.support import et

TICKER = "AAPL"
DAY = "2025-11-25"
PRICE_SOURCE = "fake_feed"  # deliberately not a real provider: the suite proves adapters are replaceable


def _d(value) -> Decimal:
    return Decimal(str(value))


def raw(day: str, hm: str, o, h, l, c, volume=1000, ticker: str = TICKER) -> RawBar:  # noqa: E741
    return RawBar(ticker, et(day, hm), _d(o), _d(h), _d(l), _d(c), _d(volume))


def flat_raw(day: str, start_hm: str, end_hm: str, price, ticker: str = TICKER) -> list[RawBar]:
    start, end = et(day, start_hm), et(day, end_hm)
    minutes = calendar_for_window(start, end).expected_minutes(start, end)
    return [RawBar(ticker, m, _d(price), _d(price), _d(price), _d(price), _d(1000)) for m in minutes]


def feeds(*sources: FakeBarSource) -> MarketDataGateway:
    return MarketDataGateway(sources)


class FakeBarSource:
    provider = "fake"
    provider_version = "test"
    data_tier = DataTier.RESEARCH

    def __init__(self, bars: Iterable[RawBar] = (), source: str = PRICE_SOURCE) -> None:
        self.source = source
        self.bars: dict[tuple[str, datetime], RawBar] = {}
        self.calls: list[tuple[str, datetime, datetime]] = []
        self.failing: set[str] = set()
        self.load(bars)

    def load(self, bars: Iterable[RawBar]) -> None:
        """Adds bars; a bar for an existing minute replaces it (vendor correction)."""
        for item in bars:
            self.bars[(item.ticker, item.ts)] = item

    def remove(self, ticker: str, ts: datetime) -> None:
        self.bars.pop((ticker, ts), None)

    def fetch_bars(self, ticker: str, start: datetime, end: datetime) -> list[RawBar]:
        self.calls.append((ticker, start, end))
        if ticker in self.failing:
            raise SourceUnavailable(f"{ticker} unavailable (fake)")
        return sorted(
            (b for (t, ts), b in self.bars.items() if t == ticker and start <= ts < end),
            key=lambda b: b.ts,
        )


def backdated_batch(
    engine: Engine,
    ticker: str,
    bars: Iterable[RawBar],
    ingested_at: datetime,
    batch_id: UUID | None = None,
    source: str = PRICE_SOURCE,
) -> UUID:
    """Test-only: writes a batch with an explicit ingested_at, bypassing the watermark lock."""
    batch_id = batch_id or uuid4()
    with engine.begin() as conn:
        conn.execute(tables.bar_batches.insert().values(
            batch_id=batch_id, provider="fake", provider_version="backdated", data_tier="RESEARCH",
            request={"ticker": ticker}, content_hash="backdated", ingested_at=ingested_at,
        ))
        conn.execute(tables.bars_1m.insert(), [
            dict(ticker=ticker, ts=b.ts, open=b.open, high=b.high, low=b.low, close=b.close,
                 volume=b.volume, source=source, batch_id=batch_id)
            for b in bars
        ])
    return batch_id


def count(engine: Engine, table_name: str) -> int:
    table = tables.metadata.tables[table_name]
    with engine.connect() as conn:
        return int(conn.execute(select(func.count()).select_from(table)).scalar_one())
```

`tests/integration/test_ingest_asof.py`:
```python
import threading
import time
from datetime import date
from uuid import UUID

import pytest
from sqlalchemy import text

from virtual_orders.marketdata.asof import acquire_data_as_of, read_bar_version, read_bars_as_of
from virtual_orders.marketdata.calendars import calendar_for_window
from virtual_orders.marketdata.ingest import ingest_bars, store_batch
from virtual_orders.marketdata.sources import SourceDataError
from virtual_orders.storage.database import INGEST_LOCK_KEY
from tests.integration.support import (
    DAY, PRICE_SOURCE, TICKER, FakeBarSource, backdated_batch, count, flat_raw, raw,
)
from tests.support import et

START, END = et(DAY, "10:00"), et(DAY, "10:05")


def read(engine, as_of, start=START, end=END):
    with engine.connect() as conn:
        return read_bars_as_of(conn, TICKER, PRICE_SOURCE, start, end, as_of)


def test_ingest_then_read_one_version_per_minute(engine):
    source = FakeBarSource(flat_raw(DAY, "10:00", "10:05", 100))
    result = ingest_bars(engine, source, TICKER, START, END)
    assert (result.fetched, result.stored) == (5, 5) and result.batch_id is not None
    bars = read(engine, acquire_data_as_of(engine))
    assert [b.ts for b in bars] == [et(DAY, f"10:0{i}") for i in range(5)]
    assert {b.batch_id for b in bars} == {result.batch_id}
    assert count(engine, "bar_batches") == 1


def test_identical_reingestion_creates_no_batch(engine):
    source = FakeBarSource(flat_raw(DAY, "10:00", "10:05", 100))
    ingest_bars(engine, source, TICKER, START, END)
    again = ingest_bars(engine, source, TICKER, START, END)
    assert (again.batch_id, again.stored) == (None, 0)
    assert count(engine, "bar_batches") == 1 and count(engine, "bars_1m") == 5


def test_vendor_correction_is_a_new_version_and_old_reads_are_stable(engine):
    source = FakeBarSource(flat_raw(DAY, "10:00", "10:05", 100))
    first = ingest_bars(engine, source, TICKER, START, END)
    before = acquire_data_as_of(engine)
    source.load([raw(DAY, "10:02", 100, 104, 99, 103)])
    corrected = ingest_bars(engine, source, TICKER, START, END)
    assert corrected.stored == 1

    old = {b.ts: b for b in read(engine, before)}
    new = {b.ts: b for b in read(engine, acquire_data_as_of(engine))}
    assert old[et(DAY, "10:02")].high == 100 and old[et(DAY, "10:02")].batch_id == first.batch_id
    assert new[et(DAY, "10:02")].high == 104 and new[et(DAY, "10:02")].batch_id == corrected.batch_id
    assert len(new) == 5
    with engine.connect() as conn:
        pinned = read_bar_version(conn, TICKER, PRICE_SOURCE, et(DAY, "10:02"), first.batch_id)
        assert pinned.high == 100
        with pytest.raises(LookupError):
            read_bar_version(conn, TICKER, PRICE_SOURCE, et(DAY, "10:02"), UUID(int=7))


def test_minute_delivered_later_stays_missing_for_earlier_as_of(engine):
    bars = flat_raw(DAY, "10:00", "10:05", 100)
    source = FakeBarSource([b for b in bars if b.ts != et(DAY, "10:03")])
    ingest_bars(engine, source, TICKER, START, END)
    before = acquire_data_as_of(engine)
    source.load(bars)
    ingest_bars(engine, source, TICKER, START, END)
    assert et(DAY, "10:03") not in {b.ts for b in read(engine, before)}
    assert et(DAY, "10:03") in {b.ts for b in read(engine, acquire_data_as_of(engine))}


def test_as_of_reads_are_isolated_by_source(engine):
    ingest_bars(engine, FakeBarSource(flat_raw(DAY, "10:00", "10:05", 100)), TICKER, START, END)
    other = FakeBarSource(flat_raw(DAY, "10:00", "10:05", 200), source="other_feed")
    assert ingest_bars(engine, other, TICKER, START, END).stored == 5  # dedup never crosses sources
    as_of = acquire_data_as_of(engine)
    assert {b.open for b in read(engine, as_of)} == {100}
    with engine.connect() as conn:
        assert {b.open for b in read_bars_as_of(conn, TICKER, "other_feed", START, END, as_of)} == {200}


def test_same_ingested_at_breaks_ties_by_batch_id(engine):
    at = et(DAY, "20:00")
    backdated_batch(engine, TICKER, [raw(DAY, "10:00", 1, 1, 1, 1)], at, batch_id=UUID(int=1))
    backdated_batch(engine, TICKER, [raw(DAY, "10:00", 2, 2, 2, 2)], at, batch_id=UUID(int=2))
    (only,) = read(engine, at)
    assert only.batch_id == UUID(int=2) and only.open == 2


def test_watermark_waits_for_in_flight_ingestion(engine):
    holder = engine.connect()
    transaction = holder.begin()
    holder.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": INGEST_LOCK_KEY})
    in_flight_at = holder.execute(text("SELECT clock_timestamp()")).scalar_one()
    result: dict = {}
    worker = threading.Thread(target=lambda: result.update(at=acquire_data_as_of(engine)))
    worker.start()
    time.sleep(0.3)
    assert worker.is_alive()
    transaction.commit()
    holder.close()
    worker.join(timeout=5)
    assert result["at"] > in_flight_at


@pytest.mark.parametrize("bars, message", [
    ([raw(DAY, "10:00", 100, 99, 98, 100)], "invalid bar"),
    ([raw(DAY, "10:00", 100, 100, 100, 100), raw(DAY, "10:00", 100, 100, 100, 100)], "duplicate"),
    ([raw(DAY, "10:07", 100, 100, 100, 100)], "outside"),
    ([raw(DAY, "10:00", 100, 100, 100, 100, ticker="MSFT")], "ticker"),
])
def test_invalid_batches_are_rejected_without_writes(engine, bars, message):
    with pytest.raises(SourceDataError, match=message):
        store_batch(engine, FakeBarSource(), TICKER, START, END, bars)
    assert count(engine, "bar_batches") == 0


def test_calendar_window_is_padded_on_both_sides():
    earliest, latest = et(DAY, "08:00"), et("2025-12-01", "16:00")
    calendar = calendar_for_window(earliest, latest)
    days = [s.day for s in calendar.sessions]
    assert days[0] < date(2025, 11, 25) and days[-1] > date(2025, 12, 1)
    assert calendar.first_expected_minute_at_or_after(earliest) == et(DAY, "09:30")
    assert calendar_for_window(earliest, latest) is calendar
```

- [ ] **Step 3: Rodar para ver falhar**

Run: `uv run pytest tests/marketdata/test_asof_hash.py tests/integration/test_ingest_asof.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'virtual_orders.marketdata.asof'`.

- [ ] **Step 4: Implementar**

`src/virtual_orders/marketdata/calendars.py`:
```python
"""NYSE calendars padded around a window (Plan 1 close-out entry 7)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from functools import lru_cache

from core.domain.calendar import SessionCalendar
from core.marketdata.nyse_calendar import load_nyse_calendar

CALENDAR_PAD = timedelta(days=10)


@lru_cache(maxsize=128)
def _load(start: date, end: date) -> SessionCalendar:
    return load_nyse_calendar(start, end)


def calendar_for_window(earliest: datetime, latest: datetime) -> SessionCalendar:
    """At least one session before `earliest` and one after `latest` are always loaded."""
    if earliest.tzinfo is None or latest.tzinfo is None:
        raise ValueError("calendar window bounds must be timezone-aware")
    start = (earliest.astimezone(UTC) - CALENDAR_PAD).date()
    end = (latest.astimezone(UTC) + CALENDAR_PAD).date()
    return _load(start, end)
```

`src/virtual_orders/marketdata/asof.py`:
```python
"""As-of reads (spec 3.6) and the ingestion watermark (plan decision 3)."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Engine, text

from core.domain.hashing import sha256_hex
from core.domain.models import Bar
from virtual_orders.storage.database import INGEST_LOCK_KEY

_AS_OF_SQL = text(
    """
    SELECT DISTINCT ON (b.ts) b.ts, b.open, b.high, b.low, b.close, b.volume, b.batch_id
    FROM bars_1m b
    JOIN bar_batches bb ON bb.batch_id = b.batch_id
    WHERE b.ticker = :ticker AND b.source = :source
      AND b.ts >= :start AND b.ts < :end
      AND bb.ingested_at <= :as_of
    ORDER BY b.ts, bb.ingested_at DESC, b.batch_id DESC
    """
)

_VERSION_SQL = text(
    """
    SELECT ts, open, high, low, close, volume, batch_id FROM bars_1m
    WHERE ticker = :ticker AND source = :source AND ts = :ts AND batch_id = :batch_id
    """
)


def floor_minute(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        raise ValueError("naive datetime is not allowed")
    return ts.astimezone(UTC).replace(second=0, microsecond=0)


def acquire_data_as_of(engine: Engine) -> datetime:
    """A knowledge instant T such that no batch with ingested_at <= T can still be committed."""
    with engine.begin() as conn:
        conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": INGEST_LOCK_KEY})
        value: datetime = conn.execute(text("SELECT clock_timestamp()")).scalar_one()
    return value.astimezone(UTC)


def _bar(row: Any) -> Bar:
    return Bar(ts=row.ts, open=row.open, high=row.high, low=row.low, close=row.close,
               volume=row.volume, batch_id=row.batch_id)


def read_bars_as_of(
    conn: Connection, ticker: str, source: str, start: datetime, end: datetime, as_of: datetime
) -> list[Bar]:
    rows = conn.execute(
        _AS_OF_SQL,
        {"ticker": ticker, "source": source, "start": start, "end": end, "as_of": as_of},
    ).all()
    return [_bar(row) for row in rows]


def read_bar_version(conn: Connection, ticker: str, source: str, ts: datetime, batch_id: UUID) -> Bar:
    row = conn.execute(
        _VERSION_SQL, {"ticker": ticker, "source": source, "ts": ts, "batch_id": batch_id}
    ).first()
    if row is None:
        raise LookupError(f"no bar {ticker} {ts.isoformat()} in batch {batch_id}")
    return _bar(row)


def bars_in_minutes(bars: Iterable[Bar], minutes: Sequence[datetime]) -> list[Bar]:
    wanted = set(minutes)
    return [b for b in bars if b.ts in wanted]


def selected_data_hash(source: str, ticker: str, minutes: Sequence[datetime], bars: Sequence[Bar]) -> str:
    """Identity of the data a run used: source, version and OHLCV per expected minute, or MISSING (D6)."""
    by_ts: dict[datetime, Bar] = {}
    for item in bars:
        if item.batch_id is None:
            raise ValueError(f"bar {item.ts.isoformat()} has no batch_id")
        if item.ts in by_ts:
            raise ValueError(f"duplicate bar minute {item.ts.isoformat()}")
        by_ts[item.ts] = item
    outside = set(by_ts) - set(minutes)
    if outside:
        raise ValueError(f"bars outside the selected minutes: {sorted(t.isoformat() for t in outside)}")
    entries: list[list[Any]] = []
    for minute in sorted(minutes):
        found = by_ts.get(minute)
        if found is None:
            entries.append([source, ticker, minute, "MISSING"])
        else:
            entries.append([source, ticker, minute, found.batch_id, found.open, found.high, found.low,
                            found.close, found.volume])
    return sha256_hex(entries)
```

`src/virtual_orders/marketdata/ingest.py`:
```python
"""Versioned, deduplicated ingestion into bar_batches + bars_1m (spec 3.1, 3.6; plan decisions 3 and 6)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Engine, text

from core.domain.hashing import sha256_hex
from core.domain.models import Bar
from virtual_orders.marketdata.asof import read_bars_as_of
from virtual_orders.marketdata.sources import BarSource, RawBar, SourceDataError
from virtual_orders.storage.codec import to_document
from virtual_orders.storage.database import INGEST_LOCK_KEY
from virtual_orders.storage.tables import bar_batches, bars_1m


@dataclass(frozen=True)
class IngestResult:
    batch_id: UUID | None
    fetched: int
    stored: int


def _validated(ticker: str, start: datetime, end: datetime, bars: Sequence[RawBar]) -> list[RawBar]:
    seen: set[datetime] = set()
    for item in bars:
        if item.ticker != ticker:
            raise SourceDataError(f"ticker mismatch: expected {ticker}, got {item.ticker}")
        if not start <= item.ts < end:
            raise SourceDataError(f"bar {item.ts.isoformat()} outside [{start.isoformat()}, {end.isoformat()})")
        if item.ts in seen:
            raise SourceDataError(f"duplicate bar minute {item.ts.isoformat()}")
        seen.add(item.ts)
        try:
            item.to_bar()
        except (TypeError, ValueError) as exc:
            raise SourceDataError(f"invalid bar {item.ts.isoformat()}: {exc}") from exc
    return sorted(bars, key=lambda b: b.ts)


def _same(item: RawBar, current: Bar) -> bool:
    return (item.open, item.high, item.low, item.close, item.volume) == (
        current.open, current.high, current.low, current.close, current.volume
    )


def store_batch(
    engine: Engine, source: BarSource, ticker: str, start: datetime, end: datetime, bars: Sequence[RawBar]
) -> IngestResult:
    ordered = _validated(ticker, start, end, bars)
    with engine.begin() as conn:
        conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": INGEST_LOCK_KEY})
        ingested_at: datetime = conn.execute(text("SELECT clock_timestamp()")).scalar_one()
        current = {b.ts: b for b in read_bars_as_of(conn, ticker, source.source, start, end, ingested_at)}
        changed = [b for b in ordered if b.ts not in current or not _same(b, current[b.ts])]
        if not changed:
            return IngestResult(None, len(ordered), 0)
        batch_id = uuid4()
        conn.execute(bar_batches.insert().values(
            batch_id=batch_id,
            provider=source.provider,
            provider_version=source.provider_version,
            data_tier=source.data_tier.value,
            request=to_document({"ticker": ticker, "start": start, "end": end, "source": source.source}),
            content_hash=sha256_hex([[b.ticker, b.ts, b.open, b.high, b.low, b.close, b.volume] for b in changed]),
            ingested_at=ingested_at,
        ))
        conn.execute(bars_1m.insert(), [
            {"ticker": b.ticker, "ts": b.ts, "open": b.open, "high": b.high, "low": b.low,
             "close": b.close, "volume": b.volume, "source": source.source, "batch_id": batch_id}
            for b in changed
        ])
    return IngestResult(batch_id, len(ordered), len(changed))


def ingest_bars(engine: Engine, source: BarSource, ticker: str, start: datetime, end: datetime) -> IngestResult:
    """Network fetch happens outside the database transaction."""
    if end <= start:
        return IngestResult(None, 0, 0)
    return store_batch(engine, source, ticker, start, end, source.fetch_bars(ticker, start, end))
```

- [ ] **Step 5: Rodar os testes**

Run: `uv run pytest tests/marketdata/test_asof_hash.py tests/integration/test_ingest_asof.py -v`
Expected: PASS (5 unitários; 12 de integração, contando os parametrizados).

- [ ] **Step 6: Lint, tipos e commit**

Run: `uv run ruff check src tests && uv run mypy && uv run pytest tests/test_import_boundaries.py`
Expected: verde.

```bash
git add src/virtual_orders/marketdata/calendars.py src/virtual_orders/marketdata/asof.py src/virtual_orders/marketdata/ingest.py tests/marketdata/test_asof_hash.py tests/integration/support.py tests/integration/test_ingest_asof.py
git commit -m "feat(marketdata): versioned ingestion, as-of reads, watermark and selected_data_hash

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BKUdi9RGWWdXRXVYFWj1yp"
```

---

### Task 7: Ledger — linhas, eventos com idempotência estrita, runs, segmentos e quarentena

Spec 3.3, 3.4 (invariante), 5.2 (`FOR UPDATE`), 6; D2 (projeção guarda `OrderState` completo); notas, entrada 6.

**Files:**
- Create: `src/virtual_orders/ledger/__init__.py`, `src/virtual_orders/ledger/errors.py`, `src/virtual_orders/ledger/orders.py`, `src/virtual_orders/ledger/events.py`, `src/virtual_orders/ledger/runs.py`, `src/virtual_orders/ledger/writes.py`, `src/virtual_orders/ledger/quarantine.py`
- Create: `tests/integration/test_ledger.py`

**Interfaces:**
- Consumes: `virtual_orders.storage.codec.*` (Task 3), `virtual_orders.storage.tables.*` (Task 2), `virtual_orders.marketdata.calendars.calendar_for_window` (Task 6), `core.domain.position.{r_multiple, excursion_r}`, `core.fills.get_fill_model`.
- Produces (`virtual_orders.ledger.errors`):
  - constantes `EVENT_HASH_CONFLICT`, `EVENT_BEFORE_EVALUATION_START`, `STORED_HASH_MISMATCH`, `CALENDAR_MISMATCH`, `PROJECTION_MISSING`, `PROCESSED_BAR_MISSING`, `PROJECTION_INTEGRITY_ERROR`, `REPRODUCE_DIVERGENCE`
  - `LedgerIntegrityError(kind: str, message: str, *, order_id=None, event_key=None, existing_hash=None, attempted_hash=None, detail=None)` com atributos homônimos
  - `ProjectionIntegrityError(message, *, order_id=None, detail=None)` (subclasse, `kind = PROJECTION_INTEGRITY_ERROR`)
  - `HistoryDivergence(reason: str, diff: list[dict] | None = None)`; `OrderNotFound(LookupError)`; `SignalNotFound(LookupError)`
- Produces (`virtual_orders.ledger.orders`):
  - `SignalRow(id, client_signal_id, payload_hash, created_at, strategy, strategy_version, source, spec: SignalSpec, evaluation_start_ts, valid_until_ts, confirmation_note=None, score=None, thesis=None)`
  - `OrderRow(id, signal_id, origin: Origin, created_at, evaluation_start_ts, valid_until_ts, fill_model_version, config: FillConfig, code_version, risk_amount: Decimal, price_source: str, replay=False, replay_mode: str | None = None, replay_of_order_id: UUID | None = None, market_data_snapshot_id: UUID | None = None)`
  - `Projection(state: OrderState, next_seq: int)`
  - `insert_signal(conn, row, raw_payload) -> bool`; `get_signal(conn, signal_id) -> SignalRow`; `find_signal_by_client_id(conn, client_signal_id) -> SignalRow | None`
  - `insert_order(conn, row)`; `get_order(conn, order_id) -> OrderRow`; `lock_order(conn, order_id) -> OrderRow`; `auto_order_id(conn, signal_id) -> UUID | None`
  - `load_projection(conn, order_id) -> Projection | None`; `projection_row(conn, order, direction, projection) -> dict[str, Any]`; `save_projection(conn, order, direction, projection)`; `read_projection_row(conn, order_id) -> dict[str, Any] | None` (sem `updated_at`); `delete_projection(conn, order_id)`
- Produces (`virtual_orders.ledger.events`):
  - `StoredEvent(seq: int, prepared: PreparedEvent)`; `AppendResult(inserted: tuple[PreparedEvent, ...], first_seq: int, next_seq: int)`
  - `as_prepared(item: Event | PreparedEvent) -> PreparedEvent`
  - `dedupe_events(known: dict[str, str], events, *, order_id: UUID, evaluation_start_ts: datetime) -> list[PreparedEvent]` — muta `known`; mesmo hash → descarta; hash diferente → `EVENT_HASH_CONFLICT`; evento de mercado antes do início → `EVENT_BEFORE_EVALUATION_START`
  - `known_hashes(conn, order_id) -> dict[str, str]`; `insert_event(conn, order_id, seq, prepared)`; `append_events(conn, order: OrderRow, events, next_seq) -> AppendResult`; `stored_events(conn, order_id) -> list[StoredEvent]` (valida `payload_hash == sha256(hash_material)`)
- Produces (`virtual_orders.ledger.runs`):
  - `RunKind(StrEnum)`: `LIVE`, `REPLAY`, `ACTIONABILITY`, `END_OF_DAY`; `RunStatus(StrEnum)`: `RUNNING`, `COMPLETED`, `FAILED`
  - `RunInfo(run_id, kind, data_as_of, code_version, started_at)`; `start_run(conn, kind, data_as_of, code_version, started_at=None, detail=None) -> RunInfo`; `finish_run(conn, run_id, status, detail=None)`; `get_run(conn, run_id) -> RunInfo`; `latest_run_status(conn, run_id) -> tuple[RunStatus, dict]`
  - `SegmentRow(order_id, run_id, bar_from, bar_to, selected_data_hash, first_seq, event_count)`; `record_segment(conn, segment)`; `list_segments(conn, order_id) -> list[SegmentRow]` (ordem `first_seq, bar_from`); `last_segment_end(conn, order_id) -> datetime | None`; `segment_containing(conn, order_id, ts) -> SegmentRow | None`
- Produces (`virtual_orders.ledger.writes`): `apply_result(conn, order, direction, next_seq, result: StepResult) -> AppendResult`; `persist_new_order(conn, order, direction, created: StepResult) -> AppendResult`
- Produces (`virtual_orders.ledger.quarantine`): `INTEGRITY_REASON = "INTEGRITY"`; `record_incident(conn, error)`; `quarantine(engine, error) -> bool`; `run_guarded(engine, operation: Callable[[], T]) -> T`

- [ ] **Step 1: Escrever os testes que falham**

`tests/integration/test_ledger.py`:
```python
import threading
import time
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from core.domain.calendar import evaluation_start_ts, signal_valid_until_ts
from core.domain.hashing import canonical_json
from core.domain.models import Direction, Event, EventType, FillConfig, OrderContext, Origin, OrderStatus
from core.fills import get_fill_model
from virtual_orders.ledger import errors
from virtual_orders.ledger.events import append_events, stored_events
from virtual_orders.ledger.orders import (
    OrderRow, SignalRow, delete_projection, find_signal_by_client_id, get_order, insert_signal,
    load_projection, lock_order, read_projection_row,
)
from virtual_orders.ledger.quarantine import run_guarded
from virtual_orders.ledger.runs import (
    RunKind, RunStatus, SegmentRow, finish_run, get_run, last_segment_end, latest_run_status,
    list_segments, record_segment, segment_containing, start_run,
)
from virtual_orders.ledger.writes import apply_result, persist_new_order
from virtual_orders.marketdata.calendars import calendar_for_window
from virtual_orders.storage import tables
from tests.integration.support import DAY, PRICE_SOURCE, count
from tests.support import et, long_signal

V1 = get_fill_model("v1")


def seed(engine, client_id="sig-1"):
    created_at = et(DAY, "09:00")
    spec = long_signal()
    calendar = calendar_for_window(created_at, created_at + timedelta(days=10))
    start = evaluation_start_ts(calendar, created_at)
    until = signal_valid_until_ts(calendar, start, spec.valid_sessions)
    signal = SignalRow(uuid4(), client_id, "hash", created_at, "test", "1", "unit", spec, start, until)
    config = FillConfig()
    order = OrderRow(uuid4(), signal.id, Origin.AUTO_STRATEGY, created_at, start, until, "v1", config,
                     "sha-test", config.risk_amount, PRICE_SOURCE)
    ctx = OrderContext(spec, config, calendar, start, until)
    created = V1.new_order_state(ctx)
    with engine.begin() as conn:
        assert insert_signal(conn, signal, {"client_signal_id": client_id})
        persist_new_order(conn, order, Direction.LONG, created)
    return signal, order, ctx, created


def test_new_order_persists_created_event_and_projection(engine):
    signal, order, ctx, created = seed(engine)
    (event,) = created.events
    with engine.connect() as conn:
        (stored,) = stored_events(conn, order.id)
        projection = load_projection(conn, order.id)
        row = read_projection_row(conn, order.id)
        assert get_order(conn, order.id) == order
        assert find_signal_by_client_id(conn, "sig-1") == signal
    assert stored.seq == 1 and stored.prepared.payload_hash == event.payload_hash
    assert stored.prepared.hash_material == canonical_json(event.hash_material())
    assert projection.state == created.state and projection.next_seq == 2
    assert row["status"] == "PENDING" and row["r_multiple"] == 0 and row["expected_bars"] == 0
    assert "updated_at" not in row


def test_duplicate_signal_insert_returns_false(engine):
    signal, *_ = seed(engine)
    with engine.begin() as conn:
        assert not insert_signal(conn, replace(signal, id=uuid4()), {})


def test_same_key_same_hash_is_noop(engine):
    _, order, _, created = seed(engine)
    with engine.begin() as conn:
        result = append_events(conn, order, created.events, next_seq=2)
    assert result.inserted == () and result.next_seq == 2
    assert count(engine, "order_events") == 1


def test_same_key_different_hash_aborts_records_incident_and_freezes(engine):
    _, order, _, _ = seed(engine)
    innocent = Event(EventType.NEEDS_REVIEW, "NEEDS_REVIEW:X:1", payload={"reason": "X", "ref": "1"})
    tampered = Event(EventType.ORDER_CREATED, "ORDER_CREATED", payload={"tampered": True})

    def attempt():
        with engine.begin() as conn:
            append_events(conn, order, [innocent, tampered], next_seq=2)

    with pytest.raises(errors.LedgerIntegrityError) as caught:
        run_guarded(engine, attempt)
    assert caught.value.kind == errors.EVENT_HASH_CONFLICT
    with engine.connect() as conn:
        keys = [e.prepared.event_key for e in stored_events(conn, order.id)]
        incident = conn.execute(select(tables.integrity_incidents)).one()
        projection = load_projection(conn, order.id)
    assert keys == ["ORDER_CREATED", "FROZEN:INTEGRITY", "NEEDS_REVIEW:INTEGRITY:EVENT_HASH_CONFLICT"]
    assert incident.kind == errors.EVENT_HASH_CONFLICT and incident.event_key == "ORDER_CREATED"
    assert incident.existing_hash != incident.attempted_hash
    assert projection.state.frozen and projection.state.review_reasons == ("INTEGRITY",)
    assert projection.next_seq == 4

    with pytest.raises(errors.LedgerIntegrityError):
        run_guarded(engine, attempt)
    assert count(engine, "integrity_incidents") == 2 and count(engine, "order_events") == 3


def test_market_event_before_evaluation_start_is_rejected(engine):
    _, order, ctx, _ = seed(engine)
    early = Event(EventType.TRIGGER_HIT, "TRIGGER_HIT", ctx.evaluation_start_ts - timedelta(minutes=1))
    with pytest.raises(errors.LedgerIntegrityError) as caught:
        with engine.begin() as conn:
            append_events(conn, order, [early], next_seq=2)
    assert caught.value.kind == errors.EVENT_BEFORE_EVALUATION_START


def test_tampered_stored_hash_is_detected_on_read(engine):
    _, order, _, _ = seed(engine)
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE order_events DISABLE TRIGGER USER"))
        conn.execute(text("UPDATE order_events SET hash_material = hash_material || ' '"))
        conn.execute(text("ALTER TABLE order_events ENABLE TRIGGER USER"))
    with engine.connect() as conn, pytest.raises(errors.LedgerIntegrityError) as caught:
        stored_events(conn, order.id)
    assert caught.value.kind == errors.STORED_HASH_MISMATCH


def test_apply_result_appends_and_updates_projection(engine):
    _, order, _, created = seed(engine)
    flagged = V1.flag_review(created.state, "MANUAL_CHECK", "r1")
    with engine.begin() as conn:
        result = apply_result(conn, order, Direction.LONG, 2, flagged)
        row = read_projection_row(conn, order.id)
    assert result.first_seq == 2 and result.next_seq == 3
    assert row["needs_review"] is True and row["next_seq"] == 3
    with engine.begin() as conn:
        delete_projection(conn, order.id)
        assert load_projection(conn, order.id) is None


def test_runs_are_append_only_with_latest_status(engine):
    with engine.begin() as conn:
        run = start_run(conn, RunKind.LIVE, et(DAY, "12:00"), "sha", detail={"n": 1})
        finish_run(conn, run.run_id, RunStatus.COMPLETED, {"orders": 3})
    with engine.connect() as conn:
        assert get_run(conn, run.run_id) == run
        assert latest_run_status(conn, run.run_id) == (RunStatus.COMPLETED, {"orders": 3})
    assert count(engine, "evaluation_run_status") == 2


def test_segments_order_by_seq_then_time(engine):
    _, order, _, _ = seed(engine)
    with engine.begin() as conn:
        runs = [start_run(conn, RunKind.LIVE, et(DAY, "16:00"), "sha") for _ in range(3)]
        record_segment(conn, SegmentRow(order.id, runs[0].run_id, et(DAY, "10:31"), et(DAY, "11:00"), "h2", 2, 0))
        record_segment(conn, SegmentRow(order.id, runs[1].run_id, et(DAY, "09:30"), et(DAY, "10:30"), "h1", 2, 0))
        record_segment(conn, SegmentRow(order.id, runs[2].run_id, et(DAY, "11:01"), et(DAY, "11:30"), "h3", 2, 1))
    with engine.connect() as conn:
        segments = list_segments(conn, order.id)
        assert [s.selected_data_hash for s in segments] == ["h1", "h2", "h3"]
        assert last_segment_end(conn, order.id) == et(DAY, "11:30")
        assert segment_containing(conn, order.id, et(DAY, "10:45")).selected_data_hash == "h2"
        assert segment_containing(conn, order.id, et(DAY, "12:00")) is None


def test_lock_order_blocks_concurrent_writer(engine):
    _, order, _, _ = seed(engine)
    holder = engine.connect()
    transaction = holder.begin()
    lock_order(holder, order.id)
    done = threading.Event()

    def contender():
        with engine.begin() as conn:
            lock_order(conn, order.id)
            done.set()

    worker = threading.Thread(target=contender)
    worker.start()
    time.sleep(0.3)
    assert not done.is_set()
    transaction.commit()
    holder.close()
    worker.join(timeout=5)
    assert done.is_set()


def test_unknown_order_raises_not_found(engine):
    with engine.connect() as conn, pytest.raises(errors.OrderNotFound):
        get_order(conn, uuid4())


def test_projection_status_enum_round_trip(engine):
    _, order, _, _ = seed(engine)
    with engine.connect() as conn:
        assert load_projection(conn, order.id).state.status is OrderStatus.PENDING
```

- [ ] **Step 2: Rodar para ver falhar**

Run: `uv run pytest tests/integration/test_ledger.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'virtual_orders.ledger'`.

- [ ] **Step 3: Implementar erros e linhas**

`src/virtual_orders/ledger/__init__.py`: vazio.

`src/virtual_orders/ledger/errors.py`:
```python
"""Integrity failures are never silenced (spec 3.3, 6)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

EVENT_HASH_CONFLICT = "EVENT_HASH_CONFLICT"
EVENT_BEFORE_EVALUATION_START = "EVENT_BEFORE_EVALUATION_START"
STORED_HASH_MISMATCH = "STORED_HASH_MISMATCH"
CALENDAR_MISMATCH = "CALENDAR_MISMATCH"
PROJECTION_MISSING = "PROJECTION_MISSING"
PROCESSED_BAR_MISSING = "PROCESSED_BAR_MISSING"
PROJECTION_INTEGRITY_ERROR = "PROJECTION_INTEGRITY_ERROR"
REPRODUCE_DIVERGENCE = "REPRODUCE_DIVERGENCE"


class LedgerIntegrityError(Exception):
    def __init__(
        self,
        kind: str,
        message: str,
        *,
        order_id: UUID | None = None,
        event_key: str | None = None,
        existing_hash: str | None = None,
        attempted_hash: str | None = None,
        detail: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(f"{kind}: {message}")
        self.kind = kind
        self.order_id = order_id
        self.event_key = event_key
        self.existing_hash = existing_hash
        self.attempted_hash = attempted_hash
        self.detail: dict[str, Any] = dict(detail or {})


class ProjectionIntegrityError(LedgerIntegrityError):
    def __init__(
        self, message: str, *, order_id: UUID | None = None, detail: Mapping[str, Any] | None = None
    ) -> None:
        super().__init__(PROJECTION_INTEGRITY_ERROR, message, order_id=order_id, detail=detail)


class HistoryDivergence(Exception):
    """Regenerated history differs from the stored authoritative history (spec 10, D2)."""

    def __init__(self, reason: str, diff: list[dict[str, Any]] | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.diff = diff or []


class OrderNotFound(LookupError):
    pass


class SignalNotFound(LookupError):
    pass
```

`src/virtual_orders/ledger/orders.py`:
```python
"""Rows and repository for signals, orders and the order_state projection."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, delete, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from core.domain.models import Direction, FillConfig, OrderState, Origin, SignalSpec
from core.domain.position import excursion_r, r_multiple
from virtual_orders.ledger.errors import OrderNotFound, SignalNotFound
from virtual_orders.storage.codec import config_from_snapshot, config_to_snapshot, state_from_document, state_to_document, to_document
from virtual_orders.storage.tables import order_state, orders, signals


@dataclass(frozen=True)
class SignalRow:
    id: UUID
    client_signal_id: str
    payload_hash: str
    created_at: datetime
    strategy: str
    strategy_version: str
    source: str
    spec: SignalSpec
    evaluation_start_ts: datetime
    valid_until_ts: datetime
    confirmation_note: str | None = None
    score: Decimal | None = None
    thesis: str | None = None


@dataclass(frozen=True)
class OrderRow:
    id: UUID
    signal_id: UUID
    origin: Origin
    created_at: datetime
    evaluation_start_ts: datetime
    valid_until_ts: datetime
    fill_model_version: str
    config: FillConfig
    code_version: str
    risk_amount: Decimal
    price_source: str
    replay: bool = False
    replay_mode: str | None = None
    replay_of_order_id: UUID | None = None
    market_data_snapshot_id: UUID | None = None


@dataclass(frozen=True)
class Projection:
    state: OrderState
    next_seq: int


def insert_signal(conn: Connection, row: SignalRow, raw_payload: Mapping[str, Any]) -> bool:
    spec = row.spec
    stmt = (
        pg_insert(signals)
        .values(
            id=row.id, client_signal_id=row.client_signal_id, payload_hash=row.payload_hash,
            created_at=row.created_at, strategy=row.strategy, strategy_version=row.strategy_version,
            source=row.source, ticker=spec.ticker, direction=spec.direction.value,
            entry_zone_low=spec.entry_zone_low, entry_zone_high=spec.entry_zone_high,
            trigger_price=spec.trigger_price, confirmation_note=row.confirmation_note,
            target1=spec.target1, target2=spec.target2, stop=spec.stop,
            valid_sessions=spec.valid_sessions, evaluation_start_ts=row.evaluation_start_ts,
            valid_until_ts=row.valid_until_ts, score=row.score, thesis=row.thesis,
            raw_payload=to_document(dict(raw_payload)),
        )
        .on_conflict_do_nothing(index_elements=["client_signal_id"])
        .returning(signals.c.id)
    )
    return conn.execute(stmt).first() is not None


def _signal(row: Any) -> SignalRow:
    spec = SignalSpec(
        ticker=row.ticker, direction=Direction(row.direction), entry_zone_low=row.entry_zone_low,
        entry_zone_high=row.entry_zone_high, stop=row.stop, target1=row.target1, target2=row.target2,
        trigger_price=row.trigger_price, valid_sessions=row.valid_sessions,
    )
    return SignalRow(
        id=row.id, client_signal_id=row.client_signal_id, payload_hash=row.payload_hash,
        created_at=row.created_at, strategy=row.strategy, strategy_version=row.strategy_version,
        source=row.source, spec=spec, evaluation_start_ts=row.evaluation_start_ts,
        valid_until_ts=row.valid_until_ts, confirmation_note=row.confirmation_note,
        score=row.score, thesis=row.thesis,
    )


def get_signal(conn: Connection, signal_id: UUID) -> SignalRow:
    row = conn.execute(select(signals).where(signals.c.id == signal_id)).first()
    if row is None:
        raise SignalNotFound(str(signal_id))
    return _signal(row)


def find_signal_by_client_id(conn: Connection, client_signal_id: str) -> SignalRow | None:
    row = conn.execute(select(signals).where(signals.c.client_signal_id == client_signal_id)).first()
    return None if row is None else _signal(row)


def insert_order(conn: Connection, row: OrderRow) -> None:
    conn.execute(orders.insert().values(
        id=row.id, signal_id=row.signal_id, origin=row.origin.value, created_at=row.created_at,
        evaluation_start_ts=row.evaluation_start_ts, valid_until_ts=row.valid_until_ts,
        fill_model_version=row.fill_model_version, config_snapshot=config_to_snapshot(row.config),
        code_version=row.code_version, replay=row.replay, replay_mode=row.replay_mode,
        replay_of_order_id=row.replay_of_order_id, market_data_snapshot_id=row.market_data_snapshot_id,
        risk_amount=row.risk_amount, price_source=row.price_source,
    ))


def _order(row: Any) -> OrderRow:
    return OrderRow(
        id=row.id, signal_id=row.signal_id, origin=Origin(row.origin), created_at=row.created_at,
        evaluation_start_ts=row.evaluation_start_ts, valid_until_ts=row.valid_until_ts,
        fill_model_version=row.fill_model_version, config=config_from_snapshot(row.config_snapshot),
        code_version=row.code_version, risk_amount=row.risk_amount, price_source=row.price_source,
        replay=row.replay,
        replay_mode=row.replay_mode, replay_of_order_id=row.replay_of_order_id,
        market_data_snapshot_id=row.market_data_snapshot_id,
    )


def get_order(conn: Connection, order_id: UUID) -> OrderRow:
    row = conn.execute(select(orders).where(orders.c.id == order_id)).first()
    if row is None:
        raise OrderNotFound(str(order_id))
    return _order(row)


def lock_order(conn: Connection, order_id: UUID) -> OrderRow:
    """Every event write for an order starts here (spec 5.2)."""
    row = conn.execute(select(orders).where(orders.c.id == order_id).with_for_update()).first()
    if row is None:
        raise OrderNotFound(str(order_id))
    return _order(row)


def auto_order_id(conn: Connection, signal_id: UUID) -> UUID | None:
    value: UUID | None = conn.execute(
        select(orders.c.id).where(
            orders.c.signal_id == signal_id, orders.c.origin == Origin.AUTO_STRATEGY.value,
            orders.c.replay.is_(False),
        )
    ).scalar_one_or_none()
    return value


def load_projection(conn: Connection, order_id: UUID) -> Projection | None:
    row = conn.execute(
        select(order_state.c.state_document, order_state.c.next_seq).where(order_state.c.order_id == order_id)
    ).first()
    if row is None:
        return None
    return Projection(state_from_document(row.state_document), row.next_seq)


_QUALITY_TOTALS = text(
    """
    SELECT COALESCE(SUM((payload->>'expected_bars')::int), 0) AS expected,
           COALESCE(SUM((payload->>'missing_bars')::int), 0) AS missing
    FROM order_events WHERE order_id = :order_id AND type = 'DATA_QUALITY'
    """
)


def projection_row(conn: Connection, order: OrderRow, direction: Direction, projection: Projection) -> dict[str, Any]:
    state = projection.state
    mfe, mae = excursion_r(state, direction)
    totals = conn.execute(_QUALITY_TOTALS, {"order_id": order.id}).one()
    return {
        "order_id": order.id,
        "status": state.status.value,
        "zone_lost": state.zone_lost,
        "entry_eligible_from": state.entry_eligible_from,
        "trigger_hit_at": state.trigger_hit_at,
        "entry_path": None if state.entry_path is None else state.entry_path.value,
        "avg_entry": state.avg_entry,
        "initial_stop": state.initial_stop,
        "stop_current": state.stop_current,
        "stop_active_from": state.stop_active_from,
        "qty_total": state.qty_total,
        "qty_open": state.qty_open,
        "realized_pnl": state.realized_pnl,
        "costs": state.costs,
        "r_multiple": r_multiple(state, order.risk_amount),
        "mfe_r": mfe,
        "mae_r": mae,
        "opened_at": state.opened_at,
        "closed_at": state.closed_at,
        "final_event_ts": state.final_event_ts,
        "last_bar_ts": state.last_bar_ts,
        "expected_bars": int(totals.expected),
        "missing_bars": int(totals.missing),
        "needs_review": state.needs_review,
        "frozen": state.frozen,
        "state_document": state_to_document(state),
        "next_seq": projection.next_seq,
    }


def save_projection(conn: Connection, order: OrderRow, direction: Direction, projection: Projection) -> None:
    row = projection_row(conn, order, direction, projection)
    stmt = pg_insert(order_state).values(**row, updated_at=func.clock_timestamp())
    stmt = stmt.on_conflict_do_update(
        index_elements=["order_id"],
        set_={**{key: stmt.excluded[key] for key in row if key != "order_id"}, "updated_at": func.clock_timestamp()},
    )
    conn.execute(stmt)


def read_projection_row(conn: Connection, order_id: UUID) -> dict[str, Any] | None:
    row = conn.execute(select(order_state).where(order_state.c.order_id == order_id)).mappings().first()
    if row is None:
        return None
    return {key: value for key, value in row.items() if key != "updated_at"}


def delete_projection(conn: Connection, order_id: UUID) -> None:
    conn.execute(delete(order_state).where(order_state.c.order_id == order_id))
```

- [ ] **Step 4: Implementar eventos, runs, escrita e quarentena**

`src/virtual_orders/ledger/events.py`:
```python
"""Append-only order events with strict idempotency (spec 3.3) and the 3.4 invariant."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import Connection, select

from core.domain.models import MARKET_EVENT_TYPES, Event
from virtual_orders.ledger.errors import (
    EVENT_BEFORE_EVALUATION_START, EVENT_HASH_CONFLICT, STORED_HASH_MISMATCH, LedgerIntegrityError,
)
from virtual_orders.ledger.orders import OrderRow
from virtual_orders.storage.codec import PreparedEvent, material_hash, prepare_event
from virtual_orders.storage.tables import order_events

MARKET_TYPE_VALUES = frozenset(t.value for t in MARKET_EVENT_TYPES)


@dataclass(frozen=True)
class StoredEvent:
    seq: int
    prepared: PreparedEvent


@dataclass(frozen=True)
class AppendResult:
    inserted: tuple[PreparedEvent, ...]
    first_seq: int
    next_seq: int


def as_prepared(item: Event | PreparedEvent) -> PreparedEvent:
    return item if isinstance(item, PreparedEvent) else prepare_event(item)


def dedupe_events(
    known: dict[str, str],
    events: Iterable[Event | PreparedEvent],
    *,
    order_id: UUID,
    evaluation_start_ts: datetime,
) -> list[PreparedEvent]:
    fresh: list[PreparedEvent] = []
    for item in events:
        prepared = as_prepared(item)
        if prepared.type in MARKET_TYPE_VALUES and (
            prepared.bar_ts is None or prepared.bar_ts < evaluation_start_ts
        ):
            raise LedgerIntegrityError(
                EVENT_BEFORE_EVALUATION_START,
                f"{prepared.event_key} bar_ts {prepared.bar_ts} precedes {evaluation_start_ts.isoformat()}",
                order_id=order_id, event_key=prepared.event_key, attempted_hash=prepared.payload_hash,
            )
        existing = known.get(prepared.event_key)
        if existing is not None:
            if existing == prepared.payload_hash:
                continue
            raise LedgerIntegrityError(
                EVENT_HASH_CONFLICT, f"{prepared.event_key} already recorded with a different payload",
                order_id=order_id, event_key=prepared.event_key, existing_hash=existing,
                attempted_hash=prepared.payload_hash,
            )
        known[prepared.event_key] = prepared.payload_hash
        fresh.append(prepared)
    return fresh


def known_hashes(conn: Connection, order_id: UUID) -> dict[str, str]:
    rows = conn.execute(
        select(order_events.c.event_key, order_events.c.payload_hash).where(order_events.c.order_id == order_id)
    ).all()
    return {row.event_key: row.payload_hash for row in rows}


def insert_event(conn: Connection, order_id: UUID, seq: int, prepared: PreparedEvent) -> None:
    conn.execute(order_events.insert().values(
        order_id=order_id, seq=seq, event_key=prepared.event_key, payload_hash=prepared.payload_hash,
        hash_material=prepared.hash_material, type=prepared.type, bar_ts=prepared.bar_ts,
        price=prepared.price, qty=prepared.qty, bar_batch_id=prepared.bar_batch_id, payload=prepared.payload,
    ))


def append_events(
    conn: Connection, order: OrderRow, events: Sequence[Event | PreparedEvent], next_seq: int
) -> AppendResult:
    fresh = dedupe_events(
        known_hashes(conn, order.id), events, order_id=order.id, evaluation_start_ts=order.evaluation_start_ts
    )
    for offset, prepared in enumerate(fresh):
        insert_event(conn, order.id, next_seq + offset, prepared)
    return AppendResult(tuple(fresh), next_seq, next_seq + len(fresh))


def stored_events(conn: Connection, order_id: UUID) -> list[StoredEvent]:
    rows = conn.execute(
        select(order_events).where(order_events.c.order_id == order_id).order_by(order_events.c.seq)
    ).all()
    result: list[StoredEvent] = []
    for row in rows:
        if material_hash(row.hash_material) != row.payload_hash:
            raise LedgerIntegrityError(
                STORED_HASH_MISMATCH, f"stored material of {row.event_key} does not match its hash",
                order_id=order_id, event_key=row.event_key, existing_hash=row.payload_hash,
            )
        result.append(StoredEvent(row.seq, PreparedEvent(
            type=row.type, event_key=row.event_key, bar_ts=row.bar_ts, price=row.price, qty=row.qty,
            bar_batch_id=row.bar_batch_id, payload=row.payload, hash_material=row.hash_material,
            payload_hash=row.payload_hash,
        )))
    return result
```

`src/virtual_orders/ledger/runs.py`:
```python
"""Evaluation facts (spec 3.1, 3.6; D2): runs, append-only run status and per-order segments."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Connection, func, select, text

from virtual_orders.storage.codec import to_document
from virtual_orders.storage.tables import evaluation_run_status, evaluation_runs, order_eval_segments


class RunKind(StrEnum):
    LIVE = "LIVE"
    REPLAY = "REPLAY"
    ACTIONABILITY = "ACTIONABILITY"
    END_OF_DAY = "END_OF_DAY"


class RunStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class RunInfo:
    run_id: UUID
    kind: RunKind
    data_as_of: datetime
    code_version: str
    started_at: datetime


@dataclass(frozen=True)
class SegmentRow:
    order_id: UUID
    run_id: UUID
    bar_from: datetime
    bar_to: datetime
    selected_data_hash: str
    first_seq: int
    event_count: int


def start_run(
    conn: Connection,
    kind: RunKind,
    data_as_of: datetime,
    code_version: str,
    started_at: datetime | None = None,
    detail: Mapping[str, Any] | None = None,
) -> RunInfo:
    started: datetime = started_at or conn.execute(text("SELECT clock_timestamp()")).scalar_one()
    run = RunInfo(uuid4(), kind, data_as_of, code_version, started)
    conn.execute(evaluation_runs.insert().values(
        run_id=run.run_id, kind=kind.value, data_as_of=data_as_of, code_version=code_version, started_at=started,
    ))
    conn.execute(evaluation_run_status.insert().values(
        run_id=run.run_id, status=RunStatus.RUNNING.value, detail=to_document(dict(detail or {})),
        recorded_at=func.clock_timestamp(),
    ))
    return run


def finish_run(conn: Connection, run_id: UUID, status: RunStatus, detail: Mapping[str, Any] | None = None) -> None:
    conn.execute(evaluation_run_status.insert().values(
        run_id=run_id, status=status.value, detail=to_document(dict(detail or {})),
        recorded_at=func.clock_timestamp(),
    ))


def get_run(conn: Connection, run_id: UUID) -> RunInfo:
    row = conn.execute(select(evaluation_runs).where(evaluation_runs.c.run_id == run_id)).one()
    return RunInfo(row.run_id, RunKind(row.kind), row.data_as_of, row.code_version, row.started_at)


def latest_run_status(conn: Connection, run_id: UUID) -> tuple[RunStatus, dict[str, Any]]:
    row = conn.execute(
        select(evaluation_run_status.c.status, evaluation_run_status.c.detail)
        .where(evaluation_run_status.c.run_id == run_id)
        .order_by(evaluation_run_status.c.id.desc())
        .limit(1)
    ).one()
    return RunStatus(row.status), dict(row.detail)


def record_segment(conn: Connection, segment: SegmentRow) -> None:
    conn.execute(order_eval_segments.insert().values(
        order_id=segment.order_id, run_id=segment.run_id, bar_from=segment.bar_from, bar_to=segment.bar_to,
        selected_data_hash=segment.selected_data_hash, first_seq=segment.first_seq,
        event_count=segment.event_count,
    ))


def _segment(row: Any) -> SegmentRow:
    return SegmentRow(row.order_id, row.run_id, row.bar_from, row.bar_to, row.selected_data_hash,
                      row.first_seq, row.event_count)


def list_segments(conn: Connection, order_id: UUID) -> list[SegmentRow]:
    rows = conn.execute(
        select(order_eval_segments)
        .where(order_eval_segments.c.order_id == order_id)
        .order_by(order_eval_segments.c.first_seq, order_eval_segments.c.bar_from)
    ).all()
    return [_segment(row) for row in rows]


def last_segment_end(conn: Connection, order_id: UUID) -> datetime | None:
    value: datetime | None = conn.execute(
        select(func.max(order_eval_segments.c.bar_to)).where(order_eval_segments.c.order_id == order_id)
    ).scalar_one()
    return value


def segment_containing(conn: Connection, order_id: UUID, ts: datetime) -> SegmentRow | None:
    row = conn.execute(
        select(order_eval_segments).where(
            order_eval_segments.c.order_id == order_id,
            order_eval_segments.c.bar_from <= ts,
            order_eval_segments.c.bar_to >= ts,
        )
    ).first()
    return None if row is None else _segment(row)
```

`src/virtual_orders/ledger/writes.py`:
```python
"""Append a StepResult and refresh the projection in the caller's transaction."""

from __future__ import annotations

from sqlalchemy import Connection

from core.domain.models import Direction, StepResult
from virtual_orders.ledger.events import AppendResult, append_events
from virtual_orders.ledger.orders import OrderRow, Projection, insert_order, save_projection


def apply_result(
    conn: Connection, order: OrderRow, direction: Direction, next_seq: int, result: StepResult
) -> AppendResult:
    appended = append_events(conn, order, result.events, next_seq)
    save_projection(conn, order, direction, Projection(result.state, appended.next_seq))
    return appended


def persist_new_order(conn: Connection, order: OrderRow, direction: Direction, created: StepResult) -> AppendResult:
    insert_order(conn, order)
    return apply_result(conn, order, direction, 1, created)
```

`src/virtual_orders/ledger/quarantine.py`:
```python
"""Integrity incidents: separate transaction, incident row, order frozen with NEEDS_REVIEW:INTEGRITY (spec 3.3)."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from sqlalchemy import Connection, Engine

from core.fills import get_fill_model
from virtual_orders.ledger.errors import LedgerIntegrityError
from virtual_orders.ledger.orders import get_signal, load_projection, lock_order
from virtual_orders.ledger.writes import apply_result
from virtual_orders.storage.codec import to_document
from virtual_orders.storage.tables import integrity_incidents

INTEGRITY_REASON = "INTEGRITY"
T = TypeVar("T")


def record_incident(conn: Connection, error: LedgerIntegrityError) -> None:
    conn.execute(integrity_incidents.insert().values(
        kind=error.kind, order_id=error.order_id, event_key=error.event_key,
        existing_hash=error.existing_hash, attempted_hash=error.attempted_hash,
        detail=to_document({"message": str(error), **error.detail}),
    ))


def quarantine(engine: Engine, error: LedgerIntegrityError) -> bool:
    """Returns True when the order projection was frozen."""
    with engine.begin() as conn:
        record_incident(conn, error)
        if error.order_id is None:
            return False
        order = lock_order(conn, error.order_id)
        projection = load_projection(conn, order.id)
        if projection is None:
            return False
        signal = get_signal(conn, order.signal_id)
        frozen = get_fill_model(order.fill_model_version).freeze(projection.state, INTEGRITY_REASON, error.kind)
        apply_result(conn, order, signal.spec.direction, projection.next_seq, frozen)
        return True


def run_guarded(engine: Engine, operation: Callable[[], T]) -> T:
    try:
        return operation()
    except LedgerIntegrityError as error:
        quarantine(engine, error)
        raise
```

- [ ] **Step 5: Rodar os testes**

Run: `uv run pytest tests/integration/test_ledger.py -v`
Expected: PASS (12 testes).

- [ ] **Step 6: Lint, tipos e commit**

Run: `uv run ruff check src tests && uv run mypy && uv run pytest tests/test_import_boundaries.py`
Expected: verde.

```bash
git add src/virtual_orders/ledger tests/integration/test_ledger.py
git commit -m "feat(ledger): strict idempotent events, runs, segments, projection and quarantine

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BKUdi9RGWWdXRXVYFWj1yp"
```

---

### Task 8: Contexto da ordem e entrada de sinais (`submit_signal`)

Spec 3.3 (`POST /signals`: 200/201/409/422), 3.4 (validade), 3.8; notas, entrada 7. A rota HTTP é do Plano 3; aqui fica o serviço.

**Files:**
- Create: `src/virtual_orders/evaluator/__init__.py`, `src/virtual_orders/evaluator/context.py`, `src/virtual_orders/evaluator/signals.py`
- Modify: `tests/integration/support.py` (acrescentar helpers ao final)
- Create: `tests/integration/test_signals.py`

**Interfaces:**
- Consumes: `virtual_orders.ledger.orders.*`, `virtual_orders.ledger.writes.persist_new_order`, `virtual_orders.ledger.errors.{LedgerIntegrityError, CALENDAR_MISMATCH}` (Task 7); `virtual_orders.marketdata.calendars.calendar_for_window` (Task 6); `core.domain.calendar.{evaluation_start_ts, signal_valid_until_ts, calendar_window_hash}`; `core.domain.validation.validate_signal`.
- Produces (`virtual_orders.evaluator.context`):
  - `DEFAULT_FILL_MODEL_VERSION = "v1"`; `SIGNAL_CALENDAR_HORIZON = timedelta(days=35)`
  - `build_order_context(signal: SignalRow, order: OrderRow) -> OrderContext`
  - `signal_context(signal: SignalRow, config: FillConfig) -> OrderContext` (contexto da ordem hipotética do sinal)
  - `load_order_context(conn, order: OrderRow) -> tuple[SignalRow, OrderContext]` — `LedgerIntegrityError(CALENDAR_MISMATCH)` se `calendar_sessions_hash` do `ORDER_CREATED` diferir do calendário carregado
- Produces (`virtual_orders.evaluator.signals`):
  - `SignalValidationError(errors: list[str])`; `IdempotencyConflict(client_signal_id: str, existing_signal_id: UUID)`
  - `ParsedSignal(client_signal_id, strategy, strategy_version, source, spec: SignalSpec, confirmation_note, score, thesis, auto_order: bool)`; `parse_signal_body(body: Mapping[str, Any]) -> ParsedSignal`
  - `SignalSubmission(status: str, signal_id: UUID, auto_order_id: UUID | None)` — `status` ∈ `{"CREATED", "EXISTING"}` (API: 201/200)
  - `submit_signal(engine, body: Mapping[str, Any], *, config: FillConfig, code_version: str, price_source: str, now: datetime | None = None) -> SignalSubmission` — `price_source` vem da configuração (Plano 3), nunca do corpo do sinal
- Produces (`tests.integration.support`): `CODE_VERSION`, `SIGNAL_CREATED_AT`, `signal_body(**overrides) -> dict`, `scenario_bars(day=DAY, ticker=TICKER) -> list[RawBar]` (201 candles: entrada 10:05, T1 11:00, T2 12:50), `submit_default(engine, **overrides) -> SignalSubmission`

- [ ] **Step 1: Acrescentar helpers ao suporte**

No bloco de imports do topo de `tests/integration/support.py`, acrescentar:
```python
from typing import Any

from core.domain.models import FillConfig
from virtual_orders.evaluator.signals import SignalSubmission, submit_signal
```

Ao final do arquivo:
```python
CODE_VERSION = "test-sha"
SIGNAL_CREATED_AT = et(DAY, "09:00")


def signal_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "client_signal_id": "rex-2025-11-25-aapl",
        "strategy": "REXSHARE",
        "strategy_version": "1.0",
        "source": "test",
        "ticker": TICKER,
        "direction": "LONG",
        "entry_zone_low": Decimal("100"),
        "entry_zone_high": Decimal("102"),
        "stop": Decimal("97"),
        "target1": Decimal("106"),
        "target2": Decimal("110"),
        "valid_sessions": 3,
    }
    body.update(overrides)
    return body


def scenario_bars(day: str = DAY, ticker: str = TICKER) -> list[RawBar]:
    """201 bars 09:30-12:50: fill 10:05 @101, TARGET1 11:00 @106, TARGET2 12:50 @110 (r = 1.75)."""
    return (
        flat_raw(day, "09:30", "10:05", 105, ticker)
        + [raw(day, "10:05", 101, 101.5, 100.5, 101.2, ticker=ticker)]
        + flat_raw(day, "10:06", "11:00", 103, ticker)
        + [raw(day, "11:00", 105, 106, 104.8, 105.5, ticker=ticker)]
        + flat_raw(day, "11:01", "12:50", 107, ticker)
        + [raw(day, "12:50", 109, 110.5, 108.8, 110, ticker=ticker)]
    )


def submit_default(engine: Engine, **overrides: Any) -> SignalSubmission:
    return submit_signal(engine, signal_body(**overrides), config=FillConfig(),
                         code_version=CODE_VERSION, price_source=PRICE_SOURCE, now=SIGNAL_CREATED_AT)
```

Rode `uv run ruff check --fix tests/integration/support.py` para ordenar os imports (arquivo novo do Plano 2).

- [ ] **Step 2: Escrever os testes que falham**

`tests/integration/test_signals.py`:
```python
import threading
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from core.domain.calendar import SessionCalendar
from core.domain.models import FillConfig
from virtual_orders.evaluator import context as context_module
from virtual_orders.evaluator.context import load_order_context
from virtual_orders.evaluator.signals import IdempotencyConflict, SignalValidationError, submit_signal
from virtual_orders.ledger import errors
from virtual_orders.ledger.events import stored_events
from virtual_orders.ledger.orders import get_order, get_signal, read_projection_row
from virtual_orders.marketdata.calendars import calendar_for_window
from virtual_orders.storage import tables
from tests.integration.support import (
    CODE_VERSION, DAY, PRICE_SOURCE, SIGNAL_CREATED_AT, count, signal_body, submit_default,
)
from tests.support import et


def test_signal_created_with_auto_order(engine):
    result = submit_default(engine)
    assert result.status == "CREATED" and result.auto_order_id is not None
    with engine.connect() as conn:
        signal = get_signal(conn, result.signal_id)
        order = get_order(conn, result.auto_order_id)
        (created,) = stored_events(conn, order.id)
        row = read_projection_row(conn, order.id)
        raw_payload = conn.execute(select(tables.signals.c.raw_payload)).scalar_one()
    assert signal.evaluation_start_ts == et(DAY, "09:30")
    assert signal.valid_until_ts == et("2025-11-28", "13:00")  # 11-25, 11-26, half day 11-28
    assert order.created_at == SIGNAL_CREATED_AT and order.valid_until_ts == signal.valid_until_ts
    assert order.fill_model_version == "v1" and order.code_version == CODE_VERSION
    assert order.price_source == PRICE_SOURCE
    assert created.prepared.event_key == "ORDER_CREATED" and row["status"] == "PENDING"
    assert created.prepared.payload["signal"]["stop"] == "97"
    assert raw_payload["entry_zone_low"] == "100"


def test_same_body_returns_existing_resource(engine):
    first = submit_default(engine)
    again = submit_signal(engine, dict(reversed(list(signal_body(entry_zone_low=Decimal("100.0")).items()))),
                          config=FillConfig(), code_version="other", price_source="other_feed", now=et(DAY, "11:00"))
    assert (again.status, again.signal_id, again.auto_order_id) == ("EXISTING", first.signal_id, first.auto_order_id)
    assert count(engine, "signals") == 1 and count(engine, "orders") == 1


def test_same_client_id_with_different_body_conflicts(engine):
    first = submit_default(engine)
    with pytest.raises(IdempotencyConflict) as caught:
        submit_default(engine, stop=Decimal("96"))
    assert caught.value.existing_signal_id == first.signal_id
    assert count(engine, "signals") == 1


def test_auto_order_can_be_disabled(engine):
    result = submit_default(engine, auto_order=False)
    assert result.auto_order_id is None and count(engine, "orders") == 0


@pytest.mark.parametrize("overrides, expected", [
    ({"stop": Decimal("101")}, ["STOP_NOT_BEYOND_ZONE"]),
    ({"ticker": None}, ["MISSING_FIELD:ticker"]),
    ({"direction": "SIDEWAYS"}, ["INVALID_DIRECTION"]),
    ({"valid_sessions": 21}, ["VALID_SESSIONS_OUT_OF_RANGE"]),
    ({"valid_sessions": "3"}, ["INVALID_INTEGER:valid_sessions"]),
    ({"target2": "abc"}, ["INVALID_DECIMAL:target2"]),
    ({"auto_order": "yes"}, ["INVALID_BOOLEAN:auto_order"]),
])
def test_invalid_signals_are_rejected_without_writes(engine, overrides, expected):
    with pytest.raises(SignalValidationError) as caught:
        submit_default(engine, **overrides)
    assert caught.value.errors == expected
    assert count(engine, "signals") == 0


def test_float_body_is_not_canonical(engine):
    with pytest.raises(SignalValidationError) as caught:
        submit_default(engine, stop=97.0)
    assert caught.value.errors[0].startswith("NON_CANONICAL_BODY")


def test_short_signal_is_accepted(engine):
    result = submit_default(engine, client_signal_id="short-1", direction="SHORT", entry_zone_low=Decimal("100"),
                            entry_zone_high=Decimal("102"), stop=Decimal("105"), target1=Decimal("95"),
                            target2=Decimal("90"))
    assert result.status == "CREATED"


def test_concurrent_duplicate_submissions_create_one_signal(engine):
    barrier = threading.Barrier(4)
    results = []

    def submit():
        barrier.wait()
        results.append(submit_default(engine))

    threads = [threading.Thread(target=submit) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert sorted(r.status for r in results) == ["CREATED", "EXISTING", "EXISTING", "EXISTING"]
    assert count(engine, "signals") == 1 and count(engine, "orders") == 1


def test_calendar_identity_is_verified_when_loading_context(engine, monkeypatch):
    result = submit_default(engine)
    with engine.connect() as conn:
        order = get_order(conn, result.auto_order_id)
        _, ctx = load_order_context(conn, order)
    assert ctx.evaluation_start_ts == et(DAY, "09:30")

    real = calendar_for_window(et(DAY, "09:00"), et("2025-11-28", "13:00"))
    without_wednesday = SessionCalendar([s for s in real.sessions if s.day != date(2025, 11, 26)])
    monkeypatch.setattr(context_module, "calendar_for_window", lambda earliest, latest: without_wednesday)
    with engine.connect() as conn, pytest.raises(errors.LedgerIntegrityError) as caught:
        load_order_context(conn, order)
    assert caught.value.kind == errors.CALENDAR_MISMATCH
```

- [ ] **Step 3: Rodar para ver falhar**

Run: `uv run pytest tests/integration/test_signals.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'virtual_orders.evaluator'`.

- [ ] **Step 4: Implementar**

`src/virtual_orders/evaluator/__init__.py`: vazio.

`src/virtual_orders/evaluator/context.py`:
```python
"""OrderContext from persisted rows, with the calendar identity check (Plan 1 R3)."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import Connection, select

from core.domain.calendar import calendar_window_hash
from core.domain.models import FillConfig, OrderContext
from virtual_orders.ledger.errors import CALENDAR_MISMATCH, LedgerIntegrityError
from virtual_orders.ledger.orders import OrderRow, SignalRow, get_signal
from virtual_orders.marketdata.calendars import calendar_for_window
from virtual_orders.storage.tables import order_events

DEFAULT_FILL_MODEL_VERSION = "v1"
SIGNAL_CALENDAR_HORIZON = timedelta(days=35)  # 20 sessions plus holidays, before padding


def build_order_context(signal: SignalRow, order: OrderRow) -> OrderContext:
    calendar = calendar_for_window(min(signal.created_at, order.created_at), order.valid_until_ts)
    return OrderContext(signal.spec, order.config, calendar, order.evaluation_start_ts, order.valid_until_ts)


def signal_context(signal: SignalRow, config: FillConfig) -> OrderContext:
    calendar = calendar_for_window(signal.created_at, signal.valid_until_ts)
    return OrderContext(signal.spec, config, calendar, signal.evaluation_start_ts, signal.valid_until_ts)


def load_order_context(conn: Connection, order: OrderRow) -> tuple[SignalRow, OrderContext]:
    signal = get_signal(conn, order.signal_id)
    ctx = build_order_context(signal, order)
    payload = conn.execute(
        select(order_events.c.payload).where(
            order_events.c.order_id == order.id, order_events.c.event_key == "ORDER_CREATED"
        )
    ).scalar_one_or_none()
    expected = calendar_window_hash(ctx.calendar, ctx.evaluation_start_ts, ctx.valid_until_ts)
    recorded = None if payload is None else payload.get("calendar_sessions_hash")
    if recorded != expected:
        raise LedgerIntegrityError(
            CALENDAR_MISMATCH, "loaded calendar differs from the one recorded at order creation",
            order_id=order.id, event_key="ORDER_CREATED", existing_hash=recorded, attempted_hash=expected,
        )
    return signal, ctx
```

`src/virtual_orders/evaluator/signals.py`:
```python
"""Signal intake with strict idempotency (spec 3.3) and automatic AUTO_STRATEGY order."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Connection, Engine

from core.domain.calendar import evaluation_start_ts, signal_valid_until_ts
from core.domain.hashing import sha256_hex
from core.domain.models import Direction, FillConfig, Origin, SignalSpec, coerce_decimal
from core.domain.validation import validate_signal
from virtual_orders.evaluator.context import DEFAULT_FILL_MODEL_VERSION, SIGNAL_CALENDAR_HORIZON, build_order_context
from core.fills import get_fill_model
from virtual_orders.ledger.orders import OrderRow, SignalRow, auto_order_id, find_signal_by_client_id, insert_signal
from virtual_orders.ledger.writes import persist_new_order
from virtual_orders.marketdata.calendars import calendar_for_window

REQUIRED_FIELDS = (
    "client_signal_id", "strategy", "strategy_version", "source", "ticker", "direction",
    "entry_zone_low", "entry_zone_high", "stop", "target1", "valid_sessions",
)
TEXT_FIELDS = ("client_signal_id", "strategy", "strategy_version", "source", "ticker")
OPTIONAL_TEXT_FIELDS = ("confirmation_note", "thesis")
DECIMAL_FIELDS = ("entry_zone_low", "entry_zone_high", "stop", "target1")
OPTIONAL_DECIMAL_FIELDS = ("target2", "trigger_price", "score")


class SignalValidationError(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__(", ".join(errors))
        self.errors = errors


class IdempotencyConflict(Exception):
    def __init__(self, client_signal_id: str, existing_signal_id: UUID) -> None:
        super().__init__(f"IDEMPOTENCY_CONFLICT: {client_signal_id}")
        self.client_signal_id = client_signal_id
        self.existing_signal_id = existing_signal_id


@dataclass(frozen=True)
class ParsedSignal:
    client_signal_id: str
    strategy: str
    strategy_version: str
    source: str
    spec: SignalSpec
    confirmation_note: str | None
    score: Decimal | None
    thesis: str | None
    auto_order: bool


@dataclass(frozen=True)
class SignalSubmission:
    status: str
    signal_id: UUID
    auto_order_id: UUID | None


def parse_signal_body(body: Mapping[str, Any]) -> ParsedSignal:
    errors = [f"MISSING_FIELD:{name}" for name in REQUIRED_FIELDS if body.get(name) in (None, "")]
    if errors:
        raise SignalValidationError(errors)
    for name in TEXT_FIELDS:
        if not isinstance(body[name], str):
            errors.append(f"INVALID_TEXT:{name}")
    for name in OPTIONAL_TEXT_FIELDS:
        if body.get(name) is not None and not isinstance(body[name], str):
            errors.append(f"INVALID_TEXT:{name}")
    try:
        direction = Direction(body["direction"])
    except ValueError:
        errors.append("INVALID_DIRECTION")
    decimals: dict[str, Decimal | None] = {}
    for name in DECIMAL_FIELDS + OPTIONAL_DECIMAL_FIELDS:
        try:
            decimals[name] = coerce_decimal(body.get(name), name, optional=name in OPTIONAL_DECIMAL_FIELDS)
        except (TypeError, ValueError):
            errors.append(f"INVALID_DECIMAL:{name}")
    sessions = body["valid_sessions"]
    if isinstance(sessions, bool) or not isinstance(sessions, int):
        errors.append("INVALID_INTEGER:valid_sessions")
    auto_order = body.get("auto_order", True)
    if not isinstance(auto_order, bool):
        errors.append("INVALID_BOOLEAN:auto_order")
    if errors:
        raise SignalValidationError(errors)

    spec = SignalSpec(
        ticker=body["ticker"], direction=direction, entry_zone_low=decimals["entry_zone_low"],
        entry_zone_high=decimals["entry_zone_high"], stop=decimals["stop"], target1=decimals["target1"],
        target2=decimals["target2"], trigger_price=decimals["trigger_price"], valid_sessions=sessions,
    )
    level_errors = validate_signal(spec)
    if level_errors:
        raise SignalValidationError(level_errors)
    return ParsedSignal(
        client_signal_id=body["client_signal_id"], strategy=body["strategy"],
        strategy_version=body["strategy_version"], source=body["source"], spec=spec,
        confirmation_note=body.get("confirmation_note"), score=decimals["score"], thesis=body.get("thesis"),
        auto_order=auto_order,
    )


def _existing(conn: Connection, existing: SignalRow, payload_hash: str) -> SignalSubmission:
    if existing.payload_hash != payload_hash:
        raise IdempotencyConflict(existing.client_signal_id, existing.id)
    return SignalSubmission("EXISTING", existing.id, auto_order_id(conn, existing.id))


def submit_signal(
    engine: Engine,
    body: Mapping[str, Any],
    *,
    config: FillConfig,
    code_version: str,
    price_source: str,
    now: datetime | None = None,
) -> SignalSubmission:
    try:
        payload_hash = sha256_hex(dict(body))
    except (TypeError, ValueError) as exc:
        raise SignalValidationError([f"NON_CANONICAL_BODY:{exc}"]) from exc
    client_id = body.get("client_signal_id")
    if isinstance(client_id, str):
        with engine.connect() as conn:
            found = find_signal_by_client_id(conn, client_id)
            if found is not None:
                return _existing(conn, found, payload_hash)

    parsed = parse_signal_body(body)
    created_at = (now or datetime.now(UTC)).astimezone(UTC)
    calendar = calendar_for_window(created_at, created_at + SIGNAL_CALENDAR_HORIZON)
    start = evaluation_start_ts(calendar, created_at)
    until = signal_valid_until_ts(calendar, start, parsed.spec.valid_sessions)
    signal = SignalRow(
        id=uuid4(), client_signal_id=parsed.client_signal_id, payload_hash=payload_hash,
        created_at=created_at, strategy=parsed.strategy, strategy_version=parsed.strategy_version,
        source=parsed.source, spec=parsed.spec, evaluation_start_ts=start, valid_until_ts=until,
        confirmation_note=parsed.confirmation_note, score=parsed.score, thesis=parsed.thesis,
    )
    with engine.begin() as conn:
        if not insert_signal(conn, signal, body):
            raced = find_signal_by_client_id(conn, parsed.client_signal_id)
            if raced is None:
                raise RuntimeError(f"signal {parsed.client_signal_id} conflicted but is not visible")
            return _existing(conn, raced, payload_hash)
        order_id: UUID | None = None
        if parsed.auto_order:
            order = OrderRow(
                id=uuid4(), signal_id=signal.id, origin=Origin.AUTO_STRATEGY, created_at=created_at,
                evaluation_start_ts=start, valid_until_ts=until,
                fill_model_version=DEFAULT_FILL_MODEL_VERSION, config=config, code_version=code_version,
                risk_amount=config.risk_amount, price_source=price_source,
            )
            created = get_fill_model(order.fill_model_version).new_order_state(build_order_context(signal, order))
            persist_new_order(conn, order, parsed.spec.direction, created)
            order_id = order.id
    return SignalSubmission("CREATED", signal.id, order_id)
```

- [ ] **Step 5: Rodar os testes**

Run: `uv run pytest tests/integration/test_signals.py -v`
Expected: PASS (15 testes, contando os parametrizados).

- [ ] **Step 6: Lint, tipos e commit**

Run: `uv run ruff check src tests && uv run mypy && uv run pytest tests/test_import_boundaries.py`
Expected: verde.

```bash
git add src/virtual_orders/evaluator/__init__.py src/virtual_orders/evaluator/context.py src/virtual_orders/evaluator/signals.py tests/integration/support.py tests/integration/test_signals.py
git commit -m "feat(evaluator): signal intake with strict idempotency and auto order creation

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BKUdi9RGWWdXRXVYFWj1yp"
```

---

### Task 9: Ciclo live — `evaluate_order`, `run_live_cycle` e lock global

Spec 3.6 (ordens live), 4.1, 5.2, 5.3 (ciclo), 6 (provider indisponível, worker cai); D5, D6; decisão de detalhe 3; Global Constraints (sem troca silenciosa de provider).

**Files:**
- Create: `src/virtual_orders/evaluator/outcomes.py`, `src/virtual_orders/evaluator/cycle.py`
- Create: `tests/integration/test_cycle.py`

**Interfaces:**
- Consumes: `load_order_context` (Task 8); `lock_order`, `load_projection`, `apply_result`, `start_run`, `finish_run`, `record_segment`, `last_segment_end`, `SegmentRow`, `RunInfo`, `RunKind`, `RunStatus`, `run_guarded`, `LedgerIntegrityError`, `PROJECTION_MISSING` (Task 7); `ingest_bars`, `acquire_data_as_of`, `read_bars_as_of`, `bars_in_minutes`, `selected_data_hash`, `floor_minute` (Task 6); `MarketDataGateway`, `UnknownDataSource`, `SourceUnavailable`, `SourceDataError` (Task 4).
- Produces:
  - `virtual_orders.evaluator.outcomes.OrderOutcome(order_id: UUID, event_keys: tuple[str, ...] = (), segment: tuple[datetime, datetime] | None = None, error: str | None = None)`
  - `virtual_orders.evaluator.cycle.OpenOrderCursor(order_id: UUID, price_source: str, ticker: str, resume_from: datetime)`; `open_order_cursors(conn) -> list[OpenOrderCursor]` (não replay, não final, não `frozen`)
  - `CycleReport(skipped: bool, run_id: UUID | None = None, data_as_of: datetime | None = None, outcomes: tuple[OrderOutcome, ...] = (), ingest_failures: dict[str, str] = {})` — chaves `"{price_source}:{ticker}"`
  - `try_cycle_lock(conn) -> bool`; `release_cycle_lock(conn) -> None`
  - `evaluate_order(engine, order_id, run: RunInfo, *, market_now: datetime, close_trailing_gap: bool = False) -> OrderOutcome` — uma transação por ordem; erros de integridade viram `OrderOutcome.error` após quarentena
  - `run_live_cycle(engine, gateway: MarketDataGateway, *, code_version: str, market_now: datetime | None = None, close_trailing_gap: bool = False) -> CycleReport` — cada ordem só é lida do feed `gateway.bar_source(order.price_source)`; feed desconhecido ou falho → falha registrada no run e ordem não avaliada (nunca outro feed)

- [ ] **Step 1: Escrever os testes que falham**

`tests/integration/test_cycle.py`:
```python
from decimal import Decimal

from sqlalchemy import select, text

from core.domain.models import Direction, Event, EventType
from virtual_orders.evaluator.cycle import evaluate_order, run_live_cycle
from core.fills import get_fill_model
from virtual_orders.ledger import errors
from virtual_orders.ledger.events import append_events, stored_events
from virtual_orders.ledger.orders import delete_projection, load_projection, lock_order, read_projection_row
from virtual_orders.ledger.runs import RunKind, RunStatus, latest_run_status, list_segments, start_run
from virtual_orders.ledger.writes import apply_result
from virtual_orders.marketdata.asof import acquire_data_as_of
from virtual_orders.storage import tables
from virtual_orders.storage.database import CYCLE_LOCK_KEY
from tests.integration.support import (
    CODE_VERSION, DAY, PRICE_SOURCE, TICKER, FakeBarSource, count, feeds, raw, scenario_bars, submit_default,
)
from tests.support import et

V1 = get_fill_model("v1")


def cycle(engine, source, hm, **kwargs):
    return run_live_cycle(engine, feeds(source), code_version=CODE_VERSION, market_now=et(DAY, hm), **kwargs)


def segments(engine, order_id):
    with engine.connect() as conn:
        return [(s.bar_from, s.bar_to, s.first_seq, s.event_count) for s in list_segments(conn, order_id)]


def test_three_cycles_fill_scale_out_and_close(engine):
    order_id = submit_default(engine).auto_order_id
    source = FakeBarSource(scenario_bars())

    first = cycle(engine, source, "10:30")
    assert first.outcomes[0].event_keys == ("FILLED",)
    assert first.outcomes[0].segment == (et(DAY, "09:30"), et(DAY, "10:29"))
    assert source.calls[0] == (TICKER, et(DAY, "09:30"), et(DAY, "10:30"))
    second = cycle(engine, source, "11:30")
    assert second.outcomes[0].event_keys == ("TARGET1_HIT",)
    assert source.calls[1][1] == et(DAY, "10:30")
    third = cycle(engine, source, "13:00")
    assert third.outcomes[0].event_keys == ("TARGET2_HIT",)
    assert cycle(engine, source, "13:30").outcomes == ()

    assert segments(engine, order_id) == [
        (et(DAY, "09:30"), et(DAY, "10:29"), 2, 1),
        (et(DAY, "10:30"), et(DAY, "11:29"), 3, 1),
        (et(DAY, "11:30"), et(DAY, "12:50"), 4, 1),
    ]
    with engine.connect() as conn:
        row = read_projection_row(conn, order_id)
        kinds = conn.execute(select(tables.evaluation_runs.c.kind)).scalars().all()
        status, detail = latest_run_status(conn, first.run_id)
    assert row["status"] == "CLOSED" and row["r_multiple"] == Decimal("1.75") and row["next_seq"] == 5
    assert set(kinds) == {"LIVE"} and len(kinds) == 4
    assert status is RunStatus.COMPLETED and detail["orders"] == 1


def test_trailing_missing_minute_is_not_consumed(engine):
    order_id = submit_default(engine).auto_order_id
    source = FakeBarSource(scenario_bars())
    late = next(b for b in scenario_bars() if b.ts == et(DAY, "10:29"))
    source.remove(TICKER, late.ts)
    cycle(engine, source, "10:30")
    source.load([late])
    cycle(engine, source, "10:45")
    assert [s[:2] for s in segments(engine, order_id)] == [
        (et(DAY, "09:30"), et(DAY, "10:28")), (et(DAY, "10:29"), et(DAY, "10:44")),
    ]


def test_internal_missing_minute_stays_missing_after_late_delivery(engine):
    order_id = submit_default(engine).auto_order_id
    source = FakeBarSource(scenario_bars())
    source.remove(TICKER, et(DAY, "10:05"))
    report = cycle(engine, source, "10:30")
    assert report.outcomes[0].event_keys == ()
    source.load([raw(DAY, "10:05", 101, 101.5, 100.5, 101.2)])
    cycle(engine, source, "10:45")
    with engine.connect() as conn:
        assert load_projection(conn, order_id).state.status.value == "PENDING"


def test_close_trailing_gap_consumes_missing_tail(engine):
    order_id = submit_default(engine).auto_order_id
    source = FakeBarSource(scenario_bars())
    source.remove(TICKER, et(DAY, "10:29"))
    cycle(engine, source, "10:30", close_trailing_gap=True)
    assert segments(engine, order_id)[0][:2] == (et(DAY, "09:30"), et(DAY, "10:29"))


def test_unclosed_minute_is_neither_read_nor_missing(engine):
    order_id = submit_default(engine).auto_order_id
    source = FakeBarSource(scenario_bars())
    report = run_live_cycle(engine, feeds(source), code_version=CODE_VERSION, market_now=et(DAY, "10:30", 40),
                            close_trailing_gap=True)
    assert source.calls[0][2] == et(DAY, "10:30")
    assert report.outcomes[0].segment == (et(DAY, "09:30"), et(DAY, "10:29"))
    assert segments(engine, order_id)[0][:2] == (et(DAY, "09:30"), et(DAY, "10:29"))


def test_no_silent_provider_switch(engine):
    order_id = submit_default(engine).auto_order_id
    feed_key = f"{PRICE_SOURCE}:{TICKER}"
    backup = FakeBarSource(scenario_bars(), source="backup_feed")
    unknown = run_live_cycle(engine, feeds(backup), code_version=CODE_VERSION, market_now=et(DAY, "10:30"))
    assert unknown.ingest_failures[feed_key].startswith("UNKNOWN_DATA_SOURCE") and unknown.outcomes == ()

    primary = FakeBarSource(scenario_bars())
    primary.failing.add(TICKER)
    failed = run_live_cycle(engine, feeds(primary, backup), code_version=CODE_VERSION, market_now=et(DAY, "10:30"))
    assert feed_key in failed.ingest_failures and failed.outcomes == ()
    assert backup.calls == [] and segments(engine, order_id) == []


def test_cycle_skips_when_lock_is_held(engine):
    submit_default(engine)
    with engine.connect() as holder:
        holder.execute(text("SELECT pg_advisory_lock(:k)"), {"k": CYCLE_LOCK_KEY})
        report = cycle(engine, FakeBarSource(scenario_bars()), "10:30")
        holder.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": CYCLE_LOCK_KEY})
    assert report.skipped and count(engine, "evaluation_runs") == 0
    assert not cycle(engine, FakeBarSource(scenario_bars()), "10:30").skipped


def test_ingest_failure_skips_ticker_without_advancing_cursor(engine):
    order_id = submit_default(engine).auto_order_id
    source = FakeBarSource(scenario_bars())
    source.failing.add(TICKER)
    report = cycle(engine, source, "10:30")
    assert f"{PRICE_SOURCE}:{TICKER}" in report.ingest_failures and report.outcomes == ()
    assert segments(engine, order_id) == []
    with engine.connect() as conn:
        assert latest_run_status(conn, report.run_id)[1]["ingest_failures"] == report.ingest_failures


def test_frozen_orders_are_skipped_but_review_flags_keep_evaluating(engine):
    frozen_id = submit_default(engine, client_signal_id="frozen").auto_order_id
    flagged_id = submit_default(engine, client_signal_id="flagged").auto_order_id
    for order_id, command in ((frozen_id, "freeze"), (flagged_id, "flag_review")):
        with engine.begin() as conn:
            order = lock_order(conn, order_id)
            projection = load_projection(conn, order_id)
            result = getattr(V1, command)(projection.state, "SPLIT" if command == "freeze" else "MANUAL", "r")
            apply_result(conn, order, Direction.LONG, projection.next_seq, result)
    report = cycle(engine, FakeBarSource(scenario_bars()), "10:30")
    assert [(o.order_id, o.event_keys) for o in report.outcomes] == [(flagged_id, ("FILLED",))]


def test_integrity_error_isolates_one_order(engine):
    bad_id = submit_default(engine).auto_order_id
    good_id = submit_default(engine, client_signal_id="msft", ticker="MSFT").auto_order_id
    forged = Event(EventType.FILLED, "FILLED", et(DAY, "10:05"), price=Decimal("999"), qty=Decimal("1"))
    with engine.begin() as conn:
        order = lock_order(conn, bad_id)
        append_events(conn, order, [forged], load_projection(conn, bad_id).next_seq)
        conn.execute(text("UPDATE order_state SET next_seq = next_seq + 1 WHERE order_id = :id"), {"id": bad_id})
    source = FakeBarSource(scenario_bars() + scenario_bars(ticker="MSFT"))
    outcomes = {o.order_id: o for o in cycle(engine, source, "10:30").outcomes}
    assert outcomes[bad_id].error == errors.EVENT_HASH_CONFLICT
    assert outcomes[good_id].event_keys == ("FILLED",)
    with engine.connect() as conn:
        assert load_projection(conn, bad_id).state.frozen
        assert [e.prepared.event_key for e in stored_events(conn, bad_id)][-2:] == [
            "FROZEN:INTEGRITY", "NEEDS_REVIEW:INTEGRITY:EVENT_HASH_CONFLICT",
        ]
    assert count(engine, "integrity_incidents") == 1


def test_missing_projection_is_an_integrity_error(engine):
    order_id = submit_default(engine).auto_order_id
    with engine.begin() as conn:
        delete_projection(conn, order_id)
        run = start_run(conn, RunKind.LIVE, et(DAY, "23:00"), CODE_VERSION)
    outcome = evaluate_order(engine, order_id, run, market_now=et(DAY, "10:30"))
    assert outcome.error == errors.PROJECTION_MISSING
    assert count(engine, "integrity_incidents") == 1


def test_bars_are_read_as_of_the_run_watermark(engine):
    order_id = submit_default(engine).auto_order_id
    source = FakeBarSource(scenario_bars())
    report = cycle(engine, source, "10:30")
    assert report.data_as_of <= acquire_data_as_of(engine)
    with engine.connect() as conn:
        filled = [e for e in stored_events(conn, order_id) if e.prepared.type == "FILLED"][0]
        batch = conn.execute(select(tables.bar_batches.c.ingested_at).where(
            tables.bar_batches.c.batch_id == filled.prepared.bar_batch_id)).scalar_one()
    assert batch <= report.data_as_of
```

- [ ] **Step 2: Rodar para ver falhar**

Run: `uv run pytest tests/integration/test_cycle.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'virtual_orders.evaluator.cycle'`.

- [ ] **Step 3: Implementar**

`src/virtual_orders/evaluator/outcomes.py`:
```python
"""Per-order result of an evaluator operation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class OrderOutcome:
    order_id: UUID
    event_keys: tuple[str, ...] = ()
    segment: tuple[datetime, datetime] | None = None
    error: str | None = None
```

`src/virtual_orders/evaluator/cycle.py`:
```python
"""Live evaluator cycle (spec 5.3): ingest -> watermark -> per-order locked evaluation -> segment."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Connection, Engine, text

from virtual_orders.evaluator.context import load_order_context
from virtual_orders.evaluator.outcomes import OrderOutcome
from core.fills import get_fill_model
from virtual_orders.ledger.errors import PROJECTION_MISSING, LedgerIntegrityError
from virtual_orders.ledger.orders import load_projection, lock_order
from virtual_orders.ledger.quarantine import run_guarded
from virtual_orders.ledger.runs import (
    RunInfo, RunKind, RunStatus, SegmentRow, finish_run, last_segment_end, record_segment, start_run,
)
from virtual_orders.ledger.writes import apply_result
from virtual_orders.marketdata.asof import (
    acquire_data_as_of, bars_in_minutes, floor_minute, read_bars_as_of, selected_data_hash,
)
from virtual_orders.marketdata.ingest import ingest_bars
from virtual_orders.marketdata.gateway import MarketDataGateway, UnknownDataSource
from virtual_orders.marketdata.sources import SourceDataError, SourceUnavailable
from virtual_orders.storage.database import CYCLE_LOCK_KEY

_OPEN_ORDERS = text(
    """
    SELECT o.id AS order_id, o.price_source AS price_source, g.ticker AS ticker,
           COALESCE(MAX(s.bar_to) + interval '1 minute', o.evaluation_start_ts) AS resume_from
    FROM orders o
    JOIN order_state st ON st.order_id = o.id
    JOIN signals g ON g.id = o.signal_id
    LEFT JOIN order_eval_segments s ON s.order_id = o.id
    WHERE NOT o.replay AND NOT st.frozen AND st.status IN ('PENDING', 'OPEN', 'PARTIAL')
    GROUP BY o.id, o.price_source, g.ticker, o.evaluation_start_ts
    ORDER BY o.price_source, g.ticker, o.id
    """
)


@dataclass(frozen=True)
class OpenOrderCursor:
    order_id: UUID
    price_source: str
    ticker: str
    resume_from: datetime


@dataclass(frozen=True)
class CycleReport:
    skipped: bool
    run_id: UUID | None = None
    data_as_of: datetime | None = None
    outcomes: tuple[OrderOutcome, ...] = ()
    ingest_failures: dict[str, str] = field(default_factory=dict)


def try_cycle_lock(conn: Connection) -> bool:
    return bool(conn.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": CYCLE_LOCK_KEY}).scalar_one())


def release_cycle_lock(conn: Connection) -> None:
    conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": CYCLE_LOCK_KEY})


def open_order_cursors(conn: Connection) -> list[OpenOrderCursor]:
    return [OpenOrderCursor(row.order_id, row.price_source, row.ticker, row.resume_from) for row in conn.execute(_OPEN_ORDERS)]


def evaluate_order(
    engine: Engine, order_id: UUID, run: RunInfo, *, market_now: datetime, close_trailing_gap: bool = False
) -> OrderOutcome:
    def operation() -> OrderOutcome:
        with engine.begin() as conn:
            order = lock_order(conn, order_id)
            projection = load_projection(conn, order_id)
            if projection is None:
                raise LedgerIntegrityError(
                    PROJECTION_MISSING, "order_state missing; rebuild before evaluating", order_id=order_id
                )
            state = projection.state
            if order.replay or state.is_final or state.frozen:
                return OrderOutcome(order_id)
            signal, ctx = load_order_context(conn, order)
            last_to = last_segment_end(conn, order_id)
            start = ctx.evaluation_start_ts if last_to is None else ctx.calendar.next_expected_minute(last_to)
            horizon = min(ctx.valid_until_ts, floor_minute(market_now))
            if start >= horizon:
                return OrderOutcome(order_id)
            minutes = ctx.calendar.expected_minutes(start, horizon)
            ticker = signal.spec.ticker
            bars = bars_in_minutes(
                read_bars_as_of(conn, ticker, order.price_source, start, horizon, run.data_as_of), minutes
            )
            if close_trailing_gap:
                bar_to = minutes[-1] if minutes else None
            else:
                bar_to = bars[-1].ts if bars else None
            if bar_to is None:
                return OrderOutcome(order_id)
            window = [minute for minute in minutes if minute <= bar_to]
            used = [item for item in bars if item.ts <= bar_to]
            data_hash = selected_data_hash(order.price_source, ticker, window, used)
            result = get_fill_model(order.fill_model_version).run_bars(state, used, ctx)
            appended = apply_result(conn, order, signal.spec.direction, projection.next_seq, result)
            record_segment(conn, SegmentRow(
                order_id, run.run_id, window[0], bar_to, data_hash, appended.first_seq, len(appended.inserted)
            ))
            return OrderOutcome(order_id, tuple(p.event_key for p in appended.inserted), (window[0], bar_to))

    try:
        return run_guarded(engine, operation)
    except LedgerIntegrityError as error:
        return OrderOutcome(order_id, error=error.kind)


def run_live_cycle(
    engine: Engine,
    gateway: MarketDataGateway,
    *,
    code_version: str,
    market_now: datetime | None = None,
    close_trailing_gap: bool = False,
) -> CycleReport:
    started = datetime.now(UTC)
    now = (market_now or started).astimezone(UTC)
    lock_conn = engine.connect()
    try:
        if not try_cycle_lock(lock_conn):
            lock_conn.rollback()
            return CycleReport(skipped=True)
        lock_conn.commit()
        run: RunInfo | None = None
        try:
            with engine.connect() as conn:
                cursors = open_order_cursors(conn)
            resume: dict[tuple[str, str], datetime] = {}
            for cursor in cursors:
                key = (cursor.price_source, cursor.ticker)
                resume[key] = min(resume.get(key, cursor.resume_from), cursor.resume_from)
            failures: dict[str, str] = {}
            for (price_source, ticker), begin in sorted(resume.items()):
                feed_key = f"{price_source}:{ticker}"
                try:
                    ingest_bars(engine, gateway.bar_source(price_source), ticker, begin, floor_minute(now))
                except UnknownDataSource as exc:
                    failures[feed_key] = f"UNKNOWN_DATA_SOURCE: {exc}"
                except (SourceUnavailable, SourceDataError) as exc:
                    failures[feed_key] = str(exc)
            data_as_of = acquire_data_as_of(engine)
            with engine.begin() as conn:
                run = start_run(conn, RunKind.LIVE, data_as_of, code_version, started_at=started)
            outcomes = tuple(
                evaluate_order(engine, cursor.order_id, run, market_now=now, close_trailing_gap=close_trailing_gap)
                for cursor in cursors
                if f"{cursor.price_source}:{cursor.ticker}" not in failures
            )
            with engine.begin() as conn:
                finish_run(conn, run.run_id, RunStatus.COMPLETED, {
                    "orders": len(outcomes),
                    "ingest_failures": failures,
                    "integrity_errors": {str(o.order_id): o.error for o in outcomes if o.error},
                    "market_now": now,
                })
            return CycleReport(False, run.run_id, data_as_of, outcomes, failures)
        except Exception as exc:
            if run is not None:
                with engine.begin() as conn:
                    finish_run(conn, run.run_id, RunStatus.FAILED, {"error": repr(exc)})
            raise
        finally:
            release_cycle_lock(lock_conn)
            lock_conn.commit()
    finally:
        lock_conn.close()
```

- [ ] **Step 4: Rodar os testes**

Run: `uv run pytest tests/integration/test_cycle.py -v`
Expected: PASS (12 testes).

- [ ] **Step 5: Lint, tipos e commit**

Run: `uv run ruff check src tests && uv run mypy`
Expected: verde.

```bash
git add src/virtual_orders/evaluator/outcomes.py src/virtual_orders/evaluator/cycle.py tests/integration/test_cycle.py
git commit -m "feat(evaluator): live cycle with watermark reads, per-order locks and segments

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BKUdi9RGWWdXRXVYFWj1yp"
```

---

### Task 10: Comandos — cancelamento, validade, congelamento e revisão (com disputa `FOR UPDATE`)

Spec 3.5, 4.4 (validade), 5.1 (`POST /orders/{id}/cancel`), 5.2, 7 (disputa cancel × worker).

**Files:**
- Create: `src/virtual_orders/evaluator/commands.py`
- Create: `tests/integration/test_commands.py`

**Interfaces:**
- Consumes: `load_order_context` (Task 8); `OrderOutcome`, `evaluate_order`, `run_live_cycle` (Task 9); `lock_order`, `load_projection`, `apply_result`, `segment_containing`, `get_run`, `run_guarded`, `LedgerIntegrityError`, `PROJECTION_MISSING`, `PROCESSED_BAR_MISSING` (Task 7); `read_bars_as_of` (Task 6).
- Produces (`virtual_orders.evaluator.commands`):
  - `OrderAlreadyFinal(order_id: UUID)`
  - `CommandInput(conn, order: OrderRow, signal: SignalRow, ctx: OrderContext, projection: Projection, model: ModuleType)`
  - `apply_command(engine, order_id, command: Callable[[CommandInput], StepResult]) -> OrderOutcome` (integridade → quarentena e re-raise)
  - `processed_bar(conn, order: OrderRow, ticker: str, ts: datetime) -> Bar` — a versão exata do candle processado em `ts` (do `order.price_source`, segmento que o contém, lido as-of o `data_as_of` do run)
  - `cancel_order(engine, order_id, *, at: datetime | None = None, requested_by: str = "user") -> OrderOutcome`
  - `finalize_validity(engine, order_id, *, now: datetime) -> OrderOutcome`
  - `freeze_order(engine, order_id, *, reason: str, ref: str) -> OrderOutcome`; `flag_order_review(engine, order_id, *, reason: str, ref: str) -> OrderOutcome`
  - `expire_due_orders(engine, *, now: datetime) -> list[OrderOutcome]` (não replay, não final, não `frozen`, `valid_until_ts ≤ now`)

- [ ] **Step 1: Escrever os testes que falham**

`tests/integration/test_commands.py`:
```python
import threading
import time
from decimal import Decimal

import pytest

from core.domain.models import Direction
from virtual_orders.evaluator.commands import (
    OrderAlreadyFinal, cancel_order, expire_due_orders, finalize_validity, flag_order_review, freeze_order,
)
from virtual_orders.evaluator.cycle import evaluate_order, run_live_cycle
from core.fills import get_fill_model
from virtual_orders.ledger.events import stored_events
from virtual_orders.ledger.orders import load_projection, lock_order, read_projection_row
from virtual_orders.ledger.runs import RunKind, start_run
from virtual_orders.ledger.writes import apply_result
from virtual_orders.marketdata.asof import acquire_data_as_of
from virtual_orders.marketdata.ingest import ingest_bars
from tests.integration.support import CODE_VERSION, DAY, FakeBarSource, feeds, flat_raw, scenario_bars, submit_default
from tests.support import et

V1 = get_fill_model("v1")
AFTER_VALIDITY = et("2025-11-28", "16:30")


def keys(engine, order_id):
    with engine.connect() as conn:
        return [e.prepared.event_key for e in stored_events(conn, order_id)]


def cycle(engine, source, hm):
    return run_live_cycle(engine, feeds(source), code_version=CODE_VERSION, market_now=et(DAY, hm))


def test_cancel_pending_order_is_final_and_not_evaluated(engine):
    order_id = submit_default(engine).auto_order_id
    outcome = cancel_order(engine, order_id, at=et(DAY, "09:45"), requested_by="ricardo")
    assert outcome.event_keys == ("CANCELED",)
    with engine.connect() as conn:
        row = read_projection_row(conn, order_id)
    assert row["status"] == "CANCELED" and row["final_event_ts"] == et(DAY, "09:45")
    with pytest.raises(OrderAlreadyFinal):
        cancel_order(engine, order_id, at=et(DAY, "09:46"))
    assert cycle(engine, FakeBarSource(scenario_bars()), "10:30").outcomes == ()


def test_cancel_with_open_position_records_open_qty(engine):
    order_id = submit_default(engine).auto_order_id
    cycle(engine, FakeBarSource(scenario_bars()), "10:30")
    cancel_order(engine, order_id, at=et(DAY, "10:31"))
    with engine.connect() as conn:
        canceled = stored_events(conn, order_id)[-1].prepared
    assert canceled.payload["open_qty"] == "25"


def test_cancel_waits_for_worker_lock(engine):
    order_id = submit_default(engine).auto_order_id
    holder = engine.connect()
    transaction = holder.begin()
    lock_order(holder, order_id)
    results = []
    worker = threading.Thread(target=lambda: results.append(cancel_order(engine, order_id, at=et(DAY, "10:00"))))
    worker.start()
    time.sleep(0.3)
    assert worker.is_alive()
    transaction.commit()
    holder.close()
    worker.join(timeout=5)
    assert results[0].event_keys == ("CANCELED",)


def test_worker_reloads_projection_after_waiting_for_cancel(engine):
    order_id = submit_default(engine).auto_order_id
    ingest_bars(engine, FakeBarSource(scenario_bars()), "AAPL", et(DAY, "09:30"), et(DAY, "10:30"))
    with engine.begin() as conn:
        run = start_run(conn, RunKind.LIVE, acquire_data_as_of(engine), CODE_VERSION)
    holder = engine.connect()
    transaction = holder.begin()
    order = lock_order(holder, order_id)
    projection = load_projection(holder, order_id)
    results = []
    worker = threading.Thread(
        target=lambda: results.append(evaluate_order(engine, order_id, run, market_now=et(DAY, "10:30")))
    )
    worker.start()
    time.sleep(0.3)
    assert worker.is_alive()
    apply_result(holder, order, Direction.LONG, projection.next_seq,
                 V1.cancel(projection.state, et(DAY, "10:00"), "user"))
    transaction.commit()
    holder.close()
    worker.join(timeout=5)
    assert results[0].event_keys == () and results[0].error is None
    assert keys(engine, order_id) == ["ORDER_CREATED", "CANCELED"]


def test_pending_order_expires_after_validity(engine):
    order_id = submit_default(engine).auto_order_id
    assert finalize_validity(engine, order_id, now=et("2025-11-28", "12:59")).event_keys == ()
    outcome = finalize_validity(engine, order_id, now=AFTER_VALIDITY)
    assert outcome.event_keys == ("EXPIRED",)
    with engine.connect() as conn:
        assert read_projection_row(conn, order_id)["final_event_ts"] == et("2025-11-28", "13:00")


def test_open_position_time_exit_uses_processed_bar_version(engine):
    order_id = submit_default(engine).auto_order_id
    source = FakeBarSource(scenario_bars())
    cycle(engine, source, "10:30")
    source.load(flat_raw(DAY, "10:29", "10:30", 250))  # vendor correction after processing
    ingest_bars(engine, source, "AAPL", et(DAY, "10:29"), et(DAY, "10:30"))
    outcome = finalize_validity(engine, order_id, now=AFTER_VALIDITY)
    assert outcome.event_keys == ("TIME_EXIT", f"NEEDS_REVIEW:STALE_EXIT_BAR:{et('2025-11-28', '13:00').isoformat()}")
    with engine.connect() as conn:
        exit_event = [e.prepared for e in stored_events(conn, order_id) if e.prepared.type == "TIME_EXIT"][0]
    assert exit_event.bar_ts == et(DAY, "10:29")
    assert exit_event.payload["raw_price"] == "103"
    assert exit_event.price == Decimal("102.9485")


def test_expire_due_orders_only_touches_due_orders(engine):
    due = submit_default(engine).auto_order_id
    later = submit_default(engine, client_signal_id="later", valid_sessions=10).auto_order_id
    outcomes = expire_due_orders(engine, now=AFTER_VALIDITY)
    assert [(o.order_id, o.event_keys) for o in outcomes] == [(due, ("EXPIRED",))]
    assert keys(engine, later) == ["ORDER_CREATED"]


def test_freeze_and_review_commands_are_idempotent(engine):
    order_id = submit_default(engine).auto_order_id
    assert freeze_order(engine, order_id, reason="SPLIT", ref="2025-11-26").event_keys == (
        "FROZEN:SPLIT", "NEEDS_REVIEW:SPLIT:2025-11-26",
    )
    assert freeze_order(engine, order_id, reason="SPLIT", ref="2025-11-26").event_keys == ()
    assert flag_order_review(engine, order_id, reason="MANUAL", ref="x").event_keys == ("NEEDS_REVIEW:MANUAL:x",)
    assert flag_order_review(engine, order_id, reason="MANUAL", ref="x").event_keys == ()
    assert expire_due_orders(engine, now=AFTER_VALIDITY) == []
```

- [ ] **Step 2: Rodar para ver falhar**

Run: `uv run pytest tests/integration/test_commands.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'virtual_orders.evaluator.commands'`.

- [ ] **Step 3: Implementar**

`src/virtual_orders/evaluator/commands.py`:
```python
"""Non-bar commands on an order, all through the locked write path (spec 5.2)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from types import ModuleType
from uuid import UUID

from sqlalchemy import Connection, Engine, text

from core.domain.calendar import ONE_MINUTE
from core.domain.models import Bar, OrderContext, OrderStatus, StepResult
from virtual_orders.evaluator.context import load_order_context
from virtual_orders.evaluator.outcomes import OrderOutcome
from core.fills import get_fill_model
from virtual_orders.ledger.errors import PROCESSED_BAR_MISSING, PROJECTION_MISSING, LedgerIntegrityError
from virtual_orders.ledger.orders import OrderRow, Projection, SignalRow, load_projection, lock_order
from virtual_orders.ledger.quarantine import run_guarded
from virtual_orders.ledger.runs import get_run, segment_containing
from virtual_orders.ledger.writes import apply_result
from virtual_orders.marketdata.asof import read_bars_as_of


class OrderAlreadyFinal(Exception):
    def __init__(self, order_id: UUID) -> None:
        super().__init__(f"order {order_id} is already final")
        self.order_id = order_id


@dataclass(frozen=True)
class CommandInput:
    conn: Connection
    order: OrderRow
    signal: SignalRow
    ctx: OrderContext
    projection: Projection
    model: ModuleType


def apply_command(engine: Engine, order_id: UUID, command: Callable[[CommandInput], StepResult]) -> OrderOutcome:
    def operation() -> OrderOutcome:
        with engine.begin() as conn:
            order = lock_order(conn, order_id)
            projection = load_projection(conn, order_id)
            if projection is None:
                raise LedgerIntegrityError(PROJECTION_MISSING, "order_state missing", order_id=order_id)
            signal, ctx = load_order_context(conn, order)
            model = get_fill_model(order.fill_model_version)
            result = command(CommandInput(conn, order, signal, ctx, projection, model))
            appended = apply_result(conn, order, signal.spec.direction, projection.next_seq, result)
            return OrderOutcome(order_id, tuple(p.event_key for p in appended.inserted))

    return run_guarded(engine, operation)


def processed_bar(conn: Connection, order: OrderRow, ticker: str, ts: datetime) -> Bar:
    segment = segment_containing(conn, order.id, ts)
    bars = []
    if segment is not None:
        run = get_run(conn, segment.run_id)
        bars = read_bars_as_of(conn, ticker, order.price_source, ts, ts + ONE_MINUTE, run.data_as_of)
    if not bars:
        raise LedgerIntegrityError(
            PROCESSED_BAR_MISSING, f"no recorded version of the bar processed at {ts.isoformat()}", order_id=order.id
        )
    return bars[0]


def cancel_order(
    engine: Engine, order_id: UUID, *, at: datetime | None = None, requested_by: str = "user"
) -> OrderOutcome:
    moment = (at or datetime.now(UTC)).astimezone(UTC)

    def command(inp: CommandInput) -> StepResult:
        if inp.projection.state.is_final:
            raise OrderAlreadyFinal(order_id)
        result: StepResult = inp.model.cancel(inp.projection.state, moment, requested_by)
        return result

    return apply_command(engine, order_id, command)


def finalize_validity(engine: Engine, order_id: UUID, *, now: datetime) -> OrderOutcome:
    def command(inp: CommandInput) -> StepResult:
        state = inp.projection.state
        last_bar = None
        if state.status in (OrderStatus.OPEN, OrderStatus.PARTIAL) and state.last_bar_ts is not None:
            last_bar = processed_bar(inp.conn, inp.order, inp.signal.spec.ticker, state.last_bar_ts)
        result: StepResult = inp.model.apply_validity_end(state, inp.ctx, last_bar, now)
        return result

    return apply_command(engine, order_id, command)


def freeze_order(engine: Engine, order_id: UUID, *, reason: str, ref: str) -> OrderOutcome:
    return apply_command(engine, order_id, lambda inp: inp.model.freeze(inp.projection.state, reason, ref))


def flag_order_review(engine: Engine, order_id: UUID, *, reason: str, ref: str) -> OrderOutcome:
    return apply_command(engine, order_id, lambda inp: inp.model.flag_review(inp.projection.state, reason, ref))


_DUE_ORDERS = text(
    """
    SELECT o.id FROM orders o JOIN order_state st ON st.order_id = o.id
    WHERE NOT o.replay AND NOT st.frozen AND st.status IN ('PENDING', 'OPEN', 'PARTIAL')
      AND o.valid_until_ts <= :now
    ORDER BY o.valid_until_ts, o.id
    """
)


def expire_due_orders(engine: Engine, *, now: datetime) -> list[OrderOutcome]:
    with engine.connect() as conn:
        order_ids = list(conn.execute(_DUE_ORDERS, {"now": now}).scalars())
    return [finalize_validity(engine, order_id, now=now) for order_id in order_ids]
```

- [ ] **Step 4: Rodar os testes**

Run: `uv run pytest tests/integration/test_commands.py -v`
Expected: PASS (8 testes).

- [ ] **Step 5: Lint, tipos e commit**

Run: `uv run ruff check src tests && uv run mypy`
Expected: verde.

```bash
git add src/virtual_orders/evaluator/commands.py tests/integration/test_commands.py
git commit -m "feat(evaluator): cancel, validity end, freeze and review commands under order lock

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BKUdi9RGWWdXRXVYFWj1yp"
```

---

### Task 11: Ordens manuais — actionability as-of, cobertura e auditoria

Spec 3.4, 3.4.1, 5.1 (`POST /signals/{id}/orders`), 6; D1; D7; notas, entrada 4; decisões de detalhe 3 e 6.

**Files:**
- Create: `src/virtual_orders/evaluator/coverage.py`, `src/virtual_orders/evaluator/manual.py`
- Create: `tests/integration/test_manual_orders.py`

**Interfaces:**
- Consumes: `signal_context`, `DEFAULT_FILL_MODEL_VERSION` (Task 8); `run_live_cycle` (Task 9, só nos testes); `start_run`, `finish_run`, `RunKind`, `RunStatus`, `get_signal`, `OrderRow`, `persist_new_order` (Task 7); `MarketDataGateway`, `UnknownDataSource` (Task 4); `ingest_bars`, `acquire_data_as_of`, `read_bars_as_of`, `bars_in_minutes`, `selected_data_hash`, `floor_minute` (Task 6); `core.actionability.{build_manual_order, ManualOrderRejected}`.
- Produces (`virtual_orders.evaluator.manual`):
  - `ManualOrderError(code: str, reason: str | None = None, detail: dict | None = None)` — `code` ∈ `SIGNAL_EXPIRED` (422), `SIGNAL_NO_LONGER_ACTIONABLE` (422, com `reason`), `ACTIONABILITY_UNVERIFIABLE` (503)
  - `ManualOrderCreated(order_id: UUID, actionability_run_id: UUID, data_as_of: datetime, partial_bar_skipped: bool)`
  - `create_manual_order(engine, signal_id, *, config: FillConfig, code_version: str, price_source: str, created_at: datetime | None = None, gateway: MarketDataGateway | None = None, coverage_policy: CoveragePolicy = STRICT_PRIMARY_COVERAGE) -> ManualOrderCreated` — sempre grava um run `ACTIONABILITY` (COMPLETED para decisões, FAILED para dados indisponíveis) com `ticker`, `price_source`, `coverage_policy` e `result` no `detail`
  - `actionability_outcomes(conn, *, since: datetime) -> dict[str, int]` — contagem do último status de cada run `ACTIONABILITY` por `result` (observabilidade da D7)
- Produces (`virtual_orders.evaluator.coverage`): `CoverageDecision(verified: bool, unresolved: tuple[datetime, ...], evidence: dict[str, Any] = {})`; `Protocol CoveragePolicy`: `name: str`, `assess(*, price_source: str, ticker: str, expected: Sequence[datetime], bars: Sequence[Bar]) -> CoverageDecision`; `StrictPrimaryCoverage` (`name = "STRICT_PRIMARY"`); `STRICT_PRIMARY_COVERAGE`

- [ ] **Step 1: Escrever os testes que falham**

`tests/integration/test_manual_orders.py`:
```python
from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import select

from core.domain.hashing import sha256_hex
from core.domain.models import FillConfig
from virtual_orders.evaluator.cycle import run_live_cycle
from virtual_orders.evaluator.coverage import CoverageDecision
from virtual_orders.evaluator.manual import ManualOrderError, actionability_outcomes, create_manual_order
from virtual_orders.ledger.events import stored_events
from virtual_orders.ledger.orders import get_order, get_signal, read_projection_row
from virtual_orders.ledger.runs import RunStatus, latest_run_status
from virtual_orders.storage import tables
from tests.integration.support import (
    CODE_VERSION, DAY, PRICE_SOURCE, TICKER, FakeBarSource, count, feeds, flat_raw, raw, scenario_bars, submit_default,
)
from tests.support import et


def manual(engine, signal_id, source, hm, second=0, day=DAY, **kwargs):
    return create_manual_order(engine, signal_id, config=FillConfig(), code_version=CODE_VERSION,
                               price_source=PRICE_SOURCE, created_at=et(day, hm, second),
                               gateway=None if source is None else feeds(source), **kwargs)


def actionability_runs(engine):
    with engine.connect() as conn:
        ids = conn.execute(select(tables.evaluation_runs.c.run_id).where(
            tables.evaluation_runs.c.kind == "ACTIONABILITY")).scalars().all()
        return [latest_run_status(conn, run_id) for run_id in ids]


def test_actionable_signal_creates_manual_order_with_audit(engine):
    signal_id = submit_default(engine).signal_id
    created = manual(engine, signal_id, FakeBarSource(flat_raw(DAY, "09:30", "13:00", 105)), "13:00")
    with engine.connect() as conn:
        order = get_order(conn, created.order_id)
        signal = get_signal(conn, signal_id)
        (event,) = stored_events(conn, order.id)
        row = read_projection_row(conn, order.id)
        status, detail = latest_run_status(conn, created.actionability_run_id)
    payload = event.prepared.payload
    assert order.origin.value == "MANUAL_USER" and order.created_at == et(DAY, "13:00")
    assert order.evaluation_start_ts == et(DAY, "13:00") and order.valid_until_ts == signal.valid_until_ts
    assert payload["partial_bar_skipped"] is False and payload["skipped_bar_ts"] is None
    assert payload["actionability_run_id"] == str(created.actionability_run_id)
    assert payload["inherited_signal_state"]["status"] == "PENDING"
    assert payload["inherited_signal_state_hash"] == sha256_hex(payload["inherited_signal_state"])
    assert payload["actionability_selected_data_hash"] == detail["selected_data_hash"]
    assert status is RunStatus.COMPLETED and detail["result"] == "ACTIONABLE"
    assert detail["coverage_policy"] == "STRICT_PRIMARY" and payload["actionability_coverage_policy"] == "STRICT_PRIMARY"
    assert detail["price_source"] == PRICE_SOURCE and order.price_source == PRICE_SOURCE
    assert detail["bar_from"] == et(DAY, "09:30").isoformat() and detail["bar_to"] == et(DAY, "12:59").isoformat()
    assert row["status"] == "PENDING" and created.partial_bar_skipped is False


def test_click_inside_a_minute_skips_the_partial_bar(engine):
    signal_id = submit_default(engine).signal_id
    created = manual(engine, signal_id, FakeBarSource(flat_raw(DAY, "09:30", "13:00", 105)), "13:00", second=18)
    with engine.connect() as conn:
        order = get_order(conn, created.order_id)
        payload = stored_events(conn, order.id)[0].prepared.payload
    assert created.partial_bar_skipped and payload["skipped_bar_ts"] == et(DAY, "13:00").isoformat()
    assert order.evaluation_start_ts == et(DAY, "13:01")


@pytest.mark.parametrize("bars, hm, code, reason", [
    (scenario_bars() + flat_raw(DAY, "12:51", "13:30", 108), "13:00", "SIGNAL_NO_LONGER_ACTIONABLE", "TARGET_REACHED"),
    (scenario_bars(), "10:30", "SIGNAL_NO_LONGER_ACTIONABLE", "ENTRY_OPPORTUNITY_ALREADY_OCCURRED"),
    (flat_raw(DAY, "09:30", "10:20", 105) + [raw(DAY, "10:20", 98, 98.5, 96, 98)] + flat_raw(DAY, "10:21", "13:00", 99),
     "13:00", "SIGNAL_NO_LONGER_ACTIONABLE", "INVALIDATED"),
])
def test_finished_thesis_is_rejected_and_audited(engine, bars, hm, code, reason):
    signal_id = submit_default(engine, auto_order=False).signal_id
    with pytest.raises(ManualOrderError) as caught:
        manual(engine, signal_id, FakeBarSource(bars), hm)
    assert (caught.value.code, caught.value.reason) == (code, reason)
    assert count(engine, "orders") == 0
    ((status, detail),) = actionability_runs(engine)
    assert status is RunStatus.COMPLETED and detail["result"] == code and detail["reason"] == reason


def test_expired_signal_is_rejected(engine):
    signal_id = submit_default(engine, auto_order=False).signal_id
    with pytest.raises(ManualOrderError) as caught:
        manual(engine, signal_id, None, "13:00", day="2025-11-28")
    assert (caught.value.code, caught.value.reason) == ("SIGNAL_EXPIRED", None)


def test_missing_minute_makes_actionability_unverifiable(engine):
    signal_id = submit_default(engine, auto_order=False).signal_id
    bars = [b for b in flat_raw(DAY, "09:30", "13:00", 105) if b.ts != et(DAY, "11:11")]
    with pytest.raises(ManualOrderError) as caught:
        manual(engine, signal_id, FakeBarSource(bars), "13:00")
    assert caught.value.code == "ACTIONABILITY_UNVERIFIABLE"
    assert caught.value.detail["missing_count"] == 1
    ((status, detail),) = actionability_runs(engine)
    assert status is RunStatus.FAILED and detail["missing_minutes"] == [et(DAY, "11:11").isoformat()]


def test_provider_failure_makes_actionability_unverifiable(engine):
    signal_id = submit_default(engine, auto_order=False).signal_id
    source = FakeBarSource(flat_raw(DAY, "09:30", "13:00", 105))
    source.failing.add(TICKER)
    with pytest.raises(ManualOrderError) as caught:
        manual(engine, signal_id, source, "13:00")
    assert caught.value.code == "ACTIONABILITY_UNVERIFIABLE" and "ingest_error" in caught.value.detail


def test_manual_order_inherits_lost_zone_and_enters_only_after_reclaim(engine):
    signal_id = submit_default(engine, auto_order=False).signal_id
    source = FakeBarSource(
        [raw(DAY, "09:30", 99.5, 99.8, 99, 99.2)] + flat_raw(DAY, "09:31", "11:00", 99)
        + [raw(DAY, "11:00", 99.5, 100.8, 99.4, 100.5), raw(DAY, "11:01", 101, 101.5, 100.6, 101.2)]
        + flat_raw(DAY, "11:02", "11:10", 101)
    )
    created = manual(engine, signal_id, source, "11:00")
    with engine.connect() as conn:
        inherited = stored_events(conn, created.order_id)[0].prepared.payload["inherited_signal_state"]
    assert inherited["zone_lost"] is True

    run_live_cycle(engine, feeds(source), code_version=CODE_VERSION, market_now=et(DAY, "11:05"))
    with engine.connect() as conn:
        events = [e.prepared for e in stored_events(conn, created.order_id)]
    assert [e.type for e in events] == ["ORDER_CREATED", "ZONE_RECLAIMED", "FILLED"]
    assert events[2].bar_ts == et(DAY, "11:01") and events[2].payload["entry_path"] == "RECLAIMED"


def test_manual_order_does_not_depend_on_auto_order(engine):
    bars = flat_raw(DAY, "09:30", "13:00", 105)
    with_auto = submit_default(engine, client_signal_id="with-auto").signal_id
    without_auto = submit_default(engine, client_signal_id="without-auto", auto_order=False).signal_id
    first = manual(engine, with_auto, FakeBarSource(bars), "13:00")
    second = manual(engine, without_auto, FakeBarSource(bars), "13:00")
    with engine.connect() as conn:
        a = stored_events(conn, first.order_id)[0].prepared.payload
        b = stored_events(conn, second.order_id)[0].prepared.payload
    assert a["inherited_signal_state_hash"] == b["inherited_signal_state_hash"]
    assert a["actionability_selected_data_hash"] == b["actionability_selected_data_hash"]
    assert isinstance(UUID(a["actionability_run_id"]), UUID)


class OneMinuteShortPolicy:
    """Test double: reports the first expected minute as unresolved and records evidence."""

    name = "TEST_ONE_MINUTE_SHORT"

    def assess(self, *, price_source, ticker, expected, bars):
        return CoverageDecision(False, tuple(expected[:1]), {"checked_source": price_source})


def test_coverage_policy_is_pluggable_and_recorded(engine):
    signal_id = submit_default(engine, auto_order=False).signal_id
    with pytest.raises(ManualOrderError) as caught:
        manual(engine, signal_id, FakeBarSource(flat_raw(DAY, "09:30", "13:00", 105)), "13:00",
               coverage_policy=OneMinuteShortPolicy())
    assert caught.value.code == "ACTIONABILITY_UNVERIFIABLE"
    ((status, detail),) = actionability_runs(engine)
    assert status is RunStatus.FAILED and detail["coverage_policy"] == "TEST_ONE_MINUTE_SHORT"
    assert detail["coverage_evidence"] == {"checked_source": PRICE_SOURCE}
    assert detail["missing_minutes"] == [et(DAY, "09:30").isoformat()]


def test_actionability_outcomes_are_observable(engine):
    signal_id = submit_default(engine, auto_order=False).signal_id
    source = FakeBarSource(flat_raw(DAY, "09:30", "13:00", 105))
    manual(engine, signal_id, source, "13:00")
    with pytest.raises(ManualOrderError):
        manual(engine, signal_id, source, "13:40")  # 13:00-13:39 never delivered
    with engine.connect() as conn:
        counts = actionability_outcomes(conn, since=datetime(2000, 1, 1, tzinfo=UTC))
    assert counts == {"ACTIONABLE": 1, "ACTIONABILITY_UNVERIFIABLE": 1}
```

- [ ] **Step 2: Rodar para ver falhar**

Run: `uv run pytest tests/integration/test_manual_orders.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'virtual_orders.evaluator.manual'`.

- [ ] **Step 3: Implementar**

`src/virtual_orders/evaluator/coverage.py`:
```python
"""Coverage policies for actionability (D7): a policy, not a domain truth.

STRICT_PRIMARY treats every expected minute without a bar in the order's price_source as unresolved, which
makes actionability unverifiable (503). A future policy may consult a secondary provider; it must then return
the sources and versions it used in `evidence` (persisted in the ACTIONABILITY run) and never substitute bars
silently. Actionability itself always runs on the primary price_source bars.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from core.domain.models import Bar


@dataclass(frozen=True)
class CoverageDecision:
    verified: bool
    unresolved: tuple[datetime, ...]
    evidence: dict[str, Any] = field(default_factory=dict)


class CoveragePolicy(Protocol):
    name: str

    def assess(
        self, *, price_source: str, ticker: str, expected: Sequence[datetime], bars: Sequence[Bar]
    ) -> CoverageDecision: ...


class StrictPrimaryCoverage:
    name = "STRICT_PRIMARY"

    def assess(
        self, *, price_source: str, ticker: str, expected: Sequence[datetime], bars: Sequence[Bar]
    ) -> CoverageDecision:
        present = {item.ts for item in bars}
        unresolved = tuple(minute for minute in expected if minute not in present)
        return CoverageDecision(not unresolved, unresolved)


STRICT_PRIMARY_COVERAGE = StrictPrimaryCoverage()
```

`src/virtual_orders/evaluator/manual.py`:
```python
"""MANUAL_USER orders (spec 3.4.1): actionability as-of the click, coverage-gated by policy and audited."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Connection, Engine, text

from core.actionability import ManualOrderRejected, build_manual_order
from core.domain.models import FillConfig, OrderContext, Origin
from core.fills import get_fill_model
from virtual_orders.evaluator.context import DEFAULT_FILL_MODEL_VERSION, signal_context
from virtual_orders.evaluator.coverage import STRICT_PRIMARY_COVERAGE, CoveragePolicy
from virtual_orders.ledger.orders import OrderRow, SignalRow, get_signal
from virtual_orders.ledger.runs import RunInfo, RunKind, RunStatus, finish_run, start_run
from virtual_orders.ledger.writes import persist_new_order
from virtual_orders.marketdata.asof import (
    acquire_data_as_of, bars_in_minutes, floor_minute, read_bars_as_of, selected_data_hash,
)
from virtual_orders.marketdata.gateway import MarketDataGateway, UnknownDataSource
from virtual_orders.marketdata.ingest import ingest_bars
from virtual_orders.marketdata.sources import SourceDataError, SourceUnavailable

SIGNAL_EXPIRED = "SIGNAL_EXPIRED"
ACTIONABILITY_UNVERIFIABLE = "ACTIONABILITY_UNVERIFIABLE"
MAX_REPORTED_MISSING = 50

_OUTCOMES = text(
    """
    SELECT latest.detail->>'result' AS result, COUNT(*) AS runs
    FROM evaluation_runs r
    JOIN LATERAL (
        SELECT detail FROM evaluation_run_status s WHERE s.run_id = r.run_id ORDER BY s.id DESC LIMIT 1
    ) latest ON true
    WHERE r.kind = 'ACTIONABILITY' AND r.started_at >= :since
    GROUP BY 1
    """
)


class ManualOrderError(Exception):
    def __init__(self, code: str, reason: str | None = None, detail: dict[str, Any] | None = None) -> None:
        super().__init__(code if reason is None else f"{code}: {reason}")
        self.code = code
        self.reason = reason
        self.detail: dict[str, Any] = detail or {}


@dataclass(frozen=True)
class ManualOrderCreated:
    order_id: UUID
    actionability_run_id: UUID
    data_as_of: datetime
    partial_bar_skipped: bool


Decision = ManualOrderCreated | ManualOrderError


def _decide(
    conn: Connection,
    run: RunInfo,
    signal: SignalRow,
    ctx: OrderContext,
    config: FillConfig,
    code_version: str,
    decided_at: datetime,
    price_source: str,
    policy: CoveragePolicy,
) -> tuple[Decision, dict[str, Any]]:
    if decided_at >= signal.valid_until_ts:
        return ManualOrderError(SIGNAL_EXPIRED), {}
    ticker = signal.spec.ticker
    start = signal.evaluation_start_ts
    bar_end = min(floor_minute(decided_at), signal.valid_until_ts)
    minutes = ctx.calendar.expected_minutes(start, bar_end) if bar_end > start else []
    bars = []
    if minutes:
        bars = bars_in_minutes(read_bars_as_of(conn, ticker, price_source, start, bar_end, run.data_as_of), minutes)
    data_hash = selected_data_hash(price_source, ticker, minutes, bars)
    coverage = policy.assess(price_source=price_source, ticker=ticker, expected=minutes, bars=bars)
    audit: dict[str, Any] = {
        "bar_from": minutes[0] if minutes else None,
        "bar_to": minutes[-1] if minutes else None,
        "selected_data_hash": data_hash,
        "coverage_evidence": coverage.evidence,
    }
    if not coverage.verified:
        unresolved = sorted(coverage.unresolved)
        detail = {**audit, "missing_count": len(unresolved), "missing_minutes": unresolved[:MAX_REPORTED_MISSING]}
        return ManualOrderError(ACTIONABILITY_UNVERIFIABLE, None, detail), audit

    model = get_fill_model(DEFAULT_FILL_MODEL_VERSION)
    extra = {
        "actionability_run_id": run.run_id,
        "actionability_data_as_of": run.data_as_of,
        "actionability_selected_data_hash": data_hash,
        "actionability_coverage_policy": policy.name,
    }
    try:
        built = build_manual_order(model, ctx, bars, decided_at, extra_payload=extra)
    except ManualOrderRejected as rejected:
        decision = rejected.actionability
        code = decision.error_code or SIGNAL_EXPIRED
        reason = None if code == SIGNAL_EXPIRED else decision.reason.value
        return ManualOrderError(code, reason, audit), audit

    order = OrderRow(
        id=uuid4(), signal_id=signal.id, origin=Origin.MANUAL_USER, created_at=decided_at,
        evaluation_start_ts=built.context.evaluation_start_ts, valid_until_ts=built.context.valid_until_ts,
        fill_model_version=DEFAULT_FILL_MODEL_VERSION, config=config, code_version=code_version,
        risk_amount=config.risk_amount, price_source=price_source,
    )
    persist_new_order(conn, order, signal.spec.direction, built.created)
    skipped = bool(built.created.events[0].payload["partial_bar_skipped"])
    return ManualOrderCreated(order.id, run.run_id, run.data_as_of, skipped), audit


def create_manual_order(
    engine: Engine,
    signal_id: UUID,
    *,
    config: FillConfig,
    code_version: str,
    price_source: str,
    created_at: datetime | None = None,
    gateway: MarketDataGateway | None = None,
    coverage_policy: CoveragePolicy = STRICT_PRIMARY_COVERAGE,
) -> ManualOrderCreated:
    with engine.connect() as conn:
        signal = get_signal(conn, signal_id)
    ingest_error: str | None = None
    if gateway is not None:
        wall = (created_at or datetime.now(UTC)).astimezone(UTC)
        try:
            ingest_bars(engine, gateway.bar_source(price_source), signal.spec.ticker, signal.evaluation_start_ts,
                        min(floor_minute(wall), signal.valid_until_ts))
        except UnknownDataSource as exc:
            ingest_error = f"UNKNOWN_DATA_SOURCE: {exc}"
        except (SourceUnavailable, SourceDataError) as exc:
            ingest_error = str(exc)
    data_as_of = acquire_data_as_of(engine)
    decided_at = (created_at or data_as_of).astimezone(UTC)
    ctx = signal_context(signal, config)
    base = {"signal_id": signal.id, "ticker": signal.spec.ticker, "price_source": price_source,
            "coverage_policy": coverage_policy.name}

    with engine.begin() as conn:
        run = start_run(conn, RunKind.ACTIONABILITY, data_as_of, code_version,
                        detail={**base, "created_at": decided_at})
        audit: dict[str, Any] = {}
        outcome: Decision
        if ingest_error is not None:
            outcome = ManualOrderError(ACTIONABILITY_UNVERIFIABLE, None, {"ingest_error": ingest_error})
        else:
            outcome, audit = _decide(conn, run, signal, ctx, config, code_version, decided_at, price_source,
                                     coverage_policy)
        if isinstance(outcome, ManualOrderError):
            status = RunStatus.FAILED if outcome.code == ACTIONABILITY_UNVERIFIABLE else RunStatus.COMPLETED
            finish_run(conn, run.run_id, status,
                       {**base, "result": outcome.code, "reason": outcome.reason, **outcome.detail})
        else:
            finish_run(conn, run.run_id, RunStatus.COMPLETED,
                       {**base, "result": "ACTIONABLE", "order_id": outcome.order_id, **audit})
    if isinstance(outcome, ManualOrderError):
        raise outcome
    return outcome


def actionability_outcomes(conn: Connection, *, since: datetime) -> dict[str, int]:
    """Observability for D7: how often each actionability result occurs, e.g. ACTIONABILITY_UNVERIFIABLE."""
    return {
        row.result: int(row.runs)
        for row in conn.execute(_OUTCOMES, {"since": since})
        if row.result is not None
    }
```

- [ ] **Step 4: Rodar os testes**

Run: `uv run pytest tests/integration/test_manual_orders.py -v`
Expected: PASS (12 testes, contando os parametrizados).

- [ ] **Step 5: Lint, tipos e commit**

Run: `uv run ruff check src tests && uv run mypy`
Expected: verde.

```bash
git add src/virtual_orders/evaluator/coverage.py src/virtual_orders/evaluator/manual.py tests/integration/test_manual_orders.py
git commit -m "feat(evaluator): manual orders gated by as-of actionability and a pluggable coverage policy

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BKUdi9RGWWdXRXVYFWj1yp"
```

---

### Task 12: Regeneração do histórico e reconstrução da projeção (D2, teste `A == B`)

Spec 6 ("Projeção inconsistente"), 10 (D2); notas, entradas 1 e 2.

**Algoritmo (`regenerate_history`)** — percorre o `seq` da ordem:
1. `seq = 1` precisa ser `ORDER_CREATED`; é regenerado com `new_order_state(ctx, inherited, extra_payload)` a partir do contexto persistido (estado herdado e chaves extras vêm do próprio payload; o hash tem de bater).
2. Enquanto houver segmento com `first_seq == seq` (ordem `first_seq, bar_from`): lê os candles do segmento as-of o `data_as_of` do run, exige `selected_data_hash` igual, roda `run_bars`, deduplica como o ledger e compara `event_count` eventos com os armazenados.
3. Senão, o evento armazenado em `seq` é um **comando**: `CANCELED` → `cancel`; `FROZEN` → `freeze`; `NEEDS_REVIEW` → `flag_review`; `DIVIDEND` → `apply_dividend(validated=True)`; `EXPIRED` → `apply_validity_end(last_bar=None)`; `TIME_EXIT` → `apply_validity_end` com o candle `(bar_ts, bar_batch_id)` exato. `DATA_QUALITY`/`DATA_GAP` são **registros** (aceitos como estão, sem comando). Qualquer outro tipo fora de segmento é divergência.
4. Sobrou segmento não consumido → divergência.

**Files:**
- Create: `src/virtual_orders/evaluator/history.py`, `src/virtual_orders/evaluator/rebuild.py`
- Create: `tests/integration/test_rebuild.py`

**Interfaces:**
- Consumes: `load_order_context` (Task 8); `stored_events`, `StoredEvent`, `dedupe_events`, `list_segments`, `get_run`, `SegmentRow`, `lock_order`, `load_projection`, `projection_row`, `read_projection_row`, `save_projection`, `Projection`, `HistoryDivergence`, `ProjectionIntegrityError`, `LedgerIntegrityError`, `run_guarded` (Task 7); `read_bars_as_of`, `read_bar_version`, `bars_in_minutes`, `selected_data_hash` (Task 6); `PreparedEvent`, `parse_ts` (Task 3). Testes usam `run_live_cycle` (Task 9), comandos (Task 10) e `create_manual_order` (Task 11).
- Produces (`virtual_orders.evaluator.history`):
  - `CREATED_BASE_KEYS: frozenset[str]`; `RECORD_TYPES = frozenset({"DATA_QUALITY", "DATA_GAP"})`
  - `ItemKind(StrEnum)`: `CREATED`, `SEGMENT`, `COMMAND`, `RECORD`
  - `HistoryItem(kind: ItemKind, events: tuple[PreparedEvent, ...], segment: SegmentRow | None = None)`
  - `RegeneratedHistory(order: OrderRow, signal: SignalRow, ctx: OrderContext, state: OrderState, items: tuple[HistoryItem, ...], next_seq: int)`
  - `inherited_from_payload(payload: Mapping[str, Any]) -> GatingState | None`
  - `regenerate_created(model: ModuleType, ctx: OrderContext, payload: Mapping[str, Any]) -> StepResult`
  - `regenerate_history(conn, order: OrderRow) -> RegeneratedHistory` — levanta `HistoryDivergence(reason, diff)`
- Produces (`virtual_orders.evaluator.rebuild`):
  - `rebuild_projection(engine, order_id) -> dict[str, Any]` — sem projeção: grava a reconstruída; com projeção: exige igualdade de todas as colunas; divergência → `ProjectionIntegrityError` (incidente + ordem `frozen`)
  - `rebuild_all_projections(engine) -> dict[UUID, str]` (`"OK"` ou o `kind` do erro)

- [ ] **Step 1: Escrever os testes que falham**

`tests/integration/test_rebuild.py`:
```python
from decimal import Decimal

import pytest
from sqlalchemy import select, text

from core.domain.models import FillConfig
from virtual_orders.evaluator.commands import cancel_order, finalize_validity, flag_order_review
from virtual_orders.evaluator.cycle import run_live_cycle
from virtual_orders.evaluator.manual import create_manual_order
from virtual_orders.evaluator.rebuild import rebuild_all_projections, rebuild_projection
from virtual_orders.ledger import errors
from virtual_orders.ledger.events import stored_events
from virtual_orders.ledger.orders import delete_projection, load_projection, read_projection_row
from virtual_orders.ledger.runs import get_run, list_segments
from virtual_orders.marketdata.ingest import ingest_bars
from virtual_orders.storage import tables
from tests.integration.support import (
    CODE_VERSION, DAY, PRICE_SOURCE, TICKER, FakeBarSource, backdated_batch, count, feeds, flat_raw, raw, scenario_bars,
    submit_default,
)
from tests.support import et

AFTER_VALIDITY = et("2025-11-28", "16:30")


def cycles(engine, source, *hms):
    for hm in hms:
        run_live_cycle(engine, feeds(source), code_version=CODE_VERSION, market_now=et(DAY, hm))


def snapshot(engine, order_id):
    with engine.connect() as conn:
        return read_projection_row(conn, order_id)


def delete_and_rebuild(engine, order_id):
    with engine.begin() as conn:
        delete_projection(conn, order_id)
    assert snapshot(engine, order_id) is None
    rebuilt = rebuild_projection(engine, order_id)
    return rebuilt, snapshot(engine, order_id)


def test_d2_projection_rebuilt_from_history_equals_original(engine):
    """Spec 10, D2: create -> ~200 bars -> close -> A; delete order_state; rebuild -> B; A == B."""
    order_id = submit_default(engine).auto_order_id
    cycles(engine, FakeBarSource(scenario_bars()), "10:30", "11:30", "13:00")
    projection_a = snapshot(engine, order_id)
    assert projection_a["status"] == "CLOSED"
    with engine.connect() as conn:
        processed = sum(int((s.bar_to - s.bar_from).total_seconds() // 60) + 1 for s in list_segments(conn, order_id))
    assert processed == 201  # all minutes 09:30-12:50 fall in one session

    rebuilt, projection_b = delete_and_rebuild(engine, order_id)
    assert projection_b == projection_a
    assert rebuilt == projection_a
    assert rebuild_projection(engine, order_id) == projection_a  # verify mode on the stored projection


def test_rebuild_is_immune_to_later_vendor_corrections(engine):
    order_id = submit_default(engine).auto_order_id
    source = FakeBarSource(scenario_bars())
    cycles(engine, source, "10:30", "11:30", "13:00")
    projection_a = snapshot(engine, order_id)
    source.load([raw(DAY, "10:05", 104, 104, 104, 104)])  # would remove the fill
    ingest_bars(engine, source, TICKER, et(DAY, "10:05"), et(DAY, "10:06"))
    assert delete_and_rebuild(engine, order_id)[1] == projection_a


def test_rebuild_interleaves_commands_and_segments(engine):
    order_id = submit_default(engine).auto_order_id
    source = FakeBarSource(scenario_bars())
    cycles(engine, source, "10:30")
    flag_order_review(engine, order_id, reason="MANUAL", ref="looked-odd")
    cycles(engine, source, "11:30", "11:30")
    cancel_order(engine, order_id, at=et(DAY, "11:45"))
    projection_a = snapshot(engine, order_id)
    with engine.connect() as conn:
        types = [e.prepared.type for e in stored_events(conn, order_id)]
    assert types == ["ORDER_CREATED", "FILLED", "NEEDS_REVIEW", "TARGET1_HIT", "CANCELED"]
    assert delete_and_rebuild(engine, order_id)[1] == projection_a


def test_rebuild_after_time_exit_with_stale_bar(engine):
    order_id = submit_default(engine).auto_order_id
    cycles(engine, FakeBarSource(scenario_bars()), "10:30")
    finalize_validity(engine, order_id, now=AFTER_VALIDITY)
    projection_a = snapshot(engine, order_id)
    assert projection_a["status"] == "CLOSED" and projection_a["needs_review"]
    assert delete_and_rebuild(engine, order_id)[1] == projection_a


def test_rebuild_expired_pending_order(engine):
    order_id = submit_default(engine).auto_order_id
    finalize_validity(engine, order_id, now=AFTER_VALIDITY)
    projection_a = snapshot(engine, order_id)
    assert delete_and_rebuild(engine, order_id)[1] == projection_a


def test_rebuild_manual_order_with_inherited_state_and_audit_payload(engine):
    signal_id = submit_default(engine, auto_order=False).signal_id
    source = FakeBarSource(
        [raw(DAY, "09:30", 99.5, 99.8, 99, 99.2)] + flat_raw(DAY, "09:31", "11:00", 99)
        + [raw(DAY, "11:00", 99.5, 100.8, 99.4, 100.5), raw(DAY, "11:01", 101, 101.5, 100.6, 101.2)]
        + flat_raw(DAY, "11:02", "11:10", 101)
    )
    created = create_manual_order(engine, signal_id, config=FillConfig(), code_version=CODE_VERSION,
                                  price_source=PRICE_SOURCE, created_at=et(DAY, "10:59", 30), gateway=feeds(source))
    cycles(engine, source, "11:05")
    projection_a = snapshot(engine, created.order_id)
    assert projection_a["status"] == "OPEN" and projection_a["entry_path"] == "RECLAIMED"
    assert delete_and_rebuild(engine, created.order_id)[1] == projection_a


def test_backdated_version_breaks_regeneration_and_freezes(engine):
    order_id = submit_default(engine).auto_order_id
    cycles(engine, FakeBarSource(scenario_bars()), "10:30")
    with engine.connect() as conn:
        run = get_run(conn, list_segments(conn, order_id)[0].run_id)
    backdated_batch(engine, TICKER, [raw(DAY, "10:05", 104, 104, 104, 104)], run.data_as_of)

    with pytest.raises(errors.ProjectionIntegrityError) as caught:
        rebuild_projection(engine, order_id)
    assert caught.value.detail["reason"] == "SELECTED_DATA_HASH_MISMATCH"
    with engine.connect() as conn:
        incident = conn.execute(select(tables.integrity_incidents)).one()
        assert load_projection(conn, order_id).state.frozen
    assert incident.kind == errors.PROJECTION_INTEGRITY_ERROR and incident.order_id == order_id


def test_tampered_projection_is_detected(engine):
    order_id = submit_default(engine).auto_order_id
    cycles(engine, FakeBarSource(scenario_bars()), "10:30", "11:30", "13:00")
    with engine.begin() as conn:
        conn.execute(text("UPDATE order_state SET r_multiple = 9 WHERE order_id = :id"), {"id": order_id})
    with pytest.raises(errors.ProjectionIntegrityError) as caught:
        rebuild_projection(engine, order_id)
    differences = caught.value.detail["differences"]
    assert set(differences) == {"r_multiple"}
    assert differences["r_multiple"]["rebuilt"] == Decimal("1.75")
    assert count(engine, "integrity_incidents") == 1


def test_rebuild_all_reports_each_order(engine):
    first = submit_default(engine).auto_order_id
    second = submit_default(engine, client_signal_id="other").auto_order_id
    cycles(engine, FakeBarSource(scenario_bars()), "10:30")
    assert rebuild_all_projections(engine) == {first: "OK", second: "OK"}
```

- [ ] **Step 2: Rodar para ver falhar**

Run: `uv run pytest tests/integration/test_rebuild.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'virtual_orders.evaluator.rebuild'`.

- [ ] **Step 3: Implementar a regeneração**

`src/virtual_orders/evaluator/history.py`:
```python
"""Regenerate an order's authoritative history (spec 10, D2; Plan 1 close-out entry 1).

Domain facts (order_events) + evaluation facts (runs, segments) + market facts (bars as-of) must
reproduce every stored (event_key, payload_hash) in seq order.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from types import ModuleType
from typing import Any

from sqlalchemy import Connection

from core.domain.calendar import ONE_MINUTE
from core.domain.models import Event, GatingState, OrderContext, OrderState, OrderStatus, StepResult
from virtual_orders.evaluator.context import load_order_context
from core.fills import get_fill_model
from virtual_orders.ledger.errors import HistoryDivergence, LedgerIntegrityError
from virtual_orders.ledger.events import StoredEvent, dedupe_events, stored_events
from virtual_orders.ledger.orders import OrderRow, SignalRow
from virtual_orders.ledger.runs import SegmentRow, get_run, list_segments
from virtual_orders.marketdata.asof import bars_in_minutes, read_bar_version, read_bars_as_of, selected_data_hash
from virtual_orders.storage.codec import PreparedEvent, parse_ts

CREATED_BASE_KEYS = frozenset({
    "fill_model_version", "signal", "config", "evaluation_start_ts", "valid_until_ts",
    "calendar_sessions_hash", "inherited_signal_state", "inherited_signal_state_hash",
})
RECORD_TYPES = frozenset({"DATA_QUALITY", "DATA_GAP"})


class ItemKind(StrEnum):
    CREATED = "CREATED"
    SEGMENT = "SEGMENT"
    COMMAND = "COMMAND"
    RECORD = "RECORD"


@dataclass(frozen=True)
class HistoryItem:
    kind: ItemKind
    events: tuple[PreparedEvent, ...]
    segment: SegmentRow | None = None


@dataclass(frozen=True)
class RegeneratedHistory:
    order: OrderRow
    signal: SignalRow
    ctx: OrderContext
    state: OrderState
    items: tuple[HistoryItem, ...]
    next_seq: int


def inherited_from_payload(payload: Mapping[str, Any]) -> GatingState | None:
    raw = payload.get("inherited_signal_state")
    if raw is None:
        return None
    return GatingState(
        status=OrderStatus(raw["status"]),
        zone_lost=bool(raw["zone_lost"]),
        entry_eligible_from=parse_ts(raw["entry_eligible_from"]),
        trigger_hit_at=parse_ts(raw["trigger_hit_at"]),
    )


def regenerate_created(model: ModuleType, ctx: OrderContext, payload: Mapping[str, Any]) -> StepResult:
    extra = {key: value for key, value in payload.items() if key not in CREATED_BASE_KEYS}
    result: StepResult = model.new_order_state(
        ctx, inherited=inherited_from_payload(payload), extra_payload=extra or None
    )
    return result


def _compare(stored: list[StoredEvent], seq: int, expected_count: int,
             regenerated: list[PreparedEvent], reason: str) -> None:
    window = stored[seq - 1 : seq - 1 + expected_count]
    if [s.prepared.identity() for s in window] == [p.identity() for p in regenerated]:
        return
    diff: list[dict[str, Any]] = []
    for offset in range(max(len(window), len(regenerated))):
        old = window[offset].prepared.identity() if offset < len(window) else None
        new = regenerated[offset].identity() if offset < len(regenerated) else None
        if old != new:
            diff.append({"seq": seq + offset, "stored": old and list(old), "regenerated": new and list(new)})
    raise HistoryDivergence(reason, diff)


def _command(
    conn: Connection,
    model: ModuleType,
    ctx: OrderContext,
    signal: SignalRow,
    price_source: str,
    state: OrderState,
    head: PreparedEvent,
) -> StepResult:
    payload = head.payload
    result: StepResult
    if head.type == "CANCELED":
        result = model.cancel(state, parse_ts(payload["at"]), payload["requested_by"])
    elif head.type == "FROZEN":
        result = model.freeze(state, payload["reason"], payload["ref"])
    elif head.type == "NEEDS_REVIEW":
        result = model.flag_review(state, payload["reason"], payload["ref"])
    elif head.type == "DIVIDEND":
        result = model.apply_dividend(
            state, ctx, date.fromisoformat(payload["ex_date"]), Decimal(payload["amount_per_share"]), True
        )
    elif head.type == "EXPIRED":
        result = model.apply_validity_end(state, ctx, None, ctx.valid_until_ts)
    elif head.type == "TIME_EXIT":
        if head.bar_ts is None or head.bar_batch_id is None:
            raise HistoryDivergence(f"TIME_EXIT_WITHOUT_BAR:{head.event_key}")
        try:
            bar = read_bar_version(conn, signal.spec.ticker, price_source, head.bar_ts, head.bar_batch_id)
        except LookupError as exc:
            raise HistoryDivergence(f"TIME_EXIT_BAR_MISSING:{head.event_key}") from exc
        result = model.apply_validity_end(state, ctx, bar, ctx.valid_until_ts)
    else:
        raise HistoryDivergence(f"UNEXPECTED_EVENT_OUTSIDE_SEGMENT:{head.event_key}")
    return result


def regenerate_history(conn: Connection, order: OrderRow) -> RegeneratedHistory:
    signal, ctx = load_order_context(conn, order)
    model = get_fill_model(order.fill_model_version)
    ticker = signal.spec.ticker
    stored = stored_events(conn, order.id)
    if [s.seq for s in stored] != list(range(1, len(stored) + 1)):
        raise HistoryDivergence("SEQ_NOT_CONTIGUOUS")
    if not stored or stored[0].prepared.type != "ORDER_CREATED":
        raise HistoryDivergence("ORDER_CREATED_MISSING")

    known: dict[str, str] = {}

    def prepare(events: Iterable[Event | PreparedEvent]) -> list[PreparedEvent]:
        try:
            return dedupe_events(known, events, order_id=order.id, evaluation_start_ts=order.evaluation_start_ts)
        except LedgerIntegrityError as exc:
            raise HistoryDivergence(f"REGENERATED_{exc.kind}") from exc

    created = regenerate_created(model, ctx, stored[0].prepared.payload)
    created_events = prepare(created.events)
    _compare(stored, 1, 1, created_events, "ORDER_CREATED")
    state = created.state
    items = [HistoryItem(ItemKind.CREATED, tuple(created_events))]
    seq = 2
    segments = list_segments(conn, order.id)
    index = 0

    while True:
        if index < len(segments) and segments[index].first_seq == seq:
            segment = segments[index]
            index += 1
            run = get_run(conn, segment.run_id)
            end = segment.bar_to + ONE_MINUTE
            minutes = ctx.calendar.expected_minutes(segment.bar_from, end)
            bars = bars_in_minutes(
                read_bars_as_of(conn, ticker, order.price_source, segment.bar_from, end, run.data_as_of), minutes
            )
            data_hash = selected_data_hash(order.price_source, ticker, minutes, bars)
            if data_hash != segment.selected_data_hash:
                raise HistoryDivergence("SELECTED_DATA_HASH_MISMATCH", [{
                    "run_id": str(segment.run_id), "bar_from": segment.bar_from.isoformat(),
                    "stored": segment.selected_data_hash, "regenerated": data_hash,
                }])
            result = model.run_bars(state, bars, ctx)
            events = prepare(result.events)
            _compare(stored, seq, segment.event_count, events, "SEGMENT")
            state = result.state
            seq += len(events)
            items.append(HistoryItem(ItemKind.SEGMENT, tuple(events), segment))
            continue
        if seq > len(stored):
            break
        head = stored[seq - 1].prepared
        if head.type in RECORD_TYPES:
            items.append(HistoryItem(ItemKind.RECORD, tuple(prepare([head]))))
            seq += 1
            continue
        result = _command(conn, model, ctx, signal, order.price_source, state, head)
        events = prepare(result.events)
        if not events:
            raise HistoryDivergence(f"COMMAND_PRODUCED_NO_EVENTS:{head.event_key}")
        _compare(stored, seq, len(events), events, "COMMAND")
        state = result.state
        seq += len(events)
        items.append(HistoryItem(ItemKind.COMMAND, tuple(events)))

    if index < len(segments):
        raise HistoryDivergence("ORPHAN_SEGMENT", [
            {"run_id": str(s.run_id), "first_seq": s.first_seq} for s in segments[index:]
        ])
    return RegeneratedHistory(order, signal, ctx, state, tuple(items), seq)
```

- [ ] **Step 4: Implementar a reconstrução**

`src/virtual_orders/evaluator/rebuild.py`:
```python
"""rebuild-projections (spec 6): order_state is a cache of the authoritative history (D2)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import Engine, select

from virtual_orders.evaluator.history import regenerate_history
from virtual_orders.ledger.errors import HistoryDivergence, LedgerIntegrityError, ProjectionIntegrityError
from virtual_orders.ledger.orders import Projection, lock_order, projection_row, read_projection_row, save_projection
from virtual_orders.ledger.quarantine import run_guarded
from virtual_orders.storage.tables import orders


def rebuild_projection(engine: Engine, order_id: UUID) -> dict[str, Any]:
    def operation() -> dict[str, Any]:
        with engine.begin() as conn:
            order = lock_order(conn, order_id)
            try:
                history = regenerate_history(conn, order)
            except HistoryDivergence as divergence:
                raise ProjectionIntegrityError(
                    f"history divergence: {divergence.reason}", order_id=order_id,
                    detail={"reason": divergence.reason, "diff": divergence.diff},
                ) from divergence
            direction = history.signal.spec.direction
            rebuilt = Projection(history.state, history.next_seq)
            expected = projection_row(conn, order, direction, rebuilt)
            current = read_projection_row(conn, order_id)
            if current is None:
                save_projection(conn, order, direction, rebuilt)
                return expected
            differences = {
                key: {"stored": current[key], "rebuilt": value}
                for key, value in expected.items()
                if current[key] != value
            }
            if differences:
                raise ProjectionIntegrityError(
                    "stored projection differs from the rebuilt projection", order_id=order_id,
                    detail={"reason": "PROJECTION_MISMATCH", "differences": differences},
                )
            return expected

    return run_guarded(engine, operation)


def rebuild_all_projections(engine: Engine) -> dict[UUID, str]:
    with engine.connect() as conn:
        order_ids = list(conn.execute(select(orders.c.id).order_by(orders.c.created_at, orders.c.id)).scalars())
    report: dict[UUID, str] = {}
    for order_id in order_ids:
        try:
            rebuild_projection(engine, order_id)
            report[order_id] = "OK"
        except LedgerIntegrityError as error:
            report[order_id] = error.kind
    return report
```

- [ ] **Step 5: Rodar os testes**

Run: `uv run pytest tests/integration/test_rebuild.py -v`
Expected: PASS (9 testes). Se o teste D2 falhar por diferença de projeção, **não** ajuste o teste: investigue com `caught.value.detail` e aplique a regra de congelamento do núcleo se a causa estiver em `src/core/` do Plano 1.

- [ ] **Step 6: Lint, tipos e commit**

Run: `uv run ruff check src tests && uv run mypy`
Expected: verde.

```bash
git add src/virtual_orders/evaluator/history.py src/virtual_orders/evaluator/rebuild.py tests/integration/test_rebuild.py
git commit -m "feat(evaluator): history regeneration and projection rebuild with the D2 A == B test

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BKUdi9RGWWdXRXVYFWj1yp"
```

---

### Task 13: Eventos corporativos — dividendos validados e congelamento por split

Spec 4.7, 5.3 (job de abertura), 6 (yfinance/FMP indisponíveis); decisão de detalhe 10.

**Files:**
- Create: `src/virtual_orders/evaluator/corporate.py`
- Modify: `tests/integration/support.py` (acrescentar fontes falsas)
- Create: `tests/integration/test_corporate.py`

**Interfaces:**
- Consumes: `apply_command`, `freeze_order`, `CommandInput` (Task 10); `OrderOutcome` (Task 9); `calendar_for_window` (Task 6); `DividendSource`, `DividendRecord`, `SplitSource`, `SourceUnavailable`, `SourceDataError` (Task 4); `core.dataquality.dividends_agree`; `virtual_orders.storage.tables.dividends`. Testes usam `run_live_cycle`, `rebuild_projection`.
- Produces (`virtual_orders.evaluator.corporate`):
  - `apply_dividends(engine, *, ex_date: date, primary: DividendSource, secondary: DividendSource, tolerance: Decimal, now: datetime) -> list[OrderOutcome]` — só posições `OPEN`/`PARTIAL` não `frozen` e não replay; `now` deve ser anterior à abertura do pregão da ex-date (`ValueError`); grava `dividends` (primeira verificação vence); crédito só se as duas fontes concordarem pela `dividend_tolerance` da ordem, senão `NEEDS_REVIEW:DIVIDEND_UNVERIFIED`
  - `freeze_for_splits(engine, split_source: SplitSource, *, as_of_day: date) -> list[OrderOutcome]` — ordens não finais (inclusive já `frozen`) com split de ex-date em `[data do evaluation_start_ts, as_of_day]` → `FROZEN:SPLIT` + `NEEDS_REVIEW:SPLIT:{ex_date}`
- Produces (`tests.integration.support`): `FakeDividends(name, records=(), failing=False)`, `FakeSplits(records=())`, `dividend(ticker, day, amount) -> DividendRecord`

- [ ] **Step 1: Acrescentar fontes falsas ao suporte**

Nos imports do topo de `tests/integration/support.py`, acrescentar `from collections.abc import Sequence`, `from datetime import date` e, ao import de `virtual_orders.marketdata.sources`, os nomes `DividendRecord` e `SplitRecord`. Ao final do arquivo:
```python
def dividend(ticker: str, day: str, amount: str) -> DividendRecord:
    return DividendRecord(ticker, date.fromisoformat(day), Decimal(amount), None)


class FakeDividends:
    def __init__(self, name: str, records: Iterable[DividendRecord] = (), failing: bool = False) -> None:
        self.name = name
        self.records = list(records)
        self.failing = failing

    def fetch_dividends(self, ticker: str, start: date, end: date) -> list[DividendRecord]:
        if self.failing:
            raise SourceUnavailable(f"{self.name} unavailable (fake)")
        return [r for r in self.records if r.ticker == ticker and start <= r.ex_date <= end]


class FakeSplits:
    def __init__(self, records: Iterable[SplitRecord] = ()) -> None:
        self.records = list(records)
        self.calls: list[tuple[tuple[str, ...], date, date]] = []

    def fetch_splits(self, tickers: Sequence[str], start: date, end: date) -> list[SplitRecord]:
        self.calls.append((tuple(tickers), start, end))
        return [r for r in self.records if r.ticker in tickers and start <= r.ex_date <= end]
```

- [ ] **Step 2: Escrever os testes que falham**

`tests/integration/test_corporate.py`:
```python
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from virtual_orders.evaluator.corporate import apply_dividends, freeze_for_splits
from virtual_orders.evaluator.cycle import run_live_cycle
from virtual_orders.evaluator.rebuild import rebuild_projection
from virtual_orders.ledger.events import stored_events
from virtual_orders.ledger.orders import delete_projection, read_projection_row
from virtual_orders.marketdata.sources import SplitRecord
from virtual_orders.storage import tables
from tests.integration.support import (
    CODE_VERSION, DAY, FakeBarSource, FakeDividends, FakeSplits, dividend, feeds, flat_raw, scenario_bars, submit_default,
)
from tests.support import et

EX_DAY = "2025-11-26"
TOLERANCE = Decimal("0.001")


def open_position(engine, **overrides):
    order_id = submit_default(engine, **overrides).auto_order_id
    run_live_cycle(engine, feeds(FakeBarSource(scenario_bars(ticker=overrides.get("ticker", "AAPL")))),
                   code_version=CODE_VERSION, market_now=et(DAY, "10:30"))
    return order_id


def pay(engine, fmp, yfinance, hm="09:25"):
    return apply_dividends(engine, ex_date=date.fromisoformat(EX_DAY), primary=fmp, secondary=yfinance,
                           tolerance=TOLERANCE, now=et(EX_DAY, hm))


def keys(engine, order_id):
    with engine.connect() as conn:
        return [e.prepared.event_key for e in stored_events(conn, order_id)]


def test_validated_dividend_is_credited_once(engine):
    order_id = open_position(engine)
    fmp = FakeDividends("fmp", [dividend("AAPL", EX_DAY, "0.26")])
    yfinance = FakeDividends("yfinance", [dividend("AAPL", EX_DAY, "0.2605")])
    assert [o.event_keys for o in pay(engine, fmp, yfinance)] == [(f"DIVIDEND:{EX_DAY}",)]
    assert [o.event_keys for o in pay(engine, fmp, yfinance)] == [()]
    with engine.connect() as conn:
        row = read_projection_row(conn, order_id)
        credited = stored_events(conn, order_id)[-1].prepared
        record = conn.execute(select(tables.dividends)).one()
    assert credited.payload["cash"] == "6.5" and row["r_multiple"] == Decimal("0.065")
    assert record.validated and record.amount == Decimal("0.26") and record.sources == ["fmp", "yfinance"]


@pytest.mark.parametrize("fmp, yfinance, sources", [
    (FakeDividends("fmp", [dividend("AAPL", EX_DAY, "0.26")]),
     FakeDividends("yfinance", [dividend("AAPL", EX_DAY, "0.30")]), ["fmp", "yfinance"]),
    (FakeDividends("fmp", [dividend("AAPL", EX_DAY, "0.26")]), FakeDividends("yfinance", failing=True), ["fmp"]),
])
def test_unverified_dividend_flags_review_without_credit(engine, fmp, yfinance, sources):
    order_id = open_position(engine)
    assert [o.event_keys for o in pay(engine, fmp, yfinance)] == [(f"NEEDS_REVIEW:DIVIDEND_UNVERIFIED:{EX_DAY}",)]
    with engine.connect() as conn:
        row = read_projection_row(conn, order_id)
        record = conn.execute(select(tables.dividends)).one()
    assert row["r_multiple"] == 0 and row["needs_review"]
    assert not record.validated and record.sources == sources


def test_pending_orders_and_unrelated_tickers_are_untouched(engine):
    open_id = open_position(engine)
    pending_id = submit_default(engine, client_signal_id="pending", ticker="MSFT").auto_order_id
    fmp = FakeDividends("fmp", [dividend("AAPL", EX_DAY, "0.26"), dividend("MSFT", EX_DAY, "0.91")])
    yfinance = FakeDividends("yfinance", [dividend("AAPL", EX_DAY, "0.26"), dividend("MSFT", EX_DAY, "0.91")])
    assert [o.order_id for o in pay(engine, fmp, yfinance)] == [open_id]
    assert keys(engine, pending_id) == ["ORDER_CREATED"]


def test_dividends_must_run_before_the_ex_date_open(engine):
    open_position(engine)
    with pytest.raises(ValueError, match="before the ex-date session opens"):
        pay(engine, FakeDividends("fmp"), FakeDividends("yfinance"), hm="09:30")


def test_rebuild_interleaves_dividend_between_sessions(engine):
    order_id = open_position(engine, target2=Decimal("150"), client_signal_id="long-hold")
    fmp = FakeDividends("fmp", [dividend("AAPL", EX_DAY, "0.26")])
    pay(engine, fmp, FakeDividends("yfinance", [dividend("AAPL", EX_DAY, "0.26")]))
    source = FakeBarSource(scenario_bars() + flat_raw(EX_DAY, "09:30", "10:00", 103))
    run_live_cycle(engine, feeds(source), code_version=CODE_VERSION, market_now=et(EX_DAY, "10:00"))
    with engine.connect() as conn:
        projection_a = read_projection_row(conn, order_id)
        types = [e.prepared.type for e in stored_events(conn, order_id)]
    assert types == ["ORDER_CREATED", "FILLED", "DIVIDEND", "TARGET1_HIT"]
    with engine.begin() as conn:
        delete_projection(conn, order_id)
    assert rebuild_projection(engine, order_id) == projection_a


def test_split_freezes_every_non_final_order_of_the_ticker(engine):
    open_id = open_position(engine)
    pending_id = submit_default(engine, client_signal_id="pending-aapl").auto_order_id
    other_id = submit_default(engine, client_signal_id="msft", ticker="MSFT").auto_order_id
    splits = FakeSplits([
        SplitRecord("AAPL", date(2025, 11, 26), Decimal(1), Decimal(4)),
        SplitRecord("AAPL", date(2025, 11, 20), Decimal(1), Decimal(2)),
    ])
    outcomes = {o.order_id: o.event_keys for o in freeze_for_splits(engine, splits, as_of_day=date(2025, 11, 26))}
    expected = ("FROZEN:SPLIT", "NEEDS_REVIEW:SPLIT:2025-11-26")
    assert outcomes == {open_id: expected, pending_id: expected}
    assert splits.calls == [(("AAPL", "MSFT"), date(2025, 11, 25), date(2025, 11, 26))]
    assert keys(engine, other_id) == ["ORDER_CREATED"]
    repeated = freeze_for_splits(engine, splits, as_of_day=date(2025, 11, 26))
    assert [o.event_keys for o in repeated] == [(), ()]
    report = run_live_cycle(engine, feeds(FakeBarSource(scenario_bars())), code_version=CODE_VERSION,
                            market_now=et(DAY, "11:30"))
    assert {o.order_id for o in report.outcomes} == {other_id}
```

- [ ] **Step 3: Rodar para ver falhar**

Run: `uv run pytest tests/integration/test_corporate.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'virtual_orders.evaluator.corporate'`.

- [ ] **Step 4: Implementar**

`src/virtual_orders/evaluator/corporate.py`:
```python
"""Opening job pieces (spec 4.7, 5.3): validated dividends and split freezes."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, date, datetime, time
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Engine, func, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from core.dataquality import dividends_agree
from core.domain.models import StepResult
from virtual_orders.evaluator.commands import CommandInput, apply_command, freeze_order
from virtual_orders.evaluator.outcomes import OrderOutcome
from virtual_orders.marketdata.calendars import calendar_for_window
from virtual_orders.marketdata.sources import DividendRecord, DividendSource, SourceDataError, SourceUnavailable, SplitSource
from virtual_orders.storage.tables import dividends

_OPEN_POSITIONS = text(
    """
    SELECT o.id AS order_id, g.ticker AS ticker
    FROM orders o JOIN order_state st ON st.order_id = o.id JOIN signals g ON g.id = o.signal_id
    WHERE NOT o.replay AND NOT st.frozen AND st.status IN ('OPEN', 'PARTIAL')
    ORDER BY g.ticker, o.id
    """
)

_NON_FINAL = text(
    """
    SELECT o.id AS order_id, g.ticker AS ticker, o.evaluation_start_ts AS evaluation_start_ts
    FROM orders o JOIN order_state st ON st.order_id = o.id JOIN signals g ON g.id = o.signal_id
    WHERE NOT o.replay AND st.status IN ('PENDING', 'OPEN', 'PARTIAL')
    ORDER BY g.ticker, o.id
    """
)


def _lookup(source: DividendSource, ticker: str, ex_date: date) -> DividendRecord | None:
    try:
        records = source.fetch_dividends(ticker, ex_date, ex_date)
    except (SourceUnavailable, SourceDataError):
        return None
    return next((record for record in records if record.ex_date == ex_date), None)


def _dividend_command(
    ex_date: date, amount: Decimal, first: Decimal | None, second: Decimal | None
) -> Callable[[CommandInput], StepResult]:
    def command(inp: CommandInput) -> StepResult:
        validated = dividends_agree(first, second, inp.order.config.dividend_tolerance)
        result: StepResult = inp.model.apply_dividend(inp.projection.state, inp.ctx, ex_date, amount, validated)
        return result

    return command


def apply_dividends(
    engine: Engine,
    *,
    ex_date: date,
    primary: DividendSource,
    secondary: DividendSource,
    tolerance: Decimal,
    now: datetime,
) -> list[OrderOutcome]:
    probe = datetime.combine(ex_date, time(12), tzinfo=UTC)
    session = next((s for s in calendar_for_window(probe, probe).sessions if s.day == ex_date), None)
    if session is None:
        raise ValueError(f"{ex_date} is not a trading session")
    if now >= session.open_utc:
        raise ValueError("dividends must be applied before the ex-date session opens")

    with engine.connect() as conn:
        by_ticker: dict[str, list[UUID]] = defaultdict(list)
        for row in conn.execute(_OPEN_POSITIONS):
            by_ticker[row.ticker].append(row.order_id)

    outcomes: list[OrderOutcome] = []
    for ticker, order_ids in sorted(by_ticker.items()):
        first, second = _lookup(primary, ticker, ex_date), _lookup(secondary, ticker, ex_date)
        if first is None and second is None:
            continue
        first_amount = None if first is None else first.amount
        second_amount = None if second is None else second.amount
        amount = first_amount if first_amount is not None else second_amount
        if amount is None:
            raise RuntimeError(f"dividend for {ticker} {ex_date} has no amount")
        known = first or second
        with engine.begin() as conn:
            conn.execute(
                pg_insert(dividends)
                .values(
                    ticker=ticker, ex_date=ex_date, amount=amount, pay_date=None if known is None else known.pay_date,
                    sources=[s.name for s, r in ((primary, first), (secondary, second)) if r is not None],
                    validated=dividends_agree(first_amount, second_amount, tolerance),
                    checked_at=func.clock_timestamp(),
                )
                .on_conflict_do_nothing(index_elements=["ticker", "ex_date"])
            )
        command = _dividend_command(ex_date, amount, first_amount, second_amount)
        outcomes.extend(apply_command(engine, order_id, command) for order_id in order_ids)
    return outcomes


def freeze_for_splits(engine: Engine, split_source: SplitSource, *, as_of_day: date) -> list[OrderOutcome]:
    with engine.connect() as conn:
        rows = conn.execute(_NON_FINAL).all()
    if not rows:
        return []
    start = min(row.evaluation_start_ts for row in rows).astimezone(UTC).date()
    splits = split_source.fetch_splits(sorted({row.ticker for row in rows}), start, as_of_day)
    outcomes: list[OrderOutcome] = []
    for row in rows:
        order_start = row.evaluation_start_ts.astimezone(UTC).date()
        for split in sorted(splits, key=lambda s: s.ex_date):
            if split.ticker == row.ticker and order_start <= split.ex_date <= as_of_day:
                outcomes.append(freeze_order(engine, row.order_id, reason="SPLIT", ref=split.ex_date.isoformat()))
    return outcomes
```

- [ ] **Step 5: Rodar os testes**

Run: `uv run pytest tests/integration/test_corporate.py -v`
Expected: PASS (7 testes, contando os parametrizados).

- [ ] **Step 6: Lint, tipos e commit**

Run: `uv run ruff check src tests && uv run mypy`
Expected: verde.

```bash
git add src/virtual_orders/evaluator/corporate.py tests/integration/support.py tests/integration/test_corporate.py
git commit -m "feat(evaluator): validated dividends and split freezes for open orders

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BKUdi9RGWWdXRXVYFWj1yp"
```

---

### Task 14: Fim de dia — `DATA_QUALITY` imutável (D4), `DATA_GAP`, minutos ausentes e conferência diária

Spec 4.6, 5.3 (job de fim de dia), 6; D4, D6, D10; decisões de detalhe 2 e 7.

**Files:**
- Create: `src/virtual_orders/evaluator/quality.py`
- Modify: `tests/integration/support.py` (acrescentar `FakeReference`)
- Create: `tests/integration/test_quality.py`

**Interfaces:**
- Consumes: `apply_command`, `CommandInput`, `expire_due_orders` (Task 10); `run_live_cycle`, `CycleReport` (Task 9); `start_run`, `finish_run`, `RunKind.END_OF_DAY`, `RunStatus`, `RunInfo` (Task 7); `acquire_data_as_of`, `read_bars_as_of`, `bars_in_minutes`, `calendar_for_window` (Task 6); `ReferenceSource`, `MarketDataGateway` (Task 4); `core.dataquality.{quality_window, session_quality, find_gaps, gap_events, missing_bar_reviews, daily_range_mismatch, ReviewFlag}`.
- Produces (`virtual_orders.evaluator.quality`):
  - `QualityReport(run_id: UUID, session_day: date, outcomes: tuple[OrderOutcome, ...], unavailable: dict[str, str])`
  - `EndOfDayReport(cycle: CycleReport, expired: tuple[OrderOutcome, ...], quality: QualityReport)`
  - `session_for_day(day: date) -> Session` (`ValueError` se não for pregão)
  - `run_session_quality(engine, *, session_day: date, reference: ReferenceSource, code_version: str) -> QualityReport` — um run `END_OF_DAY`; por ordem não replay cuja janela intersecta o pregão e que ainda **não** tem `DATA_QUALITY:{session_day}`: `DATA_QUALITY` (+ `data_as_of`, `evaluation_run_id`), `DATA_GAP` dentro do pregão, `NEEDS_REVIEW:MISSING_BAR_*`, `NEEDS_REVIEW:DAILY_RANGE_MISMATCH` (janela cobrindo o pregão inteiro)
  - `run_end_of_day(engine, *, gateway: MarketDataGateway, reference: ReferenceSource, session_day: date, code_version: str, market_now: datetime) -> EndOfDayReport` — ciclo com `close_trailing_gap=True` → `expire_due_orders` → `run_session_quality`
- Produces (`tests.integration.support`): `FakeReference(minute=None, daily=None, failing=False)`

- [ ] **Step 1: Acrescentar a referência falsa ao suporte**

Nos imports do topo de `tests/integration/support.py`, acrescentar `from core.domain.models import Bar` (junto de `FillConfig`). Ao final do arquivo:
```python
class FakeReference:
    """yfinance stand-in: minute[(ticker, day)] -> {ts: Bar}; daily[(ticker, day)] -> (high, low)."""

    def __init__(
        self,
        minute: dict[tuple[str, date], dict[datetime, Bar]] | None = None,
        daily: dict[tuple[str, date], tuple[Decimal, Decimal]] | None = None,
        failing: bool = False,
    ) -> None:
        self.minute = minute or {}
        self.daily = daily or {}
        self.failing = failing

    def fetch_minute_bars(self, ticker: str, day: date) -> dict[datetime, Bar]:
        if self.failing:
            raise SourceUnavailable("yfinance unavailable (fake)")
        return dict(self.minute.get((ticker, day), {}))

    def fetch_daily_range(self, ticker: str, day: date) -> tuple[Decimal, Decimal] | None:
        if self.failing:
            raise SourceUnavailable("yfinance unavailable (fake)")
        return self.daily.get((ticker, day))
```

- [ ] **Step 2: Escrever os testes que falham**

`tests/integration/test_quality.py`:
```python
from collections import Counter
from datetime import date
from decimal import Decimal

import pytest

from virtual_orders.evaluator.quality import run_end_of_day, run_session_quality, session_for_day
from virtual_orders.evaluator.rebuild import rebuild_projection
from virtual_orders.ledger.events import stored_events
from virtual_orders.ledger.orders import delete_projection, read_projection_row
from virtual_orders.marketdata.ingest import ingest_bars
from virtual_orders.marketdata.sources import RawBar
from tests.integration.support import (
    CODE_VERSION, DAY, TICKER, FakeBarSource, FakeReference, feeds, flat_raw, scenario_bars, submit_default,
)
from tests.support import bar, et

SESSION = date(2025, 11, 25)


def end_of_day(engine, source, reference=None):
    return run_end_of_day(engine, gateway=feeds(source), reference=reference or FakeReference(),
                          session_day=SESSION, code_version=CODE_VERSION, market_now=et(DAY, "16:30"))


def events(engine, order_id):
    with engine.connect() as conn:
        return [e.prepared for e in stored_events(conn, order_id)]


def without(bars: list[RawBar], start_hm: str, end_hm: str) -> list[RawBar]:
    return [b for b in bars if not et(DAY, start_hm) <= b.ts < et(DAY, end_hm)]


def test_full_coverage_emits_one_immutable_data_quality_event(engine):
    order_id = submit_default(engine).auto_order_id
    report = end_of_day(engine, FakeBarSource(scenario_bars()))
    (quality,) = [e for e in events(engine, order_id) if e.type == "DATA_QUALITY"]
    assert quality.event_key == "DATA_QUALITY:2025-11-25"
    assert quality.payload["expected_bars"] == 201 and quality.payload["missing_bars"] == 0
    assert quality.payload["coverage_pct"] == "100"
    assert quality.payload["evaluation_run_id"] == str(report.quality.run_id)
    assert quality.payload["data_as_of"] is not None
    with engine.connect() as conn:
        row = read_projection_row(conn, order_id)
    assert (row["expected_bars"], row["missing_bars"]) == (201, 0)


def test_gap_missing_bar_reviews_and_d4_immutability(engine):
    order_id = submit_default(engine).auto_order_id
    source = FakeBarSource(without(scenario_bars(), "10:10", "10:45"))
    touching = bar(et(DAY, "10:10"), 103, 103, 101.9, 103)  # touches entry_zone_high 102
    reference = FakeReference(minute={(TICKER, SESSION): {touching.ts: touching}})
    end_of_day(engine, source, reference)

    stored = events(engine, order_id)
    (quality,) = [e for e in stored if e.type == "DATA_QUALITY"]
    (gap,) = [e for e in stored if e.type == "DATA_GAP"]
    reviews = Counter(e.payload["reason"] for e in stored if e.type == "NEEDS_REVIEW")
    assert quality.payload["missing_bars"] == 35 and quality.payload["coverage_pct"] == "82.59"
    assert gap.event_key == f"DATA_GAP:{et(DAY, '10:10').isoformat()}" and gap.payload["minutes"] == 35
    assert reviews == {"MISSING_BAR_LEVEL_TOUCH": 1, "MISSING_BAR_UNVERIFIABLE": 34}

    source.load(flat_raw(DAY, "10:10", "10:45", 103))
    ingest_bars(engine, source, TICKER, et(DAY, "10:10"), et(DAY, "10:45"))
    again = run_session_quality(engine, session_day=SESSION, reference=reference, code_version=CODE_VERSION)
    assert [o.event_keys for o in again.outcomes] == [()]
    assert [e.identity() for e in events(engine, order_id)] == [e.identity() for e in stored]


def test_daily_crosscheck_flags_full_session_window(engine):
    order_id = submit_default(engine).auto_order_id
    reference = FakeReference(daily={(TICKER, SESSION): (Decimal("110"), Decimal("105"))})
    end_of_day(engine, FakeBarSource(flat_raw(DAY, "09:30", "16:00", 105)), reference)
    reviews = [e.event_key for e in events(engine, order_id) if e.type == "NEEDS_REVIEW"]
    assert reviews == ["NEEDS_REVIEW:DAILY_RANGE_MISMATCH:2025-11-25"]


def test_matching_daily_range_is_not_flagged(engine):
    order_id = submit_default(engine).auto_order_id
    reference = FakeReference(daily={(TICKER, SESSION): (Decimal("105.2"), Decimal("104.8"))})
    end_of_day(engine, FakeBarSource(flat_raw(DAY, "09:30", "16:00", 105)), reference)
    assert [e for e in events(engine, order_id) if e.type == "NEEDS_REVIEW"] == []


def test_partial_window_skips_crosscheck(engine):
    order_id = submit_default(engine).auto_order_id
    reference = FakeReference(daily={(TICKER, SESSION): (Decimal("200"), Decimal("50"))})
    end_of_day(engine, FakeBarSource(scenario_bars()), reference)
    assert [e for e in events(engine, order_id) if e.type == "NEEDS_REVIEW"] == []


def test_reference_unavailable_marks_missing_minutes_unverifiable(engine):
    order_id = submit_default(engine).auto_order_id
    report = end_of_day(engine, FakeBarSource(without(scenario_bars(), "10:10", "10:12")), FakeReference(failing=True))
    reasons = [e.payload["reason"] for e in events(engine, order_id) if e.type == "NEEDS_REVIEW"]
    assert reasons == ["MISSING_BAR_UNVERIFIABLE", "MISSING_BAR_UNVERIFIABLE"]
    assert set(report.quality.unavailable) == {f"{TICKER}:1m", f"{TICKER}:1d"}


def test_end_of_day_expires_pending_order_without_last_bar(engine):
    order_id = submit_default(engine, valid_sessions=1).auto_order_id
    report = end_of_day(engine, FakeBarSource(flat_raw(DAY, "09:30", "15:59", 105)))
    assert [(o.order_id, o.event_keys) for o in report.expired] == [(order_id, ("EXPIRED",))]
    (quality,) = [e for e in events(engine, order_id) if e.type == "DATA_QUALITY"]
    assert (quality.payload["expected_bars"], quality.payload["missing_bars"]) == (390, 1)


def test_rebuild_accepts_quality_records_and_review_commands(engine):
    order_id = submit_default(engine).auto_order_id
    end_of_day(engine, FakeBarSource(without(scenario_bars(), "10:10", "10:45")), FakeReference())
    with engine.connect() as conn:
        projection_a = read_projection_row(conn, order_id)
    with engine.begin() as conn:
        delete_projection(conn, order_id)
    assert rebuild_projection(engine, order_id) == projection_a


def test_non_session_day_is_rejected():
    with pytest.raises(ValueError, match="not a trading session"):
        session_for_day(date(2025, 11, 27))
    assert session_for_day(SESSION).close_utc == et(DAY, "16:00")

```

- [ ] **Step 3: Rodar para ver falhar**

Run: `uv run pytest tests/integration/test_quality.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'virtual_orders.evaluator.quality'`.

- [ ] **Step 4: Implementar**

`src/virtual_orders/evaluator/quality.py`:
```python
"""End-of-day job pieces (spec 4.6, 5.3) with DATA_QUALITY immutability (spec 10, D4)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Connection, Engine, select, text

from core.dataquality import (
    ReviewFlag, daily_range_mismatch, find_gaps, gap_events, missing_bar_reviews, quality_window, session_quality,
)
from core.domain.calendar import Session
from core.domain.models import Bar, Event, EventType, StepResult
from virtual_orders.evaluator.commands import CommandInput, apply_command, expire_due_orders
from virtual_orders.evaluator.cycle import CycleReport, run_live_cycle
from virtual_orders.evaluator.outcomes import OrderOutcome
from virtual_orders.ledger.runs import RunInfo, RunKind, RunStatus, finish_run, start_run
from virtual_orders.marketdata.asof import acquire_data_as_of, bars_in_minutes, read_bars_as_of
from virtual_orders.marketdata.calendars import calendar_for_window
from virtual_orders.marketdata.gateway import MarketDataGateway
from virtual_orders.marketdata.sources import ReferenceSource, SourceDataError, SourceUnavailable
from virtual_orders.storage.tables import order_events

_CANDIDATES = text(
    """
    SELECT o.id AS order_id, g.ticker AS ticker
    FROM orders o JOIN order_state st ON st.order_id = o.id JOIN signals g ON g.id = o.signal_id
    WHERE NOT o.replay AND o.evaluation_start_ts < :session_close
      AND (st.final_event_ts IS NULL OR st.final_event_ts >= :session_open)
    ORDER BY g.ticker, o.id
    """
)


@dataclass(frozen=True)
class QualityReport:
    run_id: UUID
    session_day: date
    outcomes: tuple[OrderOutcome, ...]
    unavailable: dict[str, str]


@dataclass(frozen=True)
class EndOfDayReport:
    cycle: CycleReport
    expired: tuple[OrderOutcome, ...]
    quality: QualityReport


def session_for_day(day: date) -> Session:
    probe = datetime.combine(day, time(12), tzinfo=UTC)
    session = next((s for s in calendar_for_window(probe, probe).sessions if s.day == day), None)
    if session is None:
        raise ValueError(f"{day} is not a trading session")
    return session


def _already_emitted(conn: Connection, order_id: UUID, event_key: str) -> bool:
    return conn.execute(
        select(order_events.c.id).where(order_events.c.order_id == order_id, order_events.c.event_key == event_key)
    ).first() is not None


def _quality_command(
    run: RunInfo,
    session: Session,
    minute_reference: dict[datetime, Bar] | None,
    daily_reference: tuple[Decimal, Decimal] | None,
) -> Callable[[CommandInput], StepResult]:
    def command(inp: CommandInput) -> StepResult:
        state = inp.projection.state
        key = f"DATA_QUALITY:{session.day.isoformat()}"
        if _already_emitted(inp.conn, inp.order.id, key):
            return StepResult(state)  # D4: never re-emitted
        window_start, window_end = quality_window(state, inp.ctx, session.close_utc)
        start, end = max(window_start, session.open_utc), min(window_end, session.close_utc)
        if end <= start:
            return StepResult(state)
        ticker = inp.signal.spec.ticker
        minutes = inp.ctx.calendar.expected_minutes(start, end)
        source_id = inp.order.price_source
        bars = bars_in_minutes(read_bars_as_of(inp.conn, ticker, source_id, start, end, run.data_as_of), minutes)
        quality = next((q for q in session_quality(inp.ctx.calendar, start, end, [b.ts for b in bars])
                        if q.day == session.day), None)
        if quality is None:
            return StepResult(state)

        base = quality.event()
        emitted: list[Event] = [Event(
            EventType.DATA_QUALITY, base.event_key,
            payload={**base.payload, "data_as_of": run.data_as_of, "evaluation_run_id": run.run_id},
        )]
        emitted.extend(gap_events(find_gaps(inp.ctx.calendar, quality.missing, inp.order.config.data_gap_minutes)))

        flags: list[ReviewFlag] = []
        if quality.missing:
            flags.extend(missing_bar_reviews(quality.missing, minute_reference or {}, inp.signal.spec, state))
        covers_session = start == session.open_utc and end == session.close_utc
        if daily_reference is not None and bars and covers_session:
            high, low = daily_reference
            if daily_range_mismatch(bars, high, low, inp.order.config.crosscheck_tolerance_pct):
                flags.append(ReviewFlag("DAILY_RANGE_MISMATCH", session.day.isoformat()))

        result = StepResult(state, tuple(emitted))
        for flag in flags:
            flagged = inp.model.flag_review(result.state, flag.reason, flag.ref)
            result = StepResult(flagged.state, result.events + flagged.events)
        return result

    return command


def run_session_quality(
    engine: Engine, *, session_day: date, reference: ReferenceSource, code_version: str
) -> QualityReport:
    session = session_for_day(session_day)
    data_as_of = acquire_data_as_of(engine)
    with engine.begin() as conn:
        run = start_run(conn, RunKind.END_OF_DAY, data_as_of, code_version, detail={"session_day": session_day})
    with engine.connect() as conn:
        candidates = conn.execute(
            _CANDIDATES, {"session_open": session.open_utc, "session_close": session.close_utc}
        ).all()

    unavailable: dict[str, str] = {}
    minute_refs: dict[str, dict[datetime, Bar] | None] = {}
    daily_refs: dict[str, tuple[Decimal, Decimal] | None] = {}
    for ticker in sorted({row.ticker for row in candidates}):
        try:
            minute_refs[ticker] = reference.fetch_minute_bars(ticker, session_day)
        except (SourceUnavailable, SourceDataError) as exc:
            minute_refs[ticker] = None
            unavailable[f"{ticker}:1m"] = str(exc)
        try:
            daily_refs[ticker] = reference.fetch_daily_range(ticker, session_day)
        except (SourceUnavailable, SourceDataError) as exc:
            daily_refs[ticker] = None
            unavailable[f"{ticker}:1d"] = str(exc)

    outcomes = tuple(
        apply_command(engine, row.order_id,
                      _quality_command(run, session, minute_refs[row.ticker], daily_refs[row.ticker]))
        for row in candidates
    )
    with engine.begin() as conn:
        finish_run(conn, run.run_id, RunStatus.COMPLETED,
                   {"session_day": session_day, "orders": len(outcomes), "unavailable": unavailable})
    return QualityReport(run.run_id, session_day, outcomes, unavailable)


def run_end_of_day(
    engine: Engine,
    *,
    gateway: MarketDataGateway,
    reference: ReferenceSource,
    session_day: date,
    code_version: str,
    market_now: datetime,
) -> EndOfDayReport:
    cycle = run_live_cycle(engine, gateway, code_version=code_version, market_now=market_now,
                           close_trailing_gap=True)
    expired = tuple(expire_due_orders(engine, now=market_now))
    quality = run_session_quality(engine, session_day=session_day, reference=reference, code_version=code_version)
    return EndOfDayReport(cycle, expired, quality)
```

- [ ] **Step 5: Rodar os testes**

Run: `uv run pytest tests/integration/test_quality.py -v`
Expected: PASS (9 testes).

- [ ] **Step 6: Lint, tipos e commit**

Run: `uv run ruff check src tests && uv run mypy`
Expected: verde.

```bash
git add src/virtual_orders/evaluator/quality.py tests/integration/support.py tests/integration/test_quality.py
git commit -m "feat(evaluator): end-of-day data quality (D4), gaps, missing-bar reviews and daily cross-check

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BKUdi9RGWWdXRXVYFWj1yp"
```

---

### Task 15: Replay `REPRODUCE`

Spec 3.6 (replay), 5.1 (`POST /replay`), 6, 7 (`REPRODUCE` idêntico após correção do fornecedor; candle ausente no run original continua ausente no REPRODUCE); D5, D6; decisão de detalhe 8.

**Files:**
- Create: `src/virtual_orders/evaluator/replay.py`
- Create: `tests/integration/test_reproduce.py`

**Interfaces:**
- Consumes: `regenerate_history`, `RegeneratedHistory` (Task 12); `lock_order`, `insert_order`, `save_projection`, `Projection`, `insert_event`, `record_segment`, `start_run`, `finish_run`, `RunKind`, `RunStatus`, `run_guarded`, `LedgerIntegrityError`, `HistoryDivergence`, `OrderNotFound`, `REPRODUCE_DIVERGENCE` (Task 7); `acquire_data_as_of` (Task 6).
- Produces (`virtual_orders.evaluator.replay`):
  - `ReplayMode(StrEnum)`: `REPRODUCE`, `RECALCULATE`
  - `ReplayReport(run_id: UUID, mode: ReplayMode, created: dict[UUID, UUID], failures: dict[UUID, str])` (`created`: origem → nova ordem)
  - `ReplaySelectionError(ValueError)`
  - `select_source_orders(conn, *, order_ids: Sequence[UUID] | None, created_from: datetime | None, created_to: datetime | None) -> list[UUID]` — exatamente um critério; intervalo `[from, to)` seleciona ordens não replay por `created_at`
  - `reproduce_order(engine, source_id: UUID, *, code_version: str) -> UUID`
  - `reproduce_orders(engine, *, code_version: str, order_ids=None, created_from=None, created_to=None) -> ReplayReport`

- [ ] **Step 1: Escrever os testes que falham**

`tests/integration/test_reproduce.py`:
```python
from uuid import uuid4

import pytest

from core.domain.models import FillConfig
from virtual_orders.evaluator.commands import cancel_order
from virtual_orders.evaluator.cycle import run_live_cycle
from virtual_orders.evaluator.manual import create_manual_order
from virtual_orders.evaluator.rebuild import rebuild_projection
from virtual_orders.evaluator.replay import ReplayMode, ReplaySelectionError, reproduce_orders
from virtual_orders.ledger import errors
from virtual_orders.ledger.events import stored_events
from virtual_orders.ledger.orders import get_order, load_projection, read_projection_row
from virtual_orders.ledger.runs import get_run, list_segments
from virtual_orders.marketdata.ingest import ingest_bars
from tests.integration.support import (
    CODE_VERSION, DAY, PRICE_SOURCE, SIGNAL_CREATED_AT, TICKER, FakeBarSource, backdated_batch, count, feeds, flat_raw, raw,
    scenario_bars, submit_default,
)
from tests.support import et


def cycles(engine, source, *hms):
    for hm in hms:
        run_live_cycle(engine, feeds(source), code_version=CODE_VERSION, market_now=et(DAY, hm))


def identities(engine, order_id):
    with engine.connect() as conn:
        return [e.prepared.identity() for e in stored_events(conn, order_id)]


def segment_facts(engine, order_id):
    with engine.connect() as conn:
        return [(s.run_id, s.bar_from, s.bar_to, s.selected_data_hash, s.first_seq, s.event_count)
                for s in list_segments(conn, order_id)]


def comparable_projection(engine, order_id):
    with engine.connect() as conn:
        row = read_projection_row(conn, order_id)
    return {key: value for key, value in row.items() if key != "order_id"}


def test_reproduce_is_identical_after_vendor_correction_and_late_delivery(engine):
    source_id = submit_default(engine).auto_order_id
    original_bars = scenario_bars()
    source = FakeBarSource([b for b in original_bars if b.ts != et(DAY, "10:40")])
    cycles(engine, source, "10:30", "11:30", "13:00")

    source.load([raw(DAY, "10:05", 104, 104, 104, 104)])       # correction that would remove the fill
    source.load([b for b in original_bars if b.ts == et(DAY, "10:40")])  # minute delivered late
    ingest_bars(engine, source, TICKER, et(DAY, "09:30"), et(DAY, "13:00"))

    report = reproduce_orders(engine, code_version="replay-sha", order_ids=[source_id])
    assert report.mode is ReplayMode.REPRODUCE and report.failures == {}
    replay_id = report.created[source_id]
    assert identities(engine, replay_id) == identities(engine, source_id)
    assert segment_facts(engine, replay_id) == segment_facts(engine, source_id)
    assert comparable_projection(engine, replay_id) == comparable_projection(engine, source_id)
    with engine.connect() as conn:
        replay = get_order(conn, replay_id)
        assert get_run(conn, report.run_id).kind.value == "REPLAY"
    assert replay.replay and replay.replay_mode == "REPRODUCE" and replay.replay_of_order_id == source_id
    assert replay.code_version == "replay-sha" and replay.created_at == SIGNAL_CREATED_AT
    with engine.connect() as conn:
        assert rebuild_projection(engine, replay_id) == read_projection_row(conn, replay_id)


def test_reproduce_manual_order_with_commands(engine):
    signal_id = submit_default(engine, auto_order=False).signal_id
    source = FakeBarSource(flat_raw(DAY, "09:30", "13:30", 105))
    created = create_manual_order(engine, signal_id, config=FillConfig(), code_version=CODE_VERSION,
                                  price_source=PRICE_SOURCE, created_at=et(DAY, "13:00", 5), gateway=feeds(source))
    cycles(engine, source, "13:20")
    cancel_order(engine, created.order_id, at=et(DAY, "13:21"))
    report = reproduce_orders(engine, code_version=CODE_VERSION, order_ids=[created.order_id])
    assert identities(engine, report.created[created.order_id]) == identities(engine, created.order_id)


def test_divergent_history_fails_records_incident_and_freezes_source(engine):
    source_id = submit_default(engine).auto_order_id
    cycles(engine, FakeBarSource(scenario_bars()), "10:30")
    with engine.connect() as conn:
        run = get_run(conn, list_segments(conn, source_id)[0].run_id)
    backdated_batch(engine, TICKER, [raw(DAY, "10:05", 104, 104, 104, 104)], run.data_as_of)

    report = reproduce_orders(engine, code_version=CODE_VERSION, order_ids=[source_id])
    assert report.created == {} and report.failures == {source_id: errors.REPRODUCE_DIVERGENCE}
    assert count(engine, "orders") == 1 and count(engine, "integrity_incidents") == 1
    with engine.connect() as conn:
        assert load_projection(conn, source_id).state.frozen


def test_selection_rules(engine):
    first = submit_default(engine).auto_order_id
    second = submit_default(engine, client_signal_id="second").auto_order_id
    with pytest.raises(ReplaySelectionError):
        reproduce_orders(engine, code_version=CODE_VERSION, order_ids=[first],
                         created_from=et(DAY, "08:00"), created_to=et(DAY, "10:00"))
    with pytest.raises(ReplaySelectionError):
        reproduce_orders(engine, code_version=CODE_VERSION, created_from=et(DAY, "08:00"))
    report = reproduce_orders(engine, code_version=CODE_VERSION, created_from=et(DAY, "08:00"),
                              created_to=et(DAY, "10:00"))
    assert set(report.created) == {first, second}
    again = reproduce_orders(engine, code_version=CODE_VERSION, created_from=et(DAY, "08:00"),
                             created_to=et(DAY, "10:00"))
    assert set(again.created) == {first, second}  # replay orders themselves are never selected


def test_unknown_order_is_reported_not_raised(engine):
    missing = uuid4()
    report = reproduce_orders(engine, code_version=CODE_VERSION, order_ids=[missing])
    assert report.failures == {missing: "ORDER_NOT_FOUND"}


def test_replay_orders_are_not_evaluated_live(engine):
    source_id = submit_default(engine).auto_order_id
    bars = FakeBarSource(scenario_bars())
    cycles(engine, bars, "10:30")
    replay_id = reproduce_orders(engine, code_version=CODE_VERSION, order_ids=[source_id]).created[source_id]
    report = run_live_cycle(engine, feeds(bars), code_version=CODE_VERSION, market_now=et(DAY, "11:30"))
    assert [o.order_id for o in report.outcomes] == [source_id]
    assert identities(engine, replay_id)[-1][0] == "FILLED"
```

- [ ] **Step 2: Rodar para ver falhar**

Run: `uv run pytest tests/integration/test_reproduce.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'virtual_orders.evaluator.replay'`.

- [ ] **Step 3: Implementar**

`src/virtual_orders/evaluator/replay.py`:
```python
"""Replay (spec 3.6): REPRODUCE regenerates recorded history into a new order; nothing existing changes."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import Connection, Engine, select

from virtual_orders.evaluator.history import regenerate_history
from virtual_orders.ledger.errors import REPRODUCE_DIVERGENCE, HistoryDivergence, LedgerIntegrityError, OrderNotFound
from virtual_orders.ledger.events import insert_event
from virtual_orders.ledger.orders import Projection, insert_order, lock_order, save_projection
from virtual_orders.ledger.quarantine import run_guarded
from virtual_orders.ledger.runs import RunKind, RunStatus, finish_run, record_segment, start_run
from virtual_orders.marketdata.asof import acquire_data_as_of
from virtual_orders.storage.tables import orders

ORDER_NOT_FOUND = "ORDER_NOT_FOUND"


class ReplayMode(StrEnum):
    REPRODUCE = "REPRODUCE"
    RECALCULATE = "RECALCULATE"


class ReplaySelectionError(ValueError):
    pass


@dataclass(frozen=True)
class ReplayReport:
    run_id: UUID
    mode: ReplayMode
    created: dict[UUID, UUID]
    failures: dict[UUID, str]


def select_source_orders(
    conn: Connection,
    *,
    order_ids: Sequence[UUID] | None,
    created_from: datetime | None,
    created_to: datetime | None,
) -> list[UUID]:
    has_interval = created_from is not None or created_to is not None
    if order_ids is not None and has_interval:
        raise ReplaySelectionError("use order_ids or an interval, not both")
    if order_ids is not None:
        return list(dict.fromkeys(order_ids))
    if created_from is None or created_to is None:
        raise ReplaySelectionError("an interval needs both created_from and created_to")
    return list(conn.execute(
        select(orders.c.id)
        .where(orders.c.replay.is_(False), orders.c.created_at >= created_from, orders.c.created_at < created_to)
        .order_by(orders.c.created_at, orders.c.id)
    ).scalars())


def reproduce_order(engine: Engine, source_id: UUID, *, code_version: str) -> UUID:
    def operation() -> UUID:
        with engine.begin() as conn:
            source = lock_order(conn, source_id)
            try:
                history = regenerate_history(conn, source)
            except HistoryDivergence as divergence:
                raise LedgerIntegrityError(
                    REPRODUCE_DIVERGENCE, f"history cannot be reproduced: {divergence.reason}",
                    order_id=source_id, detail={"reason": divergence.reason, "diff": divergence.diff},
                ) from divergence
            replay_order = replace(
                source, id=uuid4(), code_version=code_version, replay=True,
                replay_mode=ReplayMode.REPRODUCE.value, replay_of_order_id=source.id,
            )
            insert_order(conn, replay_order)
            seq = 1
            for item in history.items:
                if item.segment is not None:
                    record_segment(conn, replace(item.segment, order_id=replay_order.id, first_seq=seq))
                for prepared in item.events:
                    insert_event(conn, replay_order.id, seq, prepared)
                    seq += 1
            save_projection(conn, replay_order, history.signal.spec.direction, Projection(history.state, seq))
            return replay_order.id

    return run_guarded(engine, operation)


def reproduce_orders(
    engine: Engine,
    *,
    code_version: str,
    order_ids: Sequence[UUID] | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
) -> ReplayReport:
    with engine.connect() as conn:
        source_ids = select_source_orders(conn, order_ids=order_ids, created_from=created_from, created_to=created_to)
    data_as_of = acquire_data_as_of(engine)
    with engine.begin() as conn:
        run = start_run(conn, RunKind.REPLAY, data_as_of, code_version,
                        detail={"mode": ReplayMode.REPRODUCE.value, "sources": source_ids})
    created: dict[UUID, UUID] = {}
    failures: dict[UUID, str] = {}
    for source_id in source_ids:
        try:
            created[source_id] = reproduce_order(engine, source_id, code_version=code_version)
        except OrderNotFound:
            failures[source_id] = ORDER_NOT_FOUND
        except LedgerIntegrityError as error:
            failures[source_id] = error.kind
    with engine.begin() as conn:
        finish_run(conn, run.run_id, RunStatus.COMPLETED, {
            "created": {str(k): v for k, v in created.items()},
            "failures": {str(k): v for k, v in failures.items()},
        })
    return ReplayReport(run.run_id, ReplayMode.REPRODUCE, created, failures)
```

- [ ] **Step 4: Rodar os testes**

Run: `uv run pytest tests/integration/test_reproduce.py -v`
Expected: PASS (6 testes).

- [ ] **Step 5: Lint, tipos e commit**

Run: `uv run ruff check src tests && uv run mypy`
Expected: verde.

```bash
git add src/virtual_orders/evaluator/replay.py tests/integration/test_reproduce.py
git commit -m "feat(evaluator): REPRODUCE replay with identical events, segments and data hashes

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BKUdi9RGWWdXRXVYFWj1yp"
```

---

### Task 16: Snapshots de dados e replay `RECALCULATE`

Spec 3.1 (`market_data_snapshots`), 3.6 (snapshots, `RECALCULATE`), 5.1 (`POST /replay`), 7 (`RECALCULATE` usando a correção); notas, entrada 9; D5, D6; decisão de detalhe 9.

**Files:**
- Create: `src/virtual_orders/marketdata/snapshots.py`
- Modify: `src/virtual_orders/evaluator/replay.py` (acrescentar `recalculate_order` e `recalculate_orders`)
- Create: `tests/integration/test_recalculate.py`

**Interfaces:**
- Consumes: `select_source_orders`, `ReplayMode`, `ReplayReport`, `ORDER_NOT_FOUND` (Task 15); `regenerate_created` (Task 12); `build_order_context` (Task 8); `get_order`, `get_signal`, `persist_new_order`, `apply_result`, `start_run`, `finish_run`, `record_segment`, `SegmentRow`, `stored_events`, `run_guarded` (Task 7); `read_bars_as_of`, `bars_in_minutes`, `selected_data_hash`, `floor_minute`, `acquire_data_as_of` (Task 6); `config_to_snapshot`, `config_from_snapshot`, `to_document` (Task 3).
- Produces:
  - `virtual_orders.marketdata.snapshots.manifest_hash(entries: Iterable[tuple[str, datetime, UUID]]) -> str`
  - `virtual_orders.marketdata.snapshots.create_or_reuse_snapshot(conn, *, source: str, data_as_of: datetime, tickers: Sequence[str], range_from: datetime, range_to: datetime) -> UUID` — reutiliza quando `(source, data_as_of, tickers ordenados, range, content_manifest_hash)` coincidem
  - `virtual_orders.evaluator.replay.recalculate_order(engine, source_id, *, code_version: str, data_as_of: datetime, fill_model_version: str | None = None, config_overrides: Mapping[str, Any] | None = None) -> UUID`
  - `virtual_orders.evaluator.replay.recalculate_orders(engine, *, code_version: str, order_ids=None, created_from=None, created_to=None, fill_model_version=None, config_overrides=None, data_as_of=None) -> ReplayReport`

- [ ] **Step 1: Escrever os testes que falham**

`tests/integration/test_recalculate.py`:
```python
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from virtual_orders.evaluator.cycle import run_live_cycle
from virtual_orders.evaluator.rebuild import rebuild_projection
from virtual_orders.evaluator.replay import ReplayMode, ReplaySelectionError, recalculate_orders, reproduce_orders
from virtual_orders.ledger.events import stored_events
from virtual_orders.ledger.orders import get_order, read_projection_row
from virtual_orders.ledger.runs import list_segments
from virtual_orders.marketdata.asof import acquire_data_as_of
from virtual_orders.marketdata.ingest import ingest_bars
from virtual_orders.storage import tables
from tests.integration.support import (
    CODE_VERSION, DAY, TICKER, FakeBarSource, count, feeds, flat_raw, raw, scenario_bars, submit_default,
)
from tests.support import et

EX_DAY = "2025-11-26"


def closed_order(engine, **overrides):
    order_id = submit_default(engine, **overrides).auto_order_id
    source = FakeBarSource(scenario_bars())
    for hm in ("10:30", "11:30", "13:00"):
        run_live_cycle(engine, feeds(source), code_version=CODE_VERSION, market_now=et(DAY, hm))
    return order_id, source


def projection(engine, order_id):
    with engine.connect() as conn:
        return read_projection_row(conn, order_id)


def types(engine, order_id):
    with engine.connect() as conn:
        return [e.prepared.type for e in stored_events(conn, order_id)]


def test_recalculate_uses_corrected_history_without_touching_the_source(engine):
    source_id, source = closed_order(engine)
    before = projection(engine, source_id)
    source.load([raw(DAY, "10:05", 104, 104, 104, 104)])
    ingest_bars(engine, source, TICKER, et(DAY, "10:05"), et(DAY, "10:06"))

    report = recalculate_orders(engine, code_version="recalc-sha", order_ids=[source_id])
    assert report.mode is ReplayMode.RECALCULATE and report.failures == {}
    replay_id = report.created[source_id]
    with engine.connect() as conn:
        replay = get_order(conn, replay_id)
    assert replay.replay_mode == "RECALCULATE" and replay.market_data_snapshot_id is not None
    with engine.connect() as conn:
        assert replay.evaluation_start_ts == get_order(conn, source_id).evaluation_start_ts
    assert projection(engine, replay_id)["status"] == "EXPIRED"
    assert types(engine, replay_id) == ["ORDER_CREATED", "EXPIRED"]
    assert projection(engine, source_id) == before
    assert rebuild_projection(engine, replay_id) == projection(engine, replay_id)


def test_config_overrides_change_costs_and_are_recorded(engine):
    source_id, _ = closed_order(engine)
    report = recalculate_orders(engine, code_version=CODE_VERSION, order_ids=[source_id],
                                config_overrides={"commission_per_execution": Decimal("1"),
                                                  "risk_amount": Decimal("200")})
    replay_id = report.created[source_id]
    with engine.connect() as conn:
        replay = get_order(conn, replay_id)
    assert replay.config.commission_per_execution == 1 and replay.risk_amount == 200
    assert projection(engine, replay_id)["r_multiple"] == Decimal("1.735")
    assert types(engine, replay_id) == ["ORDER_CREATED", "FILLED", "TARGET1_HIT", "TARGET2_HIT"]


def test_snapshot_is_reused_for_same_data_as_of_and_new_after_correction(engine):
    source_id, source = closed_order(engine)
    as_of = acquire_data_as_of(engine)
    first = recalculate_orders(engine, code_version=CODE_VERSION, order_ids=[source_id], data_as_of=as_of)
    second = recalculate_orders(engine, code_version=CODE_VERSION, order_ids=[source_id], data_as_of=as_of)
    with engine.connect() as conn:
        snap_a = get_order(conn, first.created[source_id]).market_data_snapshot_id
        snap_b = get_order(conn, second.created[source_id]).market_data_snapshot_id
    assert snap_a == snap_b and count(engine, "market_data_snapshots") == 1

    source.load([raw(DAY, "10:05", 104, 104, 104, 104)])
    ingest_bars(engine, source, TICKER, et(DAY, "10:05"), et(DAY, "10:06"))
    third = recalculate_orders(engine, code_version=CODE_VERSION, order_ids=[source_id])
    with engine.connect() as conn:
        snap_c = get_order(conn, third.created[source_id]).market_data_snapshot_id
        manifests = conn.execute(select(tables.market_data_snapshots.c.content_manifest_hash)).scalars().all()
    assert snap_c != snap_a and len(set(manifests)) == 2


def test_dividends_are_interleaved_at_the_ex_date_open(engine):
    order_id = submit_default(engine, client_signal_id="long-hold", target2=Decimal("150")).auto_order_id
    source = FakeBarSource(scenario_bars() + flat_raw(EX_DAY, "09:30", "10:00", 103))
    run_live_cycle(engine, feeds(source), code_version=CODE_VERSION, market_now=et(DAY, "16:30"))
    with engine.begin() as conn:
        conn.execute(tables.dividends.insert().values(
            ticker=TICKER, ex_date=date(2025, 11, 26), amount=Decimal("0.26"), pay_date=None,
            sources=["fmp", "yfinance"], validated=True, checked_at=et(EX_DAY, "09:25"),
        ))
    ingest_bars(engine, source, TICKER, et(EX_DAY, "09:30"), et(EX_DAY, "10:00"))

    replay_id = recalculate_orders(engine, code_version=CODE_VERSION, order_ids=[order_id]).created[order_id]
    assert types(engine, replay_id) == [
        "ORDER_CREATED", "FILLED", "TARGET1_HIT", "DIVIDEND", "TIME_EXIT", "NEEDS_REVIEW",
    ]
    with engine.connect() as conn:
        dividend = [e.prepared for e in stored_events(conn, replay_id) if e.prepared.type == "DIVIDEND"][0]
        segments = list_segments(conn, replay_id)
    assert dividend.payload["cash"] == "3.25"
    assert [(s.bar_from, s.bar_to) for s in segments] == [
        (et(DAY, "09:30"), et(DAY, "15:59")), (et(EX_DAY, "09:30"), et("2025-11-28", "12:59")),
    ]
    assert rebuild_projection(engine, replay_id) == projection(engine, replay_id)


def test_missing_minute_is_missing_for_reproduce_but_present_for_recalculate(engine):
    """D6: absence is immutable inside the run that observed it, not in the database."""
    fill_bar = next(b for b in scenario_bars() if b.ts == et(DAY, "10:05"))
    order_id = submit_default(engine).auto_order_id
    source = FakeBarSource([b for b in scenario_bars() if b.ts != fill_bar.ts])
    for hm in ("10:30", "11:30", "13:00"):
        run_live_cycle(engine, feeds(source), code_version=CODE_VERSION, market_now=et(DAY, hm))
    assert types(engine, order_id) == ["ORDER_CREATED"]

    source.load([fill_bar])  # provider delivers the 10:05 bar late
    ingest_bars(engine, source, TICKER, et(DAY, "10:05"), et(DAY, "10:06"))

    reproduced = reproduce_orders(engine, code_version=CODE_VERSION, order_ids=[order_id]).created[order_id]
    recalculated = recalculate_orders(engine, code_version=CODE_VERSION, order_ids=[order_id]).created[order_id]
    assert types(engine, reproduced) == ["ORDER_CREATED"]
    with engine.connect() as conn:
        original_hashes = [s.selected_data_hash for s in list_segments(conn, order_id)]
        assert [s.selected_data_hash for s in list_segments(conn, reproduced)] == original_hashes
        filled = [e.prepared for e in stored_events(conn, recalculated) if e.prepared.type == "FILLED"]
    assert [e.bar_ts for e in filled] == [et(DAY, "10:05")]
    assert projection(engine, order_id)["status"] == "PENDING"


def test_selection_requires_ids_or_interval(engine):
    with pytest.raises(ReplaySelectionError):
        recalculate_orders(engine, code_version=CODE_VERSION)
```

- [ ] **Step 2: Rodar para ver falhar**

Run: `uv run pytest tests/integration/test_recalculate.py -v`
Expected: FAIL com `ImportError: cannot import name 'recalculate_orders'`.

- [ ] **Step 3: Implementar snapshots**

`src/virtual_orders/marketdata/snapshots.py`:
```python
"""market_data_snapshots (spec 3.6): a pinned, hashed selection of bar versions."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Connection, func, select

from core.domain.hashing import sha256_hex
from virtual_orders.marketdata.asof import read_bars_as_of
from virtual_orders.storage.tables import market_data_snapshots


def manifest_hash(entries: Iterable[tuple[str, datetime, UUID]]) -> str:
    return sha256_hex(sorted(([ticker, ts, batch_id] for ticker, ts, batch_id in entries),
                             key=lambda entry: (entry[0], entry[1])))


def create_or_reuse_snapshot(
    conn: Connection,
    *,
    source: str,
    data_as_of: datetime,
    tickers: Sequence[str],
    range_from: datetime,
    range_to: datetime,
) -> UUID:
    ordered = sorted(set(tickers))
    entries: list[tuple[str, datetime, UUID]] = []
    for ticker in ordered:
        for item in read_bars_as_of(conn, ticker, source, range_from, range_to, data_as_of):
            if item.batch_id is None:
                raise ValueError(f"bar {item.ts.isoformat()} has no batch_id")
            entries.append((ticker, item.ts, item.batch_id))
    digest = manifest_hash(entries)
    table = market_data_snapshots
    existing: UUID | None = conn.execute(
        select(table.c.id).where(
            table.c.source == source, table.c.data_as_of == data_as_of, table.c.tickers == ordered,
            table.c.range_from == range_from, table.c.range_to == range_to,
            table.c.content_manifest_hash == digest,
        ).limit(1)
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    snapshot_id = uuid4()
    conn.execute(table.insert().values(
        id=snapshot_id, created_at=func.clock_timestamp(), source=source, data_as_of=data_as_of,
        tickers=ordered, range_from=range_from, range_to=range_to, content_manifest_hash=digest,
    ))
    return snapshot_id
```

- [ ] **Step 4: Implementar `RECALCULATE`**

Em `src/virtual_orders/evaluator/replay.py`, estender os imports do topo para:
```python
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Connection, Engine, select

from virtual_orders.evaluator.context import build_order_context
from virtual_orders.evaluator.history import regenerate_created, regenerate_history
from core.fills import get_fill_model
from virtual_orders.ledger.errors import REPRODUCE_DIVERGENCE, HistoryDivergence, LedgerIntegrityError, OrderNotFound
from virtual_orders.ledger.events import insert_event, stored_events
from virtual_orders.ledger.orders import (
    Projection, get_order, get_signal, insert_order, lock_order, save_projection,
)
from virtual_orders.ledger.quarantine import run_guarded
from virtual_orders.ledger.runs import RunKind, RunStatus, SegmentRow, finish_run, record_segment, start_run
from virtual_orders.ledger.writes import apply_result, persist_new_order
from virtual_orders.marketdata.asof import acquire_data_as_of, bars_in_minutes, floor_minute, read_bars_as_of, selected_data_hash
from virtual_orders.marketdata.snapshots import create_or_reuse_snapshot
from virtual_orders.storage.codec import config_from_snapshot, config_to_snapshot, to_document
from virtual_orders.storage.tables import dividends, orders
```

E acrescentar ao final do arquivo:
```python
def _chunks_by_dividend(
    minutes: Sequence[datetime], session_day_of: Mapping[datetime, date], dividend_days: set[date]
) -> list[tuple[date | None, list[datetime]]]:
    """Split expected minutes so each validated/unvalidated dividend is applied at its ex-date open."""
    chunks: list[tuple[date | None, list[datetime]]] = []
    pending: date | None = None
    current: list[datetime] = []
    applied: set[date] = set()
    for minute in minutes:
        day = session_day_of[minute]
        if day in dividend_days and day not in applied:
            chunks.append((pending, current))
            pending, current = day, []
            applied.add(day)
        current.append(minute)
    chunks.append((pending, current))
    return chunks


def recalculate_order(
    engine: Engine,
    source_id: UUID,
    *,
    code_version: str,
    data_as_of: datetime,
    fill_model_version: str | None = None,
    config_overrides: Mapping[str, Any] | None = None,
) -> UUID:
    def operation() -> UUID:
        with engine.begin() as conn:
            source = get_order(conn, source_id)
            signal = get_signal(conn, source.signal_id)
            ticker, direction = signal.spec.ticker, signal.spec.direction
            version = fill_model_version or source.fill_model_version
            model = get_fill_model(version)
            config = config_from_snapshot({**config_to_snapshot(source.config),
                                           **to_document(dict(config_overrides or {}))})
            snapshot_id = create_or_reuse_snapshot(
                conn, source=source.price_source, data_as_of=data_as_of, tickers=[ticker],
                range_from=source.evaluation_start_ts, range_to=source.valid_until_ts,
            )
            order = replace(
                source, id=uuid4(), fill_model_version=version, config=config, code_version=code_version,
                risk_amount=config.risk_amount, replay=True, replay_mode=ReplayMode.RECALCULATE.value,
                replay_of_order_id=source.id, market_data_snapshot_id=snapshot_id,
            )
            ctx = build_order_context(signal, order)
            source_created = stored_events(conn, source.id)[0].prepared.payload
            created = regenerate_created(model, ctx, source_created)
            next_seq = persist_new_order(conn, order, direction, created).next_seq
            state = created.state

            start = order.evaluation_start_ts
            horizon = min(order.valid_until_ts, floor_minute(data_as_of))
            minutes = ctx.calendar.expected_minutes(start, horizon) if horizon > start else []
            bars = bars_in_minutes(
                read_bars_as_of(conn, ticker, order.price_source, start, horizon, data_as_of), minutes
            )
            dividend_rows = {
                row.ex_date: row
                for row in conn.execute(
                    select(dividends).where(dividends.c.ticker == ticker, dividends.c.checked_at <= data_as_of)
                )
            }
            session_day_of = {}
            for minute in minutes:
                session = ctx.calendar.session_containing(minute)
                if session is None:
                    raise RuntimeError(f"calendar inconsistency at {minute.isoformat()}")
                session_day_of[minute] = session.day

            for dividend_day, chunk in _chunks_by_dividend(minutes, session_day_of, set(dividend_rows)):
                if dividend_day is not None:
                    row = dividend_rows[dividend_day]
                    paid = model.apply_dividend(state, ctx, row.ex_date, row.amount, row.validated)
                    next_seq = apply_result(conn, order, direction, next_seq, paid).next_seq
                    state = paid.state
                if not chunk:
                    continue
                run = start_run(conn, RunKind.REPLAY, data_as_of, code_version,
                                detail={"mode": ReplayMode.RECALCULATE.value, "source_order_id": source.id})
                finish_run(conn, run.run_id, RunStatus.COMPLETED, {"order_id": order.id})
                used = [item for item in bars if chunk[0] <= item.ts <= chunk[-1]]
                result = model.run_bars(state, used, ctx)
                appended = apply_result(conn, order, direction, next_seq, result)
                record_segment(conn, SegmentRow(
                    order.id, run.run_id, chunk[0], chunk[-1],
                    selected_data_hash(order.price_source, ticker, chunk, used),
                    appended.first_seq, len(appended.inserted),
                ))
                next_seq, state = appended.next_seq, result.state

            if data_as_of >= order.valid_until_ts and not state.is_final and not state.frozen:
                last_bar = next((item for item in reversed(bars) if item.ts == state.last_bar_ts), None)
                ended = model.apply_validity_end(state, ctx, last_bar, data_as_of)
                apply_result(conn, order, direction, next_seq, ended)
            return order.id

    return run_guarded(engine, operation)


def recalculate_orders(
    engine: Engine,
    *,
    code_version: str,
    order_ids: Sequence[UUID] | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    fill_model_version: str | None = None,
    config_overrides: Mapping[str, Any] | None = None,
    data_as_of: datetime | None = None,
) -> ReplayReport:
    with engine.connect() as conn:
        source_ids = select_source_orders(conn, order_ids=order_ids, created_from=created_from, created_to=created_to)
    as_of = data_as_of or acquire_data_as_of(engine)
    with engine.begin() as conn:
        run = start_run(conn, RunKind.REPLAY, as_of, code_version, detail={
            "mode": ReplayMode.RECALCULATE.value, "sources": source_ids,
            "fill_model_version": fill_model_version, "config_overrides": dict(config_overrides or {}),
        })
    created: dict[UUID, UUID] = {}
    failures: dict[UUID, str] = {}
    for source_id in source_ids:
        try:
            created[source_id] = recalculate_order(
                engine, source_id, code_version=code_version, data_as_of=as_of,
                fill_model_version=fill_model_version, config_overrides=config_overrides,
            )
        except OrderNotFound:
            failures[source_id] = ORDER_NOT_FOUND
        except LedgerIntegrityError as error:
            failures[source_id] = error.kind
    with engine.begin() as conn:
        finish_run(conn, run.run_id, RunStatus.COMPLETED, {
            "created": {str(k): v for k, v in created.items()},
            "failures": {str(k): v for k, v in failures.items()},
        })
    return ReplayReport(run.run_id, ReplayMode.RECALCULATE, created, failures)
```

- [ ] **Step 5: Rodar os testes**

Run: `uv run pytest tests/integration/test_recalculate.py tests/integration/test_reproduce.py -v`
Expected: PASS (6 + 6 testes).

- [ ] **Step 6: Lint, tipos e commit**

Run: `uv run ruff check src tests && uv run mypy`
Expected: verde.

```bash
git add src/virtual_orders/marketdata/snapshots.py src/virtual_orders/evaluator/replay.py tests/integration/test_recalculate.py
git commit -m "feat(evaluator): market data snapshots and RECALCULATE replay with dividend interleaving

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BKUdi9RGWWdXRXVYFWj1yp"
```

---

### Task 17: Ponta a ponta com o adapter Alpaca, verificação final e notas de encerramento

Spec 7 ("Ponta a ponta": pregão gravado → sinal → ordem → eventos → projeção → métricas → `REPRODUCE` idêntico); critérios de aceite adicionais; seção 8 do responsável (roadmap Robinhood).

**Files:**
- Create: `tests/integration/test_end_to_end.py`
- Modify: `tests/test_import_boundaries.py` (cobertura mínima da varredura)
- Create: `docs/superpowers/notes/2026-09-12-plan2-closeout.md`
- Create: `docs/superpowers/roadmap/2026-09-12-robinhood-mcp-read-only.md`

**Interfaces:**
- Consumes: tudo das Tasks 4–16; `core.metrics.summary.{TradeResult, execution_counts, summarize, INSUFFICIENT_SAMPLE}` e `virtual_orders.storage.codec.state_from_document`.
- Produces: nenhum código de produção; a suíte completa verde, as notas de encerramento e a entrada de roadmap.

- [ ] **Step 1: Escrever o teste ponta a ponta**

`tests/integration/test_end_to_end.py`:
```python
"""Recorded session through the real Alpaca adapter: signal -> orders -> events -> projection -> metrics -> REPRODUCE."""

import json
from datetime import date, datetime

import httpx
from sqlalchemy import text

from core.domain.models import Direction, FillConfig
from virtual_orders.evaluator.cycle import run_live_cycle
from virtual_orders.evaluator.manual import create_manual_order
from virtual_orders.evaluator.quality import run_end_of_day
from virtual_orders.evaluator.rebuild import rebuild_all_projections
from virtual_orders.evaluator.replay import reproduce_orders
from virtual_orders.evaluator.signals import submit_signal
from virtual_orders.ledger.events import stored_events
from virtual_orders.marketdata.alpaca import ALPACA_IEX_SOURCE, AlpacaBars
from virtual_orders.marketdata.gateway import MarketDataGateway
from core.metrics.summary import INSUFFICIENT_SAMPLE, TradeResult, execution_counts, summarize
from virtual_orders.storage.codec import state_from_document
from tests.integration.support import CODE_VERSION, DAY, SIGNAL_CREATED_AT, FakeReference, scenario_bars, signal_body
from tests.support import et

PAGE_SIZE = 100


def recorded_alpaca() -> AlpacaBars:
    ordered = sorted(scenario_bars(), key=lambda b: b.ts)

    def handler(request: httpx.Request) -> httpx.Response:
        start = datetime.fromisoformat(request.url.params["start"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(request.url.params["end"].replace("Z", "+00:00"))
        window = [b for b in ordered if start <= b.ts <= end]
        offset = int(request.url.params.get("page_token") or 0)
        page = window[offset : offset + PAGE_SIZE]
        token = str(offset + PAGE_SIZE) if offset + PAGE_SIZE < len(window) else None
        body = {
            "bars": [
                {"t": b.ts.isoformat().replace("+00:00", "Z"), "o": float(b.open), "h": float(b.high),
                 "l": float(b.low), "c": float(b.close), "v": int(b.volume), "n": 1, "vw": float(b.close)}
                for b in page
            ],
            "symbol": "AAPL",
            "next_page_token": token,
        }
        return httpx.Response(200, text=json.dumps(body))

    return AlpacaBars(httpx.Client(transport=httpx.MockTransport(handler)), "key", "secret", sleep=lambda s: None)


def trades(engine):
    with engine.connect() as conn:
        rows = conn.execute(text(
            """
            SELECT st.order_id, g.direction, st.opened_at, st.closed_at, st.r_multiple, st.mfe_r, st.mae_r,
                   st.state_document
            FROM order_state st JOIN orders o ON o.id = st.order_id JOIN signals g ON g.id = o.signal_id
            WHERE NOT o.replay
            """
        )).all()
    closed = [
        TradeResult(str(r.order_id), Direction(r.direction), r.opened_at, r.closed_at, r.r_multiple, r.mfe_r,
                    r.mae_r, tuple(r.state_document["review_reasons"]))
        for r in rows if r.state_document["status"] == "CLOSED"
    ]
    return closed, [state_from_document(r.state_document) for r in rows]


def test_recorded_session_end_to_end(engine):
    gateway = MarketDataGateway([recorded_alpaca()])  # the only place a concrete provider is chosen
    submission = submit_signal(engine, signal_body(), config=FillConfig(), code_version=CODE_VERSION,
                               price_source=ALPACA_IEX_SOURCE, now=SIGNAL_CREATED_AT)
    manual = create_manual_order(engine, submission.signal_id, config=FillConfig(), code_version=CODE_VERSION,
                                 price_source=ALPACA_IEX_SOURCE, created_at=et(DAY, "10:00", 30), gateway=gateway)
    for hm in ("10:30", "11:30", "13:00"):
        report = run_live_cycle(engine, gateway, code_version=CODE_VERSION, market_now=et(DAY, hm))
        assert report.ingest_failures == {} and all(o.error is None for o in report.outcomes)
    eod = run_end_of_day(engine, gateway=gateway, reference=FakeReference(), session_day=date(2025, 11, 25),
                         code_version=CODE_VERSION, market_now=et(DAY, "16:30"))
    assert eod.expired == ()

    with engine.connect() as conn:
        auto_events = [e.prepared for e in stored_events(conn, submission.auto_order_id)]
        manual_events = [e.prepared for e in stored_events(conn, manual.order_id)]
    assert [e.type for e in auto_events] == ["ORDER_CREATED", "FILLED", "TARGET1_HIT", "TARGET2_HIT", "DATA_QUALITY"]
    assert [e.type for e in manual_events] == ["ORDER_CREATED", "FILLED", "TARGET1_HIT", "TARGET2_HIT", "DATA_QUALITY"]
    assert auto_events[-1].payload["expected_bars"] == 201
    assert manual_events[-1].payload["expected_bars"] == 170
    assert manual_events[0].payload["partial_bar_skipped"] is True
    with engine.connect() as conn:
        providers = conn.execute(text("SELECT DISTINCT provider FROM bar_batches")).scalars().all()
        sources = conn.execute(text("SELECT DISTINCT source FROM bars_1m")).scalars().all()
    assert providers == ["alpaca"] and sources == [ALPACA_IEX_SOURCE]

    closed, states = trades(engine)
    summary = summarize(closed, execution_counts(states), resamples=200, seed=42)
    assert summary.trades == 2 and summary.win_rate == 1.0 and summary.expectancy_r == 1.75
    assert summary.execution_rate == 1.0 and INSUFFICIENT_SAMPLE in summary.warnings

    replay = reproduce_orders(engine, code_version="e2e-replay", created_from=et(DAY, "08:00"),
                              created_to=et(DAY, "23:00"))
    assert replay.failures == {} and len(replay.created) == 2
    with engine.connect() as conn:
        for source_id, replay_id in replay.created.items():
            assert [e.prepared.identity() for e in stored_events(conn, replay_id)] == [
                e.prepared.identity() for e in stored_events(conn, source_id)
            ]
    assert set(rebuild_all_projections(engine).values()) == {"OK"}
```

- [ ] **Step 2: Rodar o teste ponta a ponta**

Run: `uv run pytest tests/integration/test_end_to_end.py -v`
Expected: PASS. Se falhar, a causa está em alguma task anterior: corrija lá (com teste de regressão na suíte daquela task) e nunca afrouxe as asserções deste teste. Se a causa estiver em `src/core`, **pare e reporte**.

- [ ] **Step 3: Exigir cobertura mínima da varredura de fronteiras**

Acrescentar ao final de `tests/test_import_boundaries.py`:
```python
def test_boundary_scan_covers_the_platform_packages() -> None:
    neutral = {_rel(p) for p in _neutral_files()}
    assert {
        "virtual_orders/evaluator/cycle.py", "virtual_orders/evaluator/manual.py", "virtual_orders/ledger/events.py",
        "virtual_orders/storage/tables.py", "virtual_orders/marketdata/asof.py", "virtual_orders/marketdata/gateway.py",
    } <= neutral
    for adapter in ("alpaca.py", "fmp.py", "yfinance_source.py", "http.py"):
        assert (SRC / "virtual_orders/marketdata" / adapter).exists()
```

Run: `uv run pytest tests/test_import_boundaries.py -v`
Expected: PASS, sem `SKIPPED` no teste dos módulos neutros.

- [ ] **Step 4: Verificação completa**

Run:
```bash
docker compose -f docker-compose.test.yml up -d --wait
uv run ruff check src tests migrations
uv run mypy
uv run pytest
git diff --stat plan/virtual-order-engine-core-complete -- src/core
git status --porcelain -- src/core
```
Expected: ruff e mypy verdes; pytest com todos os testes do Plano 1 (180) e do Plano 2 passando, zero skips de integração; as duas últimas saídas **vazias**. Qualquer linha em `src/core` bloqueia o encerramento e é reportada ao responsável.

- [ ] **Step 5: Escrever as notas de encerramento**

`docs/superpowers/notes/2026-09-12-plan2-closeout.md` — preencher com os valores medidos na execução (nenhum campo pode ficar vazio):
```markdown
# Plan 2 (Virtual Order Engine — Persistência, Ledger, Dados, Evaluator, Replay) — Encerramento

- **Plano:** `docs/superpowers/plans/2026-09-12-virtual-order-engine-persistence.md`
- **Spec:** v1.2 (`spec/virtual-order-engine-v1.2`) + decisões D5–D11 registradas no plano
- **Intervalo:** `<primeiro commit>..<último commit>`
- **Testes:** `<total>` passando (`<unitários>` unitários, `<integração>` de integração); duração da suíte `<segundos>`
- **Núcleo congelado:** `git diff --stat plan/virtual-order-engine-core-complete -- src/core` e `git status --porcelain -- src/core` vazios (`<confirmado em commit>`)

## Critérios de aceite adicionais
| Critério | Teste | Resultado |
|---|---|---|
| nenhuma dependência de I/O entrou em `src/core` | `test_frozen_core_never_imports_platform_or_io` + diff vazio | `<ok>` |
| adapters substituíveis | suíte com `fake_feed` + `test_recorded_session_end_to_end` com Alpaca | `<ok>` |
| payloads de provider não vazam | `test_neutral_infrastructure_never_imports_provider_adapters` | `<ok>` |
| proveniência não hard-coded | `test_provenance_accepts_any_provider_without_migration` | `<ok>` |
| as-of independente do provider | `test_as_of_reads_are_isolated_by_source` | `<ok>` |
| `selected_data_hash` identifica os dados usados | `test_hash_changes_with_source_batch_price_and_ticker`, `test_backdated_version_breaks_regeneration_and_freezes` | `<ok>` |
| REPRODUCE preserva missing do run original | `test_missing_minute_is_missing_for_reproduce_but_present_for_recalculate` | `<ok>` |
| RECALCULATE usa dados posteriores | idem + `test_recalculate_uses_corrected_history_without_touching_the_source` | `<ok>` |
| sem troca silenciosa de provider | `test_no_silent_provider_switch` | `<ok>` |
| Plano 1 verde | suíte completa | `<ok>` |

## Decisões confirmadas ou alteradas na execução
(uma linha por decisão D5–D11 e por decisão de detalhe 1–13: mantida / alterada — motivo — custo se estiver errada)

## Rulings durante a execução
(formato das notas do Plano 1: decisão — motivo — custo se estiver errada)

## Observabilidade a acompanhar em paper
- Frequência de `ACTIONABILITY_UNVERIFIABLE` via `actionability_outcomes` (D7), por ticker (`detail.ticker`) e `missing_count`.

## Entradas para o Plano 3
1. Composição: montar `MarketDataGateway` a partir da configuração (hoje só `AlpacaBars`); `price_source` configurado é passado a `submit_signal` e `create_manual_order`; nenhum outro módulo instancia adapters.
2. `POST /signals` → `submit_signal` (201 CREATED / 200 EXISTING / 409 `IdempotencyConflict` / 422 `SignalValidationError`); verificação de ticker negociável antes do serviço; corpo JSON lido com `parse_float=Decimal`.
3. `POST /signals/{id}/orders` → `create_manual_order` (422/503 por `ManualOrderError.code`).
4. `POST /orders/{id}/cancel` → `cancel_order` (`OrderAlreadyFinal` → 409).
5. `POST /replay` → `reproduce_orders` / `recalculate_orders` (`ReplaySelectionError` → 422).
6. Worker: `run_live_cycle` a cada `EVAL_INTERVAL_MINUTES`; abertura: `apply_dividends` + `freeze_for_splits`; fim de dia: `run_end_of_day`; `rebuild-projections` → `rebuild_all_projections`.
7. `/health`: último run `LIVE` e status, `ingest_failures` consecutivos por `price_source:ticker` (3 ciclos → degradado), `UNKNOWN_DATA_SOURCE`, `integrity_incidents`, fila `NEEDS_REVIEW`, ordens `frozen`, contagem de `ACTIONABILITY_UNVERIFIABLE`.
8. `GET /metrics`: montar `TradeResult` a partir de `order_state` (como em `test_end_to_end.trades`), aplicar filtros `replay`/`needs_review` na API.

## Achados menores adiados
```

- [ ] **Step 6: Registrar o próximo sub-projeto (roadmap)**

`docs/superpowers/roadmap/2026-09-12-robinhood-mcp-read-only.md`:
```markdown
# Robinhood MCP Read-Only Market Data Integration — Roadmap

- **Estado:** registrado; não iniciado. Começa por **feasibility**, sem código.
- **Depende de:** Plano 2 concluído (contratos neutros de market data, `MarketDataGateway`, proveniência sem enum de provider).
- **Não altera:** domínio, `fill_model v1`, ledger ou evaluator. Um provider novo entra como adapter atrás dos contratos existentes.

## Componentes (independentes e nunca fundidos)

- `RobinhoodMarketDataProvider` — **READ ONLY**. Nunca possui `place_order()`, `cancel_order()`, `modify_order()`, `execute_trade()` nem equivalentes.
- `RobinhoodAgenticExecutor` — trading futuro, fora deste sub-projeto. Só depois de: historical validation → paper trading → shadow trading → risk engine → micro-live → agentic trading.
- Nunca uma classe genérica com acesso irrestrito ao MCP.

## Desenho alvo

```text
                 MarketDataGateway
                        |
         +--------------+--------------+
         |              |              |
      Alpaca        Robinhood       yfinance
      Provider       Provider       Validator
```

Usos candidatos, **somente depois de confirmados contra o MCP oficial** (não inventar capacidades não documentadas):
market data, quotes, historical data, fundamentals, earnings, Level 2 / order book, validação cruzada.

## Fase 0 — Feasibility (verificar no MCP oficial e documentar evidências)

1. Autenticação para serviço backend.
2. Persistência e renovação da autenticação.
3. Uso em VPS sem sessão interativa do Codex/Claude.
4. Schema real das tools.
5. Quotes disponíveis.
6. Historical OHLCV.
7. Timeframes.
8. Profundidade histórica.
9. Level 2 / order book.
10. Fundamentals.
11. Earnings.
12. Positions/watchlists, se úteis.
13. Paginação.
14. Rate limits.
15. Freshness.
16. Semântica de erros.
17. Storage, retenção e licenciamento dos dados.

Saída da fase: relatório com o que existe, o que não existe e o que é incerto; só então um plano de implementação.

## Regras de integração herdadas do Plano 2

- Proveniência: `provider = "robinhood"`, `provider_version`, `source` próprio (ex.: `robinhood_<feed>`), `data_tier`, `ingested_at`, `batch_id`, `content_hash`, `request` — sem migration estrutural.
- Sem fallback silencioso: usar Robinhood no lugar de outro feed exige decisão explícita, registrada no run e refletida no `selected_data_hash`.
- Primeiro uso útil previsto: uma `CoveragePolicy` com provider secundário para reduzir `ACTIONABILITY_UNVERIFIABLE` (D7), registrando fontes e versões como evidência, sem alterar a semântica do motor.

## Segurança obrigatória do provider READ ONLY

- Allowlist explícita de tools MCP. **Tool desconhecida é negada por padrão.**
- Permitidas (se existirem e forem confirmadas): quote, bars, fundamentals, earnings, order book, operações de leitura de conta.
- Negadas: place order, cancel order, modify order, options trade, crypto trade, equity trade, qualquer tool desconhecida.
- Proibido passthrough do tipo `call_mcp_tool(tool_name_from_user_or_llm)`; cada tool permitida tem um método tipado próprio.

## Indicadores prontos (RSI, MACD, …)

Se oferecidos, podem ser ingeridos apenas para **cross-validation**. A fonte autoritativa da estratégia continua sendo o Technical Indicator Engine próprio, calculado sobre market data normalizado.
```

- [ ] **Step 7: Commit**

```bash
git add tests/integration/test_end_to_end.py tests/test_import_boundaries.py docs/superpowers/notes/2026-09-12-plan2-closeout.md docs/superpowers/roadmap/2026-09-12-robinhood-mcp-read-only.md
git commit -m "test(e2e): recorded session via Alpaca adapter through gateway to metrics and REPRODUCE; close-out and Robinhood roadmap

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BKUdi9RGWWdXRXVYFWj1yp"
```
**Não** criar tag nesta task (ver Encerramento do controlador).

---

## Encerramento do controlador (depois das 17 tasks)

1. Suíte completa: `uv run pytest` (inclui `slow` e integração).
2. `uv run mypy`.
3. `uv run ruff check src tests migrations`.
4. Migrations do zero: `test_migrations_run_from_zero_and_back` e, num banco vazio criado à mão, `DATABASE_URL=… uv run alembic upgrade head`.
5. Teste com banco limpo: `docker compose -f docker-compose.test.yml down -v && docker compose -f docker-compose.test.yml up -d --wait && uv run pytest tests/integration`.
6. Rebuild D2: `uv run pytest tests/integration/test_rebuild.py -v`.
7. REPRODUCE: `uv run pytest tests/integration/test_reproduce.py -v`.
8. RECALCULATE: `uv run pytest tests/integration/test_recalculate.py -v`.
9. Revisão do plano inteiro (branch `plan-2-persistence` contra `main`) com o modelo mais capaz, incluindo congelamento de `src/core`, D5–D11 e critérios de aceite adicionais.
10. Correções dos achados (cada uma com teste de regressão e commit próprio).
11. Nova revisão completa.
12. Somente com **zero revisões abertas**: atualizar as notas de encerramento e criar a tag local `plan/virtual-order-engine-persistence-complete`. Push de branch/tag só com autorização explícita.

---

## Autorrevisão do plano

**Cobertura da spec, das decisões do responsável e das entradas do Plano 1**

| Requisito | Task |
|---|---|
| 3.1 tabelas (todas) + append-only + role sem UPDATE/DELETE + migrations do zero | 2 |
| 3.3 idempotência estrita de eventos (no-op / `IntegrityError` + incidente + `frozen` + `NEEDS_REVIEW:INTEGRITY`) | 7 |
| 3.3 `POST /signals` 200/201/409/422 (serviço) | 8 |
| 3.4 invariante `bar_ts ≥ evaluation_start_ts` no ledger | 7 |
| 3.4 validade do sinal e herança pela ordem manual | 8, 11 |
| 3.4.1 actionability as-of, run `ACTIONABILITY`, auditoria, 503 | 11 |
| 3.6 leitura as-of, ausentes reproduzíveis, `selected_data_hash`, segmentos, snapshots, `REPRODUCE`, `RECALCULATE` | 6, 9, 15, 16 |
| 4.1 fonte única de fill por ordem (`price_source`), candles fechados, `bar_batch_id` | 2, 6, 8, 9 |
| 4.4 validade (`TIME_EXIT`/`EXPIRED`) fora do candle final | 10 |
| 4.6 `DATA_QUALITY`, `DATA_GAP`, minutos ausentes, conferência diária | 14 |
| 4.7 dividendos (duas fontes) e split | 13 |
| 5.2 `FOR UPDATE`, recarga após lock, advisory lock global | 7, 9, 10 |
| 5.3 ciclo e jobs como funções (agendamento no Plano 3) | 9, 13, 14 |
| 6 falhas de fornecedor, worker cai, divergências, rebuild | 4, 9, 12, 15 |
| 7 integração com Postgres real, fixtures sem rede, ponta a ponta | 2–17 |
| D1 auditoria preservada e reconstruível | 11, 12 |
| D2 rebuild por histórico autoritativo + teste `A == B` | 12 |
| D3 (herdado do núcleo; valores de MFE persistidos em `mfe_r`) | 7, 12 |
| D4 `DATA_QUALITY` único, com `data_as_of` e `evaluation_run_id` | 14 |
| D5 `data_as_of` pós-ingestão sob lock, relógio do banco | 6, 9, 11, 15 |
| D6 ausência imutável no run; REPRODUCE × RECALCULATE; candle não fechado não é missing | 9, 15, 16 |
| D7 `CoveragePolicy` + observabilidade de 503 | 11 |
| D8 adapters `httpx` fora do core, contratos neutros, gateway sem fallback | 1, 4, 5, 9 |
| D9 colunas de infraestrutura | 2, 7 |
| D10 sem `DATA_QUALITY_RECHECK` | 14 (nada criado) |
| D11 fixtures rotuladas synthetic/documentation-derived | 4, 5 |
| Roadmap Robinhood read-only (feasibility, allowlist, indicadores) | 17 |
| Notas 1 (rebuild por comandos), 2 (projeção completa), 3 (D4), 4 (cobertura), 5 (um candle por minuto), 6 (hash uma vez), 7 (calendário com folga), 8 (ruff/mypy/fronteira), 9 (dividendos em replay), 10 (sem mexer em `nyse_calendar`) | 12, 3/7, 14, 11, 6, 3/7, 6, 1, 16, — |

**Fora deste plano:** Plano 3 (rotas FastAPI e autenticação, `/health` e webhook, verificação de ticker negociável, agendamento APScheduler, Streamlit, Docker Compose de produção, composição do gateway); `DATA_QUALITY_RECHECK`; qualquer código Robinhood.

**Consistência de tipos verificada:** `OrderRow` (com `price_source`) / `SignalRow` / `Projection` (Task 7) usados com os mesmos campos nas Tasks 8–16; `PreparedEvent.identity()` (Task 3) é a unidade de comparação em rebuild e replay; `selected_data_hash(source, ticker, minutes, bars)` (Task 6) é chamado com `order.price_source` no ciclo (9), actionability (11), rebuild (12) e `RECALCULATE` (16); `SegmentRow.first_seq/event_count` gravados pelo ciclo (9) e por `RECALCULATE` (16) e consumidos por `regenerate_history` (12); `MarketDataGateway` (Task 4) é o único parâmetro de dados de preço de `run_live_cycle`, `create_manual_order` e `run_end_of_day`; `OrderOutcome` (9) é o retorno de todos os comandos (10, 13, 14); `ManualOrderError.code` usa exatamente `SIGNAL_EXPIRED`, `SIGNAL_NO_LONGER_ACTIONABLE`, `ACTIONABILITY_UNVERIFIABLE`.

---

## Execution Handoff

Modo escolhido pelo responsável: **Subagent-Driven** (REQUIRED SUB-SKILL: superpowers:subagent-driven-development) — subagente novo por task, contexto mínimo, revisão independente entre tasks, commit próprio após verde, tasks estritamente sequenciais.
