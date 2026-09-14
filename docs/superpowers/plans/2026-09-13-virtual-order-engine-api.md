# Virtual Order Engine — Plano 3A: Configuração, Composição, API e Endurecimentos

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expor o Virtual Order Engine por uma API FastAPI autenticada (sinais, ordens manuais, cancelamento, leitura de ordens, métricas, replay e `/health` estruturado), com configuração por ambiente, uma raiz de composição única para os adapters de market data e os endurecimentos 9–15 e 17–19 das notas de encerramento do Plano 2.

**Architecture:** O núcleo (`src/core/`) continua congelado e o Plano 2 continua em `src/virtual_orders/`. Este plano acrescenta:
- `virtual_orders/config.py`: leitura do ambiente, sem vazar segredos;
- `virtual_orders/services.py`: o contêiner neutro de dependências;
- `virtual_orders/bootstrap.py`: a raiz de composição, único módulo fora dos adapters que os importa e constrói;
- `virtual_orders/readmodels/`: consultas de leitura neutras (ordens, métricas, incidentes, saúde);
- `virtual_orders/api/`: rotas finas que chamam serviços do evaluator e read models.

Os endurecimentos corrigem o evaluator e os adapters do Plano 2 sem mudar regras de fill.

**Tech Stack:** Python 3.12, `uv`, FastAPI (Pydantic v2 embutido), uvicorn, PostgreSQL 16, SQLAlchemy 2 Core + psycopg 3, Alembic, httpx (adapters e `TestClient`), pytest, Hypothesis, ruff 0.8.6, mypy 1.13 strict.

**Spec:** `docs/superpowers/specs/2026-09-12-virtual-order-engine-design.md` (SPEC v1.2 FROZEN, tag `spec/virtual-order-engine-v1.2`), mais as decisões D5–D12 do Plano 2 (`docs/superpowers/plans/2026-09-12-virtual-order-engine-persistence.md`) e as decisões D13–D19 abaixo.
- **Entradas:** `docs/superpowers/notes/2026-09-12-plan2-closeout.md`, seção "Entradas para o Plano 3". Este plano cobre as entradas 1–5, 7–15, 17–19. As entradas 6 e 16 ficam no 3B.
- **Seções da spec cobertas:** 3.3 (rota), 3.4.1 (rota), 3.8 (ticker negociável), 5.1, 5.4 (exposição), 6 (mapeamento HTTP), 8.

## Sequência do Plano 3

| Plano | Conteúdo | Estado |
|---|---|---|
| **3A (este)** | Configuração, composição do `MarketDataGateway`, FastAPI (auth, rotas, `/health` estruturado, métricas), endurecimentos 9–15 e 17–19 | — |
| 3B | Worker APScheduler (ciclo, abertura, fim de dia), agendamento do job de abertura, webhook n8n, `DATA_QUALITY_RECHECK` | depende do 3A |
| 3C | Dashboard Streamlit, Docker Compose de produção, ponta a ponta | depende do 3B |

## Fora deste plano

- **Plano 3B:**
  - worker APScheduler (ciclo, abertura, fim de dia) e agendamento do job de abertura;
  - webhook n8n;
  - `DATA_QUALITY_RECHECK` (entrada 16) e o requisito de revisão da entrada 6;
  - comando `rebuild-projections`.
- **Plano 3C:** dashboard Streamlit, Docker Compose de produção, ponta a ponta.
- **Decisões do responsável de 2026-09-13:**
  - **3B também recebe alertas**, entregues **somente** pelo webhook n8n:
    - eventos de ordem;
    - preço cruzando níveis definidos pelo usuário em tickers da watchlist;
    - limiar de "pressão forte", rotulado como estimativa.
  - **3C também recebe:**
    - uma watchlist, com ingestão de candles para tickers sem ordens;
    - gráfico de candles com volume, VWAP e sobreposição das ordens;
    - indicadores de pressão compradora/vendedora derivados de OHLCV, rotulados como estimativas;
    - uma visão de portfólio: posições virtuais e o portfólio real da Robinhood em modo somente leitura. O portfólio real depende da Fase 0 do roadmap `docs/superpowers/roadmap/2026-09-12-robinhood-mcp-read-only.md`.
  - **Nada disso é implementado no 3A.** A forma da API do 3A não pode bloquear essas entregas:
    - um router por recurso em `api/routes/`, cada um incluído separadamente em `create_app`. Watchlist, alertas, candles e portfólio entram como routers novos, sem editar os existentes;
    - um read model neutro por recurso em `readmodels/`. Indicadores derivados de OHLCV entram como read models novos, nunca em `src/core`;
    - `GET /orders/{id}` já devolve candles as-of e eventos, a base do gráfico com sobreposição de ordens;
    - nenhuma rota do 3A acopla `run_live_cycle` a si. A ingestão da watchlist fica livre para ter cursores próprios no worker;
    - nenhum adapter novo (Robinhood incluída) entra fora de `bootstrap.py`, que continua sendo a única raiz de composição.

Base: branch `plan-3a-api` a partir de `plan-2-persistence`. Depois do merge do PR #1, rebase sobre `main`.
- Todo intervalo de commits deste plano usa `$(git merge-base main HEAD)..HEAD` quando o rebase já tiver ocorrido, e `plan-2-persistence..HEAD` antes dele. A Task 16 grava no ledger qual base valeu no fechamento.

## Global Constraints

### Núcleo e evaluator

- Python **3.12**. Dependências entram via `uv add`, e os testes rodam com `uv run pytest`.
- **`src/core/` está congelado.** Nenhum arquivo em `src/core/` é criado, editado ou removido.
  - Se um teste exigir mudar a semântica do `fill_model v1` ou de qualquer módulo de `src/core/`, **pare e reporte ao responsável**. Nunca corrija silenciosamente.
  - O encerramento exige que `git diff plan/virtual-order-engine-core-complete -- src/core` saia vazio.
- Todas as Global Constraints do Plano 2 continuam valendo:
  - market data neutra;
  - sem fallback de provider;
  - `Decimal` para preço e dinheiro;
  - `datetime` sempre timezone-aware em UTC;
  - `payload_hash` calculado uma única vez;
  - nenhum teste acessa a rede;
  - Postgres real em `127.0.0.1:55432` (`docker compose -f docker-compose.test.yml up -d --wait`);
  - fixtures rotuladas **synthetic/documentation-derived fixture** em `tests/fixtures/README.md`.

### Fronteiras de import

Verificadas por `tests/test_import_boundaries.py`:
- **Adapters:** fora dos próprios adapters (`marketdata/alpaca.py`, `fmp.py`, `yfinance_source.py`, `http.py`), só `virtual_orders/bootstrap.py` importa `virtual_orders.marketdata.{alpaca,fmp,yfinance_source,http}`.
- **API:** `virtual_orders/api/**` nunca importa adapters, `httpx`, `yfinance`, `pandas` nem `virtual_orders.bootstrap`.
- **Pacotes neutros:** `storage`, `ledger`, `evaluator` e `readmodels`, mais os módulos neutros de `marketdata`, nunca importam `virtual_orders.api`, `virtual_orders.bootstrap`, `virtual_orders.config`, `fastapi`, `starlette` nem `uvicorn`.

### Segurança e HTTP

- **Segredos:** `API_KEY`, `ALPACA_API_KEY`, `ALPACA_SECRET_KEY` e `FMP_API_KEY` nunca aparecem em `repr`, mensagens de erro, respostas HTTP ou run details. Erros de configuração nomeiam a variável, nunca o valor.
- **JSON de entrada** é lido com `json.loads(raw, parse_float=Decimal)` e rejeita `NaN`/`Infinity`.
- **JSON de saída:**
  - `Decimal` sai como string normalizada, pela mesma regra de `core.domain.hashing`: `"0"` para zero e `format(value.normalize(CANONICAL_CONTEXT), "f")` nos demais casos;
  - `datetime` sai em ISO-8601 UTC;
  - `UUID` sai como string.
- **JSON de entrada de sinais:** os campos decimais de `POST /signals` são canonicalizados na fronteira da API antes do hash (D19). A função de hash do Plano 2 não muda.
- **Envelope de erro:** `{"error": {"code": <str>, "reason": <str|null>, "detail": <object>}}`. Vale para **toda** resposta de erro, inclusive rota inexistente (`404 NOT_FOUND`), método errado (`405 METHOD_NOT_ALLOWED`) e exceção não tratada (`500 INTERNAL_ERROR`, sem mensagem, só `detail.type`).
- **Códigos:** os da spec, com os novos definidos em D14, D15, D17 e D19 (tabela completa na D19).
- **Autenticação:** toda rota exige `X-API-Key`, comparado com `hmac.compare_digest`. `/docs`, `/redoc` e `/openapi.json` ficam desligados.

### Qualidade e commits

- `uv run ruff check src tests migrations` e `uv run mypy` passam limpos, sem novas entradas em `per-file-ignores` nem overrides de mypy para código novo.
- Testes que usam Postgres ficam em `tests/integration/`, marcados automaticamente como `integration`.
- Identificadores de código em inglês; prosa do plano em português.
- **Commits:** identidade repo-local `Ricardo Carneiro <132141856+ricdomingues@users.noreply.github.com>`.
  - **Nenhum** trailer `Co-Authored-By:` ou `Claude-Session:` em commit ou descrição de PR; isso vale também para subagentes.
  - Nunca alterar configuração global do git.

## Decisões deste plano (a spec v1.2 não é alterada)

- **D13 — Configuração.**
  - `load_settings(environ)` lê as variáveis da spec 8 uma vez, no startup, e reporta todos os erros juntos (`ConfigError.errors`).
  - `ENV_VARIABLES` lista todas as variáveis lidas (spec 8 e extras abaixo), para que os testes limpem o ambiente do host antes de montar o seu.
  - Variáveis além da spec:
    - `PRICE_SOURCE`, padrão `alpaca_iex`: fonte fixada nas ordens novas. Precisa estar registrada no `MarketDataGateway`, senão o startup falha com `UNKNOWN_PRICE_SOURCE`; nunca há fallback.
    - `SEC_FEE_RATE`, `TAF_FEE_PER_SHARE` e `TAF_FEE_MAX`, padrão `0`: campos de `FillConfig` que a tabela da spec 8 não nomeia. `SEC_TAF_FEES_ENABLED=true` com as três taxas em `0` é erro de configuração (`FEES_ENABLED_WITHOUT_RATES:SEC_TAF_FEES_ENABLED`), para que taxas ligadas nunca resultem silenciosamente em custo zero.
    - `ALPACA_TRADING_URL`, padrão `https://paper-api.alpaca.markets`: usada só pela checagem de ativos.
- **D14 — Ticker ativo e negociável (spec 3.8).**
  - **Protocolo:** o protocolo neutro `TickerCheck` retorna `TickerStatus(ticker, tradable, reason)`.
  - **Adapter:** `AlpacaAssets` é somente leitura: faz `GET /v2/assets/{symbol}`, nunca chama endpoints de ordem, e rejeita localmente símbolos fora de `^[A-Z][A-Z0-9.\-]{0,9}$` (`INVALID_SYMBOL`), sem requisição.
  - **Resultados:**
    - HTTP 404 → `UNKNOWN_ASSET`;
    - corpo com `symbol` diferente do ticker pedido → `SourceDataError` (dado malformado);
    - `status != "active"` → `INACTIVE`;
    - `class != "us_equity"` ou `tradable=false` → `NOT_TRADABLE`.
  - **Na API:** a checagem roda **só** para um `client_signal_id` ainda desconhecido, depois de `parse_signal_body` (um sinal inválido nunca chama o provider) e antes de `submit_signal`.
    - Não negociável → `422 SIGNAL_VALIDATION_FAILED` com `errors=["TICKER_NOT_TRADABLE:<reason>"]`.
    - Provider indisponível ou dado malformado → `503 TICKER_UNVERIFIABLE`, sem gravar nada (mesmo espírito da D7).
    - Um retry idempotente nunca chama o provider.
- **D15 — Replays são somente leitura e não se encadeiam.**
  - **Comandos:** qualquer comando (`cancel_order`, `freeze_order`, `flag_order_review`, `finalize_validity`) sobre ordem `replay` levanta `ReplayOrderReadOnly`, mapeado para `409 REPLAY_ORDER_READ_ONLY`, sem escrita nem incidente.
  - **Seleção:** `order_ids` com uma ordem replay → `ReplaySelectionError` nos dois modos.
  - **`RECALCULATE`:** valida tudo antes de abrir o run:
    - `fill_model_version` desconhecida;
    - chave desconhecida em `config_overrides`;
    - valor inválido para `FillConfig`;
    - a chave `dividend_tolerance`, que o RECALCULATE ignora (entrada 13).

    Qualquer um desses casos → `ReplaySelectionError` (`422 REPLAY_REQUEST_INVALID`). As duas guardas de `data_as_of` que já existem antes do run (*naive*; posterior à marca d'água de ingestão) passam a levantar `ReplaySelectionError` também. Como ela é subclasse de `ValueError`, os `pytest.raises(ValueError)` do Plano 2 continuam valendo, e a API não precisa capturar `ValueError` genérico.
  - **Privacidade:** `recalculate_order` passa a `_recalculate_order`.
  - **Divergência de `REPRODUCE` na API (spec 6, "IntegrityError com diff de eventos"):**
    - `POST /replay` em `REPRODUCE` responde `200` só quando nenhuma ordem divergiu.
    - Se qualquer ordem falhar com `REPRODUCE_DIVERGENCE`, a resposta é `409 REPRODUCE_DIVERGED`. O `detail` traz o resultado por ordem: `run_id`, `identical` (`{origem: replay}` das idênticas), `diverged` (`{origem: {"incident_id", "reason", "diff"}}`, lidos do incidente mais recente daquela ordem) e `failures` (demais falhas, por exemplo `ORDER_NOT_FOUND`).
    - Falhas que não são divergência (`ORDER_NOT_FOUND`, `ERROR:<Type>`) sem nenhuma divergência continuam em `200` com `failures`, como no `RECALCULATE`. O `RECALCULATE` não muda.
    - O incidente continua gravado pelo serviço (append-only); a API só o lê.
- **D16 — Run próprio do job de abertura (entrada 10).**
  - **Schema:** a migration `0002` acrescenta `OPENING` ao CHECK de `evaluation_runs.kind`.
  - **Execução:** `run_opening` valida a janela antes de criar o run e grava:
    - `RUNNING` com `session_day`, `dividend_sources` e `split_source`;
    - `COMPLETED` com `source_failures` (`"<fonte>:<ticker>"` ou `"splits"`), `dividend_orders`, `split_orders`, `dividend_integrity_errors`, `dividend_order_errors`, `split_integrity_errors` e `split_order_errors`;
    - `FAILED` com `error` se algo fora disso quebrar.
  - **Falhas de fonte:**
    - de dividendos, deixam de ser silenciosas;
    - de splits, viram falha de fonte registrada: nenhuma ordem é congelada e a parte de dividendos já feita permanece.
  - **Fora do 3A:** o agendamento é do 3B.
- **D17 — `/health` estruturado (entradas 7, 15, 17).**
  - O estado é a pior severidade entre as causas: `INFO` < `DEGRADED` < `UNHEALTHY`, com estados `HEALTHY`/`DEGRADED`/`UNHEALTHY`.
  - **HTTP:** `200` para `HEALTHY` e `DEGRADED`. `503` só para `UNHEALTHY`, e `UNHEALTHY` só existe quando o banco está inacessível ou o schema não está na head das migrations. A spec 3.3 e a 6 chamam incidentes e falhas de provider de "degradado", e um `/health` em 503 por 24 h derrubaria o healthcheck do 3C e deixaria o dashboard sem a visão de saúde justamente quando um incidente precisa de atenção.
  - Causas, com os limiares como constantes em `readmodels/health.py` (não são variáveis de ambiente):

    | Causa | Severidade |
    |---|---|
    | `DATABASE_UNAVAILABLE` (erro de conexão: `OperationalError`/`InterfaceError`) | UNHEALTHY |
    | `SCHEMA_NOT_AT_HEAD` (`alembic_version` ≠ `EXPECTED_SCHEMA_REVISION`) | UNHEALTHY |
    | `LAST_CYCLE_FAILED` (último run `LIVE` em `FAILED`) | DEGRADED |
    | `UNKNOWN_DATA_SOURCE` | DEGRADED |
    | `INTEGRITY_INCIDENTS` (nas últimas 24 h, agrupados pela D18) | DEGRADED |
    | `PROJECTION_MISSING_ORDERS` (ordens não replay sem linha em `order_state`, sem janela) | DEGRADED |
    | `INFRASTRUCTURE_ERRORS` | DEGRADED |
    | `INGEST_FAILURES_CONSECUTIVE` (mesmo feed falhando em ≥ 3 runs `LIVE` concluídos seguidos, spec 6) | DEGRADED |
    | `LIVE_CYCLE_STALE` (ver abaixo) | DEGRADED |
    | `ORDER_ERRORS` | DEGRADED |
    | `FROZEN_ORDERS` (projeções `frozen` + ordens sem projeção, ruling 10 do Plano 2) | DEGRADED |
    | `QUALITY_NOT_EVALUATED` (D12) | DEGRADED |
    | `OPENING_SOURCE_FAILURES` | DEGRADED |
    | `INGEST_FAILURES` (feed falhando há 1 ou 2 runs seguidos) | INFO |
    | `INTEGRITY_INCIDENTS_HISTORY` (há incidentes, nenhum nas últimas 24 h) | INFO |
    | `NEEDS_REVIEW_QUEUE` | INFO |
    | `ACTIONABILITY_UNVERIFIABLE` (24 h) | INFO |

  - **Obsolescência do ciclo:** só durante um pregão aberto. A referência é `max(market_now do último run LIVE, abertura do pregão atual)`; `market_now` vem do `detail` do run (gravado por `cycle.py`), com `started_at` como fallback para runs `FAILED`. Há causa quando `now - referência > 3 × EVAL_INTERVAL_MINUTES`. Assim a abertura do pregão não dispara alerta por causa do run da véspera, e `/health` e worker comparam o mesmo relógio de mercado.
  - A fila de revisão, os 503 de actionability, falhas de provider abaixo do limiar e incidentes antigos são operação normal ou histórico: aparecem, mas não degradam. Incidentes nunca somem do `/health` (spec 3.3): fora da janela, continuam como `INTEGRITY_INCIDENTS_HISTORY`.
- **D18 — Agregação do lado da leitura (entradas 18, 19).**
  - **Incidentes:** `integrity_incidents` continua com uma linha append-only por ocorrência, porque a spec 3.3 diz que incidentes nunca são silenciados. `/health` agrupa por `(kind, detail.reason)`, com `occurrences`, `affected_count`, `affected_orders` (até 100) e `first/last_recorded_at`.
  - **Ruling (pergunta do rascunho resolvida):** não há deduplicação na escrita. A entrada 18 fica fechada pela agregação na leitura. Se o volume virar problema, o 3B pode acrescentar id do run ou fingerprint ao `detail` (append-only), sem nunca pular linhas.
  - **Erros por ordem:** falhas `ERROR:<Type>` são classificadas estruturalmente em `OrderErrorGroup(error_type, category, occurrences, affected_count, affected_orders, runs)`.
    - Categoria `INFRASTRUCTURE` para `OperationalError`, `InterfaceError`, `DisconnectionError`, `TimeoutError`, `ConnectionError`, `OSError`.
    - Categoria `PROGRAMMING` para os demais tipos.
- **D19 — Convenções de leitura da API.**
  - `GET /orders` e `GET /metrics` usam `replay=false` por padrão, porque nunca misturamos replay silenciosamente (spec 5.4).
  - `from`/`to` de `/metrics` e de `/replay` filtram `orders.created_at` em `[from, to)`, o mesmo critério de `select_source_orders`.
  - `GET /signals?date=` compara a data de `created_at` em `America/New_York`.
  - Listas paginam com `limit` (1–1000, padrão 200) e `offset`.
  - `GET /metrics`: a política de revisão (`include_needs_review`) vale para as métricas de trade (spec 5.4, "métricas principais"). `execution_rate` vem de `execution_counts` sobre todas as ordens com projeção do filtro, **inclusive** as `needs_review`: a taxa de execução mede fills sobre ordens resolvidas, não resultado de trade. Sem `group_by`, a resposta sempre tem um grupo `key=null`, mesmo sem ordens, para que `excluded_needs_review` esteja sempre presente.
  - **Números de `POST /signals`:** o corpo é lido com `parse_float=Decimal`, que devolve `int` para `100` e `Decimal` para `100.0`. Antes do hash, a API converte todo `int` (não `bool`) dos campos decimais de `signals.DECIMAL_FIELDS + OPTIONAL_DECIMAL_FIELDS` em `Decimal`. Assim `100` e `100.0` são a mesma submissão e o hash é igual ao de uma chamada direta com `Decimal("100")`. A função de hash do Plano 2 não muda.
  - **Relógio (ruling):** `Services.clock` fornece só o instante de mercado T (`market_now`, `created_at` de sinais e cliques, `at` de cancelamento, `now` do `/health`). `started_at` e `data_as_of` continuam no relógio do banco (`clock_timestamp()`, D5). O worker do 3B segue a mesma regra.
  - **Códigos de erro da API** (envelope único das Global Constraints):

    | Código | HTTP | Origem |
    |---|---|---|
    | `UNAUTHORIZED` | 401 | `auth.require_api_key` |
    | `NOT_FOUND` | 404 | rota inexistente (`HTTPException` do Starlette) |
    | `METHOD_NOT_ALLOWED` | 405 | método errado (`HTTPException` do Starlette) |
    | `SIGNAL_NOT_FOUND` / `ORDER_NOT_FOUND` | 404 | `SignalNotFound` / `OrderNotFound` |
    | `IDEMPOTENCY_CONFLICT` | 409 | `IdempotencyConflict` (spec 3.3) |
    | `ORDER_ALREADY_FINAL` | 409 | `OrderAlreadyFinal` |
    | `REPLAY_ORDER_READ_ONLY` | 409 | `ReplayOrderReadOnly` (D15) |
    | `REPRODUCE_DIVERGED` | 409 | `POST /replay` em `REPRODUCE` com divergência (D15) |
    | `INTEGRITY_ERROR` (`reason` = `kind`) | 409 em comandos (`POST`); 500 em leituras (`GET`) | `LedgerIntegrityError`. Num comando, o estado do ledger impede a escrita pedida; numa leitura, o dado armazenado está corrompido e não há o que o cliente mudar |
    | `SIGNAL_VALIDATION_FAILED` | 422 | `SignalValidationError` (spec 3.8), inclusive `TICKER_NOT_TRADABLE:<reason>` (D14) |
    | `SIGNAL_EXPIRED` / `SIGNAL_NO_LONGER_ACTIONABLE` | 422 | `ManualOrderError` (spec 3.4.1) |
    | `REPLAY_REQUEST_INVALID` | 422 | `parse_replay_request` e `ReplaySelectionError` (D15) |
    | `INVALID_JSON` | 422 | `read_json_object` |
    | `REQUEST_INVALID` | 422 | `RequestValidationError` e datetimes *naive* em query |
    | `INTERNAL_ERROR` (`detail.type`) | 500 | qualquer exceção não mapeada; nunca expõe a mensagem |
    | `ACTIONABILITY_UNVERIFIABLE` | 503 | `ManualOrderError` (D7) |
    | `TICKER_UNVERIFIABLE` | 503 | `intake_signal` (D14) |

    `GET /health` não usa o envelope: responde sempre `{"state", "causes", "facts"}` (D17).

## Conflitos e lacunas da spec resolvidos acima

1. A spec 9 lista `alpaca-py`. A D8 (Plano 2) mantém httpx; nada muda.
2. A spec 3.8 exige ticker negociável, mas não diz o que fazer quando a checagem é impossível. Resolvido pela D14, com 503.
3. A spec 5.1 não isenta `/health` de autenticação. Por isso a D19 autentica tudo. Como a D17 reserva o 503 para banco ou schema, o healthcheck do Compose (3C) pode enviar a chave; se preferir, o 3C acrescenta um `/livez` sem autenticação e sem banco. Essa escolha é do 3C.
4. A spec 5.1 e a 5.4 não definem a semântica de `from`/`to`. Resolvido pela D19.
5. A entrada 18 ("agregar em vez de um por ordem/ciclo") pode conflitar com a spec 3.3 ("incidentes nunca silenciados"). Resolvido pela D18: agregação só na leitura, sem deduplicação na escrita.
6. A spec 3.3 e a 6 chamam de "degradado" o que o rascunho tratava como indisponível. Resolvido pela D17: 503 só para banco ou schema.
7. A spec 2.2 põe a API sob `core/`. Pelo mesmo raciocínio do Detalhe 12 do Plano 2 (congelamento), a API fica em `virtual_orders/`.

## Estrutura de arquivos

```
docs/superpowers/plans/2026-09-13-virtual-order-engine-api.md       # Task 0   — este plano, versionado
src/virtual_orders/
  config.py              # Task 8   — Settings, ConfigError, load_settings, ENV_VARIABLES
  services.py            # Task 10  — Services (contêiner neutro)
  bootstrap.py           # Task 10/11 — build_services, app_from_environment
  evaluator/clock.py     # Task 3   — require_aware
  evaluator/opening.py   # Task 6   — run_opening, OpeningReport
  readmodels/__init__.py # Task 1
  readmodels/incidents.py# Task 7
  readmodels/signals.py  # Task 11
  readmodels/orders.py   # Task 13
  readmodels/metrics.py  # Task 14
  readmodels/health.py   # Task 15
  api/__init__.py        # Task 1
  api/app.py, auth.py, deps.py, encoding.py, errors.py, intake.py   # Task 11
  api/replay_request.py  # Task 12
  api/routes/{signals,orders,replay,metrics,health}.py              # Tasks 11–15
migrations/versions/0002_opening_run_kind.py                        # Task 6
tests/config_support.py                                              # Task 8
tests/test_config.py, tests/test_bootstrap.py, tests/test_evaluator_clock.py, tests/test_api_errors.py
tests/readmodels/__init__.py, tests/readmodels/test_incidents.py, tests/readmodels/test_health_rules.py
tests/integration/test_naive_clocks.py, test_opening.py, test_incident_groups.py
tests/integration/api/__init__.py, conftest.py, test_signals_api.py, test_commands_api.py,
  test_orders_api.py, test_metrics_api.py, test_health_api.py
```

Comando de verificação usado em toda task (`<paths>` = arquivos de teste da task):

```bash
uv run pytest <paths> -q && uv run ruff check src tests migrations && uv run mypy
```

---

### Task 0: Conferir o plano versionado e medir a linha de base

**Files:**
- Nenhum arquivo novo. O controlador já versionou este plano em `docs/superpowers/plans/2026-09-13-virtual-order-engine-api.md` no branch `plan-3a-api`.

**Interfaces:**
- Produces: a contagem `N_BASE` de testes herdados, registrada no ledger e usada nas Tasks 1 e 16. A Task 16 completa este plano com o "Encerramento do controlador".

- [ ] **Step 1: Confirm the plan is committed on the branch**

```bash
git switch plan-3a-api
git log --oneline -1 -- docs/superpowers/plans/2026-09-13-virtual-order-engine-api.md
git status --short
```

Expected: um commit listado e árvore limpa. Se o plano não estiver versionado, pare e reporte ao controlador; não copie de outro lugar.

- [ ] **Step 2: Measure the inherited test baseline**

```bash
docker compose -f docker-compose.test.yml up -d --wait
uv run pytest 2>&1 | tail -1
```

Expected: tudo verde. Registre no ledger a linha final (por exemplo `N passed`) como `N_BASE`. Nenhuma task deste plano presume um número fixo. A Task 0 não gera commit.

---

### Task 1: Dependências da API e fronteiras de import da camada de aplicação

**Files:**
- Modify: `pyproject.toml` (via `uv add`)
- Create: `src/virtual_orders/api/__init__.py`, `src/virtual_orders/readmodels/__init__.py`
- Modify: `tests/test_import_boundaries.py`

**Interfaces:**
- Consumes: helpers existentes em `tests/test_import_boundaries.py` (`SRC`, `_rel`, `_neutral_files`, `_offending`, `PROVIDER_ADAPTERS`, `PROVIDER_LIBRARIES`).
- Produces: pacotes `virtual_orders.api` e `virtual_orders.readmodels`; constante `COMPOSITION_ROOT = "virtual_orders/bootstrap.py"`; testes que as Tasks 7–15 passam a exercitar automaticamente (parametrizados por arquivo).

- [ ] **Step 1: Write the failing test**

Em `tests/test_import_boundaries.py`:
1. Acrescente `import importlib.util` ao topo.
2. Troque `NEUTRAL_PACKAGES` por:

```python
NEUTRAL_PACKAGES = [
    "virtual_orders/storage", "virtual_orders/ledger", "virtual_orders/evaluator", "virtual_orders/readmodels",
]
```

3. Acrescente, depois de `PROVIDER_LIBRARIES`:

```python
COMPOSITION_ROOT = "virtual_orders/bootstrap.py"
ADAPTER_FILES = frozenset({
    "virtual_orders/marketdata/alpaca.py", "virtual_orders/marketdata/fmp.py",
    "virtual_orders/marketdata/yfinance_source.py", "virtual_orders/marketdata/http.py",
})
APPLICATION_LAYER = (
    "virtual_orders.api", "virtual_orders.bootstrap", "virtual_orders.config", "fastapi", "starlette", "uvicorn",
)
APPLICATION_NEUTRAL = ("virtual_orders/services.py", "virtual_orders/config.py")
```

4. Acrescente, depois de `_neutral_files`:

```python
def _platform_files() -> list[Path]:
    return sorted((SRC / "virtual_orders").rglob("*.py"))


def _api_files() -> list[Path]:
    return sorted((SRC / "virtual_orders/api").rglob("*.py"))
```

5. Acrescente ao final do arquivo:

```python
@pytest.mark.parametrize(
    "path",
    [p for p in _platform_files() if _rel(p) not in ADAPTER_FILES | {COMPOSITION_ROOT}],
    ids=_rel,
)
def test_only_the_composition_root_imports_provider_adapters(path: Path) -> None:
    assert _offending(path, PROVIDER_ADAPTERS) == []


@pytest.mark.parametrize("path", _api_files(), ids=_rel)
def test_api_never_imports_adapters_provider_libraries_or_the_composition_root(path: Path) -> None:
    assert _offending(path, PROVIDER_ADAPTERS + PROVIDER_LIBRARIES + ("virtual_orders.bootstrap",)) == []


@pytest.mark.parametrize("path", _neutral_files(), ids=_rel)
def test_neutral_infrastructure_never_imports_the_application_layer(path: Path) -> None:
    assert _offending(path, APPLICATION_LAYER) == []


@pytest.mark.parametrize(
    "path", [SRC / relative for relative in APPLICATION_NEUTRAL if (SRC / relative).exists()], ids=_rel
)
def test_services_and_config_never_import_adapters_or_provider_libraries(path: Path) -> None:
    assert _offending(path, PROVIDER_ADAPTERS + PROVIDER_LIBRARIES) == []


def test_boundary_scan_covers_the_application_layer() -> None:
    assert "virtual_orders/api/__init__.py" in {_rel(p) for p in _api_files()}
    assert "virtual_orders/readmodels/__init__.py" in {_rel(p) for p in _neutral_files()}
    assert importlib.util.find_spec("fastapi") is not None
    assert importlib.util.find_spec("uvicorn") is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_import_boundaries.py -q`
Expected: FAIL. `test_boundary_scan_covers_the_application_layer` falha no primeiro `assert`. `_api_files()` vazio e `APPLICATION_NEUTRAL` ainda sem arquivos geram parametrizes vazios (reportados como skip); a Task 10 e a Task 8 os povoam, e a Task 16 exige que existam.

- [ ] **Step 3: Write minimal implementation**

```bash
uv add "fastapi>=0.115" "uvicorn>=0.32"
```

`src/virtual_orders/api/__init__.py`:

```python
"""HTTP API (spec 5.1): thin routes over evaluator services and read models. Never imports adapters."""
```

`src/virtual_orders/readmodels/__init__.py`:

```python
"""Provider-neutral read models for the API: plain SQL reads, no writes, no adapters."""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_import_boundaries.py -q && uv run ruff check src tests migrations && uv run mypy`
Expected: PASS. Se o mypy reclamar de stubs de fastapi/uvicorn, **não** acrescente override: o fastapi é tipado, então reporte o erro exato.

- [ ] **Step 5: Run the full suite (regression)**

Run: `uv run pytest`
Expected: todos os testes anteriores continuam passando (`N_BASE` medido na Task 0, mais os novos).

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock src/virtual_orders/api/__init__.py src/virtual_orders/readmodels/__init__.py tests/test_import_boundaries.py
git commit -m "chore(api): add FastAPI and application-layer import boundaries"
```

---

### Task 2: Teto de paginação do Alpaca (entrada 11)

**Files:**
- Modify: `src/virtual_orders/marketdata/alpaca.py:51-67`
- Test: `tests/marketdata/test_http_sources.py`

**Interfaces:**
- Consumes: `_AlpacaClient._pages(path, params) -> list[Any]`.
- Produces: `MAX_PAGES = 1000` em `virtual_orders.marketdata.alpaca`. `_pages` levanta `SourceDataError` quando o `next_page_token` se repete, não é string ou excede `MAX_PAGES` páginas.

- [ ] **Step 1: Write the failing test**

Acrescente ao final de `tests/marketdata/test_http_sources.py`:

```python
def test_alpaca_repeated_page_token_fails_instead_of_looping():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) > 5:  # self-terminating: without the fix the test fails here instead of looping forever
            raise AssertionError("pagination looped")
        return httpx.Response(200, text='{"bars": [], "next_page_token": "same"}')

    source = AlpacaBars(client(handler), "key", "secret")
    with pytest.raises(SourceDataError, match="repeated or invalid next_page_token"):
        source.fetch_bars("AAPL", utc(14, 30), utc(14, 40))
    assert len(calls) == 2


def test_alpaca_pagination_is_capped(monkeypatch):
    import virtual_orders.marketdata.alpaca as alpaca_module

    monkeypatch.setattr(alpaca_module, "MAX_PAGES", 3)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) > 10:  # self-terminating without the cap
            raise AssertionError("pagination was not capped")
        return httpx.Response(200, text=f'{{"bars": [], "next_page_token": "t{len(calls)}"}}')

    source = AlpacaBars(client(handler), "key", "secret")
    with pytest.raises(SourceDataError, match="exceeded 3 pages"):
        source.fetch_bars("AAPL", utc(14, 30), utc(14, 40))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/marketdata/test_http_sources.py -q -k "page_token or capped"`
Expected: FAIL, e a execução termina sozinha. Os handlers falsos limitam as chamadas:
- o primeiro teste falha com `AssertionError: pagination looped` (propagado pelo handler, que não é `SourceDataError`);
- o segundo falha com `AssertionError: pagination was not capped`.

- [ ] **Step 3: Write minimal implementation**

Em `alpaca.py`, acrescente abaixo de `ALPACA_IEX_SOURCE`:

```python
MAX_PAGES = 1000
```

Substitua `_pages` por:

```python
    def _pages(self, path: str, params: dict[str, str | int]) -> list[Any]:
        pages: list[Any] = []
        token: str | None = None
        seen: set[str] = set()
        while True:
            if len(pages) >= MAX_PAGES:
                raise SourceDataError(f"Alpaca pagination for {path} exceeded {MAX_PAGES} pages")
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
            next_token = body.get("next_page_token")
            if not next_token:
                return pages
            if not isinstance(next_token, str) or next_token in seen:
                raise SourceDataError(f"Alpaca repeated or invalid next_page_token for {path}")
            seen.add(next_token)
            token = next_token
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/marketdata/test_http_sources.py tests/integration/test_end_to_end.py -q && uv run ruff check src tests migrations && uv run mypy`
Expected: PASS (o ponta a ponta com paginação real do Plano 2 continua verde).

- [ ] **Step 5: Commit**

```bash
git add src/virtual_orders/marketdata/alpaca.py tests/marketdata/test_http_sources.py
git commit -m "fix(marketdata): cap Alpaca pagination and reject repeated page tokens"
```

---

### Task 3: Instantes *naive* rejeitados na entrada dos serviços (entrada 12)

**Files:**
- Create: `src/virtual_orders/evaluator/clock.py`
- Modify:
  - `src/virtual_orders/evaluator/cycle.py:142-143`
  - `src/virtual_orders/evaluator/quality.py:154-155, 206-215`
  - `src/virtual_orders/evaluator/manual.py:143-147`
  - `src/virtual_orders/evaluator/commands.py:72-75`
  - `src/virtual_orders/evaluator/corporate.py:96-107`
  - `src/virtual_orders/evaluator/signals.py:120-142`
- Test: `tests/test_evaluator_clock.py`, `tests/integration/test_naive_clocks.py`

**Interfaces:**
- Produces: `require_aware(value: datetime, name: str) -> datetime` retorna o instante em UTC e levanta `ValueError(f"{name} must be timezone-aware")`. As Tasks 6, 11 e 12 usam essa função.
- Validação antes de qualquer I/O:
  - `run_live_cycle(market_now=...)`;
  - `run_session_quality(market_now=...)`;
  - `run_end_of_day(market_now=...)`, antes do ciclo;
  - `create_manual_order(created_at=...)`;
  - `cancel_order(at=...)`;
  - `apply_dividends(now=...)`;
  - `submit_signal(now=...)`, antes do hash e de qualquer leitura.

Ruling registrado: a entrada 12 nomeia três funções. `create_manual_order`, `cancel_order`, `apply_dividends` e `submit_signal` têm o mesmo defeito (Plano 2, achados das Tasks 9, 13 e 14; `signals.py:142`) e passam a ser chamadas pela API ou pelo 3B, por isso entram aqui. `evaluate_order(market_now=)`, `finalize_validity(now=)` e `expire_due_orders(now=)` só são chamadas pelo worker; ficam como entrada explícita do 3B (guardar com `require_aware` antes de agendá-las).

- [ ] **Step 1: Write the failing tests**

`tests/test_evaluator_clock.py`:

```python
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from virtual_orders.evaluator.clock import require_aware


def test_require_aware_normalizes_to_utc():
    local = datetime(2025, 11, 25, 10, 0, tzinfo=ZoneInfo("America/New_York"))
    result = require_aware(local, "market_now")
    assert result == datetime(2025, 11, 25, 15, 0, tzinfo=UTC)
    assert result.tzinfo is UTC


def test_require_aware_rejects_naive():
    with pytest.raises(ValueError, match="market_now must be timezone-aware"):
        require_aware(datetime(2025, 11, 25, 15, 0), "market_now")  # noqa: DTZ001
```

`tests/integration/test_naive_clocks.py`:

```python
from datetime import date, datetime
from decimal import Decimal

import pytest

from core.domain.models import FillConfig
from tests.integration.support import (
    CODE_VERSION,
    PRICE_SOURCE,
    FakeBarSource,
    FakeDividends,
    FakeReference,
    count,
    feeds,
    signal_body,
    submit_default,
)
from virtual_orders.evaluator.commands import cancel_order
from virtual_orders.evaluator.corporate import apply_dividends
from virtual_orders.evaluator.cycle import run_live_cycle
from virtual_orders.evaluator.manual import create_manual_order
from virtual_orders.evaluator.quality import run_end_of_day, run_session_quality
from virtual_orders.evaluator.signals import submit_signal
from virtual_orders.ledger.orders import read_projection_row

NAIVE = datetime(2025, 11, 25, 15, 0)  # noqa: DTZ001 - deliberately naive, to be rejected
SESSION = date(2025, 11, 25)


def test_live_cycle_rejects_naive_market_now_before_any_run(engine):
    submit_default(engine)
    source = FakeBarSource()
    with pytest.raises(ValueError, match="market_now must be timezone-aware"):
        run_live_cycle(engine, feeds(source), code_version=CODE_VERSION, market_now=NAIVE)
    assert source.calls == []
    assert count(engine, "evaluation_runs") == 0


def test_session_quality_rejects_naive_market_now(engine):
    with pytest.raises(ValueError, match="market_now must be timezone-aware"):
        run_session_quality(engine, session_day=SESSION, reference=FakeReference(), code_version=CODE_VERSION,
                            market_now=NAIVE)
    assert count(engine, "evaluation_runs") == 0


def test_end_of_day_rejects_naive_market_now_before_the_cycle(engine):
    submit_default(engine)
    source = FakeBarSource()
    with pytest.raises(ValueError, match="market_now must be timezone-aware"):
        run_end_of_day(engine, gateway=feeds(source), reference=FakeReference(), session_day=SESSION,
                       code_version=CODE_VERSION, market_now=NAIVE)
    assert source.calls == []
    assert count(engine, "evaluation_runs") == 0


def test_manual_order_rejects_naive_created_at(engine):
    signal_id = submit_default(engine, auto_order=False).signal_id
    with pytest.raises(ValueError, match="created_at must be timezone-aware"):
        create_manual_order(engine, signal_id, config=FillConfig(), code_version=CODE_VERSION,
                            price_source=PRICE_SOURCE, created_at=NAIVE)
    assert count(engine, "evaluation_runs") == 0


def test_cancel_rejects_naive_at(engine):
    order_id = submit_default(engine).auto_order_id
    with pytest.raises(ValueError, match="at must be timezone-aware"):
        cancel_order(engine, order_id, at=NAIVE)
    with engine.connect() as conn:
        assert read_projection_row(conn, order_id)["status"] == "PENDING"


def test_dividends_reject_naive_now(engine):
    with pytest.raises(ValueError, match="now must be timezone-aware"):
        apply_dividends(engine, ex_date=date(2025, 11, 26), primary=FakeDividends("fmp"),
                        secondary=FakeDividends("yfinance"), tolerance=Decimal("0.001"), now=NAIVE)


def test_submit_signal_rejects_naive_now(engine):
    with pytest.raises(ValueError, match="now must be timezone-aware"):
        submit_signal(engine, signal_body(), config=FillConfig(), code_version=CODE_VERSION,
                      price_source=PRICE_SOURCE, now=NAIVE)
    assert count(engine, "signals") == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_evaluator_clock.py tests/integration/test_naive_clocks.py -q`
Expected: FAIL.
- `ModuleNotFoundError: virtual_orders.evaluator.clock`.
- Depois de criar só o módulo: o ciclo cria run, a qualidade levanta outra mensagem, o cancel grava `CANCELED`, os dividendos levantam `TypeError`, e `submit_signal` grava o sinal (o `astimezone` de um instante *naive* assume o fuso local), logo `DID NOT RAISE`.

- [ ] **Step 3: Write minimal implementation**

`src/virtual_orders/evaluator/clock.py`:

```python
"""Instants entering evaluator services must be timezone-aware (Plan 2 close-out entry 12)."""

from __future__ import annotations

from datetime import UTC, datetime


def require_aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)
```

Em cada serviço, importe `from virtual_orders.evaluator.clock import require_aware` e aplique:

`cycle.py`, em `run_live_cycle`, substitua as duas primeiras linhas do corpo por:

```python
    now = require_aware(market_now, "market_now") if market_now is not None else None
    started = datetime.now(UTC)
    now = now or started
```

`quality.py`, em `run_session_quality`, substitua as duas primeiras linhas do corpo por:

```python
    now = require_aware(market_now, "market_now") if market_now is not None else datetime.now(UTC)
    session = session_for_day(session_day)
```

`quality.py`, em `run_end_of_day`, a primeira linha do corpo passa a ser:

```python
    market_now = require_aware(market_now, "market_now")
```

`manual.py`, em `create_manual_order`, a primeira linha do corpo passa a ser:

```python
    decided_at = require_aware(created_at, "created_at") if created_at is not None else datetime.now(UTC)
```

Remova a linha antiga `decided_at = (created_at or datetime.now(UTC)).astimezone(UTC)` e mantenha o comentário "T is the click…" acima da nova linha.

`commands.py`, em `cancel_order`:

```python
    moment = require_aware(at, "at") if at is not None else datetime.now(UTC)
```

`corporate.py`, em `apply_dividends`, a primeira linha do corpo passa a ser:

```python
    now = require_aware(now, "now")
```

`signals.py`, em `submit_signal`, a primeira linha do corpo (antes do `try` que calcula `payload_hash`) passa a ser:

```python
    now = require_aware(now, "now") if now is not None else None
```

A linha `created_at = (now or datetime.now(UTC)).astimezone(UTC)` continua como está.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_evaluator_clock.py tests/integration/test_naive_clocks.py tests/integration/test_cycle.py tests/integration/test_quality.py tests/integration/test_manual_orders.py tests/integration/test_commands.py tests/integration/test_corporate.py tests/integration/test_signals.py -q && uv run ruff check src tests migrations && uv run mypy`
Expected: PASS.

- [ ] **Step 5: Run the full suite (regression)**

Run: `uv run pytest`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/virtual_orders/evaluator/clock.py src/virtual_orders/evaluator/cycle.py src/virtual_orders/evaluator/quality.py src/virtual_orders/evaluator/manual.py src/virtual_orders/evaluator/commands.py src/virtual_orders/evaluator/corporate.py src/virtual_orders/evaluator/signals.py tests/test_evaluator_clock.py tests/integration/test_naive_clocks.py
git commit -m "fix(evaluator): reject naive datetimes at service entry points"
```

---

### Task 4: Ordens replay não aceitam comandos (entrada 9, primeira metade)

**Files:**
- Modify: `src/virtual_orders/evaluator/commands.py:27-56`
- Test: `tests/integration/test_commands.py`

**Interfaces:**
- Consumes: `apply_command(engine, order_id, command)`; `reproduce_orders(engine, *, code_version, order_ids)`.
- Produces: `class ReplayOrderReadOnly(Exception)` com `.order_id: UUID`.
  - É levantada dentro de `apply_command` logo depois de `lock_order`, antes de carregar a projeção. Assim uma ordem replay nunca gera incidente `PROJECTION_MISSING` nem evento.
  - A Task 11 mapeia para `409 REPLAY_ORDER_READ_ONLY`.

- [ ] **Step 1: Write the failing test**

Em `tests/integration/test_commands.py`, acrescente aos imports:

```python
from virtual_orders.evaluator.commands import ReplayOrderReadOnly
from virtual_orders.evaluator.replay import reproduce_orders
```

Acrescente ao final:

```python
@pytest.mark.parametrize("command", [
    lambda engine, order_id: cancel_order(engine, order_id, at=et(DAY, "13:30")),
    lambda engine, order_id: freeze_order(engine, order_id, reason="SPLIT", ref="2025-11-26"),
    lambda engine, order_id: flag_order_review(engine, order_id, reason="MANUAL", ref="looked-odd"),
    lambda engine, order_id: finalize_validity(engine, order_id, now=AFTER_VALIDITY),
], ids=["cancel", "freeze", "flag", "finalize"])
def test_commands_reject_replay_orders_without_writing(engine, command):
    source_id = submit_default(engine).auto_order_id
    cycle(engine, FakeBarSource(scenario_bars()), "10:30")
    replay_id = reproduce_orders(engine, code_version=CODE_VERSION, order_ids=[source_id]).created[source_id]
    before = keys(engine, replay_id)

    with pytest.raises(ReplayOrderReadOnly) as caught:
        command(engine, replay_id)

    assert caught.value.order_id == replay_id
    assert keys(engine, replay_id) == before
    assert count(engine, "integrity_incidents") == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_commands.py -q -k replay_orders`
Expected: FAIL com `ImportError: cannot import name 'ReplayOrderReadOnly'`.

- [ ] **Step 3: Write minimal implementation**

Em `commands.py`, abaixo de `OrderAlreadyFinal`:

```python
class ReplayOrderReadOnly(Exception):
    """Replay orders are derived history (spec 3.6): no command may append to them (D15)."""

    def __init__(self, order_id: UUID) -> None:
        super().__init__(f"order {order_id} is a replay and accepts no commands")
        self.order_id = order_id
```

Em `apply_command`, logo depois de `order = lock_order(conn, order_id)`:

```python
            if order.replay:
                raise ReplayOrderReadOnly(order_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_commands.py tests/integration/test_reproduce.py tests/integration/test_recalculate.py tests/integration/test_rebuild.py -q && uv run ruff check src tests migrations && uv run mypy`
Expected: PASS. Se um teste existente aplicar comando a uma ordem replay, **pare e reporte** em vez de ajustar o teste.

- [ ] **Step 5: Commit**

```bash
git add src/virtual_orders/evaluator/commands.py tests/integration/test_commands.py
git commit -m "fix(evaluator): replay orders accept no commands"
```

---

### Task 5: Pedidos de replay validados antes do run (entradas 9, segunda metade, 13 e 14)

**Files:**
- Modify: `src/virtual_orders/evaluator/replay.py:61-79, 192-304`
- Test: `tests/integration/test_recalculate.py`

**Interfaces:**
- Consumes: `select_source_orders`, `recalculate_orders`, `reproduce_orders`; `get_fill_model(version)` levanta `KeyError`; `FillConfig`; `config_from_snapshot`, `config_to_snapshot`, `to_document`.
- Produces:
  - `REJECTED_OVERRIDES = frozenset({"dividend_tolerance"})`;
  - `validate_recalculation_request(fill_model_version: str | None, config_overrides: Mapping[str, Any] | None) -> None`, que levanta `ReplaySelectionError`;
  - `select_source_orders` rejeita `order_ids` que contenham ordens replay (`ReplaySelectionError("replay orders cannot be replayed: …")`);
  - as guardas de `data_as_of` de `recalculate_orders` (*naive*; posterior à marca d'água) levantam `ReplaySelectionError` em vez de `ValueError` puro;
  - `recalculate_order` renomeada para `_recalculate_order`.
- A Task 12 mapeia `ReplaySelectionError` para `422 REPLAY_REQUEST_INVALID` com `detail.errors=[str(exc)]`.

- [ ] **Step 1: Write the failing tests**

Em `tests/integration/test_recalculate.py`:
- troque `from datetime import date, datetime, timedelta` por `from datetime import UTC, date, datetime, timedelta`;
- acrescente, antes de `from virtual_orders.evaluator.cycle import run_live_cycle`, a linha `from virtual_orders.evaluator import replay as replay_module`;
- acrescente ao final:

```python
def test_replay_of_a_replay_is_rejected_for_both_modes(engine):
    order_id, _ = closed_order(engine)
    replay_id = reproduce_orders(engine, code_version=CODE_VERSION, order_ids=[order_id]).created[order_id]
    runs_before = count(engine, "evaluation_runs")

    with pytest.raises(ReplaySelectionError, match="replay orders cannot be replayed"):
        reproduce_orders(engine, code_version=CODE_VERSION, order_ids=[replay_id])
    with pytest.raises(ReplaySelectionError, match="replay orders cannot be replayed"):
        recalculate_orders(engine, code_version=CODE_VERSION, order_ids=[order_id, replay_id])

    assert count(engine, "evaluation_runs") == runs_before


@pytest.mark.parametrize(("overrides", "version", "message"), [
    ({"no_such_field": Decimal("1")}, None, "unknown config_overrides: no_such_field"),
    ({"dividend_tolerance": Decimal("0.01")}, None, "config_overrides not applied by RECALCULATE: dividend_tolerance"),
    ({"commission_per_execution": 1.5}, None, "invalid config_overrides"),
    ({"risk_amount": Decimal("-1")}, None, "invalid config_overrides"),
    ({"zone_lost_policy": "SOMETIMES"}, None, "invalid config_overrides"),
    ({"data_gap_minutes": "15"}, None, "invalid config_overrides"),
    ({}, "v9", "unknown fill_model_version: v9"),
])
def test_invalid_recalculation_requests_are_rejected_before_any_run(engine, overrides, version, message):
    order_id, _ = closed_order(engine)
    runs_before, orders_before = count(engine, "evaluation_runs"), count(engine, "orders")

    with pytest.raises(ReplaySelectionError, match=message):
        recalculate_orders(engine, code_version=CODE_VERSION, order_ids=[order_id],
                           config_overrides=overrides, fill_model_version=version)

    assert count(engine, "evaluation_runs") == runs_before
    assert count(engine, "orders") == orders_before


def test_single_order_recalculation_is_private():
    assert not hasattr(replay_module, "recalculate_order")
    assert callable(replay_module._recalculate_order)


def test_data_as_of_guards_are_selection_errors(engine):
    order_id, _ = closed_order(engine)
    runs_before = count(engine, "evaluation_runs")
    with pytest.raises(ReplaySelectionError, match="data_as_of must be timezone-aware"):
        recalculate_orders(engine, code_version=CODE_VERSION, order_ids=[order_id],
                           data_as_of=datetime(2100, 1, 1))  # noqa: DTZ001 - deliberately naive
    with pytest.raises(ReplaySelectionError, match="data_as_of cannot be later than the ingestion watermark"):
        recalculate_orders(engine, code_version=CODE_VERSION, order_ids=[order_id],
                           data_as_of=datetime(2100, 1, 1, tzinfo=UTC))
    assert count(engine, "evaluation_runs") == runs_before
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/integration/test_recalculate.py -q -k "replay_of_a_replay or invalid_recalculation or private or guards_are_selection"`
Expected: FAIL.
- O replay de replay é aceito.
- Os overrides inválidos viram falha por fonte (`ERROR:…`) em vez de exceção.
- `recalculate_order` ainda existe.
- As guardas de `data_as_of` levantam `ValueError`, que não é `ReplaySelectionError`.

- [ ] **Step 3: Write minimal implementation**

Em `replay.py`:
1. Acrescente `from dataclasses import dataclass, fields, replace` no lugar da importação atual de dataclasses.
2. Acrescente `from core.domain.models import FillConfig`.
3. Abaixo de `ORDER_NOT_FOUND`, acrescente:

```python
REJECTED_OVERRIDES = frozenset({"dividend_tolerance"})  # RECALCULATE credits the stored validated dividend row
```

Substitua o ramo `if order_ids is not None:` de `select_source_orders` por:

```python
    if order_ids is not None:
        unique = list(dict.fromkeys(order_ids))
        replays = sorted(
            str(order_id)
            for order_id in conn.execute(
                select(orders.c.id).where(orders.c.id.in_(unique), orders.c.replay.is_(True))
            ).scalars()
        )
        if replays:
            raise ReplaySelectionError(f"replay orders cannot be replayed: {', '.join(replays)}")
        return unique
```

Acrescente, antes de `_chunks_by_dividend`:

```python
def validate_recalculation_request(
    fill_model_version: str | None, config_overrides: Mapping[str, Any] | None
) -> None:
    """Rejects a RECALCULATE request up front (D15) instead of failing per source inside the batch."""
    if fill_model_version is not None:
        try:
            get_fill_model(fill_model_version)
        except KeyError as exc:
            raise ReplaySelectionError(f"unknown fill_model_version: {fill_model_version}") from exc
    overrides = dict(config_overrides or {})
    unknown = sorted(set(overrides) - {f.name for f in fields(FillConfig)})
    if unknown:
        raise ReplaySelectionError(f"unknown config_overrides: {', '.join(unknown)}")
    rejected = sorted(set(overrides) & REJECTED_OVERRIDES)
    if rejected:
        raise ReplaySelectionError(f"config_overrides not applied by RECALCULATE: {', '.join(rejected)}")
    try:
        config_from_snapshot({**config_to_snapshot(FillConfig()), **to_document(overrides)})
    except (TypeError, ValueError, ArithmeticError) as exc:
        raise ReplaySelectionError(f"invalid config_overrides: {exc}") from exc
```

Renomeie `def recalculate_order(` para `def _recalculate_order(` e atualize a única chamada em `recalculate_orders`. Nenhum teste referencia `recalculate_order`: as únicas referências são a definição e essa chamada.

Em `recalculate_orders`, substitua a guarda *naive* por:

```python
    if data_as_of is not None and data_as_of.tzinfo is None:
        raise ReplaySelectionError("data_as_of must be timezone-aware")
    validate_recalculation_request(fill_model_version, config_overrides)
```

e a guarda da marca d'água por:

```python
    if data_as_of is not None and data_as_of > watermark:
        raise ReplaySelectionError("data_as_of cannot be later than the ingestion watermark")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/integration/test_recalculate.py tests/integration/test_reproduce.py -q && uv run ruff check src tests migrations && uv run mypy`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/virtual_orders/evaluator/replay.py tests/integration/test_recalculate.py
git commit -m "fix(evaluator): validate replay requests up front and keep single-order recalculation private"
```

---

### Task 6: Run próprio do job de abertura (entrada 10, D16)

**Files:**
- Create: `migrations/versions/0002_opening_run_kind.py`, `src/virtual_orders/evaluator/opening.py`
- Modify:
  - `src/virtual_orders/ledger/runs.py:18-22` (`RunKind.OPENING`)
  - `src/virtual_orders/evaluator/corporate.py:52-116`
- Test: `tests/integration/test_opening.py`

**Interfaces:**
- Consumes:
  - `apply_dividends(engine, *, ex_date, primary, secondary, tolerance, now)`;
  - `freeze_for_splits(engine, split_source, *, as_of_day)`;
  - `start_run`/`finish_run`;
  - `split_errors`;
  - `require_aware` (Task 3);
  - `acquire_data_as_of`.
- Produces:
  - `RunKind.OPENING = "OPENING"`;
  - `opening_window(ex_date: date, now: datetime) -> tuple[Session, Session]`, que retorna o pregão da ex-date e o anterior e levanta `ValueError` fora de `[fechamento anterior, abertura da ex-date)`;
  - `apply_dividends(..., source_failures: dict[str, str] | None = None)`, que preenche `"<fonte.name>:<ticker>" -> mensagem`;
  - `OpeningReport(run_id: UUID, session_day: date, dividends: tuple[OrderOutcome, ...], splits: tuple[OrderOutcome, ...], source_failures: dict[str, str])`;
  - `run_opening(engine, *, session_day, primary, secondary, split_source, tolerance, code_version, now) -> OpeningReport`.

  O 3B agenda `run_opening`; a Task 15 lê os runs `OPENING`.

- [ ] **Step 1: Write the failing tests**

`tests/integration/test_opening.py`:

```python
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from tests.integration.support import (
    CODE_VERSION,
    DAY,
    FakeBarSource,
    FakeDividends,
    FakeSplits,
    count,
    dividend,
    feeds,
    flat_raw,
    raw,
    submit_default,
)
from tests.support import et
from virtual_orders.evaluator.cycle import run_live_cycle
from virtual_orders.evaluator.opening import run_opening
from virtual_orders.ledger.runs import RunKind, RunStatus, get_run, latest_run_status
from virtual_orders.marketdata.asof import acquire_data_as_of
from virtual_orders.marketdata.sources import SourceUnavailable
from virtual_orders.storage import tables

EX_DAY = "2025-11-26"
TOLERANCE = Decimal("0.001")


class FailingSplits:
    def fetch_splits(self, tickers, start, end):
        raise SourceUnavailable("splits unavailable (fake)")


def open_confirmed(engine):
    """Same scenario as test_corporate.open_confirmed: filled at ~101 and evaluated through the close."""
    order_id = submit_default(engine).auto_order_id
    bars = (
        flat_raw(DAY, "09:30", "10:05", 105)
        + [raw(DAY, "10:05", 101, 101.5, 100.5, 101.2)]
        + flat_raw(DAY, "10:06", "16:00", 103)
    )
    run_live_cycle(engine, feeds(FakeBarSource(bars)), code_version=CODE_VERSION, market_now=et(DAY, "16:30"),
                   close_trailing_gap=True)
    return order_id


def opening(engine, primary, secondary, split_source, hm="09:25"):
    return run_opening(engine, session_day=date.fromisoformat(EX_DAY), primary=primary, secondary=secondary,
                       split_source=split_source, tolerance=TOLERANCE, code_version=CODE_VERSION,
                       now=et(EX_DAY, hm))


def test_opening_run_records_sources_and_outcomes(engine):
    open_confirmed(engine)
    report = opening(engine, FakeDividends("fmp", [dividend("AAPL", EX_DAY, "0.26")]),
                     FakeDividends("yfinance", [dividend("AAPL", EX_DAY, "0.2605")]), FakeSplits())

    assert [o.event_keys for o in report.dividends] == [(f"DIVIDEND:{EX_DAY}",)]
    assert report.splits == () and report.source_failures == {}
    with engine.connect() as conn:
        run = get_run(conn, report.run_id)
        status, detail = latest_run_status(conn, report.run_id)
    assert run.kind is RunKind.OPENING and status is RunStatus.COMPLETED
    assert detail["session_day"] == EX_DAY
    assert detail["dividend_sources"] == ["fmp", "yfinance"] and detail["split_source"] == "FakeSplits"
    assert detail["dividend_orders"] == 1 and detail["split_orders"] == 0
    assert detail["source_failures"] == {}
    assert detail["dividend_order_errors"] == {} and detail["split_integrity_errors"] == {}


def test_source_failures_are_recorded_instead_of_silent(engine):
    open_confirmed(engine)
    report = opening(engine, FakeDividends("fmp", failing=True),
                     FakeDividends("yfinance", [dividend("AAPL", EX_DAY, "0.26")]), FailingSplits())

    assert report.source_failures == {"fmp:AAPL": "fmp unavailable (fake)", "splits": "splits unavailable (fake)"}
    with engine.connect() as conn:
        status, detail = latest_run_status(conn, report.run_id)
    assert status is RunStatus.COMPLETED
    assert detail["source_failures"] == report.source_failures


def test_opening_outside_the_window_creates_no_run(engine):
    with pytest.raises(ValueError, match="before the ex-date session opens"):
        opening(engine, FakeDividends("fmp"), FakeDividends("yfinance"), FakeSplits(), hm="09:45")
    assert count(engine, "evaluation_runs") == 0


def test_run_kind_check_accepts_opening_and_rejects_unknown_kinds(engine):
    as_of = acquire_data_as_of(engine)
    with engine.begin() as conn:
        conn.execute(tables.evaluation_runs.insert().values(
            run_id=uuid4(), kind="OPENING", data_as_of=as_of, code_version=CODE_VERSION, started_at=as_of))
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(tables.evaluation_runs.insert().values(
                run_id=uuid4(), kind="NOPE", data_as_of=as_of, code_version=CODE_VERSION, started_at=as_of))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/integration/test_opening.py -q`
Expected: FAIL com `ModuleNotFoundError: virtual_orders.evaluator.opening`.

- [ ] **Step 3: Write the migration**

`migrations/versions/0002_opening_run_kind.py`:

```python
"""evaluation_runs.kind gains OPENING (Plan 2 close-out entry 10, D16).

Revision ID: 0002
Revises: 0001
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

_KINDS_BEFORE = "'LIVE', 'REPLAY', 'ACTIONABILITY', 'END_OF_DAY'"


def upgrade() -> None:
    op.execute("ALTER TABLE evaluation_runs DROP CONSTRAINT evaluation_runs_kind_check")
    op.execute(
        "ALTER TABLE evaluation_runs ADD CONSTRAINT evaluation_runs_kind_check "
        f"CHECK (kind IN ({_KINDS_BEFORE}, 'OPENING'))"
    )


def downgrade() -> None:
    # Fails loudly if OPENING runs exist: append-only history is never rewritten to fit a downgrade.
    op.execute("ALTER TABLE evaluation_runs DROP CONSTRAINT evaluation_runs_kind_check")
    op.execute(
        f"ALTER TABLE evaluation_runs ADD CONSTRAINT evaluation_runs_kind_check CHECK (kind IN ({_KINDS_BEFORE}))"
    )
```

- [ ] **Step 4: Refactor corporate.py and add `RunKind.OPENING`**

Em `runs.py`, acrescente `OPENING = "OPENING"` ao final de `RunKind`.

Em `corporate.py`:
1. Acrescente `from core.domain.calendar import Session`.
2. Substitua `_lookup` por:

```python
def _lookup(
    source: DividendSource, ticker: str, ex_date: date, failures: dict[str, str]
) -> DividendRecord | None:
    try:
        records = source.fetch_dividends(ticker, ex_date, ex_date)
    except (SourceUnavailable, SourceDataError) as exc:
        failures[f"{source.name}:{ticker}"] = str(exc)
        return None
    return next((record for record in records if record.ex_date == ex_date), None)


def opening_window(ex_date: date, now: datetime) -> tuple[Session, Session]:
    """(ex-date session, previous session); `now` must lie in [previous close, ex-date open)."""
    probe = datetime.combine(ex_date, time(12), tzinfo=UTC)
    sessions = calendar_for_window(probe, probe).sessions
    index = next((i for i, s in enumerate(sessions) if s.day == ex_date), None)
    if index is None:
        raise ValueError(f"{ex_date} is not a trading session")
    if index == 0:
        raise ValueError(f"no session loaded before {ex_date}")
    session, previous_session = sessions[index], sessions[index - 1]
    if now < previous_session.close_utc:
        raise ValueError("dividends must be applied at or after the previous session's close")
    if now >= session.open_utc:
        raise ValueError("dividends must be applied before the ex-date session opens")
    return session, previous_session
```

3. Em `apply_dividends`:
   - acrescente o parâmetro keyword `source_failures: dict[str, str] | None = None`;
   - substitua o bloco que vai de `probe = …` até `raise ValueError("dividends must be applied before the ex-date session opens")` por:

```python
    session, previous_session = opening_window(ex_date, now)
    failures = {} if source_failures is None else source_failures
```

   - troque as duas chamadas `_lookup(primary, ticker, ex_date)`/`_lookup(secondary, ticker, ex_date)` por `_lookup(primary, ticker, ex_date, failures)`/`_lookup(secondary, ticker, ex_date, failures)`.

A linha `now = require_aware(now, "now")` da Task 3 continua sendo a primeira do corpo.

- [ ] **Step 5: Write `opening.py`**

```python
"""Opening job (spec 5.3) with its own run row: sources consulted and failures (Plan 2 close-out entry 10, D16)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Engine

from virtual_orders.evaluator.clock import require_aware
from virtual_orders.evaluator.corporate import apply_dividends, freeze_for_splits, opening_window
from virtual_orders.evaluator.outcomes import OrderOutcome, split_errors
from virtual_orders.ledger.runs import RunInfo, RunKind, RunStatus, finish_run, start_run
from virtual_orders.marketdata.asof import acquire_data_as_of
from virtual_orders.marketdata.sources import DividendSource, SourceDataError, SourceUnavailable, SplitSource

SPLITS_FAILURE_KEY = "splits"


@dataclass(frozen=True)
class OpeningReport:
    run_id: UUID
    session_day: date
    dividends: tuple[OrderOutcome, ...]
    splits: tuple[OrderOutcome, ...]
    source_failures: dict[str, str]


def run_opening(
    engine: Engine,
    *,
    session_day: date,
    primary: DividendSource,
    secondary: DividendSource,
    split_source: SplitSource,
    tolerance: Decimal,
    code_version: str,
    now: datetime,
) -> OpeningReport:
    now = require_aware(now, "now")
    opening_window(session_day, now)  # validated before any run row is created
    base = {
        "session_day": session_day,
        "dividend_sources": [primary.name, secondary.name],
        "split_source": type(split_source).__name__,
    }
    data_as_of = acquire_data_as_of(engine)
    run: RunInfo | None = None
    try:
        with engine.begin() as conn:
            run = start_run(conn, RunKind.OPENING, data_as_of, code_version, detail=base)
        failures: dict[str, str] = {}
        dividends = tuple(apply_dividends(
            engine, ex_date=session_day, primary=primary, secondary=secondary, tolerance=tolerance, now=now,
            source_failures=failures,
        ))
        try:
            splits = tuple(freeze_for_splits(engine, split_source, as_of_day=session_day))
        except (SourceUnavailable, SourceDataError) as exc:
            splits = ()
            failures[SPLITS_FAILURE_KEY] = str(exc)
        dividend_integrity, dividend_errors = split_errors(dividends)
        split_integrity, split_order_errors = split_errors(splits)
        with engine.begin() as conn:
            finish_run(conn, run.run_id, RunStatus.COMPLETED, {
                **base,
                "dividend_orders": len(dividends),
                "split_orders": len(splits),
                "source_failures": failures,
                "dividend_integrity_errors": dividend_integrity,
                "dividend_order_errors": dividend_errors,
                "split_integrity_errors": split_integrity,
                "split_order_errors": split_order_errors,
            })
        return OpeningReport(run.run_id, session_day, dividends, splits, failures)
    except Exception as exc:
        if run is not None:
            with engine.begin() as conn:
                finish_run(conn, run.run_id, RunStatus.FAILED, {**base, "error": repr(exc)})
        raise
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/integration/test_opening.py tests/integration/test_corporate.py tests/integration/test_schema.py -q && uv run ruff check src tests migrations && uv run mypy`
Expected: PASS. O template de banco roda `alembic upgrade head`, que já inclui a `0002`.

- [ ] **Step 7: Verify migrations from zero and downgrade**

Run: `docker compose -f docker-compose.test.yml down -v && docker compose -f docker-compose.test.yml up -d --wait && uv run pytest tests/integration/test_schema.py tests/integration/test_opening.py -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add migrations/versions/0002_opening_run_kind.py src/virtual_orders/ledger/runs.py src/virtual_orders/evaluator/corporate.py src/virtual_orders/evaluator/opening.py tests/integration/test_opening.py
git commit -m "feat(evaluator): opening job records its own run with sources and failures"
```

---

### Task 7: Agregação de incidentes e classificação de erros por ordem (entradas 18, 19, D18)

**Files:**
- Create: `src/virtual_orders/readmodels/incidents.py`, `tests/readmodels/__init__.py`, `tests/readmodels/test_incidents.py`, `tests/integration/test_incident_groups.py`

**Interfaces:**
- Consumes: `ERROR_PREFIX` (`virtual_orders.evaluator.outcomes`); tabela `integrity_incidents`; `record_incident(conn, LedgerIntegrityError)`.
- Produces (usado pela Task 15):
  - `class ErrorCategory(StrEnum)`, com valores `INFRASTRUCTURE` e `PROGRAMMING`;
  - `INFRASTRUCTURE_ERROR_TYPES: frozenset[str]`;
  - `@dataclass(frozen=True) class OrderErrorGroup`, com os campos `error_type: str`, `category: ErrorCategory`, `occurrences: int`, `affected_count: int`, `affected_orders: tuple[str, ...]` (até `MAX_LISTED_ORDERS`) e `runs: tuple[str, ...]`;
  - `classify_order_errors(run_errors: Mapping[str, Mapping[str, str]]) -> list[OrderErrorGroup]`;
  - `MAX_LISTED_ORDERS = 100`;
  - `@dataclass(frozen=True) class IncidentGroup`, com os campos `kind: str`, `reason: str | None`, `occurrences: int`, `affected_count: int`, `affected_orders: tuple[str, ...]`, `first_recorded_at: datetime` e `last_recorded_at: datetime`;
  - `incident_groups(conn: Connection, *, since: datetime | None = None) -> list[IncidentGroup]`;
  - `@dataclass(frozen=True) class IncidentRecord`, com os campos `incident_id: int`, `kind: str`, `order_id: UUID`, `detail: dict[str, Any]` e `recorded_at: datetime`;
  - `latest_incidents(conn: Connection, *, kind: str, order_ids: Collection[UUID]) -> dict[UUID, IncidentRecord]`, com o incidente mais recente de `kind` por ordem. A Task 12 o usa para expor o diff de `REPRODUCE_DIVERGENCE` (D15).

- [ ] **Step 1: Write the failing unit tests**

`tests/readmodels/__init__.py`: arquivo vazio.

`tests/readmodels/test_incidents.py`:

```python
from virtual_orders.readmodels.incidents import ErrorCategory, OrderErrorGroup, classify_order_errors


def test_errors_are_grouped_by_type_with_orders_and_runs():
    groups = classify_order_errors({
        "run-2": {"o1": "ERROR:KeyError", "o3": "ERROR:OperationalError", "o4": "PROJECTION_MISSING"},
        "run-1": {"o1": "ERROR:KeyError", "o2": "ERROR:KeyError"},
    })
    assert groups == [
        OrderErrorGroup("OperationalError", ErrorCategory.INFRASTRUCTURE, 1, 1, ("o3",), ("run-2",)),
        OrderErrorGroup("KeyError", ErrorCategory.PROGRAMMING, 3, 2, ("o1", "o2"), ("run-1", "run-2")),
    ]


def test_integrity_kinds_and_empty_inputs_yield_no_groups():
    assert classify_order_errors({}) == []
    assert classify_order_errors({"run": {"o": "EVENT_HASH_CONFLICT"}}) == []
```

- [ ] **Step 2: Write the failing integration test**

`tests/integration/test_incident_groups.py`:

```python
from datetime import timedelta

from sqlalchemy import func, select

from tests.integration.support import submit_default
from virtual_orders.ledger import errors
from virtual_orders.ledger.errors import LedgerIntegrityError
from virtual_orders.ledger.quarantine import record_incident
from virtual_orders.readmodels.incidents import incident_groups, latest_incidents
from virtual_orders.storage import tables


def test_repeated_incidents_are_one_group_per_kind_and_reason(engine):
    first = submit_default(engine).auto_order_id
    second = submit_default(engine, client_signal_id="second").auto_order_id
    with engine.begin() as conn:
        for order_id in (first, second, first):
            record_incident(conn, LedgerIntegrityError(errors.PROJECTION_MISSING, "gone", order_id=order_id))
        record_incident(conn, LedgerIntegrityError(
            errors.PROJECTION_INTEGRITY_ERROR, "differs", order_id=first, detail={"reason": "PROJECTION_MISMATCH"}))
        now = conn.execute(select(func.clock_timestamp())).scalar_one()
        conn.execute(tables.integrity_incidents.insert().values(
            kind=errors.PROJECTION_MISSING, order_id=second, detail={"message": "old"},
            recorded_at=now - timedelta(days=3)))

    with engine.connect() as conn:
        recent = incident_groups(conn, since=now - timedelta(days=1))
        everything = incident_groups(conn)

    by_kind = {(g.kind, g.reason): g for g in recent}
    missing = by_kind[(errors.PROJECTION_MISSING, None)]
    assert (missing.occurrences, missing.affected_count) == (3, 2)
    assert missing.affected_orders == tuple(sorted((str(first), str(second))))
    assert missing.first_recorded_at <= missing.last_recorded_at
    assert by_kind[(errors.PROJECTION_INTEGRITY_ERROR, "PROJECTION_MISMATCH")].occurrences == 1
    assert {(g.kind, g.reason): g.occurrences for g in everything}[(errors.PROJECTION_MISSING, None)] == 4


def test_latest_incident_per_order_for_a_kind(engine):
    first = submit_default(engine).auto_order_id
    second = submit_default(engine, client_signal_id="second").auto_order_id
    with engine.begin() as conn:
        record_incident(conn, LedgerIntegrityError(
            errors.REPRODUCE_DIVERGENCE, "old", order_id=first, detail={"reason": "OLD", "diff": []}))
        record_incident(conn, LedgerIntegrityError(
            errors.REPRODUCE_DIVERGENCE, "new", order_id=first, detail={"reason": "NEW", "diff": [{"seq": 2}]}))
        record_incident(conn, LedgerIntegrityError(errors.PROJECTION_MISSING, "gone", order_id=second))

    with engine.connect() as conn:
        latest = latest_incidents(conn, kind=errors.REPRODUCE_DIVERGENCE, order_ids=[first, second])
        assert latest_incidents(conn, kind=errors.REPRODUCE_DIVERGENCE, order_ids=[]) == {}

    assert set(latest) == {first}
    assert latest[first].detail["reason"] == "NEW" and latest[first].detail["diff"] == [{"seq": 2}]
    assert latest[first].kind == errors.REPRODUCE_DIVERGENCE and latest[first].incident_id > 0
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/readmodels/test_incidents.py tests/integration/test_incident_groups.py -q`
Expected: FAIL com `ModuleNotFoundError: virtual_orders.readmodels.incidents`.

- [ ] **Step 4: Write minimal implementation**

`src/virtual_orders/readmodels/incidents.py`:

```python
"""Read-side aggregation of integrity incidents and per-order `ERROR:<Type>` failures (D18).

`integrity_incidents` keeps one append-only row per occurrence (spec 3.3: never silenced). Repeated incidents
and catch-all errors are grouped here, for /health and operational views, never by skipping writes.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, select, text

from virtual_orders.evaluator.outcomes import ERROR_PREFIX
from virtual_orders.storage.tables import integrity_incidents

MAX_LISTED_ORDERS = 100
INFRASTRUCTURE_ERROR_TYPES = frozenset({
    "OperationalError", "InterfaceError", "DisconnectionError", "TimeoutError", "ConnectionError", "OSError",
})


class ErrorCategory(StrEnum):
    INFRASTRUCTURE = "INFRASTRUCTURE"
    PROGRAMMING = "PROGRAMMING"


@dataclass(frozen=True)
class OrderErrorGroup:
    error_type: str
    category: ErrorCategory
    occurrences: int
    affected_count: int
    affected_orders: tuple[str, ...]
    runs: tuple[str, ...]


def classify_order_errors(run_errors: Mapping[str, Mapping[str, str]]) -> list[OrderErrorGroup]:
    """`run_errors`: run id -> {order id -> outcome error}. Integrity kinds (no `ERROR:` prefix) are ignored."""
    occurrences: dict[str, int] = defaultdict(int)
    orders: dict[str, set[str]] = defaultdict(set)
    runs: dict[str, set[str]] = defaultdict(set)
    for run_id, errors in run_errors.items():
        for order_id, value in errors.items():
            if not value.startswith(ERROR_PREFIX):
                continue
            error_type = value.removeprefix(ERROR_PREFIX)
            occurrences[error_type] += 1
            orders[error_type].add(order_id)
            runs[error_type].add(run_id)
    groups = [
        OrderErrorGroup(
            error_type,
            ErrorCategory.INFRASTRUCTURE if error_type in INFRASTRUCTURE_ERROR_TYPES else ErrorCategory.PROGRAMMING,
            count,
            len(orders[error_type]),
            tuple(sorted(orders[error_type]))[:MAX_LISTED_ORDERS],
            tuple(sorted(runs[error_type])),
        )
        for error_type, count in occurrences.items()
    ]
    return sorted(groups, key=lambda g: (g.category is not ErrorCategory.INFRASTRUCTURE, -g.occurrences, g.error_type))


@dataclass(frozen=True)
class IncidentGroup:
    kind: str
    reason: str | None
    occurrences: int
    affected_count: int
    affected_orders: tuple[str, ...]
    first_recorded_at: datetime
    last_recorded_at: datetime


_INCIDENT_GROUPS = text(
    """
    SELECT kind, detail->>'reason' AS reason, COUNT(*) AS occurrences,
           COUNT(DISTINCT COALESCE(order_id::text, detail->>'order_id')) AS affected_count,
           ARRAY_REMOVE(ARRAY_AGG(DISTINCT COALESCE(order_id::text, detail->>'order_id')), NULL) AS affected_orders,
           MIN(recorded_at) AS first_recorded_at, MAX(recorded_at) AS last_recorded_at
    FROM integrity_incidents
    WHERE CAST(:since AS timestamptz) IS NULL OR recorded_at >= CAST(:since AS timestamptz)
    GROUP BY kind, detail->>'reason'
    ORDER BY MAX(recorded_at) DESC, kind
    """
)


def incident_groups(conn: Connection, *, since: datetime | None = None) -> list[IncidentGroup]:
    return [
        IncidentGroup(
            row.kind, row.reason, int(row.occurrences), int(row.affected_count),
            tuple(sorted(row.affected_orders))[:MAX_LISTED_ORDERS], row.first_recorded_at, row.last_recorded_at,
        )
        for row in conn.execute(_INCIDENT_GROUPS, {"since": since})
    ]


@dataclass(frozen=True)
class IncidentRecord:
    incident_id: int
    kind: str
    order_id: UUID
    detail: dict[str, Any]
    recorded_at: datetime


def latest_incidents(conn: Connection, *, kind: str, order_ids: Collection[UUID]) -> dict[UUID, IncidentRecord]:
    """Newest incident of `kind` per order, e.g. the REPRODUCE_DIVERGENCE diff returned by POST /replay (D15)."""
    if not order_ids:
        return {}
    rows = conn.execute(
        select(
            integrity_incidents.c.id, integrity_incidents.c.kind, integrity_incidents.c.order_id,
            integrity_incidents.c.detail, integrity_incidents.c.recorded_at,
        )
        .where(integrity_incidents.c.kind == kind, integrity_incidents.c.order_id.in_(list(order_ids)))
        .order_by(integrity_incidents.c.recorded_at.desc(), integrity_incidents.c.id.desc())
    )
    latest: dict[UUID, IncidentRecord] = {}
    for row in rows:
        if row.order_id not in latest:
            latest[row.order_id] = IncidentRecord(int(row.id), row.kind, row.order_id, dict(row.detail), row.recorded_at)
    return latest
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/readmodels tests/integration/test_incident_groups.py tests/test_import_boundaries.py -q && uv run ruff check src tests migrations && uv run mypy`
Expected: PASS (`readmodels/incidents.py` aparece nas fronteiras neutras).

- [ ] **Step 6: Commit**

```bash
git add src/virtual_orders/readmodels/incidents.py tests/readmodels tests/integration/test_incident_groups.py
git commit -m "feat(readmodels): aggregate integrity incidents and classify per-order errors"
```

---

### Task 8: Configuração por ambiente (spec 8, D13)

**Files:**
- Create: `src/virtual_orders/config.py`, `tests/config_support.py`, `tests/test_config.py`

**Interfaces:**
- Consumes: `FillConfig`, `ZoneLostPolicy` (`core.domain.models`).
- Produces (usado pelas Tasks 10 e 11):
  - `DEFAULT_PRICE_SOURCE = "alpaca_iex"` e `DEFAULT_ALPACA_TRADING_URL = "https://paper-api.alpaca.markets"`;
  - `REQUIRED`, `OPTIONAL` e `ENV_VARIABLES: tuple[str, ...]` (`REQUIRED + OPTIONAL`, todas as variáveis que `load_settings` lê);
  - `class ConfigError(Exception)`, com `.errors: list[str]`;
  - `@dataclass(frozen=True, repr=False) class Settings`, com os campos:
    - `api_key`, `database_url`, `alpaca_api_key`, `alpaca_secret_key`, `fmp_api_key`, `code_version` (`str`);
    - `eval_interval_minutes` (`int`);
    - `fill_config` (`FillConfig`);
    - `bootstrap_resamples`, `bootstrap_seed` (`int`);
    - `n8n_webhook_url` (`str | None`);
    - `price_source`, `alpaca_trading_url` (`str`);
  - `load_settings(environ: Mapping[str, str]) -> Settings`;
  - `tests/config_support.py::BASE_ENV: dict[str, str]`.

- [ ] **Step 1: Write the failing tests**

`tests/config_support.py`:

```python
"""Minimal valid environment for configuration and composition tests. Values are fake."""

BASE_ENV: dict[str, str] = {
    "API_KEY": "test-api-key",
    "DATABASE_URL": "postgresql+psycopg://vo:vo@127.0.0.1:1/unused",
    "ALPACA_API_KEY": "alpaca-key",
    "ALPACA_SECRET_KEY": "alpaca-secret",
    "FMP_API_KEY": "fmp-key",
}
```

`tests/test_config.py`:

```python
from decimal import Decimal

import pytest

from core.domain.models import FillConfig, ZoneLostPolicy
from tests.config_support import BASE_ENV
from virtual_orders.config import ENV_VARIABLES, ConfigError, load_settings

REQUIRED = ("API_KEY", "DATABASE_URL", "ALPACA_API_KEY", "ALPACA_SECRET_KEY", "FMP_API_KEY")


def test_defaults_follow_spec_section_8():
    settings = load_settings(BASE_ENV)
    assert settings.code_version == "unknown"
    assert settings.eval_interval_minutes == 2
    assert settings.fill_config == FillConfig()
    assert (settings.bootstrap_resamples, settings.bootstrap_seed) == (2000, 42)
    assert settings.n8n_webhook_url is None
    assert settings.price_source == "alpaca_iex"
    assert settings.alpaca_trading_url == "https://paper-api.alpaca.markets"
    assert (settings.api_key, settings.database_url) == ("test-api-key", BASE_ENV["DATABASE_URL"])


def test_every_variable_maps_to_settings_and_fill_config():
    env = {
        **BASE_ENV, "GIT_SHA": "abc123", "EVAL_INTERVAL_MINUTES": "5", "DEFAULT_RISK_AMOUNT": "250",
        "ENTRY_SLIPPAGE_BPS": "2", "STOP_SLIPPAGE_BPS": "7.5", "COMMISSION_PER_EXECUTION": "0.35",
        "SEC_TAF_FEES_ENABLED": "true", "SEC_FEE_RATE": "0.0000278", "TAF_FEE_PER_SHARE": "0.000166",
        "TAF_FEE_MAX": "8.30", "ZONE_LOST_POLICY": "CANCEL", "TARGET1_SCALE_OUT_PCT": "40",
        "DATA_GAP_MINUTES": "15", "CROSSCHECK_TOLERANCE_PCT": "0.25", "DIVIDEND_TOLERANCE": "0.01",
        "BOOTSTRAP_RESAMPLES": "500", "BOOTSTRAP_SEED": "7", "N8N_WEBHOOK_URL": "https://n8n.example/hook",
        "PRICE_SOURCE": "fake_feed", "ALPACA_TRADING_URL": "https://api.alpaca.markets",
    }
    assert set(env) == set(ENV_VARIABLES) and len(ENV_VARIABLES) == len(set(ENV_VARIABLES))
    settings = load_settings(env)
    assert settings.fill_config == FillConfig(
        risk_amount=Decimal("250"), entry_slippage_bps=Decimal("2"), stop_slippage_bps=Decimal("7.5"),
        commission_per_execution=Decimal("0.35"), sec_taf_fees_enabled=True, sec_fee_rate=Decimal("0.0000278"),
        taf_fee_per_share=Decimal("0.000166"), taf_fee_max=Decimal("8.30"), zone_lost_policy=ZoneLostPolicy.CANCEL,
        target1_scale_out_pct=Decimal("40"), data_gap_minutes=15, crosscheck_tolerance_pct=Decimal("0.25"),
        dividend_tolerance=Decimal("0.01"),
    )
    assert settings.code_version == "abc123" and settings.eval_interval_minutes == 5
    assert (settings.bootstrap_resamples, settings.bootstrap_seed) == (500, 7)
    assert settings.n8n_webhook_url == "https://n8n.example/hook"
    assert settings.price_source == "fake_feed" and settings.alpaca_trading_url == "https://api.alpaca.markets"


def test_missing_required_variables_are_all_reported():
    with pytest.raises(ConfigError) as caught:
        load_settings({"API_KEY": "  "})
    assert caught.value.errors == [f"MISSING:{name}" for name in REQUIRED]


@pytest.mark.parametrize(("name", "value", "error"), [
    ("EVAL_INTERVAL_MINUTES", "two", "INVALID_INTEGER:EVAL_INTERVAL_MINUTES"),
    ("EVAL_INTERVAL_MINUTES", "0", "OUT_OF_RANGE:EVAL_INTERVAL_MINUTES"),
    ("BOOTSTRAP_SEED", "-1", "OUT_OF_RANGE:BOOTSTRAP_SEED"),
    ("DEFAULT_RISK_AMOUNT", "NaN", "INVALID_DECIMAL:DEFAULT_RISK_AMOUNT"),
    ("DEFAULT_RISK_AMOUNT", "abc", "INVALID_DECIMAL:DEFAULT_RISK_AMOUNT"),
    ("SEC_TAF_FEES_ENABLED", "maybe", "INVALID_BOOLEAN:SEC_TAF_FEES_ENABLED"),
    ("ZONE_LOST_POLICY", "SOMETIMES", "INVALID_CHOICE:ZONE_LOST_POLICY"),
    ("DEFAULT_RISK_AMOUNT", "-5", "INVALID_FILL_CONFIG:risk_amount must be positive"),
    ("SEC_TAF_FEES_ENABLED", "true", "FEES_ENABLED_WITHOUT_RATES:SEC_TAF_FEES_ENABLED"),
])
def test_invalid_values_name_the_variable(name, value, error):
    with pytest.raises(ConfigError) as caught:
        load_settings({**BASE_ENV, name: value})
    assert caught.value.errors == [error]


def test_secrets_never_appear_in_repr_or_errors():
    secret = "super-secret-value"
    settings = load_settings({**BASE_ENV, "API_KEY": secret, "ALPACA_SECRET_KEY": secret, "FMP_API_KEY": secret})
    assert secret not in repr(settings)
    with pytest.raises(ConfigError) as caught:
        load_settings({**BASE_ENV, "FMP_API_KEY": "", "DEFAULT_RISK_AMOUNT": secret, "ZONE_LOST_POLICY": secret})
    assert secret not in str(caught.value) and all(secret not in e for e in caught.value.errors)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_config.py -q`
Expected: FAIL com `ModuleNotFoundError: virtual_orders.config`.

- [ ] **Step 3: Write minimal implementation**

`src/virtual_orders/config.py`:

```python
"""Environment configuration (spec 8, D13), read once at startup. Errors name variables, never values."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from core.domain.models import FillConfig, ZoneLostPolicy

DEFAULT_PRICE_SOURCE = "alpaca_iex"
DEFAULT_ALPACA_TRADING_URL = "https://paper-api.alpaca.markets"
REQUIRED = ("API_KEY", "DATABASE_URL", "ALPACA_API_KEY", "ALPACA_SECRET_KEY", "FMP_API_KEY")
OPTIONAL = (
    "GIT_SHA", "EVAL_INTERVAL_MINUTES", "DEFAULT_RISK_AMOUNT", "ENTRY_SLIPPAGE_BPS", "STOP_SLIPPAGE_BPS",
    "COMMISSION_PER_EXECUTION", "SEC_TAF_FEES_ENABLED", "SEC_FEE_RATE", "TAF_FEE_PER_SHARE", "TAF_FEE_MAX",
    "ZONE_LOST_POLICY", "TARGET1_SCALE_OUT_PCT", "DATA_GAP_MINUTES", "CROSSCHECK_TOLERANCE_PCT",
    "DIVIDEND_TOLERANCE", "BOOTSTRAP_RESAMPLES", "BOOTSTRAP_SEED", "N8N_WEBHOOK_URL", "PRICE_SOURCE",
    "ALPACA_TRADING_URL",
)
ENV_VARIABLES = REQUIRED + OPTIONAL
_TRUE = frozenset({"true", "1", "yes"})
_FALSE = frozenset({"false", "0", "no"})


class ConfigError(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass(frozen=True, repr=False)
class Settings:
    api_key: str
    database_url: str
    alpaca_api_key: str
    alpaca_secret_key: str
    fmp_api_key: str
    code_version: str
    eval_interval_minutes: int
    fill_config: FillConfig
    bootstrap_resamples: int
    bootstrap_seed: int
    n8n_webhook_url: str | None
    price_source: str
    alpaca_trading_url: str

    def __repr__(self) -> str:  # secrets and the database URL (may hold a password) stay out of logs
        return (
            f"Settings(code_version={self.code_version!r}, price_source={self.price_source!r}, "
            f"eval_interval_minutes={self.eval_interval_minutes}, fill_config={self.fill_config!r})"
        )


class _Reader:
    def __init__(self, environ: Mapping[str, str]) -> None:
        self._env = environ
        self.errors: list[str] = []

    def _raw(self, name: str) -> str:
        return self._env.get(name, "").strip()

    def required(self, name: str) -> str:
        value = self._raw(name)
        if not value:
            self.errors.append(f"MISSING:{name}")
        return value

    def text(self, name: str, default: str) -> str:
        return self._raw(name) or default

    def integer(self, name: str, default: int, minimum: int) -> int:
        raw = self._raw(name)
        if not raw:
            return default
        try:
            value = int(raw)
        except ValueError:
            self.errors.append(f"INVALID_INTEGER:{name}")
            return default
        if value < minimum:
            self.errors.append(f"OUT_OF_RANGE:{name}")
            return default
        return value

    def decimal(self, name: str, default: Decimal) -> Decimal:
        raw = self._raw(name)
        if not raw:
            return default
        try:
            value = Decimal(raw)
        except InvalidOperation:
            self.errors.append(f"INVALID_DECIMAL:{name}")
            return default
        if not value.is_finite():
            self.errors.append(f"INVALID_DECIMAL:{name}")
            return default
        return value

    def boolean(self, name: str, default: bool) -> bool:
        raw = self._raw(name).lower()
        if not raw:
            return default
        if raw in _TRUE:
            return True
        if raw in _FALSE:
            return False
        self.errors.append(f"INVALID_BOOLEAN:{name}")
        return default

    def zone_lost_policy(self, name: str, default: ZoneLostPolicy) -> ZoneLostPolicy:
        raw = self._raw(name)
        if not raw:
            return default
        try:
            return ZoneLostPolicy(raw)
        except ValueError:
            self.errors.append(f"INVALID_CHOICE:{name}")
            return default


def _fill_config(read: _Reader) -> FillConfig:
    base = FillConfig()
    risk_amount = read.decimal("DEFAULT_RISK_AMOUNT", base.risk_amount)
    entry_slippage_bps = read.decimal("ENTRY_SLIPPAGE_BPS", base.entry_slippage_bps)
    stop_slippage_bps = read.decimal("STOP_SLIPPAGE_BPS", base.stop_slippage_bps)
    commission = read.decimal("COMMISSION_PER_EXECUTION", base.commission_per_execution)
    fees_enabled = read.boolean("SEC_TAF_FEES_ENABLED", base.sec_taf_fees_enabled)
    sec_fee_rate = read.decimal("SEC_FEE_RATE", base.sec_fee_rate)
    taf_fee_per_share = read.decimal("TAF_FEE_PER_SHARE", base.taf_fee_per_share)
    taf_fee_max = read.decimal("TAF_FEE_MAX", base.taf_fee_max)
    zone_lost_policy = read.zone_lost_policy("ZONE_LOST_POLICY", base.zone_lost_policy)
    scale_out = read.decimal("TARGET1_SCALE_OUT_PCT", base.target1_scale_out_pct)
    data_gap_minutes = read.integer("DATA_GAP_MINUTES", base.data_gap_minutes, minimum=1)
    crosscheck = read.decimal("CROSSCHECK_TOLERANCE_PCT", base.crosscheck_tolerance_pct)
    dividend_tolerance = read.decimal("DIVIDEND_TOLERANCE", base.dividend_tolerance)
    if fees_enabled and sec_fee_rate == 0 and taf_fee_per_share == 0 and taf_fee_max == 0:
        read.errors.append("FEES_ENABLED_WITHOUT_RATES:SEC_TAF_FEES_ENABLED")  # never silently zero fees (D13)
    try:
        return FillConfig(
            risk_amount=risk_amount, entry_slippage_bps=entry_slippage_bps, stop_slippage_bps=stop_slippage_bps,
            commission_per_execution=commission, sec_taf_fees_enabled=fees_enabled, sec_fee_rate=sec_fee_rate,
            taf_fee_per_share=taf_fee_per_share, taf_fee_max=taf_fee_max, zone_lost_policy=zone_lost_policy,
            target1_scale_out_pct=scale_out, data_gap_minutes=data_gap_minutes,
            crosscheck_tolerance_pct=crosscheck, dividend_tolerance=dividend_tolerance,
        )
    except (TypeError, ValueError) as exc:  # FillConfig messages name fields, never raw env values
        read.errors.append(f"INVALID_FILL_CONFIG:{exc}")
        return base


def load_settings(environ: Mapping[str, str]) -> Settings:
    read = _Reader(environ)
    required = {name: read.required(name) for name in REQUIRED}
    settings = Settings(
        api_key=required["API_KEY"],
        database_url=required["DATABASE_URL"],
        alpaca_api_key=required["ALPACA_API_KEY"],
        alpaca_secret_key=required["ALPACA_SECRET_KEY"],
        fmp_api_key=required["FMP_API_KEY"],
        code_version=read.text("GIT_SHA", "unknown"),
        eval_interval_minutes=read.integer("EVAL_INTERVAL_MINUTES", 2, minimum=1),
        fill_config=_fill_config(read),
        bootstrap_resamples=read.integer("BOOTSTRAP_RESAMPLES", 2000, minimum=1),
        bootstrap_seed=read.integer("BOOTSTRAP_SEED", 42, minimum=0),
        n8n_webhook_url=read.text("N8N_WEBHOOK_URL", "") or None,
        price_source=read.text("PRICE_SOURCE", DEFAULT_PRICE_SOURCE),
        alpaca_trading_url=read.text("ALPACA_TRADING_URL", DEFAULT_ALPACA_TRADING_URL),
    )
    if read.errors:
        raise ConfigError(read.errors)
    return settings
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py tests/test_import_boundaries.py -q && uv run ruff check src tests migrations && uv run mypy`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/virtual_orders/config.py tests/config_support.py tests/test_config.py
git commit -m "feat(config): settings from the environment without leaking secrets"
```

---

### Task 9: Checagem de ticker ativo e negociável (spec 3.8, D14)

**Files:**
- Modify:
  - `src/virtual_orders/marketdata/sources.py` (acrescenta `TickerStatus` e `TickerCheck`)
  - `src/virtual_orders/marketdata/http.py` (`ResourceNotFound`)
  - `src/virtual_orders/marketdata/alpaca.py` (`AlpacaAssets`)
  - `tests/fixtures/README.md`
- Create: `tests/fixtures/alpaca/asset_active.json`, `tests/fixtures/alpaca/asset_inactive.json`
- Test: `tests/marketdata/test_http_sources.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class TickerStatus`, com `ticker: str`, `tradable: bool`, `reason: str | None = None`;
  - `class TickerCheck(Protocol)`, com `name: str` e `check_ticker(ticker: str) -> TickerStatus`;
  - `class ResourceNotFound(SourceUnavailable)`, levantada por `get_json` em HTTP 404;
  - `ALPACA_TRADING_URL = "https://paper-api.alpaca.markets"`;
  - `class AlpacaAssets(_AlpacaClient)`, com `name = "alpaca_assets"` e `check_ticker`.
  - Motivos: `UNKNOWN_ASSET`, `INACTIVE`, `NOT_TRADABLE` (também para `class != "us_equity"`) e `INVALID_SYMBOL`. Indisponibilidade → `SourceUnavailable`; corpo malformado ou com `symbol` diferente do pedido → `SourceDataError`.

- [ ] **Step 1: Add the fixtures**

`tests/fixtures/alpaca/asset_active.json`:

```json
{"id": "00000000-0000-4000-8000-000000000001", "class": "us_equity", "exchange": "NASDAQ", "symbol": "AAPL",
 "name": "Synthetic Example Common Stock", "status": "active", "tradable": true, "marginable": true,
 "shortable": true, "easy_to_borrow": true, "fractionable": true}
```

`tests/fixtures/alpaca/asset_inactive.json`:

```json
{"id": "00000000-0000-4000-8000-000000000002", "class": "us_equity", "exchange": "NYSE", "symbol": "ZZZZ",
 "name": "Synthetic Delisted Example", "status": "inactive", "tradable": false, "marginable": false,
 "shortable": false, "easy_to_borrow": false, "fractionable": false}
```

Acrescente à tabela de `tests/fixtures/README.md`:

```markdown
| `alpaca/asset_active.json` | synthetic/documentation-derived fixture | Alpaca Trading API `GET /v2/assets/{symbol_or_asset_id}` (ativo negociável) |
| `alpaca/asset_inactive.json` | synthetic/documentation-derived fixture | Alpaca Trading API `GET /v2/assets/{symbol_or_asset_id}` (ativo inativo) |
```

- [ ] **Step 2: Write the failing tests**

Em `tests/marketdata/test_http_sources.py`:
- troque o import do Alpaca por `from virtual_orders.marketdata.alpaca import ALPACA_IEX_SOURCE, AlpacaAssets, AlpacaBars, AlpacaSplits`;
- acrescente `from virtual_orders.marketdata.http import ResourceNotFound, get_json`;
- acrescente `TickerStatus` ao import de `sources`;
- acrescente ao final:

```python
def test_http_404_is_a_distinct_not_found_error():
    with pytest.raises(ResourceNotFound, match="HTTP 404"):
        get_json(client(lambda request: httpx.Response(404)), "https://x.test/a", params={}, sleep=lambda s: None)
    assert issubclass(ResourceNotFound, SourceUnavailable)


def test_alpaca_assets_reports_an_active_tradable_ticker_read_only():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        symbol = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, text=fixture("alpaca/asset_active.json").replace('"AAPL"', f'"{symbol}"'))

    check = AlpacaAssets(client(handler), "key", "secret")
    assert check.check_ticker("AAPL") == TickerStatus("AAPL", True)
    assert check.check_ticker("BRK.B") == TickerStatus("BRK.B", True)
    assert [(r.method, r.url.host, r.url.path) for r in seen] == [
        ("GET", "paper-api.alpaca.markets", "/v2/assets/AAPL"),
        ("GET", "paper-api.alpaca.markets", "/v2/assets/BRK.B"),
    ]
    assert seen[0].headers["APCA-API-KEY-ID"] == "key"
    assert check.name == "alpaca_assets"


def test_alpaca_assets_inactive_unknown_untradable_and_invalid_symbols():
    inactive = AlpacaAssets(client(lambda r: httpx.Response(200, text=fixture("alpaca/asset_inactive.json"))), "k", "s")
    assert inactive.check_ticker("ZZZZ") == TickerStatus("ZZZZ", False, "INACTIVE")

    untradable_body = fixture("alpaca/asset_active.json").replace('"tradable": true', '"tradable": false')
    untradable = AlpacaAssets(client(lambda r: httpx.Response(200, text=untradable_body)), "k", "s")
    assert untradable.check_ticker("AAPL") == TickerStatus("AAPL", False, "NOT_TRADABLE")

    crypto_body = fixture("alpaca/asset_active.json").replace('"us_equity"', '"crypto"')
    crypto = AlpacaAssets(client(lambda r: httpx.Response(200, text=crypto_body)), "k", "s")
    assert crypto.check_ticker("AAPL") == TickerStatus("AAPL", False, "NOT_TRADABLE")

    missing = AlpacaAssets(client(lambda r: httpx.Response(404, text='{"message": "not found"}')), "k", "s")
    assert missing.check_ticker("NOPE") == TickerStatus("NOPE", False, "UNKNOWN_ASSET")

    calls = []
    guarded = AlpacaAssets(client(lambda r: calls.append(r) or httpx.Response(200)), "k", "s")
    for symbol in ("../v2/orders", "aapl", "", "TOOLONGSYMBOL1"):
        assert guarded.check_ticker(symbol) == TickerStatus(symbol, False, "INVALID_SYMBOL")
    assert calls == []


def test_alpaca_assets_unavailable_or_malformed_raise():
    down = AlpacaAssets(client(lambda r: httpx.Response(503)), "k", "s", sleep=lambda s: None)
    with pytest.raises(SourceUnavailable):
        down.check_ticker("AAPL")
    malformed = AlpacaAssets(client(lambda r: httpx.Response(200, text='{"status": "active"}')), "k", "s")
    with pytest.raises(SourceDataError, match="malformed Alpaca asset"):
        malformed.check_ticker("AAPL")
    other_asset = AlpacaAssets(client(lambda r: httpx.Response(200, text=fixture("alpaca/asset_active.json"))), "k", "s")
    with pytest.raises(SourceDataError, match="Alpaca asset symbol mismatch for MSFT"):
        other_asset.check_ticker("MSFT")
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/marketdata/test_http_sources.py -q`
Expected: FAIL com `ImportError: cannot import name 'AlpacaAssets'`. O teste de manifesto de fixtures continua passando, porque as linhas já foram acrescentadas.

- [ ] **Step 4: Write minimal implementation**

`sources.py`, ao final:

```python
@dataclass(frozen=True)
class TickerStatus:
    ticker: str
    tradable: bool
    reason: str | None = None


class TickerCheck(Protocol):
    """Spec 3.8: ticker active and tradable. Unavailability raises SourceUnavailable/SourceDataError."""

    name: str

    def check_ticker(self, ticker: str) -> TickerStatus: ...
```

`http.py`: acrescente abaixo de `RETRYABLE_STATUS`:

```python
class ResourceNotFound(SourceUnavailable):
    """HTTP 404: the provider answered that the requested resource does not exist."""
```

Em `get_json`, antes de `if response.status_code not in RETRYABLE_STATUS:`:

```python
            if response.status_code == 404:
                raise ResourceNotFound(f"{url} returned HTTP 404")
```

`alpaca.py`:
- acrescente `import re`;
- acrescente `from virtual_orders.marketdata.http import ResourceNotFound, get_json, vendor_decimal`;
- acrescente `TickerStatus` ao import de `sources`;
- abaixo de `MAX_PAGES`, acrescente:

```python
ALPACA_TRADING_URL = "https://paper-api.alpaca.markets"
_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
```

Ao final do arquivo:

```python
class AlpacaAssets(_AlpacaClient):
    """Read-only Trading API asset lookup (spec 3.8, D14). Never calls account or order endpoints."""

    name = "alpaca_assets"

    def __init__(
        self,
        client: httpx.Client,
        key_id: str,
        secret_key: str,
        *,
        base_url: str = ALPACA_TRADING_URL,
        attempts: int = 3,
        backoff_seconds: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        super().__init__(client, key_id, secret_key, base_url=base_url, attempts=attempts,
                         backoff_seconds=backoff_seconds, sleep=sleep)

    def check_ticker(self, ticker: str) -> TickerStatus:
        if not _SYMBOL.fullmatch(ticker):
            return TickerStatus(ticker, False, "INVALID_SYMBOL")
        try:
            body = get_json(
                self._client, f"{self._base_url}/v2/assets/{ticker}", params={}, headers=self._headers,
                attempts=self._attempts, backoff_seconds=self._backoff, sleep=self._sleep,
            )
        except ResourceNotFound:
            return TickerStatus(ticker, False, "UNKNOWN_ASSET")
        if not isinstance(body, dict):
            raise SourceDataError(f"malformed Alpaca asset for {ticker}")
        symbol, status = body.get("symbol"), body.get("status")
        tradable, asset_class = body.get("tradable"), body.get("class")
        if not (isinstance(symbol, str) and isinstance(status, str) and isinstance(tradable, bool)
                and isinstance(asset_class, str)):
            raise SourceDataError(f"malformed Alpaca asset for {ticker}")
        if symbol != ticker:
            raise SourceDataError(f"Alpaca asset symbol mismatch for {ticker}")
        if status != "active":
            return TickerStatus(ticker, False, "INACTIVE")
        if asset_class != "us_equity" or not tradable:
            return TickerStatus(ticker, False, "NOT_TRADABLE")
        return TickerStatus(ticker, True)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/marketdata tests/test_import_boundaries.py -q && uv run ruff check src tests migrations && uv run mypy`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/virtual_orders/marketdata/sources.py src/virtual_orders/marketdata/http.py src/virtual_orders/marketdata/alpaca.py tests/fixtures tests/marketdata/test_http_sources.py
git commit -m "feat(marketdata): read-only Alpaca asset check behind a neutral TickerCheck"
```

---

### Task 10: Contêiner de serviços e raiz de composição (entrada 1)

**Files:**
- Create: `src/virtual_orders/services.py`, `src/virtual_orders/bootstrap.py`, `tests/test_bootstrap.py`

**Interfaces:**
- Consumes:
  - `Settings`, `ConfigError` (Task 8); nos testes, também `DEFAULT_PRICE_SOURCE` (Task 8);
  - `AlpacaAssets` (Task 9);
  - do Plano 2: `AlpacaBars`, `AlpacaSplits`, `ALPACA_IEX_SOURCE`, `FmpDividends`, `YFinanceSource`, `MarketDataGateway`, `make_engine`.
- Produces:
  - `@dataclass(frozen=True) class Services`, com os campos:
    - `engine: Engine`, `gateway: MarketDataGateway`, `price_source: str`, `fill_config: FillConfig`, `code_version: str`;
    - `ticker_check: TickerCheck`, `dividend_primary: DividendSource`, `dividend_secondary: DividendSource`, `split_source: SplitSource`, `reference: ReferenceSource`;
    - `api_key: str = field(repr=False)`;
    - `eval_interval_minutes: int`, `bootstrap_resamples: int`, `bootstrap_seed: int`;
    - `clock: Callable[[], datetime]`, `close: Callable[[], None]`.
  - `HTTP_TIMEOUT_SECONDS = 10.0`.
  - `build_services(settings: Settings, *, http_client: httpx.Client | None = None, engine: Engine | None = None, clock: Callable[[], datetime] | None = None) -> Services`.

  A API (Task 11) só importa `virtual_orders.services`, e os testes de API constroem `Services` com fakes.

  `dividend_primary`, `dividend_secondary`, `split_source` e `reference` não são usados por rota nenhuma do 3A: são as dependências que o worker do 3B consome (`run_opening`, `run_end_of_day`). Ficam no contêiner agora para que a composição seja uma só; a nota de encerramento (Task 16) registra isso, para que a revisão do 3A não os trate como código morto.

- [ ] **Step 1: Write the failing test**

`tests/test_bootstrap.py`:

```python
from dataclasses import replace
from datetime import UTC, datetime

import httpx
import pytest

from core.domain.models import FillConfig
from tests.config_support import BASE_ENV
from virtual_orders.bootstrap import build_services
from virtual_orders.config import DEFAULT_PRICE_SOURCE, ConfigError, load_settings
from virtual_orders.marketdata.alpaca import ALPACA_IEX_SOURCE, AlpacaAssets, AlpacaBars, AlpacaSplits
from virtual_orders.marketdata.fmp import FmpDividends
from virtual_orders.marketdata.gateway import UnknownDataSource
from virtual_orders.marketdata.yfinance_source import YFinanceSource


def settings(**overrides):
    return replace(load_settings(BASE_ENV), **overrides)


def offline_client(calls):
    return httpx.Client(transport=httpx.MockTransport(lambda request: calls.append(request) or httpx.Response(500)))


def test_composition_pins_the_configured_price_source_without_fallback():
    calls = []
    services = build_services(settings(), http_client=offline_client(calls))
    try:
        assert services.gateway.source_ids == (ALPACA_IEX_SOURCE,)
        assert services.price_source == ALPACA_IEX_SOURCE == DEFAULT_PRICE_SOURCE
        assert isinstance(services.gateway.bar_source(ALPACA_IEX_SOURCE), AlpacaBars)
        with pytest.raises(UnknownDataSource):
            services.gateway.bar_source("yfinance")
        assert isinstance(services.ticker_check, AlpacaAssets)
        assert isinstance(services.dividend_primary, FmpDividends)
        assert isinstance(services.dividend_secondary, YFinanceSource)
        assert isinstance(services.split_source, AlpacaSplits)
        assert isinstance(services.reference, YFinanceSource)
        assert services.fill_config == FillConfig() and services.code_version == "unknown"
        assert services.clock().tzinfo is UTC
    finally:
        services.close()
    assert calls == []


def test_unknown_price_source_fails_at_startup():
    with pytest.raises(ConfigError, match="UNKNOWN_PRICE_SOURCE:PRICE_SOURCE"):
        build_services(settings(price_source="fake_feed"), http_client=offline_client([]))


def test_injected_client_and_clock_are_used_and_the_client_is_left_open():
    client = offline_client([])
    fixed = datetime(2025, 11, 25, 15, 0, tzinfo=UTC)
    services = build_services(settings(), http_client=client, clock=lambda: fixed)
    services.close()
    assert services.clock() == fixed
    assert not client.is_closed
    client.close()


def test_services_repr_hides_the_api_key():
    services = build_services(settings(api_key="very-secret"), http_client=offline_client([]))
    try:
        assert "very-secret" not in repr(services)
    finally:
        services.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_bootstrap.py -q`
Expected: FAIL com `ModuleNotFoundError: virtual_orders.bootstrap`.

- [ ] **Step 3: Write minimal implementation**

`src/virtual_orders/services.py`:

```python
"""Dependencies the API (and later the worker) run on. Built only by `virtual_orders.bootstrap` or by tests."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import Engine

from core.domain.models import FillConfig
from virtual_orders.marketdata.gateway import MarketDataGateway
from virtual_orders.marketdata.sources import DividendSource, ReferenceSource, SplitSource, TickerCheck


@dataclass(frozen=True)
class Services:
    engine: Engine
    gateway: MarketDataGateway
    price_source: str
    fill_config: FillConfig
    code_version: str
    ticker_check: TickerCheck
    dividend_primary: DividendSource
    dividend_secondary: DividendSource
    split_source: SplitSource
    reference: ReferenceSource
    api_key: str = field(repr=False)
    eval_interval_minutes: int
    bootstrap_resamples: int
    bootstrap_seed: int
    clock: Callable[[], datetime] = field(repr=False)
    close: Callable[[], None] = field(repr=False)
```

Todos os campos são obrigatórios: não há relógio nem `close` implícitos. `field(repr=False)` sem `default` mantém a ordem válida para o dataclass.

`src/virtual_orders/bootstrap.py`:

```python
"""Composition root (Plan 2 close-out entry 1): the only module besides the adapters that imports them."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import httpx
from sqlalchemy import Engine

from virtual_orders.config import ConfigError, Settings
from virtual_orders.marketdata.alpaca import AlpacaAssets, AlpacaBars, AlpacaSplits
from virtual_orders.marketdata.fmp import FmpDividends
from virtual_orders.marketdata.gateway import MarketDataGateway
from virtual_orders.marketdata.yfinance_source import YFinanceSource
from virtual_orders.services import Services
from virtual_orders.storage.database import make_engine

HTTP_TIMEOUT_SECONDS = 10.0


def _utc_now() -> datetime:
    return datetime.now(UTC)


def build_services(
    settings: Settings,
    *,
    http_client: httpx.Client | None = None,
    engine: Engine | None = None,
    clock: Callable[[], datetime] | None = None,
) -> Services:
    owns_client = http_client is None
    client = http_client if http_client is not None else httpx.Client(timeout=HTTP_TIMEOUT_SECONDS)
    gateway = MarketDataGateway([AlpacaBars(client, settings.alpaca_api_key, settings.alpaca_secret_key)])
    if settings.price_source not in gateway.source_ids:
        if owns_client:
            client.close()
        raise ConfigError([
            f"UNKNOWN_PRICE_SOURCE:PRICE_SOURCE (registered: {', '.join(gateway.source_ids)})",
        ])
    owns_engine = engine is None
    database = engine if engine is not None else make_engine(settings.database_url)

    def close() -> None:
        if owns_client:
            client.close()
        if owns_engine:
            database.dispose()

    yfinance = YFinanceSource()
    return Services(
        engine=database,
        gateway=gateway,
        price_source=settings.price_source,
        fill_config=settings.fill_config,
        code_version=settings.code_version,
        ticker_check=AlpacaAssets(client, settings.alpaca_api_key, settings.alpaca_secret_key,
                                  base_url=settings.alpaca_trading_url),
        dividend_primary=FmpDividends(client, settings.fmp_api_key),
        dividend_secondary=yfinance,
        split_source=AlpacaSplits(client, settings.alpaca_api_key, settings.alpaca_secret_key),
        reference=yfinance,
        api_key=settings.api_key,
        eval_interval_minutes=settings.eval_interval_minutes,
        bootstrap_resamples=settings.bootstrap_resamples,
        bootstrap_seed=settings.bootstrap_seed,
        clock=clock or _utc_now,
        close=close,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_bootstrap.py tests/test_import_boundaries.py -q && uv run ruff check src tests migrations && uv run mypy`
Expected: PASS. `bootstrap.py` é o único importador de adapters, e `services.py` passa na fronteira.

- [ ] **Step 5: Commit**

```bash
git add src/virtual_orders/services.py src/virtual_orders/bootstrap.py tests/test_bootstrap.py
git commit -m "feat(bootstrap): composition root builds the gateway and services from settings"
```

---

### Task 11: App FastAPI, autenticação, erros e rotas de sinais (entrada 2, spec 3.3, 3.8, 5.1)

**Files:**
- Create:
  - `src/virtual_orders/api/app.py`, `auth.py`, `deps.py`, `encoding.py`, `errors.py`, `intake.py`
  - `src/virtual_orders/api/routes/__init__.py`, `src/virtual_orders/api/routes/signals.py`
  - `src/virtual_orders/readmodels/signals.py`
  - `tests/integration/api/__init__.py`, `tests/integration/api/conftest.py`, `tests/integration/api/test_signals_api.py`
  - `tests/test_api_errors.py`
- Modify: `src/virtual_orders/bootstrap.py` (`app_from_environment`), `tests/test_bootstrap.py`

**Interfaces:**
- Consumes:
  - `Services` (Task 10); `ENV_VARIABLES` (Task 8);
  - `submit_signal`, `parse_signal_body`, `SignalValidationError`, `IdempotencyConflict`, `DECIMAL_FIELDS`, `OPTIONAL_DECIMAL_FIELDS`;
  - `find_signal_by_client_id`;
  - `TickerCheck`, `SourceUnavailable`, `SourceDataError`;
  - `ManualOrderError`, `ACTIONABILITY_UNVERIFIABLE`;
  - `OrderAlreadyFinal`, `ReplayOrderReadOnly` (Task 4);
  - `ReplaySelectionError` (Task 5);
  - `SignalNotFound`, `OrderNotFound`, `LedgerIntegrityError`.
- Produces (Tasks 12–15 acrescentam routers com o mesmo padrão):
  - `create_app(services: Services) -> FastAPI` (inclui cada router em `app.py`);
  - `ServicesDep = Annotated[Services, Depends(get_services)]`;
  - `ApiError(status_code, code, reason=None, detail=None)`;
  - `to_api_error(exc: Exception, method: str) -> ApiError`, que cobre as exceções de domínio, `HTTPException` do Starlette (404/405) e o fallback `500 INTERNAL_ERROR`;
  - `json_response(content, status_code=200)` e `to_json_value(value)`;
  - `async read_json_object(request) -> dict[str, Any]`;
  - `canonical_signal_body(body) -> dict[str, Any]` (D19: `int` dos campos decimais vira `Decimal`);
  - `intake_signal(services, body) -> SignalSubmission`;
  - `list_signals(conn, *, day, strategy, limit, offset) -> list[dict[str, Any]]`;
  - `bootstrap.app_from_environment() -> FastAPI`;
  - fixtures de teste `api` (`ApiHarness(client, services, bars, tickers, clock)`), `json_text(body)`, `post_json(client, path, body)`, `MutableClock`, `FakeTickerCheck`, `API_KEY`.

- [ ] **Step 1: Write the test harness**

`tests/integration/api/__init__.py`: arquivo vazio.

`tests/integration/api/conftest.py`:

```python
"""API harness: the real app over real Postgres with fake providers and a controllable clock. No network."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from core.domain.models import FillConfig
from tests.integration.support import (
    CODE_VERSION,
    PRICE_SOURCE,
    SIGNAL_CREATED_AT,
    FakeBarSource,
    FakeDividends,
    FakeReference,
    FakeSplits,
    feeds,
)
from virtual_orders.api.app import create_app
from virtual_orders.marketdata.sources import SourceUnavailable, TickerStatus
from virtual_orders.services import Services

API_KEY = "test-api-key"


class MutableClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def set(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class FakeTickerCheck:
    name = "fake_assets"

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.untradable: dict[str, str] = {}
        self.failing = False

    def check_ticker(self, ticker: str) -> TickerStatus:
        self.calls.append(ticker)
        if self.failing:
            raise SourceUnavailable("assets unavailable (fake)")
        reason = self.untradable.get(ticker)
        return TickerStatus(ticker, reason is None, reason)


@dataclass
class ApiHarness:
    client: TestClient
    services: Services
    bars: FakeBarSource
    tickers: FakeTickerCheck
    clock: MutableClock


def json_text(body: Any) -> str:
    """JSON with Decimals written as exact number tokens (never via float)."""
    numbers: dict[str, str] = {}

    def default(value: Any) -> str:
        if isinstance(value, Decimal):
            marker = f"__decimal_{len(numbers)}__"
            numbers[marker] = format(value, "f")
            return marker
        raise TypeError(f"not JSON serializable: {type(value).__name__}")

    text = json.dumps(body, default=default)
    for marker, number in numbers.items():
        text = text.replace(f'"{marker}"', number)
    return text


def post_json(client: TestClient, path: str, body: Any):
    return client.post(path, content=json_text(body), headers={"Content-Type": "application/json"})


@pytest.fixture
def api(engine) -> Iterator[ApiHarness]:
    bars = FakeBarSource()
    tickers = FakeTickerCheck()
    clock = MutableClock(SIGNAL_CREATED_AT)
    services = Services(
        engine=engine, gateway=feeds(bars), price_source=PRICE_SOURCE, fill_config=FillConfig(),
        code_version=CODE_VERSION, ticker_check=tickers, dividend_primary=FakeDividends("fmp"),
        dividend_secondary=FakeDividends("yfinance"), split_source=FakeSplits(), reference=FakeReference(),
        api_key=API_KEY, eval_interval_minutes=2, bootstrap_resamples=200, bootstrap_seed=42,
        clock=clock, close=lambda: None,
    )
    with TestClient(create_app(services), headers={"X-API-Key": API_KEY}) as client:
        yield ApiHarness(client, services, bars, tickers, clock)
```

- [ ] **Step 2: Write the failing tests**

`tests/integration/api/test_signals_api.py`:

```python
from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import select

from tests.integration.api.conftest import API_KEY, post_json
from tests.integration.support import count, signal_body, submit_default
from virtual_orders.api.app import create_app
from virtual_orders.storage import tables


def test_every_route_rejects_a_missing_or_wrong_key(api):
    anonymous = TestClient(api.client.app)
    routes = [route for route in api.client.app.routes if isinstance(route, APIRoute)]
    assert routes
    for route in routes:
        path = route.path.replace("{signal_id}", str(uuid4())).replace("{order_id}", str(uuid4()))
        for method in route.methods:
            for headers in ({}, {"X-API-Key": "wrong"}):
                response = anonymous.request(method, path, headers=headers)
                assert response.status_code == 401, (method, path)
                assert response.json() == {"error": {"code": "UNAUTHORIZED", "reason": None, "detail": {}}}


def test_docs_unknown_routes_and_wrong_methods_use_the_error_envelope(api):
    for path in ("/docs", "/redoc", "/openapi.json", "/no-such-route"):
        response = api.client.get(path)
        assert response.status_code == 404, path
        assert response.json() == {"error": {"code": "NOT_FOUND", "reason": None, "detail": {}}}
    wrong = api.client.put("/signals")
    assert wrong.status_code == 405
    assert wrong.json() == {"error": {"code": "METHOD_NOT_ALLOWED", "reason": None, "detail": {}}}


def test_unhandled_errors_are_500_without_the_message(api):
    class ExplodingCheck:
        name = "exploding"

        def check_ticker(self, ticker):
            raise RuntimeError("internal-detail-that-must-not-leak")

    services = replace(api.services, ticker_check=ExplodingCheck())
    with TestClient(create_app(services), headers={"X-API-Key": API_KEY}, raise_server_exceptions=False) as client:
        response = post_json(client, "/signals", signal_body())
    assert response.status_code == 500
    assert response.json() == {"error": {"code": "INTERNAL_ERROR", "reason": None, "detail": {"type": "RuntimeError"}}}
    assert "internal-detail-that-must-not-leak" not in response.text


def test_signal_is_created_then_returned_as_existing_without_a_second_provider_call(api):
    first = post_json(api.client, "/signals", signal_body())
    assert first.status_code == 201
    created = first.json()
    assert created["status"] == "CREATED" and created["auto_order_id"] is not None

    again = post_json(api.client, "/signals", signal_body())
    assert again.status_code == 200
    assert again.json() == {**created, "status": "EXISTING"}
    assert api.tickers.calls == ["AAPL"]


def test_conflicting_payload_is_409(api):
    post_json(api.client, "/signals", signal_body())
    response = post_json(api.client, "/signals", signal_body(stop=Decimal("96")))
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "IDEMPOTENCY_CONFLICT"
    assert error["detail"]["client_signal_id"] == "rex-2025-11-25-aapl"


def test_invalid_signal_is_422_and_never_calls_the_provider(api):
    response = post_json(api.client, "/signals", signal_body(stop=Decimal("101")))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "SIGNAL_VALIDATION_FAILED"
    assert response.json()["error"]["detail"]["errors"]
    assert api.tickers.calls == []
    assert count(api.services.engine, "signals") == 0


def test_untradable_ticker_is_422_and_nothing_is_stored(api):
    api.tickers.untradable["ZZZZ"] = "INACTIVE"
    response = post_json(api.client, "/signals", signal_body(ticker="ZZZZ"))
    assert response.status_code == 422
    assert response.json()["error"] == {
        "code": "SIGNAL_VALIDATION_FAILED", "reason": None, "detail": {"errors": ["TICKER_NOT_TRADABLE:INACTIVE"]},
    }
    assert count(api.services.engine, "signals") == 0


def test_ticker_check_unavailable_is_503_and_nothing_is_stored(api):
    api.tickers.failing = True
    response = post_json(api.client, "/signals", signal_body())
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "TICKER_UNVERIFIABLE"
    assert count(api.services.engine, "signals") == 0


def test_body_numbers_are_read_as_decimal_never_float(api):
    response = post_json(api.client, "/signals", signal_body(entry_zone_low=Decimal("100.10000000000000001")))
    assert response.status_code == 201
    with api.services.engine.connect() as conn:
        stored = conn.execute(select(tables.signals.c.entry_zone_low)).scalar_one()
    assert stored == Decimal("100.10000000000000001")


def test_integer_and_decimal_spellings_of_a_price_are_the_same_submission(api):
    first = post_json(api.client, "/signals", signal_body(entry_zone_low=Decimal("100")))  # JSON token 100
    assert first.status_code == 201
    retry = post_json(api.client, "/signals", signal_body(entry_zone_low=Decimal("100.0"), stop=Decimal("97.00")))
    assert retry.status_code == 200 and retry.json()["status"] == "EXISTING"

    direct = submit_default(api.services.engine, client_signal_id="direct")  # service call with Decimal values
    via_api = post_json(api.client, "/signals", signal_body(client_signal_id="direct"))
    assert via_api.status_code == 200 and via_api.json()["signal_id"] == str(direct.signal_id)


def test_malformed_json_is_422(api):
    for raw in ("not json", "[1, 2]", '{"stop": NaN}'):
        response = api.client.post("/signals", content=raw, headers={"Content-Type": "application/json"})
        assert response.status_code == 422, raw
        assert response.json()["error"]["code"] == "INVALID_JSON"


def test_list_signals_filters_by_market_date_and_strategy(api):
    post_json(api.client, "/signals", signal_body())
    post_json(api.client, "/signals", signal_body(client_signal_id="other", strategy="OTHER", ticker="MSFT"))

    listed = api.client.get("/signals", params={"date": "2025-11-25", "strategy": "REXSHARE"})
    assert listed.status_code == 200
    signals = listed.json()["signals"]
    assert [s["client_signal_id"] for s in signals] == ["rex-2025-11-25-aapl"]
    assert signals[0]["entry_zone_low"] == "100" and signals[0]["auto_order_status"] == "PENDING"
    assert len(api.client.get("/signals").json()["signals"]) == 2
    assert api.client.get("/signals", params={"date": "2025-11-24"}).json() == {"signals": []}
    assert api.client.get("/signals", params={"limit": 0}).json()["error"]["code"] == "REQUEST_INVALID"
```

Em `tests/test_bootstrap.py`, troque o import de configuração por `from virtual_orders.config import DEFAULT_PRICE_SOURCE, ENV_VARIABLES, ConfigError, load_settings` e acrescente:

```python
def test_app_factory_builds_from_the_environment(monkeypatch):
    from fastapi import FastAPI

    from virtual_orders.bootstrap import app_from_environment

    for name in ENV_VARIABLES:  # the host shell must neither break nor silently change this test
        monkeypatch.delenv(name, raising=False)
    for name, value in BASE_ENV.items():
        monkeypatch.setenv(name, value)
    app = app_from_environment()
    assert isinstance(app, FastAPI)
    app.state.services.close()
```

`tests/test_api_errors.py` (sem banco):

```python
from virtual_orders.api.errors import to_api_error
from virtual_orders.ledger import errors
from virtual_orders.ledger.errors import LedgerIntegrityError


def test_integrity_errors_are_409_on_commands_and_500_on_reads():
    exc = LedgerIntegrityError(errors.STORED_HASH_MISMATCH, "stored hash differs", order_id=None)
    command, read = to_api_error(exc, "POST"), to_api_error(exc, "GET")
    assert (command.status_code, command.code, command.reason) == (409, "INTEGRITY_ERROR", errors.STORED_HASH_MISMATCH)
    assert (read.status_code, read.code, read.reason) == (500, "INTEGRITY_ERROR", errors.STORED_HASH_MISMATCH)


def test_unmapped_exceptions_become_internal_error_without_the_message():
    error = to_api_error(RuntimeError("secret-ish detail"), "POST")
    assert (error.status_code, error.code, error.reason, error.detail) == (
        500, "INTERNAL_ERROR", None, {"type": "RuntimeError"})
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/integration/api/test_signals_api.py tests/test_bootstrap.py tests/test_api_errors.py -q`
Expected: FAIL com `ModuleNotFoundError: virtual_orders.api.app` (e `virtual_orders.api.errors` em `test_api_errors.py`).

- [ ] **Step 4: Write encoding, errors, auth and deps**

`src/virtual_orders/api/encoding.py`:

```python
"""JSON in with Decimal (never float); JSON out with Decimal as normalized string and UTC ISO datetimes."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from fastapi import Request, Response

from core.domain.hashing import CANONICAL_CONTEXT
from virtual_orders.api.errors import ApiError

JSON_MEDIA_TYPE = "application/json"


def to_json_value(value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, Enum):
        return to_json_value(value.value)
    if isinstance(value, str | int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Decimal):
        if not value.is_finite():
            return None
        return "0" if value == 0 else format(value.normalize(CANONICAL_CONTEXT), "f")
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("naive datetime in API response")
        return value.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: to_json_value(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): to_json_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [to_json_value(item) for item in value]
    raise TypeError(f"unsupported type in API response: {type(value).__name__}")


def json_response(content: Any, status_code: int = 200) -> Response:
    body = json.dumps(to_json_value(content), ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return Response(body, status_code=status_code, media_type=JSON_MEDIA_TYPE)


def _reject_constant(name: str) -> Any:
    raise ValueError(f"non-finite number {name} is not allowed")


async def read_json_object(request: Request) -> dict[str, Any]:
    raw = await request.body()
    try:
        body = json.loads(raw, parse_float=Decimal, parse_constant=_reject_constant)
    except ValueError as exc:
        raise ApiError(422, "INVALID_JSON", detail={"message": str(exc)}) from exc
    if not isinstance(body, dict):
        raise ApiError(422, "INVALID_JSON", detail={"message": "body must be a JSON object"})
    return body
```

`src/virtual_orders/api/errors.py`:

```python
"""Error envelope and the mapping of domain exceptions to HTTP (spec 5.1, 6; D19)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from virtual_orders.evaluator.commands import OrderAlreadyFinal, ReplayOrderReadOnly
from virtual_orders.evaluator.manual import ACTIONABILITY_UNVERIFIABLE, ManualOrderError
from virtual_orders.evaluator.replay import ReplaySelectionError
from virtual_orders.evaluator.signals import IdempotencyConflict, SignalValidationError
from virtual_orders.ledger.errors import LedgerIntegrityError, OrderNotFound, SignalNotFound


class ApiError(Exception):
    def __init__(
        self, status_code: int, code: str, reason: str | None = None, detail: Mapping[str, Any] | None = None
    ) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.reason = reason
        self.detail: dict[str, Any] = dict(detail or {})


HTTP_ERROR_CODES = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}


def to_api_error(exc: Exception, method: str) -> ApiError:
    if isinstance(exc, ApiError):
        return exc
    if isinstance(exc, SignalValidationError):
        return ApiError(422, "SIGNAL_VALIDATION_FAILED", detail={"errors": exc.errors})
    if isinstance(exc, IdempotencyConflict):
        return ApiError(409, "IDEMPOTENCY_CONFLICT", detail={
            "client_signal_id": exc.client_signal_id, "existing_signal_id": exc.existing_signal_id,
        })
    if isinstance(exc, ManualOrderError):
        status = 503 if exc.code == ACTIONABILITY_UNVERIFIABLE else 422
        return ApiError(status, exc.code, exc.reason, exc.detail)
    if isinstance(exc, SignalNotFound):
        return ApiError(404, "SIGNAL_NOT_FOUND")
    if isinstance(exc, OrderNotFound):
        return ApiError(404, "ORDER_NOT_FOUND")
    if isinstance(exc, OrderAlreadyFinal):
        return ApiError(409, "ORDER_ALREADY_FINAL", detail={"order_id": exc.order_id})
    if isinstance(exc, ReplayOrderReadOnly):
        return ApiError(409, "REPLAY_ORDER_READ_ONLY", detail={"order_id": exc.order_id})
    if isinstance(exc, ReplaySelectionError):
        return ApiError(422, "REPLAY_REQUEST_INVALID", detail={"errors": [str(exc)]})
    if isinstance(exc, LedgerIntegrityError):
        # A command meets ledger state that forbids the write (409); a read meets corrupted stored data (500).
        status = 500 if method == "GET" else 409
        return ApiError(status, "INTEGRITY_ERROR", exc.kind, {"order_id": exc.order_id})
    if isinstance(exc, RequestValidationError):
        return ApiError(422, "REQUEST_INVALID", detail={"errors": [
            {"loc": [str(part) for part in error.get("loc", ())], "msg": str(error.get("msg", ""))}
            for error in exc.errors()
        ]})
    if isinstance(exc, StarletteHTTPException):
        return ApiError(exc.status_code, HTTP_ERROR_CODES.get(exc.status_code, f"HTTP_{exc.status_code}"))
    return ApiError(500, "INTERNAL_ERROR", detail={"type": type(exc).__name__})  # never the message


HANDLED = (
    ApiError, SignalValidationError, IdempotencyConflict, ManualOrderError, SignalNotFound, OrderNotFound,
    OrderAlreadyFinal, ReplayOrderReadOnly, ReplaySelectionError, LedgerIntegrityError, RequestValidationError,
    StarletteHTTPException, Exception,
)


def install_error_handlers(app: FastAPI) -> None:
    from virtual_orders.api.encoding import json_response  # encoding imports ApiError from this module

    async def handle(request: Request, exc: Exception) -> Response:
        error = to_api_error(exc, request.method)
        response = json_response({"error": {"code": error.code, "reason": error.reason, "detail": error.detail}},
                                 error.status_code)
        if isinstance(exc, StarletteHTTPException) and exc.headers:
            response.headers.update(exc.headers)  # e.g. Allow on 405
        return response

    for exception_type in HANDLED:
        app.add_exception_handler(exception_type, handle)
```

`Exception` é registrado por último: o Starlette escolhe o handler pela classe mais específica na MRO, e o de `Exception` vai para o `ServerErrorMiddleware`. Esse middleware devolve a resposta do handler e re-levanta a exceção para o log do servidor; por isso o teste de 500 usa `raise_server_exceptions=False`.

`src/virtual_orders/api/auth.py`:

```python
"""Single API key via `X-API-Key` (spec 5.1), compared in constant time."""

from __future__ import annotations

import hmac
from collections.abc import Callable

from fastapi import Request

from virtual_orders.api.errors import ApiError

API_KEY_HEADER = "X-API-Key"


def require_api_key(expected: str) -> Callable[[Request], None]:
    expected_bytes = expected.encode("utf-8")

    def dependency(request: Request) -> None:
        provided = request.headers.get(API_KEY_HEADER)
        if provided is None or not hmac.compare_digest(provided.encode("utf-8"), expected_bytes):
            raise ApiError(401, "UNAUTHORIZED")

    return dependency
```

`src/virtual_orders/api/deps.py`:

```python
from __future__ import annotations

from typing import Annotated, cast

from fastapi import Depends, Request

from virtual_orders.services import Services


def get_services(request: Request) -> Services:
    return cast(Services, request.app.state.services)


ServicesDep = Annotated[Services, Depends(get_services)]
```

- [ ] **Step 5: Write intake, read model, route and app**

`src/virtual_orders/api/intake.py`:

```python
"""POST /signals intake: validation, tradable-ticker gate (D14), then the idempotent service (spec 3.3)."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from virtual_orders.api.errors import ApiError
from virtual_orders.evaluator.signals import (
    DECIMAL_FIELDS,
    OPTIONAL_DECIMAL_FIELDS,
    SignalSubmission,
    SignalValidationError,
    parse_signal_body,
    submit_signal,
)
from virtual_orders.ledger.orders import find_signal_by_client_id
from virtual_orders.marketdata.sources import SourceDataError, SourceUnavailable
from virtual_orders.services import Services

_DECIMAL_NAMES = frozenset(DECIMAL_FIELDS + OPTIONAL_DECIMAL_FIELDS)


def canonical_signal_body(body: Mapping[str, Any]) -> dict[str, Any]:
    """D19: `100` and `100.0` are one price. JSON integers in decimal fields become Decimal before hashing,
    so the Plan 2 canonical hash normalizes both to the same string (spec 3.3, "decimais normalizados")."""
    return {
        name: Decimal(value) if name in _DECIMAL_NAMES and isinstance(value, int) and not isinstance(value, bool)
        else value
        for name, value in body.items()
    }


def intake_signal(services: Services, raw_body: Mapping[str, Any]) -> SignalSubmission:
    body = canonical_signal_body(raw_body)
    client_id = body.get("client_signal_id")
    known = False
    if isinstance(client_id, str):
        with services.engine.connect() as conn:
            known = find_signal_by_client_id(conn, client_id) is not None
    if not known:
        parsed = parse_signal_body(body)  # an invalid signal never reaches the provider
        ticker = parsed.spec.ticker
        try:
            status = services.ticker_check.check_ticker(ticker)
        except (SourceUnavailable, SourceDataError) as exc:
            raise ApiError(503, "TICKER_UNVERIFIABLE", detail={"ticker": ticker, "check": services.ticker_check.name,
                                                               "message": str(exc)}) from exc
        if not status.tradable:
            raise SignalValidationError([f"TICKER_NOT_TRADABLE:{status.reason}"])
    return submit_signal(services.engine, body, config=services.fill_config, code_version=services.code_version,
                         price_source=services.price_source, now=services.clock())
```

`src/virtual_orders/readmodels/signals.py`:

```python
"""GET /signals read model (D19: `date` is the America/New_York date of created_at)."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import Connection, and_, func, select

from virtual_orders.storage.tables import order_state, orders, signals

MARKET_TIMEZONE = "America/New_York"


def list_signals(
    conn: Connection, *, day: date | None, strategy: str | None, limit: int, offset: int
) -> list[dict[str, Any]]:
    auto = orders.alias("auto_order")
    query = (
        select(
            signals.c.id.label("signal_id"), signals.c.client_signal_id, signals.c.created_at, signals.c.strategy,
            signals.c.strategy_version, signals.c.source, signals.c.ticker, signals.c.direction,
            signals.c.entry_zone_low, signals.c.entry_zone_high, signals.c.trigger_price, signals.c.stop,
            signals.c.target1, signals.c.target2, signals.c.valid_sessions, signals.c.evaluation_start_ts,
            signals.c.valid_until_ts, signals.c.score, auto.c.id.label("auto_order_id"),
            order_state.c.status.label("auto_order_status"),
        )
        .select_from(
            signals.outerjoin(auto, and_(auto.c.signal_id == signals.c.id, auto.c.origin == "AUTO_STRATEGY",
                                         auto.c.replay.is_(False)))
            .outerjoin(order_state, order_state.c.order_id == auto.c.id)
        )
    )
    if day is not None:
        query = query.where(func.date(func.timezone(MARKET_TIMEZONE, signals.c.created_at)) == day)
    if strategy is not None:
        query = query.where(signals.c.strategy == strategy)
    rows = conn.execute(query.order_by(signals.c.created_at.desc(), signals.c.id).limit(limit).offset(offset))
    return [dict(row) for row in rows.mappings()]
```

`src/virtual_orders/api/routes/__init__.py`: arquivo vazio.

`src/virtual_orders/api/routes/signals.py`:

```python
from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query, Request, Response
from starlette.concurrency import run_in_threadpool

from virtual_orders.api.deps import ServicesDep
from virtual_orders.api.encoding import json_response, read_json_object
from virtual_orders.api.intake import intake_signal
from virtual_orders.readmodels.signals import list_signals

router = APIRouter()


@router.post("/signals")
async def post_signal(request: Request, services: ServicesDep) -> Response:
    body = await read_json_object(request)
    submission = await run_in_threadpool(intake_signal, services, body)
    content = {"status": submission.status, "signal_id": submission.signal_id,
               "auto_order_id": submission.auto_order_id}
    return json_response(content, 201 if submission.status == "CREATED" else 200)


@router.get("/signals")
def get_signals(
    services: ServicesDep,
    day: Annotated[date | None, Query(alias="date")] = None,
    strategy: str | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Response:
    with services.engine.connect() as conn:
        rows = list_signals(conn, day=day, strategy=strategy, limit=limit, offset=offset)
    return json_response({"signals": rows})
```

`src/virtual_orders/api/app.py`:

```python
"""FastAPI application (spec 5.1). Every route requires X-API-Key; docs endpoints are disabled (D19)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

from virtual_orders.api.auth import require_api_key
from virtual_orders.api.errors import install_error_handlers
from virtual_orders.api.routes import signals
from virtual_orders.services import Services


def create_app(services: Services) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        services.close()

    app = FastAPI(
        title="Virtual Order Engine", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan,
        dependencies=[Depends(require_api_key(services.api_key))],
    )
    app.state.services = services
    install_error_handlers(app)
    app.include_router(signals.router)
    return app
```

Em `bootstrap.py`, acrescente os imports `import os`, `from fastapi import FastAPI`, `from virtual_orders.api.app import create_app` e `from virtual_orders.config import load_settings`, e ao final do arquivo:

```python
def app_from_environment() -> FastAPI:
    """ASGI factory: `uvicorn virtual_orders.bootstrap:app_from_environment --factory`."""
    return create_app(build_services(load_settings(os.environ)))
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/integration/api tests/test_bootstrap.py tests/test_import_boundaries.py -q && uv run ruff check src tests migrations && uv run mypy`
Expected: PASS.

- [ ] **Step 7: Run the full suite (regression)**

Run: `uv run pytest`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/virtual_orders/api src/virtual_orders/readmodels/signals.py src/virtual_orders/bootstrap.py tests/integration/api tests/test_bootstrap.py tests/test_api_errors.py
git commit -m "feat(api): authenticated FastAPI app with signal intake and listing"
```

---

### Task 12: Ordens manuais, cancelamento e replay pela API (entradas 3, 4, 5)

**Files:**
- Create:
  - `src/virtual_orders/api/routes/orders.py`
  - `src/virtual_orders/api/replay_request.py`
  - `src/virtual_orders/api/routes/replay.py`
  - `tests/integration/api/test_commands_api.py`
- Modify: `src/virtual_orders/api/app.py` (include routers)

**Interfaces:**
- Consumes:
  - `create_manual_order(engine, signal_id, *, config, code_version, price_source, created_at, gateway)`
  - `cancel_order(engine, order_id, *, at, requested_by)`
  - `reproduce_orders` / `recalculate_orders` / `ReplayMode` / `ReplayReport`
  - `latest_incidents` (Task 7); `REPRODUCE_DIVERGENCE` (`virtual_orders.ledger.errors`)
  - harness `api` (Task 11)
- Produces:
  - `routes/orders.py::router` (a Task 13 acrescenta as rotas `GET` ao mesmo router);
  - dataclass `ReplayRequest`;
  - `parse_replay_request(body: Mapping[str, Any]) -> ReplayRequest`, que levanta `ApiError(422, "REPLAY_REQUEST_INVALID", detail={"errors": [...]})`;
  - `run_replay(services: Services, request: ReplayRequest) -> ReplayReport`, que levanta `ApiError(409, "REPRODUCE_DIVERGED", ...)` quando alguma ordem `REPRODUCE` diverge (D15). `ReplaySelectionError` sobe sem captura e o handler global a mapeia para 422 (Task 5 converteu as guardas de `data_as_of`).

Respostas:
- `POST /signals/{signal_id}/orders` → `201 {"order_id", "actionability_run_id", "data_as_of", "partial_bar_skipped"}`.
- `POST /orders/{order_id}/cancel` → `200 {"order_id", "event_keys"}`.
- `POST /replay` → `200 {"run_id", "mode", "created": {origem: replay}, "failures": {origem: motivo}}`.
- `POST /replay` em `REPRODUCE` com qualquer `REPRODUCE_DIVERGENCE` → `409 REPRODUCE_DIVERGED`, com `detail = {"run_id", "mode", "identical": {origem: replay}, "diverged": {origem: {"incident_id", "reason", "diff"}}, "failures": {origem: motivo}}`.

- [ ] **Step 1: Write the failing tests**

`tests/integration/api/test_commands_api.py`:

```python
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from core.domain.models import Origin
from tests.integration.api.conftest import post_json
from tests.integration.support import (
    CODE_VERSION,
    DAY,
    PRICE_SOURCE,
    TICKER,
    backdated_batch,
    count,
    raw,
    scenario_bars,
    signal_body,
)
from tests.support import et
from virtual_orders.evaluator.cycle import run_live_cycle
from virtual_orders.evaluator.replay import reproduce_orders
from virtual_orders.ledger.orders import get_order
from virtual_orders.ledger.runs import get_run, list_segments


def new_signal(api, **overrides):
    return post_json(api.client, "/signals", signal_body(**overrides)).json()


def closed_order(api) -> UUID:
    order_id = UUID(new_signal(api)["auto_order_id"])
    api.bars.load(scenario_bars())
    for hm in ("10:30", "11:30", "13:00"):
        run_live_cycle(api.services.engine, api.services.gateway, code_version=CODE_VERSION, market_now=et(DAY, hm))
    return order_id


def test_manual_order_is_created_when_actionable(api):
    signal_id = new_signal(api, auto_order=False)["signal_id"]
    api.bars.load(scenario_bars())
    api.clock.set(et(DAY, "09:50"))

    response = api.client.post(f"/signals/{signal_id}/orders")

    assert response.status_code == 201
    body = response.json()
    assert body["partial_bar_skipped"] is False and body["actionability_run_id"]
    with api.services.engine.connect() as conn:
        order = get_order(conn, UUID(body["order_id"]))
    assert order.origin is Origin.MANUAL_USER and order.price_source == PRICE_SOURCE


def test_manual_order_after_the_fill_is_422_with_reason(api):
    signal_id = new_signal(api, auto_order=False)["signal_id"]
    api.bars.load(scenario_bars())
    api.clock.set(et(DAY, "10:30"))
    response = api.client.post(f"/signals/{signal_id}/orders")
    assert response.status_code == 422
    error = response.json()["error"]
    assert (error["code"], error["reason"]) == ("SIGNAL_NO_LONGER_ACTIONABLE", "ENTRY_OPPORTUNITY_ALREADY_OCCURRED")


def test_manual_order_with_the_provider_down_is_503_and_creates_nothing(api):
    signal_id = new_signal(api, auto_order=False)["signal_id"]
    api.bars.failing.add(TICKER)
    api.clock.set(et(DAY, "09:50"))
    response = api.client.post(f"/signals/{signal_id}/orders")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "ACTIONABILITY_UNVERIFIABLE"
    assert count(api.services.engine, "orders") == 0


def test_manual_order_after_validity_is_422_expired(api):
    signal_id = new_signal(api, auto_order=False)["signal_id"]
    api.clock.set(et("2025-12-05", "10:00"))
    response = api.client.post(f"/signals/{signal_id}/orders")
    assert response.status_code == 422 and response.json()["error"]["code"] == "SIGNAL_EXPIRED"


def test_manual_order_for_unknown_or_malformed_signal(api):
    assert api.client.post(f"/signals/{uuid4()}/orders").json()["error"]["code"] == "SIGNAL_NOT_FOUND"
    malformed = api.client.post("/signals/not-a-uuid/orders")
    assert malformed.status_code == 422 and malformed.json()["error"]["code"] == "REQUEST_INVALID"


def test_cancel_then_cancel_again_is_409(api):
    order_id = new_signal(api)["auto_order_id"]
    api.clock.set(et(DAY, "09:45"))
    first = api.client.post(f"/orders/{order_id}/cancel")
    assert first.status_code == 200
    assert first.json() == {"order_id": order_id, "event_keys": ["CANCELED"]}
    second = api.client.post(f"/orders/{order_id}/cancel")
    assert second.status_code == 409 and second.json()["error"]["code"] == "ORDER_ALREADY_FINAL"


def test_cancel_unknown_order_is_404(api):
    response = api.client.post(f"/orders/{uuid4()}/cancel")
    assert response.status_code == 404 and response.json()["error"]["code"] == "ORDER_NOT_FOUND"


def test_cancel_replay_order_is_409_read_only(api):
    order_id = closed_order(api)
    replay_id = reproduce_orders(api.services.engine, code_version=CODE_VERSION, order_ids=[order_id]).created[order_id]
    response = api.client.post(f"/orders/{replay_id}/cancel")
    assert response.status_code == 409 and response.json()["error"]["code"] == "REPLAY_ORDER_READ_ONLY"


def test_reproduce_by_ids(api):
    order_id = closed_order(api)
    response = post_json(api.client, "/replay", {"mode": "REPRODUCE", "order_ids": [str(order_id)]})
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "REPRODUCE" and list(body["created"]) == [str(order_id)] and body["failures"] == {}


def test_reproduce_divergence_is_409_with_the_diff_per_order(api):
    """Same divergence setup as test_reproduce.test_divergent_history_fails_records_incident_and_freezes_source."""
    diverging = UUID(new_signal(api)["auto_order_id"])
    identical = UUID(new_signal(api, client_signal_id="msft", ticker="MSFT")["auto_order_id"])
    api.bars.load(scenario_bars())
    api.bars.load(scenario_bars(ticker="MSFT"))
    run_live_cycle(api.services.engine, api.services.gateway, code_version=CODE_VERSION, market_now=et(DAY, "10:30"))
    with api.services.engine.connect() as conn:
        run = get_run(conn, list_segments(conn, diverging)[0].run_id)
    backdated_batch(api.services.engine, TICKER, [raw(DAY, "10:05", 104, 104, 104, 104)], run.data_as_of)

    response = post_json(api.client, "/replay", {"mode": "REPRODUCE", "order_ids": [str(diverging), str(identical)]})

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "REPRODUCE_DIVERGED" and error["reason"] is None
    detail = error["detail"]
    assert detail["mode"] == "REPRODUCE" and detail["run_id"]
    assert list(detail["identical"]) == [str(identical)] and detail["failures"] == {}
    assert list(detail["diverged"]) == [str(diverging)]
    entry = detail["diverged"][str(diverging)]
    assert entry["incident_id"] is not None and entry["reason"] and isinstance(entry["diff"], list)


def test_recalculate_with_overrides(api):
    order_id = closed_order(api)
    response = post_json(api.client, "/replay", {
        "mode": "RECALCULATE", "order_ids": [str(order_id)],
        "config_overrides": {"commission_per_execution": Decimal("1")},
    })
    assert response.status_code == 200
    replay_id = UUID(response.json()["created"][str(order_id)])
    with api.services.engine.connect() as conn:
        assert get_order(conn, replay_id).config.commission_per_execution == Decimal("1")


@pytest.mark.parametrize(("body", "errors"), [
    ({"mode": "SOMETIMES"}, ["INVALID_MODE"]),
    ({"mode": "REPRODUCE", "order_ids": []}, ["INVALID_ORDER_IDS"]),
    ({"mode": "REPRODUCE", "order_ids": ["x"]}, ["INVALID_ORDER_IDS"]),
    ({"mode": "REPRODUCE", "from": "2025-11-25T00:00:00", "to": "2025-11-26T00:00:00Z"}, ["NAIVE_DATETIME:from"]),
    ({"mode": "REPRODUCE", "from": "yesterday", "to": "2025-11-26T00:00:00Z"}, ["INVALID_DATETIME:from"]),
    ({"mode": "REPRODUCE", "order_ids": [str(uuid4())], "config_overrides": {"risk_amount": 5}},
     ["NOT_ALLOWED_FOR_REPRODUCE:config_overrides"]),
    ({"mode": "RECALCULATE", "order_ids": [str(uuid4())], "config_overrides": [1]}, ["INVALID_OBJECT:config_overrides"]),
    ({"mode": "RECALCULATE", "order_ids": [str(uuid4())], "extra": 1}, ["UNKNOWN_FIELD:extra"]),
])
def test_malformed_replay_requests_are_422_before_any_run(api, body, errors):
    response = post_json(api.client, "/replay", body)
    assert response.status_code == 422
    assert response.json()["error"] == {"code": "REPLAY_REQUEST_INVALID", "reason": None, "detail": {"errors": errors}}
    assert count(api.services.engine, "evaluation_runs") == 0


def test_service_level_replay_rejections_are_422(api):
    order_id = closed_order(api)
    replay_id = reproduce_orders(api.services.engine, code_version=CODE_VERSION, order_ids=[order_id]).created[order_id]
    cases = [
        {"mode": "REPRODUCE"},
        {"mode": "REPRODUCE", "order_ids": [str(replay_id)]},
        {"mode": "RECALCULATE", "order_ids": [str(order_id)], "config_overrides": {"dividend_tolerance": Decimal("0.1")}},
        {"mode": "RECALCULATE", "order_ids": [str(order_id)], "data_as_of": "2100-01-01T00:00:00Z"},
    ]
    for body in cases:
        response = post_json(api.client, "/replay", body)
        assert response.status_code == 422, body
        assert response.json()["error"]["code"] == "REPLAY_REQUEST_INVALID"
        assert response.json()["error"]["detail"]["errors"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/integration/api/test_commands_api.py -q`
Expected: FAIL. As rotas ainda não existem: os `POST` respondem `404 NOT_FOUND` (envelope da Task 11), e as asserções de status e de código de erro falham.

- [ ] **Step 3: Write the order command routes**

`src/virtual_orders/api/routes/orders.py`:

```python
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Response

from virtual_orders.api.deps import ServicesDep
from virtual_orders.api.encoding import json_response
from virtual_orders.evaluator.commands import cancel_order
from virtual_orders.evaluator.manual import create_manual_order

router = APIRouter()


@router.post("/signals/{signal_id}/orders")
def post_manual_order(signal_id: UUID, services: ServicesDep) -> Response:
    created = create_manual_order(
        services.engine, signal_id, config=services.fill_config, code_version=services.code_version,
        price_source=services.price_source, created_at=services.clock(), gateway=services.gateway,
    )
    return json_response({
        "order_id": created.order_id, "actionability_run_id": created.actionability_run_id,
        "data_as_of": created.data_as_of, "partial_bar_skipped": created.partial_bar_skipped,
    }, 201)


@router.post("/orders/{order_id}/cancel")
def post_cancel(order_id: UUID, services: ServicesDep) -> Response:
    outcome = cancel_order(services.engine, order_id, at=services.clock(), requested_by="user")
    return json_response({"order_id": order_id, "event_keys": list(outcome.event_keys)})
```

- [ ] **Step 4: Write the replay request parser and route**

`src/virtual_orders/api/replay_request.py`:

```python
"""POST /replay body (spec 5.1): shape checks here; selection and override rules stay in the service (D15)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from virtual_orders.api.errors import ApiError
from virtual_orders.evaluator.replay import ReplayMode, ReplayReport, recalculate_orders, reproduce_orders
from virtual_orders.ledger.errors import REPRODUCE_DIVERGENCE
from virtual_orders.readmodels.incidents import latest_incidents
from virtual_orders.services import Services

ALLOWED_FIELDS = frozenset({"mode", "order_ids", "from", "to", "fill_model_version", "config_overrides", "data_as_of"})
RECALCULATE_ONLY = ("fill_model_version", "config_overrides", "data_as_of")


@dataclass(frozen=True)
class ReplayRequest:
    mode: ReplayMode
    order_ids: tuple[UUID, ...] | None
    created_from: datetime | None
    created_to: datetime | None
    fill_model_version: str | None
    config_overrides: dict[str, Any] | None
    data_as_of: datetime | None


def _invalid(errors: list[str]) -> ApiError:
    return ApiError(422, "REPLAY_REQUEST_INVALID", detail={"errors": errors})


def _instant(body: Mapping[str, Any], name: str, errors: list[str]) -> datetime | None:
    raw = body.get(name)
    if raw is None:
        return None
    if not isinstance(raw, str):
        errors.append(f"INVALID_DATETIME:{name}")
        return None
    try:
        value = datetime.fromisoformat(raw)
    except ValueError:
        errors.append(f"INVALID_DATETIME:{name}")
        return None
    if value.tzinfo is None:
        errors.append(f"NAIVE_DATETIME:{name}")
        return None
    return value


def _order_ids(body: Mapping[str, Any], errors: list[str]) -> tuple[UUID, ...] | None:
    raw = body.get("order_ids")
    if raw is None:
        return None
    if not isinstance(raw, list) or not raw or not all(isinstance(item, str) for item in raw):
        errors.append("INVALID_ORDER_IDS")
        return None
    try:
        return tuple(UUID(item) for item in raw)
    except ValueError:
        errors.append("INVALID_ORDER_IDS")
        return None


def parse_replay_request(body: Mapping[str, Any]) -> ReplayRequest:
    errors = [f"UNKNOWN_FIELD:{name}" for name in sorted(set(body) - ALLOWED_FIELDS)]
    mode: ReplayMode | None = None
    raw_mode = body.get("mode")
    if isinstance(raw_mode, str) and raw_mode in ReplayMode.__members__:
        mode = ReplayMode(raw_mode)
    else:
        errors.append("INVALID_MODE")
    order_ids = _order_ids(body, errors)
    created_from = _instant(body, "from", errors)
    created_to = _instant(body, "to", errors)
    data_as_of = _instant(body, "data_as_of", errors)
    version = body.get("fill_model_version")
    if version is not None and not isinstance(version, str):
        errors.append("INVALID_TEXT:fill_model_version")
    overrides = body.get("config_overrides")
    if overrides is not None and not isinstance(overrides, dict):
        errors.append("INVALID_OBJECT:config_overrides")
    if mode is ReplayMode.REPRODUCE:
        errors.extend(f"NOT_ALLOWED_FOR_REPRODUCE:{name}" for name in RECALCULATE_ONLY if body.get(name) is not None)
    if errors or mode is None:
        raise _invalid(errors)
    return ReplayRequest(
        mode, order_ids, created_from, created_to,
        version if isinstance(version, str) else None,
        overrides if isinstance(overrides, dict) else None,
        data_as_of,
    )


def _divergence(services: Services, report: ReplayReport, diverged_ids: list[UUID]) -> ApiError:
    """Spec 6: a divergent REPRODUCE is an integrity error with the event diff, reported per order (D15)."""
    with services.engine.connect() as conn:
        incidents = latest_incidents(conn, kind=REPRODUCE_DIVERGENCE, order_ids=diverged_ids)
    diverged: dict[UUID, dict[str, Any]] = {}
    for source in diverged_ids:
        incident = incidents.get(source)
        diverged[source] = {
            "incident_id": None if incident is None else incident.incident_id,
            "reason": None if incident is None else incident.detail.get("reason"),
            "diff": [] if incident is None else incident.detail.get("diff", []),
        }
    return ApiError(409, "REPRODUCE_DIVERGED", detail={
        "run_id": report.run_id, "mode": report.mode, "identical": report.created, "diverged": diverged,
        "failures": {source: reason for source, reason in report.failures.items() if reason != REPRODUCE_DIVERGENCE},
    })


def run_replay(services: Services, request: ReplayRequest) -> ReplayReport:
    """ReplaySelectionError (selection, overrides, data_as_of guards) propagates to the global 422 handler."""
    order_ids = None if request.order_ids is None else list(request.order_ids)
    if request.mode is ReplayMode.RECALCULATE:
        return recalculate_orders(
            services.engine, code_version=services.code_version, order_ids=order_ids,
            created_from=request.created_from, created_to=request.created_to,
            fill_model_version=request.fill_model_version, config_overrides=request.config_overrides,
            data_as_of=request.data_as_of,
        )
    report = reproduce_orders(services.engine, code_version=services.code_version, order_ids=order_ids,
                              created_from=request.created_from, created_to=request.created_to)
    diverged_ids = [source for source, reason in report.failures.items() if reason == REPRODUCE_DIVERGENCE]
    if diverged_ids:
        raise _divergence(services, report, diverged_ids)
    return report
```

`src/virtual_orders/api/routes/replay.py`:

```python
from __future__ import annotations

from fastapi import APIRouter, Request, Response
from starlette.concurrency import run_in_threadpool

from virtual_orders.api.deps import ServicesDep
from virtual_orders.api.encoding import json_response, read_json_object
from virtual_orders.api.replay_request import parse_replay_request, run_replay

router = APIRouter()


@router.post("/replay")
async def post_replay(request: Request, services: ServicesDep) -> Response:
    replay = parse_replay_request(await read_json_object(request))
    report = await run_in_threadpool(run_replay, services, replay)
    return json_response({"run_id": report.run_id, "mode": report.mode, "created": report.created,
                          "failures": report.failures})
```

Em `app.py`:
- troque o import por `from virtual_orders.api.routes import orders, replay, signals`;
- acrescente `app.include_router(orders.router)` e `app.include_router(replay.router)` depois do router de sinais.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/integration/api -q && uv run ruff check src tests migrations && uv run mypy`
Expected: PASS. `test_every_route_rejects_a_missing_or_wrong_key` now also covers the three new routes.

- [ ] **Step 6: Commit**

```bash
git add src/virtual_orders/api tests/integration/api/test_commands_api.py
git commit -m "feat(api): manual orders, cancellation and replay routes"
```

---

### Task 13: Leitura de ordens (spec 5.1: `GET /orders`, `GET /orders/{id}`)

**Files:**
- Create: `src/virtual_orders/readmodels/orders.py`, `tests/integration/api/test_orders_api.py`
- Modify: `src/virtual_orders/api/routes/orders.py`

**Interfaces:**
- Consumes:
  - tabelas `orders`, `signals`, `order_state`;
  - `stored_events(conn, order_id) -> list[StoredEvent(seq, prepared)]`;
  - `list_segments(conn, order_id) -> list[SegmentRow]`;
  - `get_run(conn, run_id) -> RunInfo`;
  - `read_bars_as_of(conn, ticker, source, start, end, as_of) -> list[Bar]`;
  - `ONE_MINUTE` (`core.domain.calendar`).
- Produces:
  - `@dataclass(frozen=True) class OrderFilters`, com `status: str | None = None`, `origin: str | None = None`, `strategy: str | None = None`, `replay: bool = False` e `needs_review: bool | None = None`;
  - `list_orders(conn, filters: OrderFilters, *, limit: int, offset: int) -> list[dict[str, Any]]`;
  - `order_detail(conn, order_id: UUID) -> dict[str, Any]`, que levanta `OrderNotFound`.

  O detalhe tem as chaves `order`, `state`, `events`, `segments`, `data_quality` e `bars`:
  - `state` é o `state_document`, ou `None` se a projeção faltar;
  - `data_quality` traz `expected_bars`, `missing_bars` e os payloads de `DATA_QUALITY`/`DATA_GAP`;
  - `bars` são os candles as-of o maior `data_as_of` entre os runs dos segmentos, na janela `[evaluation_start_ts, max(bar_to) + 1 min)`, ou `[]` sem segmentos.

- [ ] **Step 1: Write the failing tests**

`tests/integration/api/test_orders_api.py`:

```python
from uuid import UUID, uuid4

from tests.integration.api.conftest import post_json
from tests.integration.support import CODE_VERSION, DAY, scenario_bars, signal_body
from tests.support import et
from virtual_orders.evaluator.commands import flag_order_review
from virtual_orders.evaluator.cycle import run_live_cycle
from virtual_orders.evaluator.replay import reproduce_orders


def evaluated_order(api, hms=("10:30", "11:30", "13:00"), **overrides) -> UUID:
    order_id = UUID(post_json(api.client, "/signals", signal_body(**overrides)).json()["auto_order_id"])
    api.bars.load(scenario_bars(ticker=overrides.get("ticker", "AAPL")))
    for hm in hms:
        run_live_cycle(api.services.engine, api.services.gateway, code_version=CODE_VERSION, market_now=et(DAY, hm))
    return order_id


def test_list_orders_projects_state_and_filters(api):
    closed = evaluated_order(api)
    pending = UUID(post_json(api.client, "/signals", signal_body(client_signal_id="p", strategy="OTHER",
                                                                 ticker="MSFT")).json()["auto_order_id"])
    flag_order_review(api.services.engine, pending, reason="MANUAL", ref="look")
    reproduce_orders(api.services.engine, code_version=CODE_VERSION, order_ids=[closed])

    everything = api.client.get("/orders").json()["orders"]
    assert {o["order_id"] for o in everything} == {str(closed), str(pending)}  # replay=false by default
    by_id = {o["order_id"]: o for o in everything}
    assert by_id[str(closed)]["status"] == "CLOSED" and by_id[str(closed)]["r_multiple"] == "1.75"
    assert by_id[str(closed)]["strategy"] == "REXSHARE" and by_id[str(closed)]["origin"] == "AUTO_STRATEGY"

    def ids(**params):
        return [o["order_id"] for o in api.client.get("/orders", params=params).json()["orders"]]

    assert ids(status="CLOSED") == [str(closed)]
    assert ids(strategy="OTHER") == [str(pending)]
    assert ids(needs_review="true") == [str(pending)]
    assert ids(origin="MANUAL_USER") == []
    replays = api.client.get("/orders", params={"replay": "true"}).json()["orders"]
    assert len(replays) == 1 and replays[0]["replay_of_order_id"] == str(closed)
    assert api.client.get("/orders", params={"status": "NOPE"}).json()["error"]["code"] == "REQUEST_INVALID"


def test_order_detail_has_events_segments_quality_and_bars(api):
    order_id = evaluated_order(api)
    response = api.client.get(f"/orders/{order_id}")
    assert response.status_code == 200
    detail = response.json()
    assert detail["order"]["order_id"] == str(order_id) and detail["order"]["price_source"] == "fake_feed"
    assert detail["state"]["status"] == "CLOSED"
    keys = [e["event_key"] for e in detail["events"]]
    assert keys[0] == "ORDER_CREATED" and "FILLED" in keys and "TARGET2_HIT" in keys
    assert [e["seq"] for e in detail["events"]] == list(range(1, len(detail["events"]) + 1))
    assert len(detail["segments"]) == 3 and all(s["selected_data_hash"] for s in detail["segments"])
    assert detail["data_quality"] == {"expected_bars": 0, "missing_bars": 0, "events": []}
    assert detail["bars"][0]["ts"] == et(DAY, "09:30").isoformat()
    assert detail["bars"][-1]["ts"] == et(DAY, "12:50").isoformat()
    assert detail["bars"][0]["open"] == "105"


def test_order_detail_without_segments_has_no_bars_and_unknown_is_404(api):
    order_id = post_json(api.client, "/signals", signal_body()).json()["auto_order_id"]
    detail = api.client.get(f"/orders/{order_id}").json()
    assert detail["segments"] == [] and detail["bars"] == [] and detail["state"]["status"] == "PENDING"
    missing = api.client.get(f"/orders/{uuid4()}")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "ORDER_NOT_FOUND"
```

`r_multiple` do cenário (`scenario_bars`, r = 1.75) sai normalizado como `"1.75"`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/integration/api/test_orders_api.py -q`
Expected: FAIL. `GET /orders` responde 405, porque a rota ainda não existe (só há `POST /orders/{id}/cancel`).

- [ ] **Step 3: Write the read model**

`src/virtual_orders/readmodels/orders.py`:

```python
"""GET /orders and GET /orders/{id} read models (spec 5.1). Reads only; never quarantines or rebuilds."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Select, select

from core.domain.calendar import ONE_MINUTE
from virtual_orders.ledger.errors import OrderNotFound
from virtual_orders.ledger.events import stored_events
from virtual_orders.ledger.runs import get_run, list_segments
from virtual_orders.marketdata.asof import read_bars_as_of
from virtual_orders.storage.tables import order_state, orders, signals

QUALITY_EVENT_TYPES = frozenset({"DATA_QUALITY", "DATA_GAP"})
ORDER_COLUMNS = (
    orders.c.id.label("order_id"), orders.c.signal_id, orders.c.origin, orders.c.created_at,
    orders.c.evaluation_start_ts, orders.c.valid_until_ts, orders.c.fill_model_version, orders.c.code_version,
    orders.c.replay, orders.c.replay_mode, orders.c.replay_of_order_id, orders.c.market_data_snapshot_id,
    orders.c.risk_amount, orders.c.price_source, signals.c.strategy, signals.c.ticker, signals.c.direction,
    order_state.c.status, order_state.c.entry_path, order_state.c.avg_entry, order_state.c.stop_current,
    order_state.c.qty_open, order_state.c.realized_pnl, order_state.c.costs, order_state.c.r_multiple,
    order_state.c.mfe_r, order_state.c.mae_r, order_state.c.opened_at, order_state.c.closed_at,
    order_state.c.last_bar_ts, order_state.c.expected_bars, order_state.c.missing_bars,
    order_state.c.needs_review, order_state.c.frozen,
)


@dataclass(frozen=True)
class OrderFilters:
    status: str | None = None
    origin: str | None = None
    strategy: str | None = None
    replay: bool = False
    needs_review: bool | None = None


def _base() -> Select[Any]:
    return select(*ORDER_COLUMNS).select_from(
        orders.join(signals, signals.c.id == orders.c.signal_id)
        .outerjoin(order_state, order_state.c.order_id == orders.c.id)
    )


def list_orders(conn: Connection, filters: OrderFilters, *, limit: int, offset: int) -> list[dict[str, Any]]:
    query = _base().where(orders.c.replay.is_(filters.replay))
    if filters.status is not None:
        query = query.where(order_state.c.status == filters.status)
    if filters.origin is not None:
        query = query.where(orders.c.origin == filters.origin)
    if filters.strategy is not None:
        query = query.where(signals.c.strategy == filters.strategy)
    if filters.needs_review is not None:
        query = query.where(order_state.c.needs_review.is_(filters.needs_review))
    rows = conn.execute(query.order_by(orders.c.created_at.desc(), orders.c.id).limit(limit).offset(offset))
    return [dict(row) for row in rows.mappings()]


def order_detail(conn: Connection, order_id: UUID) -> dict[str, Any]:
    row = conn.execute(_base().where(orders.c.id == order_id)).mappings().first()
    if row is None:
        raise OrderNotFound(str(order_id))
    state = conn.execute(
        select(order_state.c.state_document).where(order_state.c.order_id == order_id)
    ).scalar_one_or_none()
    events = [
        {"seq": item.seq, "type": item.prepared.type, "event_key": item.prepared.event_key,
         "bar_ts": item.prepared.bar_ts, "price": item.prepared.price, "qty": item.prepared.qty,
         "payload": item.prepared.payload, "payload_hash": item.prepared.payload_hash}
        for item in stored_events(conn, order_id)
    ]
    segments = list_segments(conn, order_id)
    bars: list[dict[str, Any]] = []
    if segments:
        as_of = max(get_run(conn, segment.run_id).data_as_of for segment in segments)
        end = max(segment.bar_to for segment in segments) + ONE_MINUTE
        bars = [
            {"ts": bar.ts, "open": bar.open, "high": bar.high, "low": bar.low, "close": bar.close,
             "volume": bar.volume, "batch_id": bar.batch_id}
            for bar in read_bars_as_of(conn, row["ticker"], row["price_source"], row["evaluation_start_ts"], end, as_of)
        ]
    return {
        "order": dict(row),
        "state": state,
        "events": events,
        "segments": [asdict(segment) for segment in segments],
        "data_quality": {
            "expected_bars": row["expected_bars"] or 0,
            "missing_bars": row["missing_bars"] or 0,
            "events": [event for event in events if event["type"] in QUALITY_EVENT_TYPES],
        },
        "bars": bars,
    }
```

- [ ] **Step 4: Add the routes**

Substitua `src/virtual_orders/api/routes/orders.py` inteiro pelo conteúdo abaixo. Os imports ficam num único `from fastapi import APIRouter, Query, Response`, na ordem do isort, e as duas rotas `POST` da Task 12 não mudam. `OrderStatus` e `Origin` são `StrEnum` do núcleo e o FastAPI os valida como query params; qualquer valor fora da enum vira `422 REQUEST_INVALID`.

```python
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response

from core.domain.models import OrderStatus, Origin
from virtual_orders.api.deps import ServicesDep
from virtual_orders.api.encoding import json_response
from virtual_orders.evaluator.commands import cancel_order
from virtual_orders.evaluator.manual import create_manual_order
from virtual_orders.readmodels.orders import OrderFilters, list_orders, order_detail

router = APIRouter()


@router.post("/signals/{signal_id}/orders")
def post_manual_order(signal_id: UUID, services: ServicesDep) -> Response:
    created = create_manual_order(
        services.engine, signal_id, config=services.fill_config, code_version=services.code_version,
        price_source=services.price_source, created_at=services.clock(), gateway=services.gateway,
    )
    return json_response({
        "order_id": created.order_id, "actionability_run_id": created.actionability_run_id,
        "data_as_of": created.data_as_of, "partial_bar_skipped": created.partial_bar_skipped,
    }, 201)


@router.post("/orders/{order_id}/cancel")
def post_cancel(order_id: UUID, services: ServicesDep) -> Response:
    outcome = cancel_order(services.engine, order_id, at=services.clock(), requested_by="user")
    return json_response({"order_id": order_id, "event_keys": list(outcome.event_keys)})


@router.get("/orders")
def get_orders(
    services: ServicesDep,
    status: OrderStatus | None = None,
    origin: Origin | None = None,
    strategy: str | None = None,
    replay: bool = False,
    needs_review: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Response:
    filters = OrderFilters(
        status=None if status is None else status.value, origin=None if origin is None else origin.value,
        strategy=strategy, replay=replay, needs_review=needs_review,
    )
    with services.engine.connect() as conn:
        return json_response({"orders": list_orders(conn, filters, limit=limit, offset=offset)})


@router.get("/orders/{order_id}")
def get_order_detail(order_id: UUID, services: ServicesDep) -> Response:
    with services.engine.connect() as conn:
        return json_response(order_detail(conn, order_id))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/integration/api -q && uv run ruff check src tests migrations && uv run mypy`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/virtual_orders/readmodels/orders.py src/virtual_orders/api/routes/orders.py tests/integration/api/test_orders_api.py
git commit -m "feat(api): order list and detail with events, segments, quality and as-of bars"
```

---

### Task 14: Métricas agregadas (entrada 8, spec 5.4)

**Files:**
- Create: `src/virtual_orders/readmodels/metrics.py`, `src/virtual_orders/api/routes/metrics.py`, `tests/integration/api/test_metrics_api.py`
- Modify: `src/virtual_orders/api/app.py`

**Interfaces:**
- Consumes:
  - `TradeResult`, `execution_counts`, `summarize`, `MetricsSummary` (`core.metrics.summary`);
  - `state_from_document`;
  - `Direction`;
  - `Services.bootstrap_resamples`/`bootstrap_seed`.
- Produces:
  - `class GroupBy(StrEnum)` com `STRATEGY = "strategy"`, `ORIGIN = "origin"`, `FILL_MODEL_VERSION = "fill_model_version"`, `ENTRY_PATH = "entry_path"`;
  - `@dataclass(frozen=True) class MetricsGroup` com `key: str | None` e `summary: MetricsSummary`;
  - `metrics_by_group(conn, *, group_by: GroupBy | None, created_from: datetime | None, created_to: datetime | None, replay: bool, include_needs_review: bool, resamples: int, seed: int) -> list[MetricsGroup]`.
- Semântica:
  - ordens com projeção entram em `execution_counts`;
  - só `CLOSED` vira `TradeResult`, como `tests/integration/test_end_to_end.py::trades`;
  - `from`/`to` filtram `orders.created_at` em `[from, to)` (D19);
  - com `group_by`, grupos vazios não aparecem; sem `group_by`, a resposta sempre tem um grupo `key=null`, mesmo sem ordens, para que `excluded_needs_review` esteja sempre presente (spec 5.4, D19);
  - `execution_rate` conta todas as ordens com projeção do filtro, inclusive `needs_review`; a política de revisão vale para as métricas de trade (D19).

- [ ] **Step 1: Write the failing tests**

`tests/integration/api/test_metrics_api.py`:

```python
from uuid import UUID

from tests.integration.api.conftest import post_json
from tests.integration.support import CODE_VERSION, DAY, scenario_bars, signal_body
from tests.support import et
from virtual_orders.evaluator.commands import flag_order_review
from virtual_orders.evaluator.cycle import run_live_cycle
from virtual_orders.evaluator.replay import reproduce_orders


def cycle(api, hm):
    run_live_cycle(api.services.engine, api.services.gateway, code_version=CODE_VERSION, market_now=et(DAY, hm))


def two_closed_orders(api) -> tuple[UUID, UUID]:
    api.bars.load(scenario_bars())
    first = UUID(post_json(api.client, "/signals", signal_body()).json()["auto_order_id"])
    second = UUID(post_json(api.client, "/signals", signal_body(client_signal_id="second",
                                                                strategy="OTHER")).json()["auto_order_id"])
    cycle(api, "10:30")
    flag_order_review(api.services.engine, second, reason="MANUAL", ref="looked-odd")
    cycle(api, "11:30")
    cycle(api, "13:00")
    return first, second


def metrics(api, **params):
    response = api.client.get("/metrics", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_needs_review_is_excluded_by_default_and_reported(api):
    two_closed_orders(api)
    default = metrics(api)
    assert [g["key"] for g in default["groups"]] == [None]
    summary = default["groups"][0]["summary"]
    assert summary["trades"] == 1 and summary["avg_r"] == 1.75
    assert summary["excluded_needs_review"] == {"count": 1, "reasons": {"MANUAL": 1}}
    assert "INSUFFICIENT_SAMPLE" in summary["warnings"]

    included = metrics(api, include_needs_review="true")["groups"][0]["summary"]
    assert included["trades"] == 2 and included["included_needs_review"]["count"] == 1


def test_group_by_strategy_and_determinism(api):
    two_closed_orders(api)
    grouped = metrics(api, group_by="strategy", include_needs_review="true")
    assert grouped["group_by"] == "strategy"
    assert [(g["key"], g["summary"]["trades"]) for g in grouped["groups"]] == [("OTHER", 1), ("REXSHARE", 1)]
    assert metrics(api, group_by="strategy", include_needs_review="true") == grouped


def test_replay_and_interval_filters(api):
    first, _ = two_closed_orders(api)
    reproduce_orders(api.services.engine, code_version=CODE_VERSION, order_ids=[first])
    replays = metrics(api, replay="true")
    assert replays["groups"][0]["summary"]["trades"] == 1
    interval = {"from": "2025-11-26T00:00:00Z", "to": "2025-11-27T00:00:00Z"}
    later = metrics(api, **interval)
    assert [g["key"] for g in later["groups"]] == [None]
    empty = later["groups"][0]["summary"]
    assert empty["trades"] == 0 and empty["excluded_needs_review"] == {"count": 0, "reasons": {}}
    assert "INSUFFICIENT_SAMPLE" in empty["warnings"]
    assert metrics(api, group_by="strategy", **interval)["groups"] == []


def test_invalid_metric_parameters_are_422(api):
    for params in ({"group_by": "ticker"}, {"from": "2025-11-26T00:00:00"}):
        response = api.client.get("/metrics", params=params)
        assert response.status_code == 422, params
        assert response.json()["error"]["code"] == "REQUEST_INVALID"
```

Fatos verificados no código que as asserções usam:
- `flag_review` do núcleo guarda o motivo sem ref em `review_reasons` (`src/core/fills/v1/engine.py:423`), e `summarize` conta esses motivos, logo `{"MANUAL": 1}`.
- `flag_order_review` não interrompe a avaliação: só `is_final` e `frozen` interrompem (`cycle.py:99`). A segunda ordem chega a `CLOSED`.
- `summarize([], ExecutionCounts(0, 0), …)` é seguro: `bootstrap_ci` e `drawdown_permutation_percentiles` devolvem `None` com menos de 2 valores.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/integration/api/test_metrics_api.py -q`
Expected: FAIL (`/metrics` responde 404).

- [ ] **Step 3: Write the read model**

`src/virtual_orders/readmodels/metrics.py`:

```python
"""GET /metrics (spec 5.4): TradeResult from order_state, review policy and replay filter applied here (D19)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import Connection, select

from core.domain.models import Direction
from core.metrics.summary import MetricsSummary, TradeResult, execution_counts, summarize
from virtual_orders.storage.codec import state_from_document
from virtual_orders.storage.tables import order_state, orders, signals


class GroupBy(StrEnum):
    STRATEGY = "strategy"
    ORIGIN = "origin"
    FILL_MODEL_VERSION = "fill_model_version"
    ENTRY_PATH = "entry_path"


@dataclass(frozen=True)
class MetricsGroup:
    key: str | None
    summary: MetricsSummary


def metrics_by_group(
    conn: Connection,
    *,
    group_by: GroupBy | None,
    created_from: datetime | None,
    created_to: datetime | None,
    replay: bool,
    include_needs_review: bool,
    resamples: int,
    seed: int,
) -> list[MetricsGroup]:
    query = (
        select(
            orders.c.id, orders.c.origin, orders.c.fill_model_version, signals.c.strategy, signals.c.direction,
            order_state.c.entry_path, order_state.c.opened_at, order_state.c.closed_at, order_state.c.r_multiple,
            order_state.c.mfe_r, order_state.c.mae_r, order_state.c.state_document,
        )
        .select_from(
            orders.join(signals, signals.c.id == orders.c.signal_id)
            .join(order_state, order_state.c.order_id == orders.c.id)
        )
        .where(orders.c.replay.is_(replay))
    )
    if created_from is not None:
        query = query.where(orders.c.created_at >= created_from)
    if created_to is not None:
        query = query.where(orders.c.created_at < created_to)
    members: dict[str | None, list[Any]] = defaultdict(list)
    for row in conn.execute(query.order_by(orders.c.created_at, orders.c.id)):
        key = None if group_by is None else getattr(row, group_by.value)
        members[key].append(row)
    if group_by is None and not members:
        members[None] = []  # spec 5.4: the answer always reports excluded_needs_review, even with no orders

    groups: list[MetricsGroup] = []
    for key in sorted(members, key=lambda k: (k is None, k or "")):
        rows = members[key]
        trades = [
            TradeResult(str(r.id), Direction(r.direction), r.opened_at, r.closed_at, r.r_multiple, r.mfe_r, r.mae_r,
                        tuple(r.state_document["review_reasons"]))
            for r in rows if r.state_document["status"] == "CLOSED"
        ]
        counts = execution_counts([state_from_document(r.state_document) for r in rows])
        summary = summarize(trades, counts, include_needs_review=include_needs_review, resamples=resamples, seed=seed)
        groups.append(MetricsGroup(key, summary))
    return groups
```

- [ ] **Step 4: Write the route**

`src/virtual_orders/api/routes/metrics.py`:

```python
from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query, Response

from virtual_orders.api.deps import ServicesDep
from virtual_orders.api.encoding import json_response
from virtual_orders.api.errors import ApiError
from virtual_orders.readmodels.metrics import GroupBy, metrics_by_group

router = APIRouter()


def _aware(value: datetime | None, name: str) -> datetime | None:
    if value is not None and value.tzinfo is None:
        raise ApiError(422, "REQUEST_INVALID", detail={"errors": [{"loc": ["query", name],
                                                                   "msg": "datetime must be timezone-aware"}]})
    return value


@router.get("/metrics")
def get_metrics(
    services: ServicesDep,
    group_by: GroupBy | None = None,
    created_from: Annotated[datetime | None, Query(alias="from")] = None,
    created_to: Annotated[datetime | None, Query(alias="to")] = None,
    replay: bool = False,
    include_needs_review: bool = False,
) -> Response:
    start, end = _aware(created_from, "from"), _aware(created_to, "to")
    with services.engine.connect() as conn:
        groups = metrics_by_group(
            conn, group_by=group_by, created_from=start, created_to=end, replay=replay,
            include_needs_review=include_needs_review, resamples=services.bootstrap_resamples,
            seed=services.bootstrap_seed,
        )
    return json_response({
        "group_by": group_by, "from": start, "to": end, "replay": replay,
        "include_needs_review": include_needs_review, "groups": groups,
    })
```

Em `app.py`, importe `metrics` junto dos outros routers e acrescente `app.include_router(metrics.router)`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/integration/api -q && uv run ruff check src tests migrations && uv run mypy`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/virtual_orders/readmodels/metrics.py src/virtual_orders/api/routes/metrics.py src/virtual_orders/api/app.py tests/integration/api/test_metrics_api.py
git commit -m "feat(api): aggregated metrics with review policy, replay filter and grouping"
```

---

### Task 15: `/health` estruturado (entradas 7, 15, 17, 18, 19; D17, D18)

**Files:**
- Create: `src/virtual_orders/readmodels/health.py`, `src/virtual_orders/api/routes/health.py`, `tests/readmodels/test_health_rules.py`, `tests/integration/api/test_health_api.py`
- Modify: `src/virtual_orders/api/app.py`

**Interfaces:**
- Consumes:
  - `IncidentGroup`, `incident_groups`, `classify_order_errors`, `ErrorCategory`, `MAX_LISTED_ORDERS` (Task 7);
  - `actionability_outcomes(conn, *, since)`;
  - `calendar_for_window`;
  - tabelas `alembic_version`, `evaluation_runs`, `evaluation_run_status`, `bar_batches`, `order_state`, `orders`, `integrity_incidents`;
  - `detail["market_now"]` dos runs `LIVE` concluídos (gravado por `cycle.py`).
- Produces:
  - `HealthState(StrEnum)` — `HEALTHY`, `DEGRADED`, `UNHEALTHY`.
  - `Severity(StrEnum)` — `INFO`, `DEGRADED`, `UNHEALTHY`.
  - `Cause(code: str, severity: Severity, detail: dict[str, Any])`.
  - `RunSummary(run_id: str, kind: str, status: str, started_at: datetime, data_as_of: datetime, detail: dict[str, Any])`.
  - `HealthSnapshot(now, session_open_utc, live_runs, last_end_of_day, last_opening, latest_ingested_at, incident_groups, incidents_total, frozen_orders, orders_without_projection, orders_without_projection_ids, needs_review, actionability)`.
  - `HealthReport(state: HealthState, causes: tuple[Cause, ...], snapshot: HealthSnapshot | None)`.
  - Constantes: `EXPECTED_SCHEMA_REVISION = "0002"`, `CONSECUTIVE_FAILURE_THRESHOLD = 3`, `INCIDENT_WINDOW = timedelta(hours=24)`, `STALE_CYCLE_MULTIPLIER = 3`, `RECENT_LIVE_RUNS = 20`.
  - `evaluate_health(snapshot: HealthSnapshot, *, eval_interval_minutes: int) -> HealthReport` (pura; nunca produz `UNHEALTHY`).
  - `database_unavailable(error: Exception) -> HealthReport` e `schema_not_at_head(found: str | None) -> HealthReport` (os dois únicos `UNHEALTHY`).
  - `schema_revision(conn: Connection) -> str | None`.
  - `collect_health_snapshot(conn: Connection, *, now: datetime) -> HealthSnapshot`.
  - `build_health_report(engine: Engine, *, now: datetime, eval_interval_minutes: int) -> HealthReport`.
  - Rota `GET /health`. Resposta: `{"state", "causes": [...], "facts": snapshot|null}`, com HTTP 200 para `HEALTHY`/`DEGRADED` e 503 só para `UNHEALTHY` (D17).
  - Qualquer migration futura precisa atualizar `EXPECTED_SCHEMA_REVISION`; `test_expected_schema_revision_is_the_migration_head` falha se esquecerem.

- [ ] **Step 1: Write the failing rule tests**

`tests/readmodels/test_health_rules.py`:

```python
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from virtual_orders.readmodels.health import (
    EXPECTED_SCHEMA_REVISION,
    HealthSnapshot,
    HealthState,
    RunSummary,
    Severity,
    database_unavailable,
    evaluate_health,
    schema_not_at_head,
)
from virtual_orders.readmodels.incidents import IncidentGroup

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2025, 11, 25, 15, 30, tzinfo=UTC)  # 10:30 ET
SESSION_OPEN = datetime(2025, 11, 25, 14, 30, tzinfo=UTC)  # 09:30 ET


def run(kind="LIVE", status="COMPLETED", minutes_ago=1, **detail) -> RunSummary:
    started = NOW - timedelta(minutes=minutes_ago)
    return RunSummary(f"{kind}-{minutes_ago}", kind, status, started, started,
                      {"market_now": started.isoformat(), **detail})


def snapshot(**overrides) -> HealthSnapshot:
    base = HealthSnapshot(
        now=NOW, session_open_utc=None, live_runs=(), last_end_of_day=None, last_opening=None,
        latest_ingested_at=None, incident_groups=(), incidents_total=0, frozen_orders=0,
        orders_without_projection=0, orders_without_projection_ids=(), needs_review={}, actionability={},
    )
    return replace(base, **overrides)


def codes(report):
    return {cause.code: cause.severity for cause in report.causes}


def test_expected_schema_revision_is_the_migration_head():
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    assert ScriptDirectory.from_config(config).get_current_head() == EXPECTED_SCHEMA_REVISION


def test_nothing_to_report_is_healthy():
    report = evaluate_health(snapshot(), eval_interval_minutes=2)
    assert report.state is HealthState.HEALTHY and report.causes == ()


def test_only_an_unreachable_database_or_a_schema_off_head_is_unhealthy():
    down = database_unavailable(ConnectionError("refused"))
    assert down.state is HealthState.UNHEALTHY and [c.code for c in down.causes] == ["DATABASE_UNAVAILABLE"]
    behind = schema_not_at_head("0001")
    assert behind.state is HealthState.UNHEALTHY and behind.snapshot is None
    assert behind.causes[0].detail == {"expected": EXPECTED_SCHEMA_REVISION, "found": "0001"}

    failing_runs = tuple(
        run(minutes_ago=m, ingest_failures={"nope:AAPL": "UNKNOWN_DATA_SOURCE: no bar source registered"},
            order_errors={"o1": "ERROR:OperationalError", "o2": "ERROR:KeyError"})
        for m in (32, 34, 36)
    )
    worst = evaluate_health(snapshot(
        session_open_utc=SESSION_OPEN,
        live_runs=(run(status="FAILED", minutes_ago=30, error="boom"), *failing_runs),
        last_end_of_day=run(kind="END_OF_DAY", not_evaluated={"o3": "PROVIDER_FAILURE"}, session_day="2025-11-24"),
        last_opening=run(kind="OPENING", status="FAILED", error="boom"),
        incident_groups=(IncidentGroup("EVENT_HASH_CONFLICT", None, 5, 1, ("a",), NOW, NOW),), incidents_total=5,
        frozen_orders=1, orders_without_projection=1, orders_without_projection_ids=("b",),
        needs_review={"MISSING_BAR": 1}, actionability={"ACTIONABILITY_UNVERIFIABLE": 1},
    ), eval_interval_minutes=2)
    assert worst.state is HealthState.DEGRADED
    assert Severity.UNHEALTHY not in {cause.severity for cause in worst.causes}
    assert {
        "LAST_CYCLE_FAILED", "UNKNOWN_DATA_SOURCE", "INGEST_FAILURES_CONSECUTIVE", "LIVE_CYCLE_STALE",
        "INTEGRITY_INCIDENTS", "PROJECTION_MISSING_ORDERS", "INFRASTRUCTURE_ERRORS", "ORDER_ERRORS",
        "FROZEN_ORDERS", "QUALITY_NOT_EVALUATED", "OPENING_SOURCE_FAILURES",
    } <= {cause.code for cause in worst.causes}


def test_last_cycle_failed_is_degraded():
    report = evaluate_health(snapshot(live_runs=(run(status="FAILED", error="boom"),)), eval_interval_minutes=2)
    assert report.state is HealthState.DEGRADED and codes(report) == {"LAST_CYCLE_FAILED": Severity.DEGRADED}


def test_provider_failures_degrade_after_three_consecutive_cycles_and_are_informational_before():
    failing = {"ingest_failures": {"alpaca_iex:AAPL": "down"}}
    three = tuple(run(minutes_ago=m, **failing) for m in (1, 3, 5))
    report = evaluate_health(snapshot(live_runs=three), eval_interval_minutes=2)
    assert codes(report) == {"INGEST_FAILURES_CONSECUTIVE": Severity.DEGRADED}
    assert report.causes[0].detail == {"feeds": {"alpaca_iex:AAPL": 3}}
    assert report.state is HealthState.DEGRADED

    two = tuple(run(minutes_ago=m, **failing) for m in (1, 3))
    report = evaluate_health(snapshot(live_runs=two), eval_interval_minutes=2)
    assert codes(report) == {"INGEST_FAILURES": Severity.INFO} and report.state is HealthState.HEALTHY
    assert report.causes[0].detail == {"feeds": {"alpaca_iex:AAPL": 2}, "threshold": 3}

    interrupted = (run(minutes_ago=1, **failing), run(minutes_ago=3, ingest_failures={}), run(minutes_ago=5, **failing))
    assert codes(evaluate_health(snapshot(live_runs=interrupted), eval_interval_minutes=2)) == {
        "INGEST_FAILURES": Severity.INFO}


def test_unknown_data_source_is_degraded():
    live = run(ingest_failures={"nope:AAPL": "UNKNOWN_DATA_SOURCE: no bar source registered for 'nope'"})
    report = evaluate_health(snapshot(live_runs=(live,)), eval_interval_minutes=2)
    assert codes(report)["UNKNOWN_DATA_SOURCE"] is Severity.DEGRADED
    assert report.state is HealthState.DEGRADED


def test_stale_cycle_counts_only_during_a_session():
    during = {"session_open_utc": SESSION_OPEN}
    assert codes(evaluate_health(snapshot(**during, live_runs=(run(minutes_ago=7),)), eval_interval_minutes=2)) == {
        "LIVE_CYCLE_STALE": Severity.DEGRADED}
    assert evaluate_health(snapshot(**during, live_runs=(run(minutes_ago=5),)), eval_interval_minutes=2).causes == ()
    assert codes(evaluate_health(snapshot(**during), eval_interval_minutes=2)) == {
        "LIVE_CYCLE_STALE": Severity.DEGRADED}
    assert evaluate_health(snapshot(live_runs=(run(minutes_ago=7),)), eval_interval_minutes=2).causes == ()


def test_the_session_open_is_not_stale_because_of_the_previous_day_cycle():
    yesterday = run(minutes_ago=60 * 18)  # previous session, 16:30 ET
    just_opened = snapshot(now=SESSION_OPEN + timedelta(minutes=1), session_open_utc=SESSION_OPEN,
                           live_runs=(yesterday,))
    assert evaluate_health(just_opened, eval_interval_minutes=2).causes == ()
    later = replace(just_opened, now=SESSION_OPEN + timedelta(minutes=7))
    assert codes(evaluate_health(later, eval_interval_minutes=2)) == {"LIVE_CYCLE_STALE": Severity.DEGRADED}


def test_incidents_frozen_orders_and_order_errors_degrade():
    group = IncidentGroup("PROJECTION_MISSING", None, 3, 2, ("a", "b"), NOW, NOW)
    live = run(order_errors={"o1": "ERROR:KeyError"})
    eod = run(kind="END_OF_DAY", order_errors={"o2": "ERROR:OperationalError"},
              not_evaluated={"o3": "PROVIDER_FAILURE", "o4": "NO_OBSERVATIONS", "o5": "PROVIDER_FAILURE"},
              session_day="2025-11-24")
    report = evaluate_health(snapshot(incident_groups=(group,), incidents_total=3, frozen_orders=2,
                                      live_runs=(live,), last_end_of_day=eod), eval_interval_minutes=2)
    assert codes(report) == {
        "INTEGRITY_INCIDENTS": Severity.DEGRADED, "INFRASTRUCTURE_ERRORS": Severity.DEGRADED,
        "ORDER_ERRORS": Severity.DEGRADED, "FROZEN_ORDERS": Severity.DEGRADED,
        "QUALITY_NOT_EVALUATED": Severity.DEGRADED,
    }
    by_code = {cause.code: cause for cause in report.causes}
    assert by_code["QUALITY_NOT_EVALUATED"].detail == {
        "session_day": "2025-11-24", "reasons": {"NO_OBSERVATIONS": 1, "PROVIDER_FAILURE": 2}}
    assert by_code["ORDER_ERRORS"].detail["groups"][0].error_type == "KeyError"
    assert by_code["INTEGRITY_INCIDENTS"].detail["total"] == 3
    assert report.state is HealthState.DEGRADED


def test_orders_without_projection_are_reported_and_count_as_frozen():
    report = evaluate_health(snapshot(frozen_orders=1, orders_without_projection=2,
                                      orders_without_projection_ids=("a", "b")), eval_interval_minutes=2)
    by_code = {cause.code: cause for cause in report.causes}
    assert by_code["PROJECTION_MISSING_ORDERS"].detail == {"count": 2, "orders": ["a", "b"]}
    assert by_code["FROZEN_ORDERS"].detail == {"count": 3, "frozen_projections": 1, "without_projection": 2}
    assert report.state is HealthState.DEGRADED


def test_incidents_outside_the_window_stay_visible_as_history():
    report = evaluate_health(snapshot(incidents_total=4), eval_interval_minutes=2)
    assert codes(report) == {"INTEGRITY_INCIDENTS_HISTORY": Severity.INFO}
    assert report.causes[0].detail == {"total": 4} and report.state is HealthState.HEALTHY


def test_opening_failures_degrade():
    opening = run(kind="OPENING", source_failures={"fmp:AAPL": "down"},
                  dividend_order_errors={}, split_order_errors={})
    assert codes(evaluate_health(snapshot(last_opening=opening), eval_interval_minutes=2)) == {
        "OPENING_SOURCE_FAILURES": Severity.DEGRADED}
    failed = run(kind="OPENING", status="FAILED", error="boom")
    assert codes(evaluate_health(snapshot(last_opening=failed), eval_interval_minutes=2)) == {
        "OPENING_SOURCE_FAILURES": Severity.DEGRADED}


def test_review_queue_and_unverifiable_actionability_are_informational():
    report = evaluate_health(snapshot(needs_review={"MISSING_BAR": 4}, actionability={"ACTIONABILITY_UNVERIFIABLE": 2,
                                                                                      "ACTIONABLE": 5}),
                             eval_interval_minutes=2)
    assert report.state is HealthState.HEALTHY
    assert codes(report) == {"NEEDS_REVIEW_QUEUE": Severity.INFO, "ACTIONABILITY_UNVERIFIABLE": Severity.INFO}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/readmodels/test_health_rules.py -q`
Expected: FAIL com `ModuleNotFoundError: virtual_orders.readmodels.health`.

- [ ] **Step 3: Write `readmodels/health.py`**

```python
"""Structured health (D17): facts collected from the database, then pure rules turn them into causes and a state.

Only an unreachable database or a schema off the migration head is UNHEALTHY (HTTP 503). Everything the
spec calls "degradado" (integrity incidents, provider failures for 3 consecutive cycles, failed cycles) is
DEGRADED, so /health keeps answering 200 with its causes exactly when an operator needs them.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import Connection, Engine, func, select, text
from sqlalchemy.exc import InterfaceError, OperationalError

from virtual_orders.evaluator.manual import ACTIONABILITY_UNVERIFIABLE, actionability_outcomes
from virtual_orders.marketdata.calendars import calendar_for_window
from virtual_orders.readmodels.incidents import (
    MAX_LISTED_ORDERS,
    ErrorCategory,
    IncidentGroup,
    classify_order_errors,
    incident_groups,
)
from virtual_orders.storage.tables import bar_batches, integrity_incidents, order_state, orders

EXPECTED_SCHEMA_REVISION = "0002"  # alembic head; pinned by test_expected_schema_revision_is_the_migration_head
CONSECUTIVE_FAILURE_THRESHOLD = 3  # spec 6: three consecutive failed cycles degrade /health
INCIDENT_WINDOW = timedelta(hours=24)
STALE_CYCLE_MULTIPLIER = 3
RECENT_LIVE_RUNS = 20
UNKNOWN_DATA_SOURCE = "UNKNOWN_DATA_SOURCE"


class HealthState(StrEnum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"


class Severity(StrEnum):
    INFO = "INFO"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"


_RANK = {Severity.UNHEALTHY: 0, Severity.DEGRADED: 1, Severity.INFO: 2}


@dataclass(frozen=True)
class Cause:
    code: str
    severity: Severity
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RunSummary:
    run_id: str
    kind: str
    status: str
    started_at: datetime
    data_as_of: datetime
    detail: dict[str, Any]


@dataclass(frozen=True)
class HealthSnapshot:
    now: datetime
    session_open_utc: datetime | None  # open of the session in progress at `now`; None outside sessions
    live_runs: tuple[RunSummary, ...]  # newest first
    last_end_of_day: RunSummary | None
    last_opening: RunSummary | None
    latest_ingested_at: datetime | None
    incident_groups: tuple[IncidentGroup, ...]  # recorded within INCIDENT_WINDOW
    incidents_total: int
    frozen_orders: int  # non-replay projections with frozen = true
    orders_without_projection: int  # non-replay orders with no order_state row (Plan 2 close-out ruling 10)
    orders_without_projection_ids: tuple[str, ...]  # the first MAX_LISTED_ORDERS of them
    needs_review: dict[str, int]  # reason -> non-replay orders flagged with it
    actionability: dict[str, int]  # result -> ACTIONABILITY runs within INCIDENT_WINDOW


@dataclass(frozen=True)
class HealthReport:
    state: HealthState
    causes: tuple[Cause, ...]
    snapshot: HealthSnapshot | None


def _completed(runs: tuple[RunSummary, ...]) -> list[RunSummary]:
    return [r for r in runs if r.status == "COMPLETED"]


def _consecutive_failures(runs: tuple[RunSummary, ...]) -> dict[str, int]:
    completed = _completed(runs)
    if not completed:
        return {}
    streaks: dict[str, int] = {}
    for key in completed[0].detail.get("ingest_failures", {}):
        streak = 0
        for item in completed:
            if key not in item.detail.get("ingest_failures", {}):
                break
            streak += 1
        streaks[key] = streak
    return streaks


def _market_now(run: RunSummary) -> datetime:
    """The market instant the cycle evaluated (`detail.market_now`); FAILED runs fall back to started_at."""
    raw = run.detail.get("market_now")
    return datetime.fromisoformat(raw) if isinstance(raw, str) else run.started_at


def _order_errors(snapshot: HealthSnapshot) -> dict[str, Mapping[str, str]]:
    errors: dict[str, Mapping[str, str]] = {}
    completed = _completed(snapshot.live_runs)
    if completed:
        errors[completed[0].run_id] = completed[0].detail.get("order_errors", {})
    if snapshot.last_end_of_day is not None:
        errors[snapshot.last_end_of_day.run_id] = snapshot.last_end_of_day.detail.get("order_errors", {})
    if snapshot.last_opening is not None:
        opening = snapshot.last_opening
        errors[f"{opening.run_id}:dividends"] = opening.detail.get("dividend_order_errors", {})
        errors[f"{opening.run_id}:splits"] = opening.detail.get("split_order_errors", {})
    return errors


def evaluate_health(snapshot: HealthSnapshot, *, eval_interval_minutes: int) -> HealthReport:
    causes: list[Cause] = []
    latest_live = snapshot.live_runs[0] if snapshot.live_runs else None
    if latest_live is not None and latest_live.status == "FAILED":
        causes.append(Cause("LAST_CYCLE_FAILED", Severity.DEGRADED,
                            {"run_id": latest_live.run_id, "error": latest_live.detail.get("error")}))
    completed = _completed(snapshot.live_runs)
    if completed:
        unknown = sorted(key for key, message in completed[0].detail.get("ingest_failures", {}).items()
                         if str(message).startswith(UNKNOWN_DATA_SOURCE))
        if unknown:
            causes.append(Cause(UNKNOWN_DATA_SOURCE, Severity.DEGRADED, {"feeds": unknown}))
    streaks = sorted(_consecutive_failures(snapshot.live_runs).items())
    sustained = {key: streak for key, streak in streaks if streak >= CONSECUTIVE_FAILURE_THRESHOLD}
    recent = {key: streak for key, streak in streaks if streak < CONSECUTIVE_FAILURE_THRESHOLD}
    if sustained:
        causes.append(Cause("INGEST_FAILURES_CONSECUTIVE", Severity.DEGRADED, {"feeds": sustained}))
    if recent:
        causes.append(Cause("INGEST_FAILURES", Severity.INFO,
                            {"feeds": recent, "threshold": CONSECUTIVE_FAILURE_THRESHOLD}))
    if snapshot.session_open_utc is not None:
        limit = timedelta(minutes=eval_interval_minutes * STALE_CYCLE_MULTIPLIER)
        last = None if latest_live is None else _market_now(latest_live)
        reference = snapshot.session_open_utc if last is None else max(last, snapshot.session_open_utc)
        if snapshot.now - reference > limit:
            causes.append(Cause("LIVE_CYCLE_STALE", Severity.DEGRADED,
                                {"last_market_now": last, "session_open_utc": snapshot.session_open_utc}))
    if snapshot.incident_groups:
        causes.append(Cause("INTEGRITY_INCIDENTS", Severity.DEGRADED,
                            {"groups": list(snapshot.incident_groups), "total": snapshot.incidents_total}))
    elif snapshot.incidents_total:
        causes.append(Cause("INTEGRITY_INCIDENTS_HISTORY", Severity.INFO, {"total": snapshot.incidents_total}))
    if snapshot.orders_without_projection:
        causes.append(Cause("PROJECTION_MISSING_ORDERS", Severity.DEGRADED, {
            "count": snapshot.orders_without_projection, "orders": list(snapshot.orders_without_projection_ids),
        }))
    groups = classify_order_errors(_order_errors(snapshot))
    infrastructure = [g for g in groups if g.category is ErrorCategory.INFRASTRUCTURE]
    programming = [g for g in groups if g.category is ErrorCategory.PROGRAMMING]
    if infrastructure:
        causes.append(Cause("INFRASTRUCTURE_ERRORS", Severity.DEGRADED, {"groups": infrastructure}))
    if programming:
        causes.append(Cause("ORDER_ERRORS", Severity.DEGRADED, {"groups": programming}))
    frozen_total = snapshot.frozen_orders + snapshot.orders_without_projection
    if frozen_total:
        causes.append(Cause("FROZEN_ORDERS", Severity.DEGRADED, {
            "count": frozen_total, "frozen_projections": snapshot.frozen_orders,
            "without_projection": snapshot.orders_without_projection,
        }))
    eod = snapshot.last_end_of_day
    if eod is not None and eod.status == "COMPLETED" and eod.detail.get("not_evaluated"):
        reasons = Counter(eod.detail["not_evaluated"].values())
        causes.append(Cause("QUALITY_NOT_EVALUATED", Severity.DEGRADED,
                            {"session_day": eod.detail.get("session_day"), "reasons": dict(sorted(reasons.items()))}))
    opening = snapshot.last_opening
    if opening is not None and (opening.status == "FAILED" or opening.detail.get("source_failures")):
        causes.append(Cause("OPENING_SOURCE_FAILURES", Severity.DEGRADED, {
            "run_id": opening.run_id, "status": opening.status,
            "source_failures": opening.detail.get("source_failures", {}), "error": opening.detail.get("error"),
        }))
    if snapshot.needs_review:
        causes.append(Cause("NEEDS_REVIEW_QUEUE", Severity.INFO,
                            {"count": sum(snapshot.needs_review.values()), "reasons": snapshot.needs_review}))
    unverifiable = snapshot.actionability.get(ACTIONABILITY_UNVERIFIABLE, 0)
    if unverifiable:
        causes.append(Cause(ACTIONABILITY_UNVERIFIABLE, Severity.INFO, {"count": unverifiable}))

    causes.sort(key=lambda cause: _RANK[cause.severity])
    worst = min((_RANK[c.severity] for c in causes), default=_RANK[Severity.INFO])
    state = {0: HealthState.UNHEALTHY, 1: HealthState.DEGRADED}.get(worst, HealthState.HEALTHY)
    return HealthReport(state, tuple(causes), snapshot)


def database_unavailable(error: Exception) -> HealthReport:
    return HealthReport(HealthState.UNHEALTHY,
                        (Cause("DATABASE_UNAVAILABLE", Severity.UNHEALTHY, {"error": type(error).__name__}),), None)


def schema_not_at_head(found: str | None) -> HealthReport:
    return HealthReport(HealthState.UNHEALTHY, (Cause("SCHEMA_NOT_AT_HEAD", Severity.UNHEALTHY, {
        "expected": EXPECTED_SCHEMA_REVISION, "found": found,
    }),), None)


_RUNS = text(
    """
    SELECT r.run_id, r.kind, r.started_at, r.data_as_of, latest.status, latest.detail
    FROM evaluation_runs r
    JOIN LATERAL (
        SELECT status, detail FROM evaluation_run_status s WHERE s.run_id = r.run_id ORDER BY s.id DESC LIMIT 1
    ) latest ON true
    WHERE r.kind = :kind
    ORDER BY r.started_at DESC, r.run_id
    LIMIT :limit
    """
)

_REVIEW_REASONS = text(
    """
    SELECT split_part(reason, ':', 1) AS reason, COUNT(DISTINCT st.order_id) AS orders
    FROM order_state st JOIN orders o ON o.id = st.order_id,
         jsonb_array_elements_text(st.state_document->'review_reasons') AS reason
    WHERE st.needs_review AND NOT o.replay
    GROUP BY 1 ORDER BY 1
    """
)

_ORDERS_WITHOUT_PROJECTION = (
    select(orders.c.id)
    .select_from(orders.outerjoin(order_state, order_state.c.order_id == orders.c.id))
    .where(orders.c.replay.is_(False), order_state.c.order_id.is_(None))
    .order_by(orders.c.created_at, orders.c.id)
)


def _runs(conn: Connection, kind: str, limit: int) -> tuple[RunSummary, ...]:
    return tuple(
        RunSummary(str(row.run_id), row.kind, row.status, row.started_at, row.data_as_of, dict(row.detail))
        for row in conn.execute(_RUNS, {"kind": kind, "limit": limit})
    )


def schema_revision(conn: Connection) -> str | None:
    if conn.execute(text("SELECT to_regclass('alembic_version')::text")).scalar_one() is None:
        return None
    versions = sorted(str(v) for v in conn.execute(text("SELECT version_num FROM alembic_version")).scalars())
    return ",".join(versions) or None


def collect_health_snapshot(conn: Connection, *, now: datetime) -> HealthSnapshot:
    since = now - INCIDENT_WINDOW
    end_of_day = _runs(conn, "END_OF_DAY", 1)
    opening = _runs(conn, "OPENING", 1)
    frozen = conn.execute(
        select(func.count()).select_from(order_state.join(orders, orders.c.id == order_state.c.order_id))
        .where(order_state.c.frozen.is_(True), orders.c.replay.is_(False))
    ).scalar_one()
    missing = [str(order_id) for order_id in conn.execute(_ORDERS_WITHOUT_PROJECTION).scalars()]
    return HealthSnapshot(
        now=now,
        session_open_utc=next(
            (s.open_utc for s in calendar_for_window(now, now).sessions if s.open_utc <= now < s.close_utc), None
        ),
        live_runs=_runs(conn, "LIVE", RECENT_LIVE_RUNS),
        last_end_of_day=end_of_day[0] if end_of_day else None,
        last_opening=opening[0] if opening else None,
        latest_ingested_at=conn.execute(select(func.max(bar_batches.c.ingested_at))).scalar_one(),
        incident_groups=tuple(incident_groups(conn, since=since)),
        incidents_total=int(conn.execute(select(func.count()).select_from(integrity_incidents)).scalar_one()),
        frozen_orders=int(frozen),
        orders_without_projection=len(missing),
        orders_without_projection_ids=tuple(missing[:MAX_LISTED_ORDERS]),
        needs_review={row.reason: int(row.orders) for row in conn.execute(_REVIEW_REASONS)},
        actionability=actionability_outcomes(conn, since=since),
    )


def build_health_report(engine: Engine, *, now: datetime, eval_interval_minutes: int) -> HealthReport:
    """Connection errors are DATABASE_UNAVAILABLE; any other exception is a defect and propagates (500)."""
    try:
        with engine.connect() as conn:
            revision = schema_revision(conn)
            if revision != EXPECTED_SCHEMA_REVISION:
                return schema_not_at_head(revision)
            snapshot = collect_health_snapshot(conn, now=now)
    except (OperationalError, InterfaceError) as exc:
        return database_unavailable(exc)
    return evaluate_health(snapshot, eval_interval_minutes=eval_interval_minutes)
```

- [ ] **Step 4: Run rule tests to verify they pass**

Run: `uv run pytest tests/readmodels/test_health_rules.py -q`
Expected: PASS.

- [ ] **Step 5: Write the failing API tests**

`tests/integration/api/test_health_api.py`:

```python
from dataclasses import replace
from uuid import UUID

from alembic import command
from fastapi.testclient import TestClient

from tests.integration.api.conftest import API_KEY, post_json
from tests.integration.conftest import alembic_config
from tests.integration.support import CODE_VERSION, DAY, TICKER, signal_body
from tests.support import et
from virtual_orders.api.app import create_app
from virtual_orders.evaluator.commands import flag_order_review
from virtual_orders.evaluator.cycle import run_live_cycle
from virtual_orders.ledger import errors
from virtual_orders.ledger.errors import LedgerIntegrityError
from virtual_orders.ledger.quarantine import record_incident
from virtual_orders.storage import tables
from virtual_orders.storage.database import make_engine


def health(api):
    response = api.client.get("/health")
    return response.status_code, response.json()


def test_fresh_system_is_healthy_before_and_at_the_open_and_stale_later_in_the_session(api):
    status, body = health(api)
    assert status == 200 and body["state"] == "HEALTHY" and body["causes"] == []
    assert body["facts"]["session_open_utc"] is None and body["facts"]["incidents_total"] == 0
    api.clock.set(et(DAY, "09:31"))
    status, body = health(api)
    assert status == 200 and body["state"] == "HEALTHY"
    assert body["facts"]["session_open_utc"] == et(DAY, "09:30").isoformat()
    api.clock.set(et(DAY, "10:30"))
    status, body = health(api)
    assert status == 200 and body["state"] == "DEGRADED"
    assert [c["code"] for c in body["causes"]] == ["LIVE_CYCLE_STALE"]


def test_three_failed_ingests_degrade_and_review_queue_is_informational(api):
    order_id = UUID(post_json(api.client, "/signals", signal_body()).json()["auto_order_id"])
    flag_order_review(api.services.engine, order_id, reason="MANUAL", ref="look")
    api.bars.failing.add(TICKER)
    for hm in ("10:00", "10:02", "10:04"):
        run_live_cycle(api.services.engine, api.services.gateway, code_version=CODE_VERSION, market_now=et(DAY, hm))
    status, body = health(api)
    assert status == 200 and body["state"] == "DEGRADED"
    causes = {c["code"]: c for c in body["causes"]}
    assert causes["INGEST_FAILURES_CONSECUTIVE"]["detail"] == {"feeds": {"fake_feed:AAPL": 3}}
    assert causes["NEEDS_REVIEW_QUEUE"]["severity"] == "INFO"
    assert causes["NEEDS_REVIEW_QUEUE"]["detail"] == {"count": 1, "reasons": {"MANUAL": 1}}


def test_integrity_incident_degrades_with_aggregated_groups(api):
    order_id = post_json(api.client, "/signals", signal_body()).json()["auto_order_id"]
    with api.services.engine.begin() as conn:
        for _ in range(2):
            record_incident(conn, LedgerIntegrityError(errors.PROJECTION_MISSING, "gone", order_id=UUID(order_id)))
    status, body = health(api)
    assert status == 200 and body["state"] == "DEGRADED"
    incidents = next(c for c in body["causes"] if c["code"] == "INTEGRITY_INCIDENTS")
    assert incidents["severity"] == "DEGRADED" and incidents["detail"]["total"] == 2
    assert incidents["detail"]["groups"][0]["occurrences"] == 2
    assert incidents["detail"]["groups"][0]["affected_orders"] == [order_id]


def test_order_without_projection_is_degraded_and_counted_as_frozen(api):
    order_id = post_json(api.client, "/signals", signal_body()).json()["auto_order_id"]
    with api.services.engine.begin() as conn:
        conn.execute(tables.order_state.delete().where(tables.order_state.c.order_id == UUID(order_id)))
    status, body = health(api)
    assert status == 200 and body["state"] == "DEGRADED"
    causes = {c["code"]: c for c in body["causes"]}
    assert causes["PROJECTION_MISSING_ORDERS"]["detail"] == {"count": 1, "orders": [order_id]}
    assert causes["FROZEN_ORDERS"]["detail"] == {"count": 1, "frozen_projections": 0, "without_projection": 1}


def test_database_unavailable_is_503_without_facts(api):
    unreachable = make_engine("postgresql+psycopg://vo:vo@127.0.0.1:1/unreachable")
    services = replace(api.services, engine=unreachable)
    with TestClient(create_app(services), headers={"X-API-Key": API_KEY}) as client:
        response = client.get("/health")
    unreachable.dispose()
    assert response.status_code == 503
    assert response.json() == {
        "state": "UNHEALTHY",
        "causes": [{"code": "DATABASE_UNAVAILABLE", "severity": "UNHEALTHY", "detail": {"error": "OperationalError"}}],
        "facts": None,
    }


def test_schema_behind_the_migration_head_is_503_without_facts(api, database_url):
    command.downgrade(alembic_config(database_url), "0001")
    status, body = health(api)
    assert status == 503
    assert body == {
        "state": "UNHEALTHY",
        "causes": [{"code": "SCHEMA_NOT_AT_HEAD", "severity": "UNHEALTHY",
                    "detail": {"expected": "0002", "found": "0001"}}],
        "facts": None,
    }
```

Por que esses testes são determinísticos:
- `LIVE_CYCLE_STALE` compara o relógio da API com `detail.market_now` dos runs, nunca com `started_at` (relógio do banco). Em `test_three_failed_ingests…` o relógio da API está em 09:00 ET (`SIGNAL_CREATED_AT`), antes da abertura; `session_open_utc` é `None`, e por isso a causa não entra.
- `flag_review` do núcleo guarda o motivo sem ref (`src/core/fills/v1/engine.py:423`), por isso `{"MANUAL": 1}`.
- `test_schema_behind…` usa o mesmo `database_url` do `engine` do harness (fixtures de função são compartilhadas no teste). O downgrade só troca o CHECK da `0002`, e a base é descartada ao fim.

- [ ] **Step 6: Write the route**

`src/virtual_orders/api/routes/health.py`:

```python
from __future__ import annotations

from fastapi import APIRouter, Response

from virtual_orders.api.deps import ServicesDep
from virtual_orders.api.encoding import json_response
from virtual_orders.readmodels.health import HealthState, build_health_report

router = APIRouter()


@router.get("/health")
def get_health(services: ServicesDep) -> Response:
    report = build_health_report(
        services.engine, now=services.clock(), eval_interval_minutes=services.eval_interval_minutes,
    )
    status = 503 if report.state is HealthState.UNHEALTHY else 200  # UNHEALTHY = database or schema only (D17)
    return json_response({"state": report.state, "causes": report.causes, "facts": report.snapshot}, status)
```

Em `app.py`, importe `health` junto dos outros routers e acrescente `app.include_router(health.router)`.

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/readmodels tests/integration/api tests/test_import_boundaries.py -q && uv run ruff check src tests migrations && uv run mypy`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/virtual_orders/readmodels/health.py src/virtual_orders/api/routes/health.py src/virtual_orders/api/app.py tests/readmodels/test_health_rules.py tests/integration/api/test_health_api.py
git commit -m "feat(api): structured /health with causes, aggregated incidents and classified errors"
```

---

### Task 16: Fechamento — cobertura de fronteiras, documentação, verificação final e revisão do plano inteiro

**Files:**
- Modify: `tests/test_import_boundaries.py` (`test_boundary_scan_covers_the_application_layer`)
- Create: `docs/superpowers/notes/2026-09-13-plan3a-closeout.md`
- Modify: `docs/superpowers/plans/2026-09-13-virtual-order-engine-api.md` (seção final "Encerramento do controlador")

**Interfaces:**
- Consumes: tudo o que as Tasks 1–15 produziram.
- Produces:
  - nota de encerramento no formato das notas do Plano 2: decisões D13–D19 (mantida/alterada — motivo — custo se estiver errada), rulings, achados menores adiados e entradas para o 3B;
  - tag local `plan/virtual-order-engine-api-complete`, criada **somente** se nenhuma revisão estiver aberta.

- [ ] **Step 1: Fix the boundary coverage to the real files**

Em `test_boundary_scan_covers_the_application_layer`, acrescente:

```python
    api = {_rel(p) for p in _api_files()}
    assert {
        "virtual_orders/api/app.py", "virtual_orders/api/intake.py", "virtual_orders/api/routes/health.py",
        "virtual_orders/api/routes/replay.py",
    } <= api
    neutral = {_rel(p) for p in _neutral_files()}
    assert {"virtual_orders/readmodels/health.py", "virtual_orders/readmodels/metrics.py"} <= neutral
    assert all((SRC / relative).exists() for relative in APPLICATION_NEUTRAL)
    assert (SRC / COMPOSITION_ROOT).exists()
    assert "virtual_orders.marketdata.alpaca" in _imported_modules(SRC / COMPOSITION_ROOT)
```

Run: `uv run pytest tests/test_import_boundaries.py -q`
Expected: PASS.

Commit:

```bash
git add tests/test_import_boundaries.py
git commit -m "test(boundaries): pin application-layer coverage to the real modules"
```

- [ ] **Step 2: Final verification from zero**

Run, em sequência e na mesma sessão de shell (o `BASE` precisa sobreviver entre os comandos), e registre as saídas no ledger:

```bash
docker compose -f docker-compose.test.yml down -v
docker compose -f docker-compose.test.yml up -d --wait
uv run pytest -p no:cacheprovider -o addopts="" -q
uv run ruff check src tests migrations
uv run mypy
git diff --stat plan/virtual-order-engine-core-complete -- src/core
git status --porcelain -- src/core
if git merge-base --is-ancestor plan-2-persistence HEAD && ! git merge-base --is-ancestor plan-2-persistence main; then
  BASE=plan-2-persistence
else
  BASE=$(git merge-base main HEAD)
fi
echo "BASE=$BASE"
git log --format='%an <%ae>%n%B' "$BASE"..HEAD | grep -E '^(Co-Authored-By|Claude-Session):' || echo "no AI trailers"
git log --format='%an <%ae>' "$BASE"..HEAD | sort -u
```

O `BASE` é `plan-2-persistence` enquanto o branch não tiver sido rebaseado sobre um `main` que contenha o Plano 2; depois do rebase, é o merge-base com `main`.

Expected:
- Suíte inteira verde, sem skips, com os `N_BASE` testes herdados (Task 0) e os novos.
- ruff e mypy limpos.
- Os dois comandos de `src/core` vazios.
- `no AI trailers`.
- Um único autor: `Ricardo Carneiro <132141856+ricdomingues@users.noreply.github.com>`.

- [ ] **Step 3: Smoke the ASGI factory without network**

O `env -i` garante que nenhuma variável do shell do host (por exemplo `DEFAULT_RISK_AMOUNT` ou `PRICE_SOURCE`) entre na composição.

```bash
env -i PATH="$PATH" HOME="$HOME" \
  API_KEY=x DATABASE_URL=postgresql+psycopg://vo:vo@127.0.0.1:55432/postgres ALPACA_API_KEY=a ALPACA_SECRET_KEY=s FMP_API_KEY=f \
  uv run python -c "from virtual_orders.bootstrap import app_from_environment; app = app_from_environment(); print(sorted((sorted(r.methods)[0], r.path) for r in app.routes)); app.state.services.close()"
```

Expected: a lista de pares método–rota (cada `APIRoute` tem um único método, e `/signals` aparece duas vezes):

```
[('GET', '/health'), ('GET', '/metrics'), ('GET', '/orders'), ('GET', '/orders/{order_id}'), ('GET', '/signals'), ('POST', '/orders/{order_id}/cancel'), ('POST', '/replay'), ('POST', '/signals'), ('POST', '/signals/{signal_id}/orders')]
```

Nenhuma chamada de rede ocorre na construção.

- [ ] **Step 4: Whole-plan review**

- Despache a revisão final de branch inteiro (`requesting-code-review`), com o modelo mais capaz, sobre `$BASE..HEAD` (o `BASE` do Step 2).
- Entregue ao revisor: a spec, este plano e as Global Constraints.
- Peça verificação explícita de:
  - (a) D13–D19;
  - (b) nenhum segredo em respostas, `repr` e run details;
  - (c) fronteiras;
  - (d) nenhuma mudança de semântica de fill;
  - (e) códigos HTTP contra a spec 5.1 e 6, e a tabela de códigos da D19;
  - (f) `/health` só em 503 para banco ou schema (D17).
- Achados viram **uma** rodada de correção seguida de uma re-revisão com escopo restrito. Resíduos são adjudicados no ledger (`Ruling: … — … — …`).

- [ ] **Step 5: Write the close-out note**

`docs/superpowers/notes/2026-09-13-plan3a-closeout.md`, com as seções:
- **Cabeçalho:**
  - Plano, Spec e Intervalo (`BASE` do Step 2 até a ponta do branch, nomeando qual base valeu);
  - Testes: contagem medida no Step 2;
  - Núcleo congelado: comando e resultado.
- **Critérios de aceite** (tabela Critério | Teste | Resultado), cobrindo:
  - autenticação em todas as rotas: `test_every_route_rejects_a_missing_or_wrong_key`;
  - envelope de erro em 404/405/500: `test_docs_unknown_routes_and_wrong_methods_use_the_error_envelope`, `test_unhandled_errors_are_500_without_the_message`;
  - JSON `Decimal`: `test_body_numbers_are_read_as_decimal_never_float`, `test_integer_and_decimal_spellings_of_a_price_are_the_same_submission`;
  - divergência de `REPRODUCE` em 409 com diff: `test_reproduce_divergence_is_409_with_the_diff_per_order`;
  - ticker negociável: `test_untradable_ticker_is_422…`, `test_ticker_check_unavailable_is_503…`;
  - composição sem fallback: `test_composition_pins_the_configured_price_source_without_fallback`, `test_unknown_price_source_fails_at_startup`;
  - somente a raiz de composição importa adapters: `test_only_the_composition_root_imports_provider_adapters`;
  - endurecimentos 9–14: testes das Tasks 2–6;
  - `/health` estruturado (15, 17), 503 só para banco ou schema: `test_health_rules`, `test_health_api`;
  - agregação (18, 19): `test_incident_groups`, `test_incidents`;
  - Planos 1 e 2 verdes: suíte completa.
- **Decisões D13–D19:** uma linha cada.
- **Rulings durante a execução**, incluindo os já fixados na revisão do plano: sem deduplicação de incidentes na escrita (D18); `Services.clock` só para o instante de mercado, `started_at`/`data_as_of` no relógio do banco (D19).
- **Achados menores adiados.**
- **Notas para a revisão do 3A:** `Services.dividend_primary`, `dividend_secondary`, `split_source` e `reference` existem para o 3B e não são código morto da API (Task 10).
- **Entradas para o Plano 3B**, no mínimo:
  1. agendar `run_live_cycle` (a cada `EVAL_INTERVAL_MINUTES`, 09:30–16:05 ET), `run_opening` (09:25 ET) e `run_end_of_day` (16:30 ET) com `Services`, usando `Services.clock` só para `market_now`/`now`;
  2. guardar com `require_aware` as entradas `evaluate_order(market_now=)`, `finalize_validity(now=)` e `expire_due_orders(now=)` antes de agendá-las (ruling da Task 3);
  3. webhook `N8N_WEBHOOK_URL` com resumo de fim de dia e alerta quando `/health` ficar `DEGRADED` ou `UNHEALTHY`;
  4. alertas do responsável (2026-09-13), só pelo webhook n8n: eventos de ordem, preço cruzando níveis do usuário em tickers da watchlist, limiar de "pressão forte" rotulado como estimativa;
  5. `DATA_QUALITY_RECHECK` (entrada 16) consumindo `QUALITY_NOT_EVALUATED`;
  6. spec 6, "yfinance/FMP indisponíveis → `NEEDS_REVIEW` com motivo": quando **as duas** consultas de dividendo levantarem (não quando ambas voltarem vazias), marcar as ordens candidatas com revisão `DIVIDEND_UNVERIFIED` e ref da ex-date. Hoje a D16 só registra a falha em `source_failures` do run `OPENING`;
  7. comando `rebuild-projections`.

Commit:

```bash
git add docs/superpowers/notes/2026-09-13-plan3a-closeout.md docs/superpowers/plans/2026-09-13-virtual-order-engine-api.md
git commit -m "docs: Plan 3A close-out"
```

- [ ] **Step 6: Tag (only with no open review)**

Só se o ledger não tiver nenhuma revisão aberta, nenhum achado bloqueante sem ruling e o Step 2 estiver verde **depois** da última correção:

```bash
git tag -a plan/virtual-order-engine-api-complete -m "Plan 3A — configuration, composition, API and hardening COMPLETE"
```

**Não crie a tag se houver qualquer revisão aberta.** Não faça push da tag nem do branch sem pedido explícito do responsável: a publicação segue o mesmo fluxo do PR #1.

---

## Critérios de aceite do Plano 3A

| # | Critério | Onde |
|---|---|---|
| 1 | Toda rota exige `X-API-Key`; docs e OpenAPI desligados | Task 11 |
| 2 | `POST /signals`: 201/200/409/422 conforme a spec 3.3; ticker negociável checado só para sinal novo; 503 `TICKER_UNVERIFIABLE` sem gravação | Task 11 |
| 3 | `POST /signals/{id}/orders`: 201; 422 `SIGNAL_EXPIRED`; 422 `SIGNAL_NO_LONGER_ACTIONABLE` com `reason`; 503 `ACTIONABILITY_UNVERIFIABLE` | Task 12 |
| 4 | `POST /orders/{id}/cancel`: 200; 409 final; 409 replay; 404 | Tasks 4, 12 |
| 5 | `POST /replay`: validação 422 antes de qualquer run; sem replay de replay; overrides validados; `REPRODUCE` divergente → 409 `REPRODUCE_DIVERGED` com diff por ordem | Tasks 5, 7, 12 |
| 6 | `GET /orders`, `GET /orders/{id}` com eventos, segmentos, qualidade e candles as-of | Task 13 |
| 7 | `GET /metrics` com política de revisão explícita, `replay` e `group_by` | Task 14 |
| 8 | `/health` com `HEALTHY`/`DEGRADED`/`UNHEALTHY`, causas, incidentes agregados e erros classificados; 503 só com banco inacessível ou schema fora da head; ordens sem projeção contam como congeladas | Tasks 7, 15 |
| 9 | Configuração pela spec 8, sem vazar segredo; `PRICE_SOURCE` desconhecido falha no startup | Tasks 8, 10 |
| 10 | Só `bootstrap.py` importa adapters; a API não importa adapters nem provider libs | Tasks 1, 16 |
| 11 | Endurecimentos 9–14 com testes (paginação, naive, replay somente leitura, validação de overrides, `_recalculate_order`, run `OPENING`) | Tasks 2–6 |
| 12 | `src/core` intocado; os `N_BASE` testes dos Planos 1 e 2 (medidos na Task 0) verdes; migrations do zero verdes | Tasks 0, 16 |
| 13 | Commits com identidade noreply e sem trailers de IA | Task 16 |
| 14 | Toda resposta de erro no envelope único, inclusive 404/405/500; `100` e `100.0` são o mesmo sinal | Task 11 |

## Encerramento do controlador

- **Modelo de execução:** subagent-driven, com modelo escolhido por task para economizar tokens. Implementadores em haiku: T1, T2, T4, T8 e T16-Step1 (tasks pequenas com código completo no brief, sem banco ou de baixo risco). Implementadores em sonnet: T3, T5–T7, T9–T15 (tasks multi-arquivo/integração). Revisões em opus (alto risco): T11 (app/auth/erros), T12 (replay/manual orders), T15 (`/health`) e a revisão final de branch inteiro. Demais revisões em sonnet; re-revisões em haiku para diffs pequenos de correção, sonnet nas demais.
- **Rodadas de correção:** T3 — 1 rodada (ordem de validação em `create_manual_order`). T15 — 1 rodada (I1 texto de exceção sanitizado na leitura; I2 `connect_timeout` + `TimeoutError` → 503). Onda de correção final (Step 4) — 1 rodada, cobrindo I1 + M1 + M2 + M3 + M8 + filtro dos 2 avisos de terceiros, seguida de re-revisão de escopo restrito (todos os 6 achados ADDRESSED, sem quebra nova).
- **Verificação final (Step 2, do zero, commit `9cb2889`):** 671 testes passando, 0 skips, 0 warnings (`docker compose -f docker-compose.test.yml down -v && up -d --wait`, depois `uv run pytest -p no:cacheprovider -o addopts="" -q`); `ruff check src tests migrations` limpo; `mypy` limpo (79 arquivos); `git diff --stat plan/virtual-order-engine-core-complete -- src/core` e `git status --porcelain -- src/core` vazios; `BASE=plan-2-persistence` (`4937615`); nenhum trailer de IA; autor único (`Ricardo Carneiro <132141856+ricdomingues@users.noreply.github.com>`). Smoke do Step 3 (`app_from_environment`, `env -i`, sem rede) listou as 9 rotas exatamente como o plano previa.
- **Estado da tag:** `plan/virtual-order-engine-api-complete` criada localmente após a verificação, sem push. Nenhuma revisão está aberta e nenhum achado bloqueante ficou sem ruling.
- **Nota de encerramento completa:** `docs/superpowers/notes/2026-09-13-plan3a-closeout.md` (decisões D13–D19, todas as rulings do ledger em prosa, resultado da revisão final, achados menores adiados e descartados, e as entradas 1–14 para o Plano 3B).

