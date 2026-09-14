# Virtual Order Engine — Plano 3C: Dashboard, Portfólio, Compose de Produção e Ponta a Ponta

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Entregar a UI do Virtual Order Engine (dashboard Streamlit + Plotly falando só com a API), as rotas de leitura que ela precisa (candles as-of com volume/VWAP, pressão rotulada, gráfico da ordem, portfólio virtual, slot desligado do portfólio real, observabilidade), o Docker Compose de produção, o teste ponta a ponta pela API e os endurecimentos baratos do encerramento do Plano 3B.

**Architecture:** O núcleo (`src/core/`) continua congelado. Na plataforma (`src/virtual_orders/`) entram só leituras: módulos puros `analytics/vwap.py` e `analytics/portfolio.py`, read models neutros `readmodels/market.py`, `readmodels/portfolio.py` e `readmodels/observability.py`, o contrato neutro `portfolio/sources.py` (sem adapter) e três routers autenticados (`market`, `portfolio`, `observability`). O dashboard é um **projeto `uv` próprio** em `dashboard/` (`dashboard/pyproject.toml` + `dashboard/uv.lock`, pacote `dashboard` em `dashboard/dashboard/`, testes em `dashboard/tests/`), de modo que as dependências da UI nunca mudam o lock do motor (D56). Ele nunca importa `core` nem `virtual_orders`: um `ApiClient` httpx, mapeadores puros de view-model, construtores puros de figuras Plotly e views Streamlit finas, testadas com `httpx.MockTransport`, asserções estruturais nas figuras, `streamlit.testing.v1.AppTest` e fixtures gravadas das respostas reais da API (D57). O Compose de produção usa duas imagens (motor e dashboard, cada uma do próprio lock) e roda `migrate` (one-shot), `api`, `worker` (instância única), `dashboard` e `postgres`.

**Tech Stack:** Python 3.12, `uv`, FastAPI, SQLAlchemy 2 Core + psycopg 3, PostgreSQL 16, Alembic, APScheduler 3.x, httpx, Streamlit (`>=1.40,<2`) e Plotly (`>=5.24,<7`) só no projeto `dashboard/`, PyYAML (só testes do motor), pytest, Hypothesis, ruff 0.8.6, mypy 1.13 strict, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-09-12-virtual-order-engine-design.md` (SPEC v1.2 FROZEN, tag `spec/virtual-order-engine-v1.2`), mais D5–D12 (`docs/superpowers/plans/2026-09-12-virtual-order-engine-persistence.md`), D13–D19 (`docs/superpowers/plans/2026-09-13-virtual-order-engine-api.md`), D20–D39 (`docs/superpowers/plans/2026-09-13-virtual-order-engine-worker.md`) e D40–D59 abaixo.
- **Entradas:** `docs/superpowers/notes/2026-09-13-plan3b-closeout.md`, seção "Entradas para o Plano 3C", itens 1–20 (todos cobertos ou descartados com motivo; tabela de rastreio abaixo). Contexto: `docs/superpowers/notes/2026-09-13-plan3a-closeout.md`.
- **Seções da spec cobertas:** 1.2 item 2 (P&L por 1R, rotulado no portfólio virtual), 2.1 (serviços `api`, `worker`, `dashboard`, `postgres` em Docker Compose; dashboard só pela API), 3.6 (leitura "atual" do dashboard como as-of no instante da requisição), 4.7 (aviso SHORT nas telas), 5.1 (rotas novas no mesmo padrão), 5.4 (cards com IC, exclusões por revisão, risco de sequência), 5.5 (as cinco telas), 7 (ponta a ponta), 8 (variáveis em `.env.example`), 9 (Streamlit, Plotly, Docker Compose).
- **Roadmap (só leitura):** `docs/superpowers/roadmap/2026-09-12-robinhood-mcp-read-only.md`, inclusive "Uso aprovado pelo dono (2026-09-13): portfólio real no dashboard". A Fase 0 não tem evidência: o 3C entrega só o contrato neutro, o slot desligado e o modelo de relatório (D46).

## Sequência do Plano 3

| Plano | Conteúdo | Estado |
|---|---|---|
| 3A | Configuração, composição, API, `/health` estruturado, endurecimentos | concluído (`plan/virtual-order-engine-api-complete`) |
| 3B | Worker, `rebuild-projections`, recheck, alertas n8n, watchlist, pressão, endurecimentos | concluído (`plan/virtual-order-engine-worker-complete`) |
| **3C (este)** | Dashboard, rotas de leitura da UI, portfólio virtual + slot do real, Compose de produção, ponta a ponta pela API, endurecimentos do 3B | — |

## Fora deste plano

- **Robinhood:** nenhum adapter, nenhuma chamada MCP, nenhuma credencial, nenhuma variável de ambiente. Só o contrato `PortfolioSource`, `Services.portfolio_source = None` e o modelo do relatório da Fase 0 (D46).
- **Não muda:** `src/core/`, regras de fill, `fill_model v1`, `EventType`, semântica de `DATA_QUALITY` (D4), D12, contratos das 14 rotas existentes (as rotas novas são aditivas).
- **Adiado para a observação em paper (D55):** colapso de revisões da D33 (entrada 6), corte de frescor do burst de cruzamentos (entrada 10), itens do T12 (entrada 17), desigualdade `*/N` e `shutdown(wait=False)` da D39 (parte da entrada 19), role de banco separado para a aplicação no Compose (desvio registrado da spec 6).
- **Limitação conhecida (D59):** o Streamlit não tem autenticação própria; o dashboard fica em `127.0.0.1` e o acesso remoto é por túnel SSH até o endurecimento de VPS.

Base: branch `plan-3c-dashboard`, criado na ponta de `plan-3b-worker` (`0fd2707`).
- Intervalo de commits: `plan-3b-worker..HEAD` enquanto o branch não for rebaseado sobre um `main` que contenha o 3B; depois, `$(git merge-base main HEAD)..HEAD`. A Task 15 registra no ledger qual base valeu.

## Rastreio das entradas do encerramento do 3B

| Entrada | Conteúdo | Onde |
|---|---|---|
| 1 | Compose de produção: `api`, `worker` único com `restart` e `stop_grace_period ≥ 120 s`, `dashboard`, `postgres`; healthcheck do `api` | Task 14 (D47, D48) |
| 2 | Rotas de candles as-of com volume/VWAP e sobreposição de ordens; pressão sempre com `method` e `DISCLAIMER` | Tasks 6, 7, 12, 13 (D41–D44) |
| 3 | Telas da watchlist e das regras | Task 13 |
| 4 | Causas `INFO` `UNDELIVERABLE_ALERTS`/`ORDER_EVENT_ALERTS_BEHIND`, alertas `EXPIRED`, último `health_state_log` | Tasks 9, 12, 13 |
| 5 | Recuperação de jobs perdidos (D38): CLI ou decisão manual explícita | D53 (manual, documentada no runbook da Task 14) |
| 6 | Rever o colapso da D33 depois da observação em paper | Descartado para o 3C (D55): depende de dados reais de paper |
| 7 | Portfólio virtual; portfólio real condicionado à Fase 0 | Tasks 6, 8, 13 (D45, D46) |
| 8 | `data_quality.rechecks` no detalhe da ordem (`status`, `terminal_reason`, `level_touch_check`) | Tasks 12, 13 |
| 9 | Pendências `PROVIDER_FAILURE` saindo de `/health` sem linha terminal | Task 5 (D52: linha terminal + `NEEDS_REVIEW` `DATA_QUALITY_UNVERIFIED`) |
| 10 | Burst de cruzamentos ao ligar o webhook | Descartado para o 3C (D55): depende de dados reais de paper |
| 11 | M1 — `ORDER_EVENT_ALERTS_BEHIND` sem idade máxima | Task 3 (D50) |
| 12 | M2 — keepalive/timeout da conexão do lock | Task 2 (D49) |
| 13 | M3 — janelas duplicadas sem teste de igualdade | Task 3 (D50) |
| 14 | M4 — worker sem banco na subida sai com traceback | Task 2 (D49) |
| 15 | T9 — `_JOB_RUNS` sem limite | Task 3 (D50) |
| 16 | T10 — `UNIQUE(order_id, session_date)`; lacunas de teste do recheck | Task 5 (D52): migration `0004`, teste da corrida `ALREADY_EVALUATED` com o `END_OF_DAY` e teste unitário de `levels_state_at_close` contra um histórico sintético diferente da projeção (D55 explica por que o caso não é construível no nível de integração no v1) |
| 17 | T12 — lista branca do outbox, varreduras sem limite, entregadores concorrentes, lacunas de teste | Descartado para o 3C (D55) |
| 18 | T13 — exceção inesperada de ingestão aborta a watchlist | Task 4 (D51) |
| 19 | T16 — `shutdown(wait=False)`, ramo "vivo com lock perdido" sem teste, cadência `*/N` | Task 2 (teste do ramo, sincronização da flag `stopping`, D49); o resto descartado (D55) |
| 20 | Erro de escopo do `conftest` ao misturar arquivos | Task 1 (D55): `MutableClock`, `FakeTickerCheck`, `API_KEY`, `json_text` e `post_json` saem de `tests/integration/api/conftest.py` para `tests/integration/support.py`; nenhum módulo importa um `conftest` (teste de fronteira) e um comando de regressão mistura diretórios |

## Global Constraints

### Núcleo, plataforma e dados

- Python **3.12**. Dependências entram via `uv add`; testes rodam com `uv run pytest` (sem `-q` extra: o `pyproject.toml` já tem `-q` em `addopts`).
- **Dois projetos `uv` (D56):** o motor na raiz (`pyproject.toml` + `uv.lock`) e o dashboard em `dashboard/` (`dashboard/pyproject.toml` + `dashboard/uv.lock`). Streamlit e Plotly **nunca** entram no `uv.lock` da raiz. Todo comando do dashboard roda com `uv run --directory dashboard …` (equivale a `cd dashboard && uv run …`: `uv` e as ferramentas leem a configuração de `dashboard/pyproject.toml`). Se o `uv.lock` da raiz mudar de versão em algum pacote existente, **pare e reporte**; a única mudança permitida é a adição do `pyyaml` na Task 14, verificada por diff do export.
- **`src/core/` está congelado.** Nenhum arquivo em `src/core/` é criado, editado ou removido. Se um teste exigir mudar a semântica do `fill_model v1` ou de qualquer módulo de `src/core/`, **pare e reporte ao responsável**. O encerramento exige `git diff plan/virtual-order-engine-core-complete -- src/core` vazio.
- Continuam valendo as Global Constraints dos Planos 2, 3A e 3B: market data neutra; sem fallback de provider; `Decimal` para preço, volume e dinheiro; `datetime` timezone-aware em UTC; proveniência por `bar_batches` e leitura as-of; nenhum teste acessa a rede; Postgres real em `127.0.0.1:55432` (`docker compose -f docker-compose.test.yml up -d --wait`); relógio (D19, D21): `Services.clock` só para o instante de mercado.
- **Rotas de leitura novas nunca chamam provider:** leem só `bars_1m`/`bar_batches` já gravados, as-of `acquire_data_as_of` (D41). Nenhuma rota nova grava run, evento ou linha de histórico.
- **SQL parametrizado:** `text()` com binds ou SQLAlchemy Core. Nenhum valor de requisição entra em SQL por interpolação de string.

### Fronteiras de import (verificadas por `tests/test_import_boundaries.py`)

- Só `virtual_orders/bootstrap.py` importa adapters (`virtual_orders.marketdata.{alpaca,fmp,yfinance_source,http}`, `virtual_orders.notify.n8n`). Não existe adapter da Robinhood nem cliente MCP.
- **`dashboard/dashboard/**` e `dashboard/tests/**` só importam a biblioteca padrão, `httpx`, `streamlit`, `plotly`, `pytest` (só testes) e o próprio `dashboard`.** Nunca `core`, `virtual_orders`, `tests` (o da raiz), `sqlalchemy`, `psycopg`, `alembic`, `yfinance`, `pandas_market_calendars`, `apscheduler`, `fastapi`, `starlette` nem `uvicorn` (spec 2.1). A varredura nunca entra em `dashboard/.venv`.
- `src/**` e `tests/**` da raiz nunca importam `dashboard`, `streamlit` nem `plotly`; o contrato entre API e dashboard passa por fixtures JSON gravadas (D57).
- Nenhum módulo importa um `conftest` (entrada 20): helpers compartilhados ficam em `tests/integration/support.py`.
- Pacotes neutros (`storage`, `ledger`, `evaluator`, `readmodels`, `alerts`, `analytics`, `portfolio` e os módulos neutros de `marketdata`) nunca importam adapters, provider libs, `virtual_orders.api`, `virtual_orders.bootstrap`, `virtual_orders.config`, `virtual_orders.worker`, `fastapi`, `starlette` nem `uvicorn`.
- Módulos puros (`analytics/pressure.py`, `alerts/rules.py`, `analytics/vwap.py`, `analytics/portfolio.py`) não importam bibliotecas de I/O nem pacotes de infraestrutura de `virtual_orders`.

### Segurança, HTTP e UI

- Segredos (`API_KEY`, `ALPACA_*`, `FMP_API_KEY`, `DATABASE_URL`, `N8N_WEBHOOK_URL`, `POSTGRES_PASSWORD`) nunca aparecem em `repr`, mensagens de erro, respostas HTTP, payloads de webhook, erros renderizados no dashboard, `docker-compose.yml`, `Dockerfile` ou `.env.example` (só placeholders `change-me`).
- **Nenhum texto de exceção** em respostas HTTP, payloads de webhook ou erros renderizados no dashboard: só códigos fixos do envelope ou `type(exc).__name__`. O Streamlit roda com `--client.showErrorDetails none` no Compose (valor sondado na versão instalada na Task 1; D58).
- Rotas novas usam o app, a autenticação `X-API-Key` no nível do app, o envelope de erro e `json_response` do 3A. Nenhuma rota sem chave (D48).
- **Portfólio real e virtual nunca somados:** views, totais e rótulos separados (D45, D46).
- **Pressão sempre rotulada:** toda resposta e toda tela de pressão exibem `estimate: true`/"estimativa", `method` e `disclaimer` (D27, D37, D44).
- No dashboard, valores vêm da API como string decimal ou `Decimal` (JSON lido com `parse_float=Decimal`); `float` só na hora de entregar números ao Plotly para desenhar.

### Qualidade e commits

- Motor: `uv run ruff check src tests migrations` e `uv run mypy` limpos (a configuração da raiz não muda). Dashboard: `uv run --directory dashboard ruff check .` e `uv run --directory dashboard mypy` limpos, com o pacote `dashboard` em mypy strict. Sem entradas em `per-file-ignores`. O único override de mypy do dashboard é `ignore_missing_imports` para `plotly` (biblioteca de terceiros sem stubs).
- Migrations verificadas do zero (`test_migrations_run_from_zero_and_back` + banco novo + `test_downgrade_to_0003_and_back`).
- Testes com Postgres ficam em `tests/integration/` (marcados automaticamente). Testes do Compose são estáticos (YAML/texto): a suíte **não** exige o binário `docker` além do Postgres de teste.
- Identificadores de código em inglês; prosa do plano e textos da UI em português; mensagens de commit em inglês.
- **Commits:** identidade repo-local `Ricardo Carneiro <132141856+ricdomingues@users.noreply.github.com>`. **Nenhum** trailer `Co-Authored-By:` ou `Claude-Session:` (também para subagentes). Nunca alterar configuração global do git. Nenhum push sem pedido explícito do responsável.

## Decisões deste plano (a spec v1.2 não é alterada)

- **D40 — Dashboard só por HTTP.**
  - Projeto `dashboard/` (D56) com o pacote `dashboard/dashboard/`: `client.py` (`ApiClient` httpx com `X-API-Key`), `viewmodels.py` (mapeadores puros), `charts.py` (figuras Plotly puras), `views/*.py` (Streamlit fino) e `app.py` (entrada `uv run --directory dashboard streamlit run dashboard/app.py`).
  - Ticker digitado na watchlist passa por `watchlist_ticker`: vazio, com espaço ou com `/` vira `InputError` antes de qualquer chamada. O servidor decodifica `%2F` antes do roteamento, então `/watchlist/BRK%2FB` nunca chegaria à rota; o cliente continua codificando o segmento por defesa, e a API não muda (M7).
  - Um `httpx.Client` por sessão do navegador, guardado em `st.session_state`, sem `close()` explícito: aceito para um único responsável com poucas sessões; o coletor fecha o cliente com a sessão (M13).
  - A curva de R acumulado usa `GET /orders?status=CLOSED&limit=1000`; com 1000 linhas a tela mostra a legenda fixa de truncamento, porque as ordens mais antigas são as que ficam de fora (D59).
  - Configuração só por ambiente: `DASHBOARD_API_URL` e `API_KEY`. Faltando, a tela mostra só os nomes (`MISSING:<VAR>`).
  - Navegação por `st.sidebar.radio` numa única entrada, e não multipage (`pages/` ou `st.navigation`): o `AppTest` exercita uma entrada só, e uma pasta `pages/` ao lado do script viraria multipage automaticamente. Por isso as views ficam em `views/`.
  - Erros: toda chamada da view passa por `guarded`, que mostra `describe_api_error` (mensagem fixa em português por código, `reason` mapeado, códigos de `detail.errors` e, para transporte, só o tipo da exceção).
  - O cliente injetado em `st.session_state["api_client"]` tem precedência sobre o ambiente, o que permite os testes com `AppTest`.
- **D41 — Leituras "atuais" do dashboard (spec 3.6).** Candles, pressão e marcação do portfólio leem as-of um `data_as_of` obtido por `acquire_data_as_of` no instante da requisição. A resposta devolve esse `data_as_of`. Nada disso cria run, evento ou snapshot, e nenhuma rota nova chama provider.
- **D42 — Rotas novas (aditivas; as 14 existentes ficam como estão).**

  | Rota | Resposta | Erros |
  |---|---|---|
  | `GET /market/bars?ticker&from&to&source?` | `{ticker, price_source, data_as_of, start, end, vwap_method, bars, vwap}` | `422 TICKER_INVALID`; `422 MARKET_REQUEST_INVALID` (`NAIVE_DATETIME:from`, `NAIVE_DATETIME:to`, `EMPTY_WINDOW`, `WINDOW_TOO_LARGE` > 7 dias); `422 REQUEST_INVALID` |
  | `GET /market/pressure?ticker&window_bars=30&cmf_threshold?&source?` | `{ticker, price_source, data_as_of, window_bars, estimate: true, method, disclaimer, available, reason, values, cmf_threshold, side}` | `422 MARKET_REQUEST_INVALID` (`OUT_OF_RANGE:window_bars` fora de 5..390, `OUT_OF_RANGE:cmf_threshold` fora de (0, 1)) |
  | `GET /orders/{order_id}/chart` | `{order_id, ticker, direction, strategy, origin, status, price_source, replay, replay_of_order_id, fill_model_version, config_snapshot, evaluation_start_ts, valid_until_ts, levels, markers, window, window_truncated}` | `404 ORDER_NOT_FOUND` |
  | `GET /portfolio/virtual` | `{kind: "VIRTUAL", data_as_of, portfolio}` | — |
  | `GET /portfolio/real` | `{kind: "REAL", available, reason, source, positions}` (+ `error` com o tipo, se a fonte falhar) | — |
  | `GET /health/log?limit=20` | `{entries: [{id, state, cause_codes, observed_at}]}` (mais recente primeiro) | `422 REQUEST_INVALID` (limite 1..500) |
  | `GET /alert-outbox?outcome?&limit=100` | `{alerts: [{id, alert_key, kind, subject, subject_ts, created_at, failures, last_outcome, last_status_code, last_error_type, last_attempted_at}]}` sem `document` | `422 REQUEST_INVALID` (`outcome` fora de `DELIVERED`/`FAILED`/`EXPIRED`/`PENDING`) |
  | `GET /quality/overview?limit=100` | `{window_days: 14, coverage: [{session_date, orders, expected_bars, missing_bars, coverage_pct}], data_gaps: [...]}` | `422 REQUEST_INVALID` |

  - `levels` = `entry_zone_low`, `entry_zone_high`, `stop`, `target1`, `target2`, `trigger_price` (do sinal), `avg_entry` e `stop_current` (da projeção).
  - `markers` = eventos com `bar_ts`, mais `DATA_GAP` no `gap_start_ts` do payload.
  - `window` = `{from: 00:00 ET do dia de evaluation_start_ts, to: min(final_event_ts ou last_bar_ts ou evaluation_start_ts + 1 min, from + 7 dias)}`; `window_truncated` é `true` quando o limite de 7 dias cortou o fim, e a tela de ordens mostra uma legenda (M10).
  - `/quality/overview` soma `expected_bars`/`missing_bars` **por ordem** (minutos-ordem): duas ordens do mesmo ticker contam o mesmo minuto duas vezes. A tela rotula as colunas como "minutos-ordem" (M8).
  - `PENDING` no outbox = sem tentativa ou última tentativa `FAILED`.
  - `/alert-outbox` nunca devolve `document`, porque os documentos carregam payloads de evento; `last_error_type` já guarda só o tipo da exceção (D24).
- **D43 — VWAP de sessão para gráficos.** `SESSION_VWAP_TYPICAL_PRICE_V1`: acumulado `Σ((H+L+C)/3 · volume) / Σ volume`, reiniciado a cada data ET, com o fechamento enquanto o volume acumulado for zero. Quantizado em 4 casas `ROUND_HALF_EVEN`. A rota lê desde 00:00 ET do dia de `from` para ancorar na abertura e só depois recorta a janela pedida. É diferente da VWAP da janela da pressão (D27), e cada resposta nomeia o próprio método.
- **D44 — Pressão exibida.**
  - Janela: os últimos `window_bars` candles (5..390, padrão 30) da data ET do último candle armazenado até `data_as_of`. Com menos candles, `available: false`, `reason: INSUFFICIENT_BARS`. Sem nenhum candle, `NO_BARS`.
  - `side` só existe com `cmf_threshold`, pela mesma regra `strong_pressure` da D37.
  - Toda resposta e toda tela trazem `estimate: true`, `method = OHLCV_PRESSURE_ESTIMATE_V1` e `disclaimer`.
- **D45 — Portfólio virtual (entrada 7).**
  - Posições: ordens não replay com `qty_open > 0` e `avg_entry` definido (`OPEN`/`PARTIAL`, inclusive `frozen`, com a flag exibida).
  - Marcação no último fechamento armazenado as-of (`price_source` da ordem).
  - P&L não realizado: LONG `(close − avg_entry) · qty_open`, SHORT o espelho.
  - `unrealized_r = unrealized_pnl / risk_amount`; `open_r = (realized_pnl − costs + dividends + unrealized_pnl) / risk_amount`.
  - Alocação: `notional / Σ notional · 100`, sobre as posições marcadas.
  - Posição sem candle: marcação `null` e fora dos totais, contada em `unmarked`.
  - Dinheiro em 2 casas e razões em 4, `ROUND_HALF_EVEN`.
  - Base `PER_1R_NORMALIZED` com aviso fixo: não é dinheiro de conta nem o Portfolio Simulator (spec 1.2 item 2); o P&L não realizado é bruto de custos de saída e de slippage de stop, e a marcação pode ser o fechamento de um pregão anterior (a coluna "Candle" mostra o instante). Totais em dólar somam `risk_amount` diferentes e são rotulados "por 1R" (M9).
- **D46 — Portfólio real: só o slot (ruling do controlador).**
  - Contrato neutro `PortfolioSource` (só `list_positions()`, tipado; sem ordens, sem passthrough) e `RealPosition`.
  - `Services.portfolio_source: PortfolioSource | None = None`; `build_services` sempre passa `None`. Não há adapter, chamada MCP, credencial nem variável de ambiente.
  - `GET /portfolio/real` responde `available: false`, `reason: PHASE_0_PENDING`, e o dashboard mostra "Portfólio real indisponível (Fase 0 pendente)."
  - Uma fonte futura que falhe vira `reason: SOURCE_UNAVAILABLE` com `error` só com o tipo.
  - Totais real e virtual nunca são somados.
  - `docs/superpowers/roadmap/2026-09-14-robinhood-phase0-feasibility-report.md` é o modelo do relatório, com os 17 itens em "não verificado".
- **D47 — Compose de produção (entrada 1).**
  - Duas imagens, cada uma do próprio lock (D56): a do motor (`Dockerfile` na raiz, `uv.lock`, sem `dashboard/`) e a do dashboard (`dashboard/Dockerfile`, contexto `dashboard/`, `dashboard/uv.lock`). Ambas com `uv sync --frozen --no-dev`, usuário não root e `GIT_SHA` como build arg.
  - Só `migrate` (imagem do motor) e `dashboard` têm bloco `build`; `api` e `worker` usam a mesma `image:` do `migrate` com `pull_policy: never`, então o Compose constrói a imagem do motor uma única vez (M11). O runbook exporta `GIT_SHA` antes de `build` e de `up`.
  - Serviços:
    - `postgres` (volume `pgdata`, sem porta publicada);
    - `migrate` (`alembic upgrade head`, `restart: "no"`, depois de `postgres` saudável; recebe só `DATABASE_URL` em `environment`, sem `env_file`, por menor privilégio — M12);
    - `api` (`uvicorn virtual_orders.bootstrap:app_from_environment --factory`);
    - `worker` (`python -m virtual_orders.worker run`, `restart: unless-stopped`, `stop_grace_period: 150s`, sem `deploy`/`scale`);
    - `dashboard` (recebe só `DASHBOARD_API_URL` e `API_KEY`; `--client.showErrorDetails none`, D58).
  - `api` e `worker` dependem de `migrate` com `service_completed_successfully`; `dashboard` depende de `api` com `service_started` (D58: com a API fora do ar o operador vê "API indisponível"/`UNHEALTHY` em vez de um dashboard que nunca sobe).
  - Portas publicadas só em `127.0.0.1` (D59).
  - `.env.example` só com placeholders e sem `GIT_SHA`, que vem do build (um `GIT_SHA=unknown` no `env_file` sobrescreveria o `code_version` da imagem). A senha em `DATABASE_URL` precisa ser igual a `POSTGRES_PASSWORD` (runbook).
- **D48 — Healthcheck do `api` com a chave, sem `/livez`.**
  - O healthcheck chama `GET /health` com `X-API-Key` lida de `os.environ['API_KEY']` dentro do contêiner. O valor nunca aparece no `docker-compose.yml`.
  - `200` (`HEALTHY`/`DEGRADED`) é saudável; `503` (banco inacessível ou schema fora da head, D17) é não saudável.
  - Motivo: toda rota exige chave (D19). Um `/livez` sem banco exigiria isentar uma rota da autenticação e não pegaria migração pendente.
  - Custo aceito: consultas de `/health` a cada 30 s, limitadas pelo `statement_timeout` de 5 s (D28). A saúde do `api` fica ligada ao banco; o `dashboard` não espera por ela (D58).
- **D49 — Endurecimentos do worker (entradas 12, 14, 19).**
  - **M4:** banco inacessível na subida (`SQLAlchemyError` em `acquire_worker_lock`) vira uma linha de log com o tipo e o código de saída `EXIT_DATABASE_UNAVAILABLE = 4`; `services.close()` continua no `finally`.
  - **Flag `stopping`:** `ShutdownGuard.claim()` com `threading.Lock().acquire(blocking=False)` é atômico entre a thread do `worker_lock` e o manipulador de sinal. Um manipulador interrompido por outro sinal nunca bloqueia: o segundo só perde a disputa. A classe expõe só `claim()`; os testes verificam que uma segunda chamada devolve `False` (M4).
  - **M2:** `make_engine` passa keepalives do libpq (`keepalives=1`, `keepalives_idle=30`, `keepalives_interval=10`, `keepalives_count=3`, `tcp_user_timeout=60000` ms). Uma sessão meio-aberta falha em cerca de 60 s em vez de travar o `worker_lock`. O libpq ignora `tcp_user_timeout` onde `TCP_USER_TIMEOUT` não existe (macOS): na máquina de desenvolvimento a suíte de integração prova que os parâmetros são aceitos, não que o timeout funciona; o efeito vale no contêiner Linux. Keepalives não limitam um servidor vivo mas bloqueado; esse caso fica fora do escopo (M3).
  - **T16:** teste do ramo "conexão viva, lock liberado".
- **D50 — `/health` e alertas (entradas 11, 13, 15).**
  - **M1:** `ORDER_EVENT_ALERTS_BEHIND` só conta eventos com `recorded_at >= clock_timestamp() − ORDER_EVENT_BEHIND_WINDOW` (7 dias, igual a `UNDELIVERABLE_WINDOW`).
  - **T9:** `missing_job_runs` lê a âncora por agregado próprio (`_JOB_ANCHOR`) e só os runs com `session_day` a partir do primeiro pregão verificado. A semântica fica idêntica.
  - **M3:** teste de paridade (`watch_session == live_session` nas bordas; `WATCH_GRACE == LIVE_WINDOW_GRACE`; fusos duplicados iguais; `END_OF_DAY_FLOOR` = retry das 18:30 + 30 min). Não extrair constantes: módulos neutros não importam o worker. As cópias novas do fuso ET do 3C (`analytics/vwap.py:_MARKET_TZ`, `readmodels/market.py:_MARKET_TZ`) entram no mesmo teste na Task 7 (M2 da revisão).
- **D51 — Watchlist isola exceção inesperada por ticker (T13, entrada 18).** `_ingest` captura `Exception` por ticker e grava `ERROR:<Type>` em `ingest_failures`; os outros tickers seguem e o run fecha `COMPLETED`. O run `FAILED` continua gravando `repr(exc)` como os outros escritores (D19: `/health` sanitiza na leitura).
- **D52 — Recheck: schema e pendências terminais (entradas 9 e 16).**
  - Migration `0004`: `UNIQUE (order_id, session_date)` em `data_quality_rechecks` (constraint `data_quality_rechecks_order_session_key`), removendo o índice não único redundante. Falha alto se já houver duplicatas: histórico append-only nunca é reescrito para caber numa constraint. `EXPECTED_SCHEMA_REVISION = "0004"`.
  - `PROVIDER_FAILURE` num pregão com `RECHECK_FINAL_AFTER_SESSIONS` (5) ou mais pregões fechados depois vira linha terminal `status = "PROVIDER_FAILURE_FINAL"`, `terminal_reason = "PROVIDER_FAILURE"`. Emenda a D22 só no caso final, pelo mesmo motivo do `NO_OBSERVATIONS_FINAL`: a pendência nunca some em silêncio da janela de 20 runs `END_OF_DAY`.
  - **`PROVIDER_FAILURE_FINAL` marca revisão (ruling do controlador, spec 6: "minutos não verificáveis → `NEEDS_REVIEW` com motivo").** Na mesma transação da linha terminal, o comando chama `inp.model.flag_review(state, "DATA_QUALITY_UNVERIFIED", "<pregão ISO>")` (constante `RECHECK_UNVERIFIED_REVIEW`), o mesmo caminho travado de `flag_order_review`/D34.
    - Idempotente por `event_key` `NEEDS_REVIEW:DATA_QUALITY_UNVERIFIED:<pregão>`: `append_events` descarta a mesma chave com o mesmo hash.
    - População igual à do recheck: ordens replay nunca viram pendência (o `_CANDIDATES` do fim de dia filtra `NOT o.replay`, e `apply_command` recusa replay) nem ordens finalizadas antes da abertura do pregão (`final_event_ts >= session_open`). Ordens fechadas depois do pregão são marcadas, como as flags de recheck da D34.
    - Pela política da D34/spec 5.4, a revisão tira a ordem das métricas padrão, inclusive retroativamente; `include_needs_review=true` a traz de volta.
    - Custo se estiver errado: uma ordem cujo feed voltou depois de 5 pregões sai das métricas até revisão manual (conservador; o motivo fica visível na fila `NEEDS_REVIEW` do `/health`).
  - **`NO_OBSERVATIONS_FINAL` continua sem revisão (D12(a)):** zero observações é evidência de pregão não medido (halt, feed sem o ticker), não de candles ausentes. Custo se estiver errado: uma ordem com pregão inteiro sem dados continua nas métricas padrão; a linha terminal e o `terminal_reason` ficam visíveis no detalhe da ordem.
- **D53 — Recuperação de jobs perdidos continua manual (entrada 5).**
  - Sem comando de CLI. Rodar o fim de dia de um pregão passado gravaria `DATA_QUALITY:{pregão}` com `data_as_of` posterior, contra a D4 (fotografia do job de fim de dia) e a D12(b) (só o recém-fechado). A qualidade desses pregões já é coberta pelo `DATA_QUALITY_RECHECK` (D22).
  - Rodar a abertura depois do horário aplicaria dividendos/splits fora da janela da spec 5.3.
  - A validade das ordens é finalizada no fim de dia seguinte.
  - O runbook da Task 14 documenta o que o operador faz diante de `OPENING_MISSING`/`END_OF_DAY_MISSING`.
- **D54 — Ponta a ponta pela API (spec 7, D19).**
  - Um teste de integração usa o harness `api` (app real, Postgres real, fonte falsa gravada `scenario_bars`, relógio controlado) e os jobs reais (`WorkerJobs.live_cycle`/`end_of_day`).
  - Percorre: `POST /signals`, `GET /signals`, `POST /signals/{id}/orders`, `GET /portfolio/virtual` no meio do pregão, eventos e projeção por `GET /orders/{id}`, `GET /orders/{id}/chart`, `GET /market/bars`, `GET /metrics` com valores esperados fixos, `POST /replay` `REPRODUCE` com eventos idênticos (`event_key`, `payload_hash`) e `GET /metrics?replay=true` igual.
  - O teste de ponta a ponta existente, no nível do adapter, continua.
- **D55 — Adiados ou descartados, com motivo.**
  - Entrada 6 (D33) e entrada 10 (burst do webhook): só dados reais de paper dizem se há problema.
  - Entrada 17 (T12):
    - payloads do núcleo já são estruturados;
    - volumes de um único responsável;
    - worker único (D20/D39) contra entregadores concorrentes;
    - as varreduras de candidatos já têm `LIMIT`/lookback.
  - Parte da entrada 19: `shutdown(wait=False)` na perda do lock é o desenho da D39; `*/N` desigual quando `N` não divide 60 já está na D36.
  - Lacunas de teste do T10 (entrada 16) — **cobertas na Task 5**, com a razão real de cada uma:
    - `ALREADY_EVALUATED` é uma corrida com o `END_OF_DAY`, não entre dois rechecks: `pending_quality_sessions` é lido fora do lock da ordem, e o fim de dia pode gravar `DATA_QUALITY:{pregão}` antes de o comando pegar o lock. A constraint da D52 só reforça o ramo separado `ALREADY_RECHECKED`. Teste: `test_a_session_evaluated_by_end_of_day_after_the_pending_read_is_left_alone`.
    - Níveis derivados × níveis da projeção: no `fill_model v1` só o `TARGET1_HIT` move o stop, e a projeção guarda `stop_previous` e `stop_active_from`. Então `active_levels` sobre a projeção dá, em cada minuto passado, o mesmo stop que a derivação do ledger, e o caso não é construível no nível de integração. Teste unitário de `levels_state_at_close` contra um histórico sintético diferente da projeção: `tests/evaluator/test_recheck_levels.py`.
  - Entrada 20 — **corrigida na Task 1**, não descartada. `tests/integration/worker/conftest.py` importava `MutableClock`/`FakeTickerCheck` de `tests.integration.api.conftest`, e os testes importavam `post_json`/`API_KEY` de um `conftest`. Um `conftest` importado como módulo comum pode ser registrado fora de ordem ao misturar diretórios. O erro não se reproduziu no pytest 9.1.1 durante a revisão deste plano (7 combinações mistas); a mudança remove a causa provável e fixa a regra com um teste de fronteira e um comando de regressão.
  - Role de banco sem `UPDATE`/`DELETE` para a aplicação no Compose — **desvio registrado da spec 6** (a spec exige role e triggers): os triggers de append-only já rejeitam mutação para qualquer role (`test_app_role_cannot_update_history`); a separação de credenciais fica para o endurecimento de VPS, e o desvio aparece nas notas de segurança do runbook e nas entradas da nota de encerramento (M14).

- **D56 — Dashboard é um projeto `uv` próprio (ruling do controlador, I1).**
  - `dashboard/pyproject.toml` + `dashboard/uv.lock`; dependências `streamlit>=1.40,<2`, `plotly>=5.24,<7`, `httpx>=0.28`; grupo `dev` com `pytest>=8.0`, `ruff==0.8.6`, `mypy==1.13.0` (as mesmas fixações da raiz). Pacote `dashboard` em `dashboard/dashboard/` (hatchling), testes em `dashboard/tests/`.
  - Motivo: vários releases do Streamlit declaram `pandas<3`, e o lock da raiz fixa `pandas 3.0.5` (usado pelo `pandas_market_calendars` do núcleo congelado e pelos stubs 3). Um `uv add` na raiz poderia rebaixar pandas, numpy, protobuf, click, packaging ou tornado sob o núcleo.
  - A Task 1 verifica `git diff --quiet uv.lock`; a fronteira da raiz afirma que o `uv.lock` da raiz não contém `streamlit` nem `plotly`.
  - Custo aceito: dois `uv sync`, duas imagens e dois conjuntos de comandos de qualidade.
- **D57 — Contrato API × dashboard por fixtures gravadas (ruling do controlador, I5).**
  - Escolha explícita: o teste da raiz **não** importa `dashboard` (o pacote vive em outro projeto e depende de Streamlit/Plotly que a raiz não tem, e o layout `dashboard/dashboard/` não é importável pela raiz).
  - `tests/integration/api/test_dashboard_contract.py` percorre um pregão gravado pelas rotas reais e compara a **forma** (chaves e tipos JSON, com listas vazias, `null` e subárvores `detail`/`payload`/`config_snapshot`/`reasons`/`needs_review`/`missing_runs` como curingas) de cada resposta com `dashboard/tests/fixtures/api/<nome>.json`. Com `DASHBOARD_CONTRACT_RECORD=1` grava as fixtures em vez de comparar.
  - `dashboard/tests/test_api_contract.py` alimenta `ApiClient` (via `MockTransport`), os view-models e as figuras com essas fixtures e afirma valores fixos do pregão.
  - Uma mudança de chave na API quebra o teste da raiz; regravar as fixtures faz o teste do dashboard mostrar qual tela quebrou.
- **D58 — Configuração do contêiner do dashboard (perguntas abertas 1–3 e 8, ruling).**
  - Pins mantidos: `streamlit>=1.40,<2`, `plotly>=5.24,<7`; a Task 1 sonda o `AppTest`, o Plotly e `client.showErrorDetails` na versão resolvida e adapta os testes, nunca a versão.
  - `client.showErrorDetails`: nas versões recentes é texto (`full | stacktrace | type | none`) e `true`/`false` são só aliases legados. Usa-se `none`, fixado em `test_compose.py`; se a sonda da Task 1 mostrar um tipo booleano, usa-se `false` com `Ruling:` no ledger.
  - `dashboard` depende de `api` com `service_started`.
  - Imagem do `uv`: a Task 14 confere `uv --version` e `docker run --rm ghcr.io/astral-sh/uv:<versão> uv --version` (quando houver docker), e usa a mesma versão nas duas imagens.
  - Custo se estiver errado: um alias legado removido numa versão futura faria o Streamlit recusar a opção na subida, visível na primeira execução do Compose.
- **D59 — Limites aceitos do dashboard (perguntas abertas 5 e 7, ruling).**
  - Sem autenticação própria: porta só em `127.0.0.1` e túnel SSH (`ssh -L 8501:127.0.0.1:8501 <host>`), registrado como limitação conhecida; autenticação e proxy vão para o endurecimento de VPS.
  - Curva de R acumulado limitada a 1000 ordens fechadas (limite de `GET /orders`), com legenda quando o limite é atingido; rota agregada ou paginação ficam como entrada do próximo plano.
  - Custo se estiver errado: com mais de 1000 ordens fechadas a curva omite as mais antigas (sinalizado na tela).

## Conflitos e lacunas da spec resolvidos acima

1. **Spec 2.1 (dashboard só pela API) × spec 5.1 (sem rotas de candles, pressão, portfólio ou observabilidade):** routers novos aditivos no mesmo padrão (D42).
2. **Spec 3.6 ("leitura atual só para `RECALCULATE` e dashboard"):** a leitura "atual" do dashboard é as-of um `data_as_of` obtido no instante da requisição e devolvido na resposta, sem run (D41).
3. **Spec 1.2 item 2 e 1.3 (P&L por 1R; Portfolio Simulator é o sub-projeto 02) × pedido de portfólio virtual:** visão somente leitura normalizada por 1R, rotulada, sem caixa nem limites (D45).
4. **Roadmap (uso aprovado do portfólio real) × Fase 0 sem evidência:** contrato e slot desligados, modelo de relatório; nada da Robinhood (D46).
5. **Spec 5.5 (cinco telas) × pedidos do responsável (watchlist, mercado, portfólio):** telas extras, sem tirar nenhuma das cinco.
6. **Spec 5.1 (toda rota com `X-API-Key`) × healthcheck de contêiner:** healthcheck com a chave do ambiente, sem `/livez` (D48).
7. **Spec 8 (sem variável para a URL da API no dashboard):** `DASHBOARD_API_URL`, lida só pelo dashboard, fora de `Settings` (D40).
8. **Spec 6 (role sem `UPDATE`/`DELETE`) × Compose conectando como dono do banco:** triggers já garantem append-only; separação adiada (D55).
9. **D22 (`PROVIDER_FAILURE` mantém a pendência) × pendência sumindo depois de 20 runs:** linha terminal no caso final (D52).
10. **D38 ("recuperação manual; CLI é entrada do 3C"):** mantida manual por conflito com D4/D12 (D53).
11. **Spec 7 (ponta a ponta com pregão gravado) × teste existente só no nível do adapter:** um segundo teste pela API (D54).
12. **Spec 5.5 "cobertura de dados" e "`DATA_GAP`" × `/health` sem esses dados:** `GET /quality/overview` a partir dos eventos `DATA_QUALITY`/`DATA_GAP` dos últimos 14 dias (D42).
13. **Spec 4.7 (aviso SHORT em telas com ordens SHORT):** a tela de ordens mostra o aviso fixo; as métricas já trazem `SHORT_BORROW_NOT_SIMULATED` (`summary_warnings`).
14. **Spec 6 ("minutos não verificáveis → `NEEDS_REVIEW` com motivo") × D22/D12(a) (pendência terminal sem revisão):** `PROVIDER_FAILURE_FINAL` marca `DATA_QUALITY_UNVERIFIED`; `NO_OBSERVATIONS_FINAL` continua sem revisão porque zero observações não é evidência de candles ausentes (D52).
15. **Spec 9 (Streamlit e Plotly no stack) × lock do núcleo congelado (pandas 3):** o dashboard vira projeto `uv` próprio com lock e imagem próprios (D56).
16. **Spec 5.1/5.5 (dashboard como tela do responsável) × Streamlit sem autenticação:** bind em `127.0.0.1` e túnel SSH, limitação conhecida até o endurecimento de VPS (D59).
17. **Spec 6 (role de banco sem `UPDATE`/`DELETE`) mantida como desvio registrado:** triggers garantem append-only; a role fica para o endurecimento de VPS (D55, runbook, nota de encerramento).

## Estrutura de arquivos

```
docs/superpowers/plans/2026-09-14-virtual-order-engine-dashboard.md    # Task 0  — este plano, versionado
tests/integration/support.py                                            # Task 1  — MutableClock, FakeTickerCheck, API_KEY, json_text, post_json
tests/integration/api/conftest.py, tests/integration/worker/conftest.py # Task 1  — importam os helpers de support.py
tests/integration/api/test_{commands,health,health_hardening,metrics,order_quality,orders,signals,watchlist}_api.py  # Task 1 — imports
dashboard/pyproject.toml, dashboard/uv.lock                             # Task 1  — projeto uv do dashboard (D56)
dashboard/dashboard/__init__.py                                         # Task 1
dashboard/tests/__init__.py, dashboard/tests/test_toolkit.py            # Task 1  — sonda do AppTest, do Plotly e de showErrorDetails
tests/test_import_boundaries.py                                         # Tasks 1, 6, 8, 15
pyproject.toml, uv.lock                                                 # Task 14 — só pyyaml (dev), com diff do export
src/virtual_orders/
  worker/runner.py          # Task 2  — EXIT_DATABASE_UNAVAILABLE, ShutdownGuard
  storage/database.py       # Task 2  — keepalives do libpq
  alerts/outbox.py          # Task 3  — ORDER_EVENT_BEHIND_WINDOW
  readmodels/health.py      # Tasks 3, 5 — _JOB_ANCHOR/_JOB_RUNS limitados; head 0004
  alerts/watch.py           # Task 4  — isolamento por ticker
  evaluator/recheck.py      # Task 5  — PROVIDER_FAILURE_FINAL
  analytics/vwap.py         # Task 6  — session_vwap (puro)
  analytics/portfolio.py    # Task 6  — mark_portfolio (puro)
  readmodels/market.py      # Task 7  — candles, latest_pressure, order_chart
  api/tickers.py            # Task 7  — normalize_ticker (extraído de routes/watchlist.py)
  api/routes/watchlist.py   # Task 7  — usa normalize_ticker
  api/routes/market.py      # Task 7
  api/app.py                # Tasks 7, 8, 9 — routers novos
  portfolio/__init__.py     # Task 8
  portfolio/sources.py      # Task 8  — PortfolioSource, RealPosition, PHASE_0_PENDING
  services.py               # Task 8  — portfolio_source
  bootstrap.py              # Task 8  — portfolio_source=None explícito
  readmodels/portfolio.py   # Task 8  — virtual_portfolio
  api/routes/portfolio.py   # Task 8
  readmodels/observability.py # Task 9 — health_log, alert_outbox_view, quality_overview
  api/routes/observability.py # Task 9
migrations/versions/0004_recheck_session_unique.py                      # Task 5
dashboard/dashboard/client.py                                           # Task 11
dashboard/dashboard/viewmodels.py                                       # Task 12
dashboard/dashboard/charts.py                                           # Task 12
dashboard/dashboard/app.py                                              # Task 13
dashboard/dashboard/views/__init__.py, common.py, overview.py, signals.py, orders.py, comparison.py, health.py, watchlist.py, market.py, portfolio.py   # Task 13
Dockerfile, .dockerignore, docker-compose.yml, .env.example             # Task 14 — imagem do motor
dashboard/Dockerfile, dashboard/.dockerignore                           # Task 14 — imagem do dashboard (dashboard/uv.lock)
docs/superpowers/roadmap/2026-09-14-robinhood-phase0-feasibility-report.md  # Task 8
docs/superpowers/runbooks/2026-09-14-compose-producao.md               # Task 14
docs/superpowers/notes/2026-09-14-plan3c-closeout.md                    # Task 15
tests/worker/test_runner_startup.py                                     # Task 2
tests/storage/test_database.py                                          # Task 2
tests/integration/worker/test_runner.py                                 # Task 2 (teste acrescentado)
tests/worker/test_window_parity.py                                      # Task 3
tests/integration/test_alert_outbox.py                                  # Task 3 (teste acrescentado)
tests/integration/api/test_health_hardening.py                          # Task 3 (teste acrescentado)
tests/integration/test_watchlist_alerts.py                              # Task 4 (teste acrescentado)
tests/integration/test_schema_0004.py                                   # Task 5
tests/integration/test_quality_recheck.py                               # Task 5 (testes acrescentados)
tests/evaluator/__init__.py, tests/evaluator/test_recheck_levels.py     # Task 5 — levels_state_at_close × projeção
tests/integration/api/test_health_api.py                                # Task 5 (head esperado 0004)
tests/analytics/test_vwap.py, tests/analytics/test_portfolio.py         # Task 6
tests/integration/api/test_market_api.py                                # Task 7
tests/worker/test_window_parity.py                                      # Tasks 3, 7
tests/integration/api/test_portfolio_api.py                             # Task 8
tests/integration/api/test_observability_api.py                         # Task 9
tests/integration/api/test_end_to_end_api.py                            # Task 10
dashboard/tests/test_client.py                                          # Task 11
dashboard/tests/test_viewmodels.py, dashboard/tests/test_charts.py      # Task 12
tests/integration/api/test_dashboard_contract.py                        # Task 12 — grava/compara a forma das respostas (D57)
dashboard/tests/fixtures/api/*.json, dashboard/tests/test_api_contract.py  # Task 12
dashboard/tests/test_app_smoke.py                                       # Task 13
tests/deploy/__init__.py, tests/deploy/test_compose.py                  # Task 14
```

Comandos de verificação usados nas tasks (`<paths>` = arquivos de teste da task). Com a Task 1, arquivos de diretórios diferentes podem ir na mesma chamada do `pytest` (entrada 20).

```bash
# motor (raiz)
uv run pytest <paths> && uv run ruff check src tests migrations && uv run mypy
# dashboard (projeto próprio, D56)
uv run --directory dashboard pytest && uv run --directory dashboard ruff check . && uv run --directory dashboard mypy
```

---

### Task 0: Conferir o plano versionado e medir a linha de base

**Files:**
- Nenhum arquivo novo. O controlador versiona este plano em `docs/superpowers/plans/2026-09-14-virtual-order-engine-dashboard.md` no branch `plan-3c-dashboard` antes de despachar a Task 1.

**Interfaces:**
- Produces: `N_BASE` (esperado: 908 passando, 0 skips, 0 warnings, medido no 3B em `8da0f24`), registrado no ledger e usado nas Tasks 1 e 15.

- [ ] **Step 1: Confirm the plan is committed on the branch**

```bash
git switch plan-3c-dashboard
git log --oneline -1 -- docs/superpowers/plans/2026-09-14-virtual-order-engine-dashboard.md
git status --short
git merge-base --is-ancestor 0fd2707 HEAD && echo "based on plan-3b-worker tip"
```

Expected: um commit listado, árvore limpa e `based on plan-3b-worker tip`. Se o plano não estiver versionado, pare e reporte ao controlador; não copie de outro lugar.

- [ ] **Step 2: Measure the inherited test baseline**

```bash
docker compose -f docker-compose.test.yml down -v
docker compose -f docker-compose.test.yml up -d --wait
uv run pytest -p no:cacheprovider -o addopts="" -q 2>&1 | tail -3
uv run ruff check src tests migrations
uv run mypy
```

Expected: `908 passed` sem `skipped` nem `warnings`; ruff e mypy limpos. Registre a linha final como `N_BASE`. Se o número diferir, registre o valor medido (nenhuma task presume um número fixo) e siga. A Task 0 não gera commit.

---

### Task 1: Helpers de integração fora dos `conftest`, projeto `uv` do dashboard e fronteiras de import (entrada 20; D40, D55, D56, D58)

**Files:**
- Modify: `tests/integration/support.py` (`ROOT`, `alembic_config`, `API_KEY`, `MutableClock`, `FakeTickerCheck`, `json_text`, `post_json`)
- Modify: `tests/integration/conftest.py`, `tests/integration/api/conftest.py`, `tests/integration/worker/conftest.py`
- Modify (só imports): `tests/integration/api/test_commands_api.py`, `test_health_api.py`, `test_health_hardening.py`, `test_metrics_api.py`, `test_order_quality_api.py`, `test_orders_api.py`, `test_signals_api.py`, `test_watchlist_api.py`; `tests/integration/test_schema.py`, `tests/integration/test_schema_0003.py`
- Modify: `tests/test_import_boundaries.py`
- Create: `dashboard/pyproject.toml`, `dashboard/uv.lock` (gerado por `uv lock`), `dashboard/dashboard/__init__.py`
- Create: `dashboard/tests/__init__.py`, `dashboard/tests/test_toolkit.py`

**Interfaces:**
- Consumes: nada de tasks anteriores.
- Produces:
  - `tests.integration.support`: `ROOT: Path`, `alembic_config(url: str) -> Config`, `API_KEY = "test-api-key"`, `class MutableClock(now)` com `set(now)` e `__call__() -> datetime`, `class FakeTickerCheck` (`name`, `calls`, `untradable`, `failing`, `check_ticker(ticker) -> TickerStatus`), `json_text(body: Any) -> str`, `post_json(client: TestClient, path: str, body: Any)`. Todas as tasks seguintes importam daqui, nunca de um `conftest`.
  - projeto `dashboard/` com o pacote `dashboard` importável dentro dele (`uv run --directory dashboard …`);
  - em `tests/test_import_boundaries.py`: `ROOT`, `DASHBOARD_PROJECT`, `DASHBOARD_FORBIDDEN`, `UI_LIBRARIES`, `_dashboard_files()`, `_dashboard_rel()`, `_root_test_files()`, usados na Task 15;
  - ledger: versões resolvidas de `streamlit`/`plotly`/`pandas` do dashboard e o tipo e os valores de `client.showErrorDetails` (as Tasks 12, 13 e 14 dependem da API sondada aqui).

#### Parte A — nenhum módulo importa um `conftest` (entrada 20)

- [ ] **Step 1: Write the failing guard test**

Em `tests/test_import_boundaries.py`, logo abaixo de `SRC = Path(__file__).resolve().parents[1] / "src"`, acrescente:

```python
ROOT = SRC.parent
```

e, ao final do arquivo:

```python
def _root_test_files() -> list[Path]:
    return sorted((ROOT / "tests").rglob("*.py"))


def test_no_module_imports_a_conftest() -> None:
    # Plan 3B close-out entry 20: a conftest imported as a plain module can be registered out of order when test
    # files from different directories share one pytest call. Shared helpers live in tests/integration/support.py.
    offenders = {
        str(path.relative_to(ROOT)): sorted(name for name in _imported_modules(path) if name.endswith(".conftest"))
        for path in _root_test_files()
    }
    assert {name: modules for name, modules in offenders.items() if modules} == {}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_import_boundaries.py::test_no_module_imports_a_conftest`
Expected: FAIL, listando 11 arquivos: os oito `tests/integration/api/test_*_api.py` acima, `tests/integration/worker/conftest.py`, `tests/integration/test_schema.py` e `tests/integration/test_schema_0003.py`.

- [ ] **Step 3: Move the shared helpers into `tests/integration/support.py`**

Em `tests/integration/support.py`, troque o bloco de imports inteiro (do `from __future__` até `from virtual_orders.storage import tables`) por:

```python
from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select

from core.domain.models import Bar, FillConfig
from tests.support import et
from virtual_orders.evaluator.signals import SignalSubmission, submit_signal
from virtual_orders.marketdata.calendars import calendar_for_window
from virtual_orders.marketdata.gateway import MarketDataGateway
from virtual_orders.marketdata.sources import (
    DataTier,
    DividendRecord,
    RawBar,
    SourceUnavailable,
    SplitRecord,
    TickerStatus,
)
from virtual_orders.storage import tables
```

e acrescente ao final do arquivo (os corpos são os mesmos dos `conftest`, movidos sem mudança):

```python
ROOT = Path(__file__).resolve().parents[2]
API_KEY = "test-api-key"


def alembic_config(url: str) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    return config


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
```

Substitua `tests/integration/conftest.py` inteiro por:

```python
"""Real Postgres per test: a migrated template database cloned for every test."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

from tests.integration.support import alembic_config
from virtual_orders.storage.database import make_engine

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

Substitua `tests/integration/api/conftest.py` inteiro por:

```python
"""API harness: the real app over real Postgres with fake providers and a controllable clock. No network."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from core.domain.models import FillConfig
from tests.integration.support import (
    API_KEY,
    CODE_VERSION,
    PRICE_SOURCE,
    SIGNAL_CREATED_AT,
    FakeBarSource,
    FakeDividends,
    FakeReference,
    FakeSplits,
    FakeTickerCheck,
    MutableClock,
    feeds,
)
from virtual_orders.api.app import create_app
from virtual_orders.services import Services


@dataclass
class ApiHarness:
    client: TestClient
    services: Services
    bars: FakeBarSource
    tickers: FakeTickerCheck
    clock: MutableClock


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

Em `tests/integration/worker/conftest.py`, troque as duas linhas `from tests.integration.api.conftest import FakeTickerCheck, MutableClock` e o bloco `from tests.integration.support import (...)` por:

```python
from tests.integration.support import (
    CODE_VERSION,
    DAY,
    PRICE_SOURCE,
    FakeBarSource,
    FakeDividends,
    FakeReference,
    FakeSplits,
    FakeTickerCheck,
    MutableClock,
    feeds,
)
```

Nos testes, remova cada import de `conftest` e junte os nomes ao import de `tests.integration.support` já existente (as linhas resultantes abaixo são exatas):

| Arquivo | Remova | Import de `support` resultante |
|---|---|---|
| `tests/integration/api/test_commands_api.py` | `from tests.integration.api.conftest import post_json` | no bloco multilinha, `post_json,` entre `count,` e `raw,` |
| `tests/integration/api/test_health_api.py` | `from tests.integration.api.conftest import API_KEY, post_json` e `from tests.integration.conftest import alembic_config` | `from tests.integration.support import API_KEY, CODE_VERSION, DAY, TICKER, alembic_config, post_json, signal_body` |
| `tests/integration/api/test_health_hardening.py` | `from tests.integration.api.conftest import post_json` | `from tests.integration.support import CODE_VERSION, DAY, post_json, signal_body` |
| `tests/integration/api/test_metrics_api.py` | `from tests.integration.api.conftest import post_json` | `from tests.integration.support import CODE_VERSION, DAY, post_json, scenario_bars, signal_body` |
| `tests/integration/api/test_order_quality_api.py` | `from tests.integration.api.conftest import post_json` | `from tests.integration.support import CODE_VERSION, DAY, FakeReference, post_json, scenario_bars, signal_body` |
| `tests/integration/api/test_orders_api.py` | `from tests.integration.api.conftest import post_json` | `from tests.integration.support import CODE_VERSION, DAY, post_json, scenario_bars, signal_body` |
| `tests/integration/api/test_signals_api.py` | `from tests.integration.api.conftest import API_KEY, post_json` | `from tests.integration.support import API_KEY, count, post_json, signal_body, submit_default` |
| `tests/integration/api/test_watchlist_api.py` | `from tests.integration.api.conftest import post_json` | `from tests.integration.support import count, post_json` |
| `tests/integration/test_schema.py` | `from tests.integration.conftest import alembic_config` | `from tests.integration.support import alembic_config` |
| `tests/integration/test_schema_0003.py` | `from tests.integration.conftest import alembic_config` | `from tests.integration.support import CODE_VERSION, alembic_config, count, submit_default` |

Se o ruff apontar só `I001` depois disso, rode `uv run ruff check --select I --fix tests/integration` e confira o diff: só a ordem dos imports pode mudar.

- [ ] **Step 4: Run the guard, a mixed-directory call and the static checks**

```bash
uv run pytest tests/test_import_boundaries.py tests/integration/api/test_health_api.py tests/integration/api/test_signals_api.py tests/integration/worker/test_runner.py tests/integration/test_quality_recheck.py tests/integration/test_schema.py tests/integration/test_schema_0003.py
uv run ruff check src tests migrations && uv run mypy
```

Expected: PASS numa única chamada que mistura a raiz, `api`, `worker` e arquivos soltos de `integration` (comando de regressão da entrada 20, repetido na Task 15); ruff e mypy limpos.

- [ ] **Step 5: Commit**

```bash
git add tests/test_import_boundaries.py tests/integration/support.py tests/integration/conftest.py tests/integration/api/conftest.py tests/integration/worker/conftest.py tests/integration/api/test_commands_api.py tests/integration/api/test_health_api.py tests/integration/api/test_health_hardening.py tests/integration/api/test_metrics_api.py tests/integration/api/test_order_quality_api.py tests/integration/api/test_orders_api.py tests/integration/api/test_signals_api.py tests/integration/api/test_watchlist_api.py tests/integration/test_schema.py tests/integration/test_schema_0003.py
git commit -m "test(integration): move shared harness helpers out of conftest modules"
```

#### Parte B — projeto `uv` do dashboard (D56) e sondas da versão instalada (D58)

- [ ] **Step 6: Write the failing tests**

Acrescente ao final de `tests/test_import_boundaries.py`:

```python
DASHBOARD_PROJECT = ROOT / "dashboard"
DASHBOARD_SOURCES = ("dashboard", "tests")  # never dashboard/.venv: that is Streamlit's own site-packages
DASHBOARD_FORBIDDEN = (
    "core", "virtual_orders", "tests", "sqlalchemy", "psycopg", "alembic", "yfinance", "pandas_market_calendars",
    "apscheduler", "fastapi", "starlette", "uvicorn",
)
UI_LIBRARIES = ("dashboard", "streamlit", "plotly")


def _dashboard_files() -> list[Path]:
    files: list[Path] = []
    for folder in DASHBOARD_SOURCES:
        if (DASHBOARD_PROJECT / folder).exists():
            files.extend(sorted((DASHBOARD_PROJECT / folder).rglob("*.py")))
    return files


def _dashboard_rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


@pytest.mark.parametrize("path", _dashboard_files(), ids=_dashboard_rel)
def test_dashboard_talks_to_the_platform_only_over_http(path: Path) -> None:
    assert _offending(path, DASHBOARD_FORBIDDEN) == []


@pytest.mark.parametrize("path", _platform_files() + _core_files(), ids=_rel)
def test_platform_and_core_never_import_the_dashboard_or_ui_libraries(path: Path) -> None:
    assert _offending(path, UI_LIBRARIES) == []


def test_root_tests_never_import_the_dashboard_or_ui_libraries() -> None:
    # D57: the API/dashboard contract crosses projects as recorded JSON fixtures, never as an import.
    offenders = {_dashboard_rel(path): _offending(path, UI_LIBRARIES) for path in _root_test_files()}
    assert {name: modules for name, modules in offenders.items() if modules} == {}


def test_boundary_scan_covers_the_dashboard_project() -> None:
    assert "dashboard/dashboard/__init__.py" in {_dashboard_rel(p) for p in _dashboard_files()}
    assert not any(".venv" in path.parts for path in _dashboard_files())
    assert (DASHBOARD_PROJECT / "pyproject.toml").exists() and (DASHBOARD_PROJECT / "uv.lock").exists()
    root_lock = (ROOT / "uv.lock").read_text()
    for library in ("streamlit", "plotly"):
        assert f'name = "{library}"' not in root_lock, library  # D56: UI libraries never enter the engine lock
        assert importlib.util.find_spec(library) is None, library
    assert "core" in DASHBOARD_FORBIDDEN and "virtual_orders" in DASHBOARD_FORBIDDEN
```

Crie `dashboard/tests/__init__.py` vazio e `dashboard/tests/test_toolkit.py`:

```python
"""Probes the resolved Streamlit and Plotly APIs the dashboard relies on (D40, D58). No network, no server."""

import plotly.graph_objects as go
from plotly.subplots import make_subplots
from streamlit import config
from streamlit.testing.v1 import AppTest

PROBE = """
import streamlit as st

st.title("probe")
choice = st.sidebar.radio("page", ["a", "b"], key="page")
st.markdown(f"page={choice}")
st.markdown(f"injected={st.session_state.get('injected')}")
if st.button("go", key="go"):
    st.error("clicked")
"""


def test_app_test_runs_scripts_clicks_buttons_and_switches_the_sidebar_radio():
    at = AppTest.from_string(PROBE, default_timeout=30)
    at.session_state["injected"] = "hello"
    at.run()
    assert not at.exception
    assert at.title[0].value == "probe"
    assert [m.value for m in at.markdown] == ["page=a", "injected=hello"]
    at.button(key="go").click().run()
    assert at.error[0].value == "clicked"
    at.radio(key="page").set_value("b").run()
    assert at.markdown[0].value == "page=b"


def test_plotly_subplots_keep_trace_axes_and_named_shapes():
    figure = make_subplots(rows=2, cols=1, shared_xaxes=True)
    figure.add_trace(go.Candlestick(x=["2025-11-25 09:30"], open=[1.0], high=[2.0], low=[0.5], close=[1.5]),
                     row=1, col=1)
    figure.add_trace(go.Bar(x=["2025-11-25 09:30"], y=[10.0]), row=2, col=1)
    figure.add_shape(type="line", xref="x domain", x0=0, x1=1, yref="y", y0=1.2, y1=1.2, name="Stop")
    assert [trace.type for trace in figure.data] == ["candlestick", "bar"]
    assert figure.data[1].yaxis == "y2"
    assert [shape.name for shape in figure.layout.shapes] == ["Stop"]


def test_error_details_can_be_hidden_with_the_none_value():
    # D58: recent Streamlit takes full | stacktrace | type | none; true/false are legacy aliases only.
    option = config.get_config_options()["client.showErrorDetails"]
    assert option.type is str, f"client.showErrorDetails is {option.type}: use the probed value and record a Ruling"
    assert "none" in (option.description or "")
    previous = config.get_option("client.showErrorDetails")
    config.set_option("client.showErrorDetails", "none")
    try:
        assert config.get_option("client.showErrorDetails") == "none"
    finally:
        config.set_option("client.showErrorDetails", previous)
```

- [ ] **Step 7: Run tests to verify they fail**

```bash
uv run pytest tests/test_import_boundaries.py
uv run --directory dashboard pytest
```

Expected:
- na raiz, `test_boundary_scan_covers_the_dashboard_project` FALHA (`dashboard/dashboard/__init__.py` e `dashboard/pyproject.toml` não existem). O parametrize do dashboard só vê `dashboard/tests/*.py` por enquanto; se não houver nenhum arquivo, o pytest reporta o parâmetro vazio como `SKIPPED`, e isso some no Step 8 da mesma task (M1);
- no dashboard, sem `dashboard/pyproject.toml` o `uv` sobe até o projeto da raiz e a coleta falha com `ModuleNotFoundError: No module named 'plotly'`.

- [ ] **Step 8: Create the dashboard project without touching the engine lock**

`dashboard/pyproject.toml`:

```toml
[project]
name = "vo-dashboard"
version = "0.1.0"
description = "Streamlit dashboard for the Virtual Order Engine; talks to the API over HTTP only (spec 2.1, D40, D56)."
requires-python = ">=3.12,<3.13"
dependencies = [
    "httpx>=0.28",
    "plotly>=5.24,<7",
    "streamlit>=1.40,<2",
]

[dependency-groups]
dev = [
    "pytest>=8.0",
    "ruff==0.8.6",
    "mypy==1.13.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["dashboard"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
addopts = "-q"

[tool.ruff]
target-version = "py312"
line-length = 120
src = ["."]

[tool.ruff.lint]
select = ["E4", "E7", "E9", "F", "B", "I", "UP"]

[tool.ruff.lint.isort]
known-first-party = ["dashboard", "tests"]

[tool.mypy]
python_version = "3.12"
strict = true
packages = ["dashboard"]

[[tool.mypy.overrides]]
# Third-party library without type information (Plan 3C, D40): figures are built and asserted structurally.
module = ["plotly", "plotly.*"]
ignore_missing_imports = true
```

`dashboard/dashboard/__init__.py`:

```python
"""Streamlit dashboard (spec 5.5). Talks to the Virtual Order Engine only through its HTTP API (spec 2.1, D40)."""
```

Depois:

```bash
uv lock --directory dashboard
uv sync --directory dashboard --locked
git diff --quiet uv.lock && echo "root uv.lock unchanged"
git diff --quiet pyproject.toml && echo "root pyproject.toml unchanged"
uv sync --locked
uv run --directory dashboard python -c "import streamlit, plotly, pandas; print('streamlit', streamlit.__version__, 'plotly', plotly.__version__, 'pandas', pandas.__version__)"
uv run --directory dashboard python -c "from streamlit import config; o = config.get_config_options()['client.showErrorDetails']; print(o.type, o.default_val); print(o.description)"
```

Expected: `root uv.lock unchanged` e `root pyproject.toml unchanged` impressos; `uv sync --locked` da raiz sem mudanças. **Se qualquer um dos `git diff --quiet` falhar, pare e reporte** ao controlador sem commitar: o lock do motor nunca muda por causa do dashboard (D56). Registre no ledger as três versões e o tipo e a descrição de `client.showErrorDetails`.

- [ ] **Step 9: Run tests to verify they pass**

```bash
uv run pytest tests/test_import_boundaries.py
uv run ruff check src tests migrations && uv run mypy
uv run --directory dashboard pytest
uv run --directory dashboard ruff check . && uv run --directory dashboard mypy
```

Expected: PASS nos dois projetos; ruff e mypy limpos nos dois.

Se a API do `AppTest`, do Plotly ou de `streamlit.config` na versão instalada diferir do que os testes sondam (por exemplo, `at.radio(key=...)`, `set_value`, `session_state` antes do primeiro `run()`, `Shape.name`, `get_config_options()` ou `ConfigOption.type`):
- adapte o teste à API real da versão instalada, preservando o que ele prova;
- registre a diferença no ledger como `Ruling: …`;
- aplique a mesma adaptação aos testes das Tasks 12 e 13.

Se `client.showErrorDetails` for booleano na versão resolvida, troque a sonda para afirmar `option.type is bool`, registre `Ruling: showErrorDetails booleano — usar false — <versão>` e use `false` no lugar de `none` na Task 14 (compose e `test_compose.py`). Nunca troque a versão só para fazer um teste passar.

Se o Streamlit gerar `DeprecationWarning` de terceiros na suíte do dashboard, acrescente a `[tool.pytest.ini_options]` de `dashboard/pyproject.toml` um `filterwarnings` com o texto e a categoria exatos e um comentário de motivo, como o 3A fez com o `starlette.testclient`; nunca ignore `DeprecationWarning` de forma genérica.

- [ ] **Step 10: Run both whole suites**

```bash
uv run pytest
uv run --directory dashboard pytest
```

Expected: raiz com `N_BASE` + os novos testes passando, 0 skips, 0 warnings; dashboard com os 3 testes da sonda passando, 0 warnings.

- [ ] **Step 11: Commit**

```bash
git add tests/test_import_boundaries.py dashboard/pyproject.toml dashboard/uv.lock dashboard/dashboard/__init__.py dashboard/tests/__init__.py dashboard/tests/test_toolkit.py
git commit -m "build(dashboard): separate uv project with Streamlit and Plotly and an HTTP-only import boundary"
```

---

### Task 2: Worker — banco fora do ar na subida, flag de parada sincronizada e keepalives (entradas 12, 14, 19; D49)

**Files:**
- Modify: `src/virtual_orders/worker/runner.py`
- Modify: `src/virtual_orders/storage/database.py`
- Create: `tests/worker/test_runner_startup.py`
- Create: `tests/storage/test_database.py`
- Modify: `tests/integration/worker/test_runner.py` (teste acrescentado)

**Interfaces:**
- Consumes: `run_worker`, `acquire_worker_lock`, `worker_lock_held`, `release_worker_lock` (3B), `build_services`, `load_settings`, `BASE_ENV`.
- Produces:
  - `virtual_orders.worker.runner.EXIT_DATABASE_UNAVAILABLE = 4`;
  - `class ShutdownGuard` com `claim() -> bool` (sem propriedade só para testes, M4);
  - `virtual_orders.storage.database.TCP_KEEPALIVE_ARGS: dict[str, int]`.

- [ ] **Step 1: Write the failing tests**

`tests/worker/test_runner_startup.py`:

```python
import logging
import threading
from dataclasses import replace

import httpx
import pytest

from tests.config_support import BASE_ENV
from virtual_orders.bootstrap import build_services
from virtual_orders.config import load_settings
from virtual_orders.worker.runner import EXIT_DATABASE_UNAVAILABLE, ShutdownGuard, run_worker


def test_shutdown_guard_is_claimed_exactly_once_across_threads():
    guard = ShutdownGuard()
    barrier = threading.Barrier(16)
    results: list[bool] = []

    def contend() -> None:
        barrier.wait()
        results.append(guard.claim())

    threads = [threading.Thread(target=contend) for _ in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results.count(True) == 1
    assert guard.claim() is False  # already claimed: every later claim loses


def test_database_down_at_start_logs_only_the_type_and_exits_4(caplog):
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500)))
    services = build_services(load_settings(BASE_ENV), http_client=client)  # DATABASE_URL points at 127.0.0.1:1
    closed: list[int] = []
    original_close = services.close

    def close() -> None:
        closed.append(1)
        original_close()

    services = replace(services, close=close)

    def never_scheduled():
        pytest.fail("the scheduler must not be built without the worker lock")

    with caplog.at_level(logging.ERROR, logger="virtual_orders.worker"):
        code = run_worker(services, scheduler_factory=never_scheduled, install_signal_handlers=False)

    assert code == EXIT_DATABASE_UNAVAILABLE == 4
    assert closed == [1]
    assert "database unavailable at worker start: OperationalError" in caplog.text
    assert "127.0.0.1" not in caplog.text and "unused" not in caplog.text
    client.close()
```

`tests/storage/test_database.py`:

```python
from virtual_orders.storage import database


def test_engine_sets_connect_timeout_utc_and_libpq_keepalives(monkeypatch):
    captured: dict = {}

    def fake_create_engine(url, **kwargs):
        captured.update(url=url, **kwargs)
        return "engine"

    monkeypatch.setattr(database, "create_engine", fake_create_engine)
    assert database.make_engine("postgresql+psycopg://vo@db/vo") == "engine"
    assert captured["pool_pre_ping"] is True
    assert captured["connect_args"] == {
        "options": "-c timezone=UTC", "connect_timeout": 5,
        "keepalives": 1, "keepalives_idle": 30, "keepalives_interval": 10, "keepalives_count": 3,
        "tcp_user_timeout": 60000,
    }
```

Acrescente ao final de `tests/integration/worker/test_runner.py` (e `worker_lock_held` ao import de `virtual_orders.worker.runner`):

```python
def test_a_live_connection_whose_lock_was_released_counts_as_lost(worker):
    lock = acquire_worker_lock(worker.services.engine)
    assert lock is not None
    try:
        assert worker_lock_held(lock) is True
        lock.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": WORKER_LOCK_KEY})
        lock.commit()
        assert worker_lock_held(lock) is False  # the session is alive, but the lock is gone (T16)
    finally:
        lock.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/worker/test_runner_startup.py tests/storage/test_database.py`
Expected: FAIL — `ImportError: cannot import name 'EXIT_DATABASE_UNAVAILABLE'` e `connect_args` sem as chaves de keepalive. O teste T16 acrescentado a `tests/integration/worker/test_runner.py` é uma fixação e passa já antes de qualquer mudança (M15): `worker_lock_held` do 3B já trata o ramo; o teste só prova isso.

- [ ] **Step 3: Implement**

Em `src/virtual_orders/storage/database.py`, substitua `make_engine` e acrescente a constante:

```python
CONNECT_TIMEOUT_SECONDS = 5  # libpq connect_timeout: an unreachable host must fail fast, never hang /health
# D49 (M2): a half-open TCP session must fail instead of hanging the worker_lock job forever.
TCP_KEEPALIVE_ARGS: dict[str, int] = {
    "keepalives": 1, "keepalives_idle": 30, "keepalives_interval": 10, "keepalives_count": 3,
    "tcp_user_timeout": 60000,
}


def make_engine(url: str) -> Engine:
    return create_engine(
        url, pool_pre_ping=True,
        connect_args={"options": "-c timezone=UTC", "connect_timeout": CONNECT_TIMEOUT_SECONDS, **TCP_KEEPALIVE_ARGS},
    )
```

Em `src/virtual_orders/worker/runner.py`:

1. Acrescente `import threading` aos imports da biblioteca padrão.
2. Troque `EXIT_LOCK_LOST = 3` por:

```python
EXIT_LOCK_LOST = 3
EXIT_DATABASE_UNAVAILABLE = 4  # D49 (M4): the database was unreachable when the worker tried to take its lock
```

3. Antes de `def blocking_scheduler`, acrescente:

```python
class ShutdownGuard:
    """One shutdown per process (D49). `acquire(blocking=False)` is atomic across the lock-watch thread and the
    signal handler, and a signal that interrupts a handler never blocks: the nested claim simply loses."""

    def __init__(self) -> None:
        self._lock = threading.Lock()

    def claim(self) -> bool:
        return self._lock.acquire(blocking=False)
```

4. Substitua a função `run_worker` inteira por:

```python
def run_worker(
    services: Services,
    *,
    scheduler_factory: Callable[[], Scheduler] = blocking_scheduler,
    install_signal_handlers: bool = True,
) -> int:
    lock: Connection | None = None
    lost = False
    guard = ShutdownGuard()  # T16 + D49: a second SIGTERM/SIGINT, or a signal after lock loss, never shuts down twice
    try:
        try:
            lock = acquire_worker_lock(services.engine)
        except SQLAlchemyError as exc:  # D49 (M4): one log line with the type, a distinct exit code, no traceback
            logger.error("database unavailable at worker start: %s", type(exc).__name__)
            return EXIT_DATABASE_UNAVAILABLE
        if lock is None:
            logger.error("another worker holds the worker lock; exiting")
            return EXIT_LOCKED
        held: Connection = lock
        jobs = WorkerJobs(services)
        schedule = build_schedule(services.eval_interval_minutes)
        scheduler = scheduler_factory()

        def watch_lock() -> JobResult:
            nonlocal lost
            if worker_lock_held(held):
                return JobResult(WORKER_LOCK, True, "HELD")
            lost = True
            logger.error("worker lock lost; stopping so a second worker can never run alongside this one")
            _alert_lock_lost(services)
            if guard.claim():
                scheduler.shutdown(wait=False)  # called from a job thread: never wait for itself
            return JobResult(WORKER_LOCK, False, "LOCK_LOST")

        for spec in (*schedule, worker_lock_schedule()):
            func = watch_lock if spec.job_id == WORKER_LOCK else jobs.runner(spec.job_id)
            scheduler.add_job(func, spec.trigger, id=spec.job_id, name=spec.job_id,
                              max_instances=1, coalesce=True, misfire_grace_time=MISFIRE_GRACE_SECONDS)
        if install_signal_handlers:
            def stop(signum: int, frame: FrameType | None) -> None:
                if not guard.claim():
                    logger.info("signal %s received again; already shutting down", signum)
                    return  # a second signal, or one racing the lock-loss shutdown: never shut down twice
                logger.info("signal %s received; waiting for running jobs and shutting down", signum)
                scheduler.shutdown(wait=True)

            signal.signal(signal.SIGTERM, stop)
            signal.signal(signal.SIGINT, stop)
        scheduler.start()
        return EXIT_LOCK_LOST if lost else EXIT_OK
    finally:
        if lock is not None:
            try:
                release_worker_lock(lock)
            except Exception as exc:  # noqa: BLE001 - closing the services below must still happen
                logger.warning("could not release the worker lock: %s", type(exc).__name__)
        services.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/worker/test_runner_startup.py tests/storage/test_database.py tests/integration/worker/test_runner.py tests/integration/api/test_health_api.py && uv run ruff check src tests migrations && uv run mypy`
Expected: PASS. Os testes do 3B `test_a_second_signal_does_not_shut_the_scheduler_down_twice` e `test_a_signal_after_the_lock_is_lost_does_not_shut_down_twice` continuam verdes com o `ShutdownGuard`. Os testes de integração passam a abrir conexões reais com os keepalives, o que prova que o libpq aceita os parâmetros. No macOS o libpq ignora `tcp_user_timeout` (sem `TCP_USER_TIMEOUT`): o efeito do timeout só vale no contêiner Linux (D49).

- [ ] **Step 5: Commit**

```bash
git add src/virtual_orders/worker/runner.py src/virtual_orders/storage/database.py tests/worker/test_runner_startup.py tests/storage/test_database.py tests/integration/worker/test_runner.py
git commit -m "fix(worker): exit 4 when the database is down at start, atomic shutdown guard, libpq keepalives"
```

---

### Task 3: `/health` e alertas — atraso com idade máxima, runs de job limitados e paridade das janelas (entradas 11, 13, 15; D50)

**Files:**
- Modify: `src/virtual_orders/alerts/outbox.py`
- Modify: `src/virtual_orders/readmodels/health.py`
- Create: `tests/worker/test_window_parity.py`
- Modify: `tests/integration/test_alert_outbox.py` (teste acrescentado)
- Modify: `tests/integration/api/test_health_hardening.py` (teste acrescentado)

**Interfaces:**
- Consumes: `order_event_alerts_behind`, `ORDER_EVENT_LOOKBACK`, `UNDELIVERABLE_WINDOW` (3B), `missing_job_runs`, `MISSING_RUN_LOOKBACK`, `END_OF_DAY_FLOOR`, `watch_session`, `WATCH_GRACE`, `live_session`, `LIVE_WINDOW_GRACE`, `MARKET_TZ`, `build_schedule`.
- Produces: `virtual_orders.alerts.outbox.ORDER_EVENT_BEHIND_WINDOW: timedelta`. `missing_job_runs(conn, *, now)` mantém a assinatura e o resultado.

- [ ] **Step 1: Write the failing tests**

Acrescente a `tests/integration/test_alert_outbox.py`:

```python
def test_behind_only_counts_events_inside_the_behind_window(engine, monkeypatch):
    order_id = closed_order(engine)
    assert enqueue_order_event_alerts(engine) == 3  # alerting started: marks exist from here on
    flag_order_review(engine, order_id, reason="MANUAL", ref="2025-11-25")
    monkeypatch.setattr(outbox, "ORDER_EVENT_LOOKBACK", timedelta(0))
    with engine.connect() as conn:
        assert order_event_alerts_behind(conn) == 1

    monkeypatch.setattr(outbox, "ORDER_EVENT_BEHIND_WINDOW", timedelta(0))  # D50 (M1): older than the cap
    with engine.connect() as conn:
        assert order_event_alerts_behind(conn) == 0
```

Acrescente a `tests/integration/api/test_health_hardening.py` (mais `from sqlalchemy import event` e `from virtual_orders.readmodels.health import missing_job_runs` aos imports):

```python
def test_missing_runs_read_only_sessions_inside_the_checked_range(api):
    engine = api.services.engine
    job_run(engine, RunKind.OPENING, date(2025, 10, 1))  # anchor far outside the lookback
    job_run(engine, RunKind.END_OF_DAY, date(2025, 10, 1))
    seen: list = []

    def spy(conn, cursor, statement, parameters, context, executemany):
        if "since_day" in statement:
            seen.append(parameters)

    event.listen(engine, "before_cursor_execute", spy)
    try:
        with engine.connect() as conn:
            missing = missing_job_runs(conn, now=et("2025-11-26", "19:00"))
    finally:
        event.remove(engine, "before_cursor_execute", spy)

    assert len(seen) == 1 and seen[0]["since_day"] >= date(2025, 11, 1)  # D50 (T9): bounded read
    assert "2025-11-26" in missing["END_OF_DAY"] and "2025-10-02" not in missing["OPENING"]
```

`tests/worker/test_window_parity.py`:

```python
"""D50 (M3): the neutral modules duplicate worker constants on purpose; these pins keep the copies equal."""

from datetime import date, datetime, time, timedelta

import pytest

from tests.support import et
from virtual_orders.alerts import outbox
from virtual_orders.alerts.watch import WATCH_GRACE, watch_session
from virtual_orders.readmodels import health
from virtual_orders.worker.schedule import LIVE_WINDOW_GRACE, MARKET_TZ, build_schedule, live_session


@pytest.mark.parametrize("day, hm", [
    ("2025-11-25", "09:29"), ("2025-11-25", "09:30"), ("2025-11-25", "16:05"), ("2025-11-25", "16:06"),
    ("2025-11-28", "13:05"), ("2025-11-28", "13:06"), ("2025-11-27", "11:00"), ("2025-11-29", "11:00"),
])
def test_watchlist_and_live_cycle_share_the_session_window(day, hm):
    now = et(day, hm)
    assert watch_session(now) == live_session(now)


def test_duplicated_window_constants_stay_equal():
    assert WATCH_GRACE == LIVE_WINDOW_GRACE
    assert health._MARKET_TZ == outbox._MARKET_TZ == MARKET_TZ
    end_of_day = {spec.job_id: spec.trigger for spec in build_schedule(2)}["end_of_day"]
    retry = end_of_day.get_next_fire_time(None, datetime(2025, 11, 25, 17, 0, tzinfo=MARKET_TZ))
    assert retry.time() == time(18, 30)
    assert health.END_OF_DAY_FLOOR == (datetime.combine(date(2025, 11, 25), retry.time()) + timedelta(minutes=30)).time()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/integration/test_alert_outbox.py::test_behind_only_counts_events_inside_the_behind_window tests/integration/api/test_health_hardening.py::test_missing_runs_read_only_sessions_inside_the_checked_range tests/worker/test_window_parity.py`
Expected:
- o teste do outbox FALHA com `AttributeError`: `ORDER_EVENT_BEHIND_WINDOW` não existe;
- o de `/health` FALHA com `len(seen) == 0`, porque não há bind `since_day`;
- o de paridade PASSA já de início, e é uma fixação.

- [ ] **Step 3: Implement**

Em `src/virtual_orders/alerts/outbox.py`, logo abaixo de `ORDER_EVENT_SCAN_LIMIT = 500`:

```python
ORDER_EVENT_BEHIND_WINDOW = UNDELIVERABLE_WINDOW  # D50 (M1): past this age a lost alert stops counting as "behind"
```

Substitua `_BEHIND` e `order_event_alerts_behind` por:

```python
_BEHIND = text(
    """
    SELECT COUNT(*)
    FROM order_events e
    JOIN orders o ON o.id = e.order_id
    WHERE NOT o.replay AND e.type = ANY(:types)
      AND e.recorded_at < clock_timestamp() - :lookback
      AND e.recorded_at >= clock_timestamp() - :window
      AND e.recorded_at >= (SELECT min(m.recorded_at) FROM alert_event_marks m)
      AND NOT EXISTS (SELECT 1 FROM alert_event_marks m WHERE m.order_event_id = e.id)
    """
)
```

```python
def order_event_alerts_behind(conn: Connection) -> int:
    """D35 + D50: unmarked alertable events that aged past the lookback after alerting started, within the cap."""
    return int(conn.execute(_BEHIND, {
        "types": list(ORDER_EVENT_ALERT_TYPES), "lookback": ORDER_EVENT_LOOKBACK, "window": ORDER_EVENT_BEHIND_WINDOW,
    }).scalar_one())
```

Em `src/virtual_orders/readmodels/health.py`, substitua `_JOB_RUNS` e `missing_job_runs` por:

```python
_JOB_ANCHOR = text(
    """
    SELECT min((latest.detail->>'session_day')::date)
    FROM evaluation_runs r
    JOIN LATERAL (
        SELECT detail FROM evaluation_run_status s WHERE s.run_id = r.run_id ORDER BY s.id DESC LIMIT 1
    ) latest ON true
    WHERE r.kind IN ('OPENING', 'END_OF_DAY') AND latest.detail->>'session_day' IS NOT NULL
    """
)

_JOB_RUNS = text(
    """
    SELECT r.kind, (latest.detail->>'session_day')::date AS session_day, latest.status
    FROM evaluation_runs r
    JOIN LATERAL (
        SELECT status, detail FROM evaluation_run_status s WHERE s.run_id = r.run_id ORDER BY s.id DESC LIMIT 1
    ) latest ON true
    WHERE r.kind IN ('OPENING', 'END_OF_DAY') AND latest.detail->>'session_day' IS NOT NULL
      AND (latest.detail->>'session_day')::date >= :since_day
    """
)


def missing_job_runs(conn: Connection, *, now: datetime) -> dict[str, list[str]]:
    """D38: past sessions after the first recorded OPENING/END_OF_DAY session without a COMPLETED run past grace.

    D50 (T9): the anchor is one aggregate; only runs of the sessions actually checked are read."""
    anchor = conn.execute(_JOB_ANCHOR).scalar_one()
    if anchor is None:
        return {}  # no scheduled job ever ran here: a fresh database is not degraded
    start = max(datetime.combine(anchor, time(12), tzinfo=UTC), now - MISSING_RUN_LOOKBACK)
    if start >= now:
        return {}
    sessions = [s for s in calendar_for_window(start, now).sessions if s.day > anchor]
    if not sessions:
        return {}
    rows = conn.execute(_JOB_RUNS, {"since_day": sessions[0].day}).all()
    completed = {(row.kind, row.session_day) for row in rows if row.status == "COMPLETED"}
    missing: dict[str, list[str]] = {}
    for session in sessions:
        if session.open_utc + OPENING_GRACE <= now and ("OPENING", session.day) not in completed:
            missing.setdefault("OPENING", []).append(session.day.isoformat())
        eod_deadline = max(
            session.close_utc + END_OF_DAY_GRACE, datetime.combine(session.day, END_OF_DAY_FLOOR, tzinfo=_MARKET_TZ)
        )
        if eod_deadline <= now and ("END_OF_DAY", session.day) not in completed:
            missing.setdefault("END_OF_DAY", []).append(session.day.isoformat())
    return missing
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/integration/test_alert_outbox.py tests/integration/api/test_health_hardening.py tests/worker/test_window_parity.py tests/readmodels && uv run ruff check src tests migrations && uv run mypy`
Expected: PASS. Os testes da D38 do 3B (`test_missing_opening_and_end_of_day_runs_are_visible_after_their_grace`, `test_half_day_end_of_day_deadline_follows_the_1830_retry_not_the_close`, `test_without_any_opening_or_end_of_day_run_nothing_is_missing`) continuam verdes: a semântica é a mesma.

- [ ] **Step 5: Commit**

```bash
git add src/virtual_orders/alerts/outbox.py src/virtual_orders/readmodels/health.py tests/worker/test_window_parity.py tests/integration/test_alert_outbox.py tests/integration/api/test_health_hardening.py
git commit -m "fix(health): cap ORDER_EVENT_ALERTS_BEHIND by age, bound job-run reads, pin duplicated windows"
```

---

### Task 4: Watchlist isola exceção inesperada de ingestão por ticker (entrada 18; D51)

**Files:**
- Modify: `src/virtual_orders/alerts/watch.py`
- Modify: `tests/integration/test_watchlist_alerts.py` (teste acrescentado)

**Interfaces:**
- Consumes: `run_watchlist_cycle`, `ERROR_PREFIX = "ERROR:"` (`virtual_orders.evaluator.outcomes`, já importado em `watch.py`).
- Produces: `WatchlistReport.ingest_failures` pode conter `"ERROR:<Type>"` por feed; o run fecha `COMPLETED`.

- [ ] **Step 1: Write the failing test**

Acrescente a `tests/integration/test_watchlist_alerts.py`:

```python
def test_an_unexpected_ingest_error_is_isolated_to_its_ticker(engine):
    class ExplodingForAapl(FakeBarSource):
        def fetch_bars(self, ticker, start, end):
            if ticker == "AAPL":
                raise RuntimeError("provider-secret-text")
            return super().fetch_bars(ticker, start, end)

    fast = add_rule(engine, cross())  # MSFT, sorted after AAPL: it must still be ingested and evaluated
    with engine.begin() as conn:
        add_ticker(conn, "AAPL", added_at=CREATED)
    source = ExplodingForAapl(crossing_bars())

    report = cycle(engine, source, "10:30")

    assert report.ingest_failures == {f"{PRICE_SOURCE}:AAPL": "ERROR:RuntimeError"}
    assert key("PRICE_CROSS", fast, "10:00") in report.enqueued
    with engine.connect() as conn:
        status, detail = latest_run_status(conn, report.run_id)
    assert status is RunStatus.COMPLETED and "provider-secret-text" not in str(detail)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_watchlist_alerts.py::test_an_unexpected_ingest_error_is_isolated_to_its_ticker`
Expected: FAIL — `RuntimeError: provider-secret-text` propagado de `run_watchlist_cycle`.

- [ ] **Step 3: Implement**

Em `src/virtual_orders/alerts/watch.py`, dentro de `_ingest`, substitua:

```python
        try:
            ingest_bars(engine, source, ticker, begin, end)
        except (SourceUnavailable, SourceDataError) as exc:
            failures[feed] = type(exc).__name__
```

por:

```python
        try:
            ingest_bars(engine, source, ticker, begin, end)
        except (SourceUnavailable, SourceDataError) as exc:
            failures[feed] = type(exc).__name__
        except Exception as exc:  # noqa: BLE001 - D51 (T13): one ticker's unexpected error never aborts the others
            failures[feed] = f"{ERROR_PREFIX}{type(exc).__name__}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/integration/test_watchlist_alerts.py && uv run ruff check src tests migrations && uv run mypy`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/virtual_orders/alerts/watch.py tests/integration/test_watchlist_alerts.py
git commit -m "fix(alerts): isolate unexpected watchlist ingest errors per ticker"
```

---

### Task 5: Recheck — `UNIQUE(order_id, session_date)` e `PROVIDER_FAILURE_FINAL` (entradas 9 e 16; D52)

**Files:**
- Create: `migrations/versions/0004_recheck_session_unique.py`
- Modify: `src/virtual_orders/readmodels/health.py` (`EXPECTED_SCHEMA_REVISION`)
- Modify: `src/virtual_orders/evaluator/recheck.py`
- Create: `tests/integration/test_schema_0004.py`
- Modify: `tests/integration/test_quality_recheck.py` (testes acrescentados)
- Create: `tests/evaluator/__init__.py`, `tests/evaluator/test_recheck_levels.py`
- Modify: `tests/integration/api/test_health_api.py` (head esperado)

**Interfaces:**
- Consumes: `run_quality_recheck`, `RECHECK_FINAL_AFTER_SESSIONS`, `alembic_config`, `start_run`, `acquire_data_as_of`, `submit_default`.
- Produces:
  - `virtual_orders.evaluator.recheck.RECHECK_PROVIDER_FAILURE_FINAL = "PROVIDER_FAILURE_FINAL"`;
  - `virtual_orders.evaluator.recheck.RECHECK_UNVERIFIED_REVIEW = "DATA_QUALITY_UNVERIFIED"` (motivo do `NEEDS_REVIEW` do caso final, D52);
  - constraint `data_quality_rechecks_order_session_key`;
  - `EXPECTED_SCHEMA_REVISION = "0004"`.

- [ ] **Step 1: Write the failing tests**

`tests/integration/test_schema_0004.py`:

```python
from datetime import UTC, date, datetime

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from tests.integration.support import CODE_VERSION, alembic_config, count, submit_default
from virtual_orders.ledger.runs import RunKind, start_run
from virtual_orders.marketdata.asof import acquire_data_as_of
from virtual_orders.storage import tables

NOW = datetime(2025, 11, 26, 21, 0, tzinfo=UTC)
CONSTRAINT = "data_quality_rechecks_order_session_key"


def recheck_row(conn, order_id, run_id, session_date, key):
    conn.execute(tables.data_quality_rechecks.insert().values(
        order_id=order_id, session_date=session_date, recheck_key=key, run_id=run_id, source_run_id=run_id,
        data_as_of=NOW, payload={},
    ))


def constraints_and_indexes(engine):
    with engine.connect() as conn:
        names = set(conn.execute(text(
            "SELECT conname FROM pg_constraint WHERE conrelid = 'data_quality_rechecks'::regclass"
        )).scalars())
        indexes = set(conn.execute(text(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'data_quality_rechecks'"
        )).scalars())
    return names, indexes


def test_a_second_recheck_row_for_the_same_order_and_session_is_rejected(engine):
    order_id = submit_default(engine).auto_order_id
    as_of = acquire_data_as_of(engine)
    with engine.begin() as conn:
        run = start_run(conn, RunKind.QUALITY_RECHECK, as_of, CODE_VERSION)
        recheck_row(conn, order_id, run.run_id, date(2025, 11, 25), "DATA_QUALITY_RECHECK:2025-11-25:a")
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            recheck_row(conn, order_id, run.run_id, date(2025, 11, 25), "DATA_QUALITY_RECHECK:2025-11-25:b")
    with engine.begin() as conn:
        recheck_row(conn, order_id, run.run_id, date(2025, 11, 26), "DATA_QUALITY_RECHECK:2025-11-26:a")
    assert count(engine, "data_quality_rechecks") == 2


def test_downgrade_to_0003_and_back(database_url, engine):
    config = alembic_config(database_url)
    command.downgrade(config, "0003")
    names, indexes = constraints_and_indexes(engine)
    assert CONSTRAINT not in names and "data_quality_rechecks_session_idx" in indexes
    command.upgrade(config, "head")
    names, indexes = constraints_and_indexes(engine)
    assert CONSTRAINT in names and "data_quality_rechecks_session_idx" not in indexes
```

Em `tests/integration/test_quality_recheck.py`:
- acrescente `PRICE_SOURCE,` ao bloco `from tests.integration.support import (...)`, entre `DAY,` e `TICKER,`;
- acrescente `from virtual_orders.evaluator.commands import flag_order_review` aos imports;
- troque `from virtual_orders.readmodels.quality import pending_quality_sessions` por `from virtual_orders.readmodels.quality import PendingQuality, pending_quality_sessions`;
- acrescente ao final:

```python
def test_provider_failure_after_the_lookback_gets_a_final_row_a_review_and_clears_the_pending(engine):
    order_id, source = skipped_first_session(engine, session_bars(DAY) + session_bars(NEXT))
    source.failing.add(TICKER)  # the feed is still down five sessions later
    key = f"{order_id}:{DAY}"

    report = recheck(engine, source, et("2025-12-03", "16:45"))  # five sessions closed after DAY

    assert report.terminal == {key: "PROVIDER_FAILURE"} and set(report.rechecked) == {key}
    assert report.not_evaluated == {}
    (row,) = _rows(engine)
    assert row.payload["status"] == "PROVIDER_FAILURE_FINAL" and row.payload["terminal_reason"] == "PROVIDER_FAILURE"
    reviews = [(e.event_key, e.payload["reason"], e.payload["ref"])
               for e in events(engine, order_id) if e.type == "NEEDS_REVIEW"]
    assert reviews == [(f"NEEDS_REVIEW:DATA_QUALITY_UNVERIFIED:{DAY}", "DATA_QUALITY_UNVERIFIED", DAY)]  # spec 6
    again = flag_order_review(engine, order_id, reason="DATA_QUALITY_UNVERIFIED", ref=DAY)
    assert again.error is None and again.event_keys == ()  # idempotent by event_key
    with engine.connect() as conn:
        assert pending_quality_sessions(conn) == []
        assert order_detail(conn, order_id)["order"]["needs_review"] is True  # leaves the default metrics (D34)


def test_a_session_evaluated_by_end_of_day_after_the_pending_read_is_left_alone(engine, monkeypatch):
    """T10: the pending list is read outside the order lock, so END_OF_DAY can write DATA_QUALITY in between."""
    order_id = submit_default(engine).auto_order_id
    source = FakeBarSource(session_bars(DAY) + session_bars(NEXT))
    first = end_of_day(engine, source, SESSION, et(DAY, "16:30"))
    end_of_day(engine, source, NEXT_SESSION, et(NEXT, "16:30"))
    assert first.quality is not None
    assert f"DATA_QUALITY:{DAY}" in [e.event_key for e in events(engine, order_id)]
    stale = PendingQuality(order_id, SESSION, first.quality.run_id, "PROVIDER_FAILURE", TICKER, PRICE_SOURCE)
    monkeypatch.setattr(recheck_module, "pending_quality_sessions", lambda conn: [stale])  # read before END_OF_DAY

    report = recheck(engine, source, et(NEXT, "16:45"))

    assert report.not_evaluated == {f"{order_id}:{DAY}": "ALREADY_EVALUATED"}
    assert report.rechecked == {} and report.terminal == {}
    assert count(engine, "data_quality_rechecks") == 0
    assert not [e for e in events(engine, order_id) if e.type == "NEEDS_REVIEW"]
```

Crie `tests/evaluator/__init__.py` vazio e `tests/evaluator/test_recheck_levels.py`:

```python
"""T10 (Plan 3B entry 16): the recheck's level-touch check uses the stop in force at the session close, rebuilt from
the ledger, never the projection's current stop. In fill_model v1 both agree at integration level (one stop move, and
the projection keeps stop_previous/stop_active_from), so the difference is pinned with a synthetic history (D55)."""

from decimal import Decimal

from core.dataquality import ReviewFlag, missing_bar_reviews
from core.domain.models import OrderState, OrderStatus
from tests.support import bar, et, long_signal
from virtual_orders.evaluator.recheck import levels_state_at_close
from virtual_orders.storage.codec import PreparedEvent

DAY, NEXT = "2025-11-25", "2025-11-26"
SIGNAL = long_signal()  # zone 100-102, stop 97, targets 106 / 110
MINUTE = et(DAY, "10:20")
TOUCHES_ONLY_101 = {MINUTE: bar(MINUTE, "101", "101.5", "100.5", "101")}  # zone edges, stop 97 and targets untouched
# A projection whose stop moved to 101 before the missing minute, while the ledger history below says otherwise.
PROJECTION = OrderState(
    status=OrderStatus.OPEN, avg_entry=Decimal("101"), initial_stop=Decimal("97"), stop_current=Decimal("101"),
    stop_previous=Decimal("97"), stop_active_from=et(DAY, "10:00"), qty_total=Decimal("25"), qty_open=Decimal("13"),
)


def target1_hit(bar_ts, active_from):
    return PreparedEvent(
        type="TARGET1_HIT", event_key="TARGET1_HIT", bar_ts=bar_ts, price=Decimal("106"), qty=Decimal("12"),
        bar_batch_id=None, payload={"new_stop_level": "101", "stop_active_from": active_from.isoformat()},
        hash_material="synthetic", payload_hash="synthetic",
    )


def test_the_projection_alone_would_flag_a_touch_of_a_stop_not_in_force_that_session():
    assert missing_bar_reviews([MINUTE], TOUCHES_ONLY_101, SIGNAL, PROJECTION) == [
        ReviewFlag("MISSING_BAR_LEVEL_TOUCH", MINUTE.isoformat())]


def test_levels_at_close_come_from_the_ledger_and_do_not_flag_that_touch():
    history = [target1_hit(et(NEXT, "11:00"), et(NEXT, "11:01"))]  # the stop only moved on the next session

    derived = levels_state_at_close(PROJECTION, SIGNAL.stop, history, "v1", et(DAY, "16:00"))

    assert derived is not None
    assert (derived.stop_current, derived.stop_previous, derived.stop_active_from) == (Decimal("97"), None, None)
    assert missing_bar_reviews([MINUTE], TOUCHES_ONLY_101, SIGNAL, derived) == []


def test_a_stop_move_before_the_minute_is_honoured_by_the_derivation():
    history = [target1_hit(et(DAY, "10:10"), et(DAY, "10:11"))]

    derived = levels_state_at_close(PROJECTION, SIGNAL.stop, history, "v1", et(DAY, "16:00"))

    assert derived is not None
    assert (derived.stop_current, derived.stop_previous, derived.stop_active_from) == (
        Decimal("101"), Decimal("97"), et(DAY, "10:11"))
    assert missing_bar_reviews([MINUTE], TOUCHES_ONLY_101, SIGNAL, derived) == [
        ReviewFlag("MISSING_BAR_LEVEL_TOUCH", MINUTE.isoformat())]
```

Em `tests/integration/api/test_health_api.py`, no teste `test_schema_behind_the_migration_head_is_503_without_facts`, troque `"detail": {"expected": "0003", "found": "0001"}` por `"detail": {"expected": "0004", "found": "0001"}`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/integration/test_schema_0004.py tests/integration/test_quality_recheck.py tests/evaluator/test_recheck_levels.py`
Expected:
- `test_a_second_recheck_row_for_the_same_order_and_session_is_rejected` falha com `DID NOT RAISE IntegrityError`;
- `test_downgrade_to_0003_and_back` falha na última asserção: com a head ainda em `0003`, o downgrade e o upgrade são no-op e a constraint não existe;
- `test_provider_failure_after_the_lookback_gets_a_final_row_a_review_and_clears_the_pending` falha com `report.terminal == {}` e `not_evaluated == {key: "PROVIDER_FAILURE"}`;
- `test_a_session_evaluated_by_end_of_day_after_the_pending_read_is_left_alone` e os três testes de `tests/evaluator/test_recheck_levels.py` **passam já de início**: são fixações do comportamento do 3B que antes não tinha teste (M15), não uma fase vermelha;
- os demais testes de `test_quality_recheck.py` continuam verdes.

- [ ] **Step 3: Implement**

`migrations/versions/0004_recheck_session_unique.py`:

```python
"""One recheck row per (order, session), enforced by the schema (Plan 3B close-out T10; Plan 3C D52).

Revision ID: 0004
Revises: 0003
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Fails loudly if duplicates already exist: append-only history is never rewritten to satisfy a constraint.
    op.execute(
        "ALTER TABLE data_quality_rechecks "
        "ADD CONSTRAINT data_quality_rechecks_order_session_key UNIQUE (order_id, session_date)"
    )
    op.execute("DROP INDEX data_quality_rechecks_session_idx")  # the unique index covers the same columns


def downgrade() -> None:
    op.execute("CREATE INDEX data_quality_rechecks_session_idx ON data_quality_rechecks (order_id, session_date)")
    op.execute("ALTER TABLE data_quality_rechecks DROP CONSTRAINT data_quality_rechecks_order_session_key")
```

Em `src/virtual_orders/readmodels/health.py`: `EXPECTED_SCHEMA_REVISION = "0004"  # alembic head; pinned by test_expected_schema_revision_is_the_migration_head`.

Em `src/virtual_orders/evaluator/recheck.py`:

1. Abaixo de `RECHECK_NO_OBSERVATIONS_FINAL = "NO_OBSERVATIONS_FINAL"`:

```python
RECHECK_PROVIDER_FAILURE_FINAL = "PROVIDER_FAILURE_FINAL"  # D52: a feed still failing after the final lookback
RECHECK_UNVERIFIED_REVIEW = "DATA_QUALITY_UNVERIFIED"  # D52 + spec 6: unverifiable minutes -> NEEDS_REVIEW with reason
```

2. Na assinatura de `_recheck_command`, troque o parâmetro `final_no_observations: bool,` por `past_final_lookback: bool,` (a chamada em `run_quality_recheck` passa o valor posicionalmente e não muda).
3. Dentro de `command`, substitua:

```python
        if feed_failed:
            return skip(inp, "PROVIDER_FAILURE")  # D12: retried by a later recheck
```

por:

```python
        if feed_failed:
            if past_final_lookback:  # D52: never let a failing feed drop out of /health without a terminal row
                final(inp, RECHECK_PROVIDER_FAILURE_FINAL, "PROVIDER_FAILURE")
                # Spec 6: the session's minutes can no longer be verified. Same locked path as flag_order_review
                # (D34); idempotent by event_key NEEDS_REVIEW:DATA_QUALITY_UNVERIFIED:<session>.
                flagged: StepResult = inp.model.flag_review(state, RECHECK_UNVERIFIED_REVIEW, day)
                return flagged
            return skip(inp, "PROVIDER_FAILURE")  # D12: retried by a later recheck
```

`NO_OBSERVATIONS_FINAL` continua chamando só `final(...)`, sem revisão (D12(a), D52).

4. No mesmo `command`, troque `if final_no_observations:` por `if past_final_lookback:`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/integration/test_schema_0004.py tests/integration/test_schema_0003.py tests/integration/test_schema.py tests/integration/test_quality_recheck.py tests/evaluator tests/integration/api/test_health_api.py tests/readmodels/test_health_rules.py && uv run ruff check src tests migrations && uv run mypy`
Expected: PASS, inclusive `test_migrations_run_from_zero_and_back`, `test_downgrade_to_0002_and_back`, `test_expected_schema_revision_is_the_migration_head` e o teste do 3B `test_no_observations_after_the_lookback_gets_a_final_row_and_clears_the_pending`, que continua afirmando **nenhum** `NEEDS_REVIEW` para `NO_OBSERVATIONS_FINAL` (D12(a), D52).

- [ ] **Step 5: Commit**

```bash
git add migrations/versions/0004_recheck_session_unique.py src/virtual_orders/readmodels/health.py src/virtual_orders/evaluator/recheck.py tests/integration/test_schema_0004.py tests/integration/test_quality_recheck.py tests/evaluator/__init__.py tests/evaluator/test_recheck_levels.py tests/integration/api/test_health_api.py
git commit -m "feat(quality): unique recheck per order and session; a lasting provider failure ends with a terminal row and a review"
```

---

### Task 6: Módulos puros — VWAP de sessão e marcação do portfólio virtual (D43, D45)

**Files:**
- Create: `src/virtual_orders/analytics/vwap.py`
- Create: `src/virtual_orders/analytics/portfolio.py`
- Create: `tests/analytics/test_vwap.py`, `tests/analytics/test_portfolio.py`
- Modify: `tests/test_import_boundaries.py` (`PLATFORM_PURE_MODULES`)

**Interfaces:**
- Consumes: `core.domain.models.Bar`, `core.domain.models.Direction`, `core.domain.hashing.CANONICAL_CONTEXT`.
- Produces:
  - `virtual_orders.analytics.vwap`: `METHOD = "SESSION_VWAP_TYPICAL_PRICE_V1"`, `VwapPoint(ts: datetime, value: Decimal)`, `session_vwap(bars: Sequence[Bar]) -> list[VwapPoint]`;
  - `virtual_orders.analytics.portfolio`:
    - `BASIS = "PER_1R_NORMALIZED"`, `DISCLAIMER: str`;
    - `OpenPosition(order_id, ticker, direction, strategy, origin, status, frozen, qty_open, avg_entry, stop_current, risk_amount, realized_pnl, costs, dividends, last_close, last_close_ts)`;
    - `MarkedPosition(position, notional, unrealized_pnl, unrealized_r, open_r, allocation_pct)`;
    - `PortfolioTotals(positions, marked, unmarked, notional, unrealized_pnl, unrealized_r, risk_amount)`;
    - `VirtualPortfolio(basis, disclaimer, positions, totals)`;
    - `mark_portfolio(positions: Sequence[OpenPosition]) -> VirtualPortfolio`.

- [ ] **Step 1: Write the failing tests**

`tests/analytics/test_vwap.py`:

```python
from decimal import Decimal

import pytest

from core.domain.models import Bar
from tests.support import et
from virtual_orders.analytics.vwap import METHOD, session_vwap


def candle(day, hm, o, h, l, c, v):  # noqa: E741
    return Bar(ts=et(day, hm), open=Decimal(o), high=Decimal(h), low=Decimal(l), close=Decimal(c), volume=Decimal(v))


def test_vwap_is_cumulative_per_session_and_restarts_each_et_date():
    bars = [
        candle("2025-11-26", "09:30", "20", "21", "19", "20", "50"),
        candle("2025-11-25", "09:32", "12", "12", "12", "12", "0"),
        candle("2025-11-25", "09:30", "10", "11", "9", "10", "100"),
        candle("2025-11-25", "09:31", "12", "13", "11", "12", "300"),
    ]
    points = session_vwap(bars)
    assert [(p.ts, str(p.value)) for p in points] == [
        (et("2025-11-25", "09:30"), "10.0000"),
        (et("2025-11-25", "09:31"), "11.5000"),  # (10*100 + 12*300) / 400
        (et("2025-11-25", "09:32"), "11.5000"),  # zero volume keeps the running value
        (et("2025-11-26", "09:30"), "20.0000"),  # new ET date: restarted
    ]
    assert METHOD == "SESSION_VWAP_TYPICAL_PRICE_V1"


def test_without_volume_yet_the_vwap_is_the_close():
    (point,) = session_vwap([candle("2025-11-25", "09:30", "5", "6", "4", "5.5", "0")])
    assert str(point.value) == "5.5000"


def test_duplicate_minutes_are_rejected():
    bar = candle("2025-11-25", "09:30", "10", "11", "9", "10", "100")
    with pytest.raises(ValueError, match="duplicate bar minute"):
        session_vwap([bar, bar])
```

`tests/analytics/test_portfolio.py`:

```python
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from core.domain.models import Direction
from virtual_orders.analytics.portfolio import BASIS, OpenPosition, mark_portfolio

MARK_TS = datetime(2025, 11, 25, 15, 29, tzinfo=UTC)


def position(**overrides):
    values = dict(
        order_id=UUID(int=1), ticker="AAPL", direction=Direction.LONG, strategy="REXSHARE", origin="AUTO_STRATEGY",
        status="OPEN", frozen=False, qty_open=Decimal("25"), avg_entry=Decimal("101"), stop_current=Decimal("97"),
        risk_amount=Decimal("100"), realized_pnl=Decimal("0"), costs=Decimal("0"), dividends=Decimal("0"),
        last_close=Decimal("103"), last_close_ts=MARK_TS,
    )
    values.update(overrides)
    return OpenPosition(**values)


def test_long_and_short_positions_are_marked_per_1r_with_allocation():
    short = position(order_id=UUID(int=2), ticker="MSFT", direction=Direction.SHORT, qty_open=Decimal("10"),
                     avg_entry=Decimal("50"), last_close=Decimal("45"), realized_pnl=Decimal("20"),
                     costs=Decimal("1"), dividends=Decimal("-2"))
    portfolio = mark_portfolio([short, position()])

    assert portfolio.basis == BASIS == "PER_1R_NORMALIZED" and "not account money" in portfolio.disclaimer
    assert "gross of exit costs" in portfolio.disclaimer  # M9: marks are not net of the exit that would close them
    rows = [(m.position.ticker, str(m.notional), str(m.unrealized_pnl), str(m.unrealized_r), str(m.open_r),
             str(m.allocation_pct)) for m in portfolio.positions]
    assert rows == [
        ("AAPL", "2575.00", "50.00", "0.5000", "0.5000", "85.1240"),
        ("MSFT", "450.00", "50.00", "0.5000", "0.6700", "14.8760"),  # SHORT: (45-50)*10 mirrored; (20-1-2+50)/100
    ]
    totals = portfolio.totals
    assert (totals.positions, totals.marked, totals.unmarked) == (2, 2, 0)
    assert (str(totals.notional), str(totals.unrealized_pnl), str(totals.unrealized_r), str(totals.risk_amount)) == (
        "3025.00", "100.00", "1.0000", "200.00")


def test_a_position_without_a_stored_close_is_unmarked_and_left_out_of_totals():
    portfolio = mark_portfolio([position(), position(order_id=UUID(int=3), ticker="NVDA", last_close=None,
                                                     last_close_ts=None)])
    nvda = portfolio.positions[1]
    assert nvda.position.ticker == "NVDA"
    assert (nvda.notional, nvda.unrealized_pnl, nvda.unrealized_r, nvda.open_r, nvda.allocation_pct) == (
        None, None, None, None, None)
    assert (portfolio.totals.positions, portfolio.totals.marked, portfolio.totals.unmarked) == (2, 1, 1)
    assert str(portfolio.positions[0].allocation_pct) == "100.0000"
    assert str(portfolio.totals.notional) == "2575.00"


def test_an_empty_portfolio_has_zero_totals():
    totals = mark_portfolio([]).totals
    assert (totals.positions, str(totals.notional), str(totals.unrealized_r)) == (0, "0.00", "0.0000")
```

Em `tests/test_import_boundaries.py`, troque a lista `PLATFORM_PURE_MODULES` por:

```python
PLATFORM_PURE_MODULES = [
    "virtual_orders/analytics/pressure.py", "virtual_orders/alerts/rules.py",
    "virtual_orders/analytics/vwap.py", "virtual_orders/analytics/portfolio.py",
]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/analytics/test_vwap.py tests/analytics/test_portfolio.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'virtual_orders.analytics.vwap'` (e `portfolio`).

- [ ] **Step 3: Implement**

`src/virtual_orders/analytics/vwap.py`:

```python
"""Session-anchored VWAP for charts (D43). Pure and deterministic; not the pressure window VWAP (D27)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from zoneinfo import ZoneInfo

from core.domain.hashing import CANONICAL_CONTEXT
from core.domain.models import Bar

METHOD = "SESSION_VWAP_TYPICAL_PRICE_V1"
QUANTUM = Decimal("0.0001")
ZERO = Decimal(0)
THREE = Decimal(3)
_MARKET_TZ = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class VwapPoint:
    ts: datetime
    value: Decimal


def session_vwap(bars: Sequence[Bar]) -> list[VwapPoint]:
    """Running sum(typical price * volume) / sum(volume), restarted at each ET date; the close while volume is zero."""
    ordered = sorted(bars, key=lambda item: item.ts)
    if len({item.ts for item in ordered}) != len(ordered):
        raise ValueError("duplicate bar minute")
    points: list[VwapPoint] = []
    session: date | None = None
    flow = volume = ZERO
    with localcontext(CANONICAL_CONTEXT):
        for item in ordered:
            day = item.ts.astimezone(_MARKET_TZ).date()
            if day != session:
                session, flow, volume = day, ZERO, ZERO
            flow += (item.high + item.low + item.close) / THREE * item.volume
            volume += item.volume
            value = item.close if volume == 0 else flow / volume
            points.append(VwapPoint(item.ts, value.quantize(QUANTUM, rounding=ROUND_HALF_EVEN)))
    return points
```

`src/virtual_orders/analytics/portfolio.py`:

```python
"""Virtual portfolio marking (D45). Pure: positions are sized per 1R of risk, never account money (spec 1.2 item 2)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from uuid import UUID

from core.domain.hashing import CANONICAL_CONTEXT
from core.domain.models import Direction

BASIS = "PER_1R_NORMALIZED"
DISCLAIMER = (
    "Virtual positions sized per 1R of risk (risk_amount), not account money: no cash, limits or overlap "
    "constraints (that is the Portfolio Simulator, a separate sub-project). Unrealized P&L is gross of exit costs "
    "and stop slippage, marked at the last stored close, which can belong to an earlier session."
)
MONEY_QUANTUM = Decimal("0.01")
RATIO_QUANTUM = Decimal("0.0001")
ZERO = Decimal(0)
HUNDRED = Decimal(100)


@dataclass(frozen=True)
class OpenPosition:
    order_id: UUID
    ticker: str
    direction: Direction
    strategy: str
    origin: str
    status: str
    frozen: bool
    qty_open: Decimal
    avg_entry: Decimal
    stop_current: Decimal | None
    risk_amount: Decimal
    realized_pnl: Decimal
    costs: Decimal
    dividends: Decimal
    last_close: Decimal | None
    last_close_ts: datetime | None


@dataclass(frozen=True)
class MarkedPosition:
    position: OpenPosition
    notional: Decimal | None
    unrealized_pnl: Decimal | None
    unrealized_r: Decimal | None
    open_r: Decimal | None
    allocation_pct: Decimal | None


@dataclass(frozen=True)
class PortfolioTotals:
    positions: int
    marked: int
    unmarked: int
    notional: Decimal
    unrealized_pnl: Decimal
    unrealized_r: Decimal
    risk_amount: Decimal


@dataclass(frozen=True)
class VirtualPortfolio:
    basis: str
    disclaimer: str
    positions: tuple[MarkedPosition, ...]
    totals: PortfolioTotals


def _quantized(value: Decimal, quantum: Decimal) -> Decimal:
    with localcontext(CANONICAL_CONTEXT):
        result = value.quantize(quantum, rounding=ROUND_HALF_EVEN)
    return result if result != 0 else ZERO.quantize(quantum)  # never "-0.00"


def mark_portfolio(positions: Sequence[OpenPosition]) -> VirtualPortfolio:
    ordered = sorted(positions, key=lambda item: (item.ticker, str(item.order_id)))
    marked: list[MarkedPosition] = []
    with localcontext(CANONICAL_CONTEXT):
        notional_total = sum((item.last_close * item.qty_open for item in ordered if item.last_close is not None), ZERO)
        unrealized_total = r_total = risk_total = ZERO
        for item in ordered:
            if item.last_close is None:
                marked.append(MarkedPosition(item, None, None, None, None, None))
                continue
            move = item.last_close - item.avg_entry
            pnl = move * item.qty_open if item.direction is Direction.LONG else -move * item.qty_open
            notional = item.last_close * item.qty_open
            unrealized_r = pnl / item.risk_amount
            open_r = (item.realized_pnl - item.costs + item.dividends + pnl) / item.risk_amount
            allocation = ZERO if notional_total == 0 else notional / notional_total * HUNDRED
            unrealized_total += pnl
            r_total += unrealized_r
            risk_total += item.risk_amount
            marked.append(MarkedPosition(
                item, _quantized(notional, MONEY_QUANTUM), _quantized(pnl, MONEY_QUANTUM),
                _quantized(unrealized_r, RATIO_QUANTUM), _quantized(open_r, RATIO_QUANTUM),
                _quantized(allocation, RATIO_QUANTUM),
            ))
    count = sum(1 for item in marked if item.notional is not None)
    totals = PortfolioTotals(
        len(marked), count, len(marked) - count, _quantized(notional_total, MONEY_QUANTUM),
        _quantized(unrealized_total, MONEY_QUANTUM), _quantized(r_total, RATIO_QUANTUM),
        _quantized(risk_total, MONEY_QUANTUM),
    )
    return VirtualPortfolio(BASIS, DISCLAIMER, tuple(marked), totals)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/analytics tests/test_import_boundaries.py && uv run ruff check src tests migrations && uv run mypy`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/virtual_orders/analytics/vwap.py src/virtual_orders/analytics/portfolio.py tests/analytics/test_vwap.py tests/analytics/test_portfolio.py tests/test_import_boundaries.py
git commit -m "feat(analytics): pure session VWAP and per-1R virtual portfolio marking"
```

---

### Task 7: Read model e rotas de mercado — candles as-of, pressão rotulada e gráfico da ordem (entrada 2; D41–D44)

**Files:**
- Create: `src/virtual_orders/readmodels/market.py`
- Create: `src/virtual_orders/api/tickers.py`
- Create: `src/virtual_orders/api/routes/market.py`
- Modify: `src/virtual_orders/api/routes/watchlist.py`
- Modify: `src/virtual_orders/api/app.py`
- Create: `tests/integration/api/test_market_api.py`
- Modify: `tests/worker/test_window_parity.py` (cópias novas do fuso ET, M2)

**Interfaces:**
- Consumes:
  - `session_vwap`, `METHOD` (vwap), `estimate_pressure`, `strong_pressure`, `DISCLAIMER`, `METHOD` (pressão);
  - `read_bars_as_of`, `acquire_data_as_of`, `stored_events`, `parse_ts`, `OrderNotFound`;
  - `MIN_WINDOW_BARS`/`MAX_WINDOW_BARS` (`alerts/watchlist.py`).
- Produces:
  - `virtual_orders.api.tickers.normalize_ticker(ticker: str) -> str` (`422 TICKER_INVALID`);
  - `virtual_orders.readmodels.market`:
    - `MAX_BARS_WINDOW`, `NO_BARS`, `INSUFFICIENT_BARS`, `market_day_start(ts) -> datetime`;
    - `CandleSeries(ticker, price_source, data_as_of, start, end, vwap_method, bars, vwap)` e `candles(conn, *, ticker, price_source, start, end, as_of) -> CandleSeries`;
    - `PressureView(...)` e `latest_pressure(conn, *, ticker, price_source, window_bars, cmf_threshold, as_of) -> PressureView`;
    - `OrderChart(...)` e `order_chart(conn, order_id) -> OrderChart`;
  - rotas `GET /market/bars`, `GET /market/pressure`, `GET /orders/{order_id}/chart`.

- [ ] **Step 1: Write the failing tests**

`tests/integration/api/test_market_api.py`:

```python
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select

from tests.integration.support import CODE_VERSION, DAY, post_json, scenario_bars, signal_body
from tests.support import et
from virtual_orders.evaluator.context import DEFAULT_FILL_MODEL_VERSION
from virtual_orders.evaluator.cycle import run_live_cycle
from virtual_orders.storage import tables

MARKET_ERROR = "MARKET_REQUEST_INVALID"


def closed_order(api) -> UUID:
    order_id = UUID(post_json(api.client, "/signals", signal_body()).json()["auto_order_id"])
    api.bars.load(scenario_bars())
    for hm in ("10:30", "11:30", "13:00"):
        run_live_cycle(api.services.engine, api.services.gateway, code_version=CODE_VERSION, market_now=et(DAY, hm))
    return order_id


def get(api, path, **params):
    response = api.client.get(path, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_bars_are_stored_bars_as_of_now_with_a_session_anchored_vwap_and_no_provider_call(api):
    closed_order(api)
    calls = len(api.bars.calls)

    body = get(api, "/market/bars", ticker=" aapl ", **{"from": et(DAY, "10:00").isoformat(),
                                                        "to": et(DAY, "10:10").isoformat()})

    assert len(api.bars.calls) == calls  # stored bars only (D41)
    assert (body["ticker"], body["price_source"], body["vwap_method"]) == ("AAPL", "fake_feed",
                                                                          "SESSION_VWAP_TYPICAL_PRICE_V1")
    assert datetime.fromisoformat(body["data_as_of"]).tzinfo is not None
    assert [b["ts"] for b in body["bars"]] == [et(DAY, f"10:0{m}").isoformat() for m in range(10)]
    assert (body["bars"][5]["open"], body["bars"][5]["volume"]) == ("101", "1000")
    assert body["vwap"][0] == {"ts": et(DAY, "10:00").isoformat(), "value": "105"}  # anchored at 09:30, not 10:00
    assert body["vwap"][5] == {"ts": et(DAY, "10:05").isoformat(), "value": "104.8907"}


def test_bar_windows_are_validated(api):
    start = et(DAY, "10:00").isoformat()
    cases = [
        ({"ticker": "AAPL", "from": start, "to": et(DAY, "09:00").isoformat()}, ["EMPTY_WINDOW"]),
        ({"ticker": "AAPL", "from": start, "to": et("2025-12-03", "10:00").isoformat()}, ["WINDOW_TOO_LARGE"]),
        ({"ticker": "AAPL", "from": "2025-11-25T10:00:00", "to": "2025-11-25T11:00:00"},
         ["NAIVE_DATETIME:from", "NAIVE_DATETIME:to"]),
    ]
    for params, errors in cases:
        response = api.client.get("/market/bars", params=params)
        assert response.status_code == 422, params
        assert response.json() == {"error": {"code": MARKET_ERROR, "reason": None, "detail": {"errors": errors}}}
    blank = api.client.get("/market/bars", params={"ticker": "  ", "from": start, "to": et(DAY, "11:00").isoformat()})
    assert blank.status_code == 422 and blank.json()["error"]["code"] == "TICKER_INVALID"
    missing = api.client.get("/market/bars", params={"ticker": "AAPL"})
    assert missing.status_code == 422 and missing.json()["error"]["code"] == "REQUEST_INVALID"


def test_pressure_is_always_labelled_as_an_estimate(api):
    closed_order(api)  # bars up to 12:50: the last 30 are 12:21-12:49 flat at 107 and the 12:50 bar closing 110

    plain = get(api, "/market/pressure", ticker="AAPL")
    assert (plain["estimate"], plain["method"], plain["available"], plain["reason"]) == (
        True, "OHLCV_PRESSURE_ESTIMATE_V1", True, None)
    assert plain["disclaimer"] == "Estimate derived from 1-minute OHLCV bars; it is not order-flow or trade-side data."
    values = plain["values"]
    assert (values["bars"], values["last_bar_ts"]) == (30, et(DAY, "12:50").isoformat())
    assert (values["chaikin_money_flow"], values["obv_slope"]) == ("0.0137", "0.0345")
    assert plain["side"] is None and plain["cmf_threshold"] is None

    assert get(api, "/market/pressure", ticker="AAPL", cmf_threshold="0.01")["side"] == "BUY"
    assert get(api, "/market/pressure", ticker="AAPL", cmf_threshold="0.3")["side"] is None

    short = get(api, "/market/pressure", ticker="AAPL", window_bars=300)
    assert (short["available"], short["reason"], short["values"], short["estimate"]) == (
        False, "INSUFFICIENT_BARS", None, True)
    nothing = get(api, "/market/pressure", ticker="MSFT")
    assert (nothing["available"], nothing["reason"], nothing["method"]) == (False, "NO_BARS",
                                                                           "OHLCV_PRESSURE_ESTIMATE_V1")


def test_pressure_parameters_are_validated(api):
    for params, errors in (
        ({"window_bars": 4}, ["OUT_OF_RANGE:window_bars"]),
        ({"cmf_threshold": "1"}, ["OUT_OF_RANGE:cmf_threshold"]),
        ({"window_bars": 391, "cmf_threshold": "0"}, ["OUT_OF_RANGE:window_bars", "OUT_OF_RANGE:cmf_threshold"]),
    ):
        response = api.client.get("/market/pressure", params={"ticker": "AAPL", **params})
        assert response.status_code == 422, params
        assert response.json()["error"] == {"code": MARKET_ERROR, "reason": None, "detail": {"errors": errors}}


def test_order_chart_has_levels_markers_fill_model_and_a_bar_window(api):
    order_id = closed_order(api)

    chart = get(api, f"/orders/{order_id}/chart")
    detail = get(api, f"/orders/{order_id}")

    assert (chart["ticker"], chart["direction"], chart["status"], chart["price_source"]) == (
        "AAPL", "LONG", "CLOSED", "fake_feed")
    levels = chart["levels"]
    assert {name: levels[name] for name in ("entry_zone_low", "entry_zone_high", "stop", "target1", "target2",
                                            "trigger_price")} == {
        "entry_zone_low": "100", "entry_zone_high": "102", "stop": "97", "target1": "106", "target2": "110",
        "trigger_price": None,
    }
    assert (levels["avg_entry"], levels["stop_current"]) == (detail["order"]["avg_entry"],
                                                            detail["order"]["stop_current"])
    assert [(m["type"], m["ts"], m["price"]) for m in chart["markers"]] == [
        ("FILLED", et(DAY, "10:05").isoformat(), "101"),
        ("TARGET1_HIT", et(DAY, "11:00").isoformat(), "106"),
        ("TARGET2_HIT", et(DAY, "12:50").isoformat(), "110"),
    ]
    with api.services.engine.connect() as conn:
        snapshot = conn.execute(select(tables.orders.c.config_snapshot)
                                .where(tables.orders.c.id == order_id)).scalar_one()
    assert chart["fill_model_version"] == DEFAULT_FILL_MODEL_VERSION and chart["config_snapshot"] == snapshot
    final = datetime.fromisoformat(detail["state"]["final_event_ts"])
    assert chart["window"] == {"from": et(DAY, "00:00").isoformat(),
                               "to": (final + timedelta(minutes=1)).isoformat()}
    assert chart["evaluation_start_ts"] == et(DAY, "09:30").isoformat()
    assert chart["window_truncated"] is False  # closed the same day: far inside the 7-day cap (M10)

    missing = api.client.get(f"/orders/{uuid4()}/chart")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "ORDER_NOT_FOUND"
```

Em `tests/worker/test_window_parity.py`, acrescente `from virtual_orders.analytics import vwap` e `from virtual_orders.readmodels import market` aos imports e, ao final:

```python
def test_3c_market_zone_copies_stay_equal():
    # D50 (M3) extended to the Plan 3C copies: neutral modules keep their own ET zone instead of importing the worker.
    assert vwap._MARKET_TZ == market._MARKET_TZ == health._MARKET_TZ == MARKET_TZ
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/integration/api/test_market_api.py tests/worker/test_window_parity.py`
Expected: FAIL — `404 NOT_FOUND` nas rotas novas; `test_window_parity.py` falha na coleta com `ModuleNotFoundError: No module named 'virtual_orders.readmodels.market'`.

- [ ] **Step 3: Implement**

`src/virtual_orders/api/tickers.py`:

```python
"""Ticker path/query normalization shared by the routes (D26, D42): the same rule as the signal validation."""

from __future__ import annotations

from virtual_orders.api.errors import ApiError


def normalize_ticker(ticker: str) -> str:
    """D26: stripped and upper-cased, so the result satisfies the signal rule (core TICKER_INVALID)."""
    normalized = ticker.strip().upper()
    if not normalized or any(character.isspace() for character in normalized):
        raise ApiError(422, "TICKER_INVALID", detail={"ticker": ticker})
    return normalized
```

O corpo de `normalize_ticker` é o de `_normalized` movido **sem mudança** (M5): mesma regra e mesmo `detail`, para que os corpos de `test_watchlist_api.py` continuem idênticos.

Em `src/virtual_orders/api/routes/watchlist.py`:
- remova a função `_normalized` inteira;
- acrescente `from virtual_orders.api.tickers import normalize_ticker` aos imports;
- troque as três chamadas `_normalized(ticker)` (em `put_watchlist_ticker`, `delete_watchlist_ticker` e `post_alert_rule`) por `normalize_ticker(ticker)`.

`src/virtual_orders/readmodels/market.py`:

```python
"""Dashboard market views (D41-D44): stored bars only, read as of the ingestion watermark. Never calls a provider."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import Connection, select, text

from core.domain.calendar import ONE_MINUTE
from core.domain.models import Bar
from virtual_orders.analytics.pressure import DISCLAIMER, METHOD, PressureEstimate, estimate_pressure, strong_pressure
from virtual_orders.analytics.vwap import METHOD as VWAP_METHOD
from virtual_orders.analytics.vwap import VwapPoint, session_vwap
from virtual_orders.ledger.errors import OrderNotFound
from virtual_orders.ledger.events import stored_events
from virtual_orders.marketdata.asof import read_bars_as_of
from virtual_orders.storage.codec import parse_ts
from virtual_orders.storage.tables import order_state, orders, signals

MAX_BARS_WINDOW = timedelta(days=7)
NO_BARS = "NO_BARS"
INSUFFICIENT_BARS = "INSUFFICIENT_BARS"
SIGNAL_LEVELS = ("entry_zone_low", "entry_zone_high", "stop", "target1", "target2", "trigger_price")
_MARKET_TZ = ZoneInfo("America/New_York")

_LAST_BAR = text(
    """
    SELECT max(b.ts) FROM bars_1m b JOIN bar_batches bb ON bb.batch_id = b.batch_id
    WHERE b.ticker = :ticker AND b.source = :source AND bb.ingested_at <= :as_of
    """
)

_CHART_COLUMNS = (
    orders.c.id.label("order_id"), orders.c.origin, orders.c.price_source, orders.c.replay,
    orders.c.replay_of_order_id, orders.c.fill_model_version, orders.c.config_snapshot, orders.c.evaluation_start_ts,
    orders.c.valid_until_ts, signals.c.ticker, signals.c.direction, signals.c.strategy,
    *(signals.c[name] for name in SIGNAL_LEVELS),
    order_state.c.status, order_state.c.avg_entry, order_state.c.stop_current, order_state.c.last_bar_ts,
    order_state.c.final_event_ts,
)


def market_day_start(ts: datetime) -> datetime:
    """00:00 America/New_York of the ET date of `ts`: every regular session of that date starts after it."""
    return datetime.combine(ts.astimezone(_MARKET_TZ).date(), time(0), tzinfo=_MARKET_TZ)


@dataclass(frozen=True)
class CandleSeries:
    ticker: str
    price_source: str
    data_as_of: datetime
    start: datetime
    end: datetime
    vwap_method: str
    bars: tuple[Bar, ...]
    vwap: tuple[VwapPoint, ...]


@dataclass(frozen=True)
class PressureView:
    ticker: str
    price_source: str
    data_as_of: datetime
    window_bars: int
    estimate: bool
    method: str
    disclaimer: str
    available: bool
    reason: str | None
    values: PressureEstimate | None
    cmf_threshold: Decimal | None
    side: str | None


@dataclass(frozen=True)
class OrderChart:
    order_id: UUID
    ticker: str
    direction: str
    strategy: str
    origin: str
    status: str | None
    price_source: str
    replay: bool
    replay_of_order_id: UUID | None
    fill_model_version: str
    config_snapshot: dict[str, Any]
    evaluation_start_ts: datetime
    valid_until_ts: datetime
    levels: dict[str, Decimal | None]
    markers: tuple[dict[str, Any], ...]
    window: dict[str, datetime]
    window_truncated: bool  # M10: the 7-day cap cut the end of the order's window


def candles(
    conn: Connection, *, ticker: str, price_source: str, start: datetime, end: datetime, as_of: datetime
) -> CandleSeries:
    """D43: bars are read from 00:00 ET of the first day so the VWAP is anchored at the session open."""
    anchored = read_bars_as_of(conn, ticker, price_source, market_day_start(start), end, as_of)
    vwap = session_vwap(anchored)
    return CandleSeries(
        ticker, price_source, as_of, start, end, VWAP_METHOD,
        tuple(item for item in anchored if item.ts >= start), tuple(point for point in vwap if point.ts >= start),
    )


def latest_pressure(
    conn: Connection,
    *,
    ticker: str,
    price_source: str,
    window_bars: int,
    cmf_threshold: Decimal | None,
    as_of: datetime,
) -> PressureView:
    """D44: the last `window_bars` bars of the ET date of the latest stored bar; always labelled as an estimate."""

    def view(available: bool, reason: str | None, values: PressureEstimate | None = None,
             side: str | None = None) -> PressureView:
        return PressureView(ticker, price_source, as_of, window_bars, True, METHOD, DISCLAIMER, available, reason,
                            values, cmf_threshold, side)

    last: datetime | None = conn.execute(_LAST_BAR, {"ticker": ticker, "source": price_source,
                                                     "as_of": as_of}).scalar_one()
    if last is None:
        return view(False, NO_BARS)
    bars = read_bars_as_of(conn, ticker, price_source, market_day_start(last), last + ONE_MINUTE, as_of)[-window_bars:]
    estimate = estimate_pressure(bars) if len(bars) == window_bars else None
    if estimate is None:
        return view(False, INSUFFICIENT_BARS)
    side = None if cmf_threshold is None else strong_pressure(estimate, cmf_threshold)
    return view(True, None, estimate, None if side is None else side.value)


def order_chart(conn: Connection, order_id: UUID) -> OrderChart:
    row = conn.execute(
        select(*_CHART_COLUMNS)
        .select_from(orders.join(signals, signals.c.id == orders.c.signal_id)
                     .outerjoin(order_state, order_state.c.order_id == orders.c.id))
        .where(orders.c.id == order_id)
    ).mappings().first()
    if row is None:
        raise OrderNotFound(str(order_id))
    markers: list[dict[str, Any]] = []
    for item in stored_events(conn, order_id):
        event = item.prepared
        ts = event.bar_ts
        if ts is None and event.type == "DATA_GAP":
            raw = event.payload.get("gap_start_ts")
            ts = parse_ts(raw) if isinstance(raw, str) else None
        if ts is not None:
            markers.append({"seq": item.seq, "type": event.type, "event_key": event.event_key, "ts": ts,
                            "price": event.price})
    levels: dict[str, Decimal | None] = {name: row[name] for name in SIGNAL_LEVELS}
    levels.update(avg_entry=row["avg_entry"], stop_current=row["stop_current"])
    start = market_day_start(row["evaluation_start_ts"])
    anchor = row["final_event_ts"] or row["last_bar_ts"] or row["evaluation_start_ts"]
    natural_end, capped_end = anchor + ONE_MINUTE, start + MAX_BARS_WINDOW
    return OrderChart(
        order_id=row["order_id"], ticker=row["ticker"], direction=row["direction"], strategy=row["strategy"],
        origin=row["origin"], status=row["status"], price_source=row["price_source"], replay=row["replay"],
        replay_of_order_id=row["replay_of_order_id"], fill_model_version=row["fill_model_version"],
        config_snapshot=dict(row["config_snapshot"]), evaluation_start_ts=row["evaluation_start_ts"],
        valid_until_ts=row["valid_until_ts"], levels=levels, markers=tuple(markers),
        window={"from": start, "to": min(natural_end, capped_end)}, window_truncated=natural_end > capped_end,
    )
```

`src/virtual_orders/api/routes/market.py`:

```python
"""Market views for the dashboard (D41-D44): stored bars only, as of the ingestion watermark; never a provider call."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response

from virtual_orders.alerts.watchlist import MAX_WINDOW_BARS, MIN_WINDOW_BARS
from virtual_orders.api.deps import ServicesDep
from virtual_orders.api.encoding import json_response
from virtual_orders.api.errors import ApiError
from virtual_orders.api.tickers import normalize_ticker
from virtual_orders.marketdata.asof import acquire_data_as_of
from virtual_orders.readmodels.market import MAX_BARS_WINDOW, candles, latest_pressure, order_chart

router = APIRouter()
DEFAULT_PRESSURE_WINDOW_BARS = 30


def _invalid(errors: list[str]) -> ApiError:
    return ApiError(422, "MARKET_REQUEST_INVALID", detail={"errors": errors})


@router.get("/market/bars")
def get_market_bars(
    services: ServicesDep,
    ticker: str,
    start: Annotated[datetime, Query(alias="from")],
    end: Annotated[datetime, Query(alias="to")],
    source: str | None = None,
) -> Response:
    normalized = normalize_ticker(ticker)
    errors = [f"NAIVE_DATETIME:{name}" for name, value in (("from", start), ("to", end)) if value.tzinfo is None]
    if not errors and end <= start:
        errors.append("EMPTY_WINDOW")
    if not errors and end - start > MAX_BARS_WINDOW:
        errors.append("WINDOW_TOO_LARGE")
    if errors:
        raise _invalid(errors)
    as_of = acquire_data_as_of(services.engine)
    with services.engine.connect() as conn:
        series = candles(conn, ticker=normalized, price_source=source or services.price_source, start=start, end=end,
                         as_of=as_of)
    return json_response(series)


@router.get("/market/pressure")
def get_market_pressure(
    services: ServicesDep,
    ticker: str,
    window_bars: int = DEFAULT_PRESSURE_WINDOW_BARS,
    cmf_threshold: Decimal | None = None,
    source: str | None = None,
) -> Response:
    normalized = normalize_ticker(ticker)
    errors: list[str] = []
    if not MIN_WINDOW_BARS <= window_bars <= MAX_WINDOW_BARS:
        errors.append("OUT_OF_RANGE:window_bars")
    if cmf_threshold is not None and not (cmf_threshold.is_finite() and 0 < cmf_threshold < 1):
        errors.append("OUT_OF_RANGE:cmf_threshold")
    if errors:
        raise _invalid(errors)
    as_of = acquire_data_as_of(services.engine)
    with services.engine.connect() as conn:
        view = latest_pressure(conn, ticker=normalized, price_source=source or services.price_source,
                               window_bars=window_bars, cmf_threshold=cmf_threshold, as_of=as_of)
    return json_response(view)


@router.get("/orders/{order_id}/chart")
def get_order_chart(order_id: UUID, services: ServicesDep) -> Response:
    with services.engine.connect() as conn:
        return json_response(order_chart(conn, order_id))
```

Em `src/virtual_orders/api/app.py`, troque a linha de import dos routers por `from virtual_orders.api.routes import health, market, metrics, orders, replay, signals, watchlist` e acrescente `app.include_router(market.router)` depois de `app.include_router(watchlist.router)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/integration/api/test_market_api.py tests/integration/api/test_watchlist_api.py tests/integration/api/test_signals_api.py tests/worker/test_window_parity.py tests/test_import_boundaries.py && uv run ruff check src tests migrations && uv run mypy`
Expected: PASS. `test_every_route_rejects_a_missing_or_wrong_key` cobre as três rotas novas automaticamente (401 antes da validação dos parâmetros, porque a dependência do app roda primeiro). Se alguma rota nova responder 422 em vez de 401 nesse teste, pare e reporte: a autenticação no nível do app é Global Constraint.

- [ ] **Step 5: Commit**

```bash
git add src/virtual_orders/readmodels/market.py src/virtual_orders/api/tickers.py src/virtual_orders/api/routes/market.py src/virtual_orders/api/routes/watchlist.py src/virtual_orders/api/app.py tests/integration/api/test_market_api.py tests/worker/test_window_parity.py
git commit -m "feat(api): as-of candles with session VWAP, labelled pressure estimates and order chart context"
```

---

### Task 8: Portfólio — read model virtual, contrato do real desligado e modelo da Fase 0 (entrada 7; D45, D46)

**Files:**
- Create: `src/virtual_orders/portfolio/__init__.py`, `src/virtual_orders/portfolio/sources.py`
- Modify: `src/virtual_orders/services.py`
- Modify: `src/virtual_orders/bootstrap.py`
- Create: `src/virtual_orders/readmodels/portfolio.py`
- Create: `src/virtual_orders/api/routes/portfolio.py`
- Modify: `src/virtual_orders/api/app.py`
- Create: `docs/superpowers/roadmap/2026-09-14-robinhood-phase0-feasibility-report.md`
- Create: `tests/integration/api/test_portfolio_api.py`
- Modify: `tests/test_import_boundaries.py`

**Interfaces:**
- Consumes: `mark_portfolio`, `OpenPosition`, `VirtualPortfolio` (Task 6), `acquire_data_as_of`.
- Produces:
  - `virtual_orders.portfolio.sources`: `PHASE_0_PENDING = "PHASE_0_PENDING"`, `SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"`, `RealPosition(ticker, quantity, average_cost, market_value, as_of)`, `class PortfolioSource(Protocol)` com `name: str` e `list_positions() -> list[RealPosition]`;
  - `Services.portfolio_source: PortfolioSource | None = None`;
  - `virtual_orders.readmodels.portfolio.virtual_portfolio(conn, *, as_of) -> VirtualPortfolio`;
  - rotas `GET /portfolio/virtual` e `GET /portfolio/real`.

- [ ] **Step 1: Write the failing tests**

`tests/integration/api/test_portfolio_api.py`:

```python
from dataclasses import replace
from uuid import UUID

from fastapi.testclient import TestClient

from tests.integration.support import API_KEY, CODE_VERSION, DAY, post_json, scenario_bars, signal_body
from tests.support import et
from virtual_orders.api.app import create_app
from virtual_orders.evaluator.cycle import run_live_cycle


def cycle(api, hm):
    run_live_cycle(api.services.engine, api.services.gateway, code_version=CODE_VERSION, market_now=et(DAY, hm))


def test_virtual_portfolio_marks_open_positions_at_the_last_stored_close_and_empties_when_closed(api):
    post_json(api.client, "/signals", signal_body())
    post_json(api.client, "/signals", signal_body(client_signal_id="msft", ticker="MSFT"))
    api.bars.load(scenario_bars() + scenario_bars(ticker="MSFT"))
    cycle(api, "10:30")  # both filled at 101 on the 10:05 bar; the last stored close is the 10:29 bar at 103

    body = api.client.get("/portfolio/virtual").json()

    assert body["kind"] == "VIRTUAL" and body["portfolio"]["basis"] == "PER_1R_NORMALIZED"
    assert "not account money" in body["portfolio"]["disclaimer"]
    rows = [(p["position"]["ticker"], p["position"]["qty_open"], p["position"]["last_close"], p["unrealized_pnl"],
             p["unrealized_r"], p["open_r"], p["allocation_pct"]) for p in body["portfolio"]["positions"]]
    assert rows == [("AAPL", "25", "103", "50", "0.5", "0.5", "50"), ("MSFT", "25", "103", "50", "0.5", "0.5", "50")]
    assert body["portfolio"]["totals"] == {"positions": 2, "marked": 2, "unmarked": 0, "notional": "5150",
                                           "unrealized_pnl": "100", "unrealized_r": "1", "risk_amount": "200"}

    cycle(api, "11:30")
    cycle(api, "13:00")
    assert api.client.get("/portfolio/virtual").json()["portfolio"]["positions"] == []


def test_real_portfolio_is_unavailable_until_phase_0(api):
    assert api.services.portfolio_source is None
    assert api.client.get("/portfolio/real").json() == {
        "kind": "REAL", "available": False, "reason": "PHASE_0_PENDING", "source": None, "positions": [],
    }


def test_a_failing_real_source_reports_only_its_type(api):
    class FailingBroker:
        name = "fake_broker"

        def list_positions(self):
            raise RuntimeError("token=secret-value")

    services = replace(api.services, portfolio_source=FailingBroker())
    with TestClient(create_app(services), headers={"X-API-Key": API_KEY}) as client:
        response = client.get("/portfolio/real")
    assert response.json() == {"kind": "REAL", "available": False, "reason": "SOURCE_UNAVAILABLE",
                               "source": "fake_broker", "error": "RuntimeError", "positions": []}
    assert "secret-value" not in response.text
```

Em `tests/test_import_boundaries.py`, troque `NEUTRAL_PACKAGES` por:

```python
NEUTRAL_PACKAGES = [
    "virtual_orders/storage", "virtual_orders/ledger", "virtual_orders/evaluator", "virtual_orders/readmodels",
    "virtual_orders/alerts", "virtual_orders/analytics", "virtual_orders/portfolio",
]
```

e acrescente ao final:

```python
def test_real_portfolio_is_a_read_only_contract_without_any_adapter() -> None:
    from virtual_orders.portfolio.sources import PortfolioSource

    assert {name for name in vars(PortfolioSource) if not name.startswith("_")} == {"list_positions"}
    assert not any("robinhood" in path.name.lower() for path in _platform_files())
    assert not any(_matches(name, "mcp") for path in _platform_files() for name in _imported_modules(path))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/integration/api/test_portfolio_api.py tests/test_import_boundaries.py`
Expected: FAIL — `404 NOT_FOUND`, `AttributeError: 'Services' object has no attribute 'portfolio_source'` e `ModuleNotFoundError: virtual_orders.portfolio`.

- [ ] **Step 3: Implement**

`src/virtual_orders/portfolio/__init__.py`:

```python
"""Real (broker) portfolio contract (D46). Display-only; nothing here feeds the engine, ledger, fills or metrics."""
```

`src/virtual_orders/portfolio/sources.py`:

```python
"""Read-only real portfolio contract (roadmap "portfólio real no dashboard", D46).

No implementation exists: a Robinhood read-only provider is gated by the roadmap Phase 0 (17 checks, none verified,
see docs/superpowers/roadmap/2026-09-14-robinhood-phase0-feasibility-report.md). Typed methods only: no order methods,
no generic tool passthrough.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

PHASE_0_PENDING = "PHASE_0_PENDING"
SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"


@dataclass(frozen=True)
class RealPosition:
    ticker: str
    quantity: Decimal
    average_cost: Decimal | None
    market_value: Decimal | None
    as_of: datetime


class PortfolioSource(Protocol):
    name: str

    def list_positions(self) -> list[RealPosition]: ...
```

Em `src/virtual_orders/services.py`, acrescente `from virtual_orders.portfolio.sources import PortfolioSource` aos imports e, depois de `alert_sink`, o último campo:

```python
    portfolio_source: PortfolioSource | None = field(default=None, repr=False)  # D46: disabled until Phase 0
```

Em `src/virtual_orders/bootstrap.py`, no `Services(...)` de `build_services`, depois de `alert_sink=alert_sink,`:

```python
        portfolio_source=None,  # D46: no adapter until the roadmap Phase 0 report approves one
```

`src/virtual_orders/readmodels/portfolio.py`:

```python
"""GET /portfolio/virtual (D45): open non-replay positions marked at the last stored close as of `as_of`."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Connection, text

from core.domain.models import Direction
from virtual_orders.analytics.portfolio import OpenPosition, VirtualPortfolio, mark_portfolio

_OPEN_POSITIONS = text(
    """
    SELECT o.id AS order_id, g.ticker, g.direction, g.strategy, o.origin, o.risk_amount,
           st.status, st.frozen, st.qty_open, st.avg_entry, st.stop_current, st.realized_pnl, st.costs,
           st.state_document->>'dividends' AS dividends, mark.close AS last_close, mark.ts AS last_close_ts
    FROM order_state st
    JOIN orders o ON o.id = st.order_id
    JOIN signals g ON g.id = o.signal_id
    LEFT JOIN LATERAL (
        SELECT b.ts, b.close
        FROM bars_1m b JOIN bar_batches bb ON bb.batch_id = b.batch_id
        WHERE b.ticker = g.ticker AND b.source = o.price_source AND bb.ingested_at <= :as_of
        ORDER BY b.ts DESC, bb.ingested_at DESC, b.batch_id DESC
        LIMIT 1
    ) mark ON true
    WHERE NOT o.replay AND st.qty_open > 0 AND st.avg_entry IS NOT NULL
    ORDER BY g.ticker, o.id
    """
)


def virtual_portfolio(conn: Connection, *, as_of: datetime) -> VirtualPortfolio:
    return mark_portfolio([
        OpenPosition(
            order_id=row.order_id, ticker=row.ticker, direction=Direction(row.direction), strategy=row.strategy,
            origin=row.origin, status=row.status, frozen=row.frozen, qty_open=row.qty_open, avg_entry=row.avg_entry,
            stop_current=row.stop_current, risk_amount=row.risk_amount, realized_pnl=row.realized_pnl,
            costs=row.costs, dividends=Decimal(row.dividends or "0"), last_close=row.last_close,
            last_close_ts=row.last_close_ts,
        )
        for row in conn.execute(_OPEN_POSITIONS, {"as_of": as_of})
    ])
```

`src/virtual_orders/api/routes/portfolio.py`:

```python
"""Virtual portfolio (D45) and the disabled real portfolio slot (D46). Real and virtual totals are never mixed."""

from __future__ import annotations

from fastapi import APIRouter, Response

from virtual_orders.api.deps import ServicesDep
from virtual_orders.api.encoding import json_response
from virtual_orders.marketdata.asof import acquire_data_as_of
from virtual_orders.portfolio.sources import PHASE_0_PENDING, SOURCE_UNAVAILABLE
from virtual_orders.readmodels.portfolio import virtual_portfolio

router = APIRouter()


@router.get("/portfolio/virtual")
def get_virtual_portfolio(services: ServicesDep) -> Response:
    as_of = acquire_data_as_of(services.engine)
    with services.engine.connect() as conn:
        portfolio = virtual_portfolio(conn, as_of=as_of)
    return json_response({"kind": "VIRTUAL", "data_as_of": as_of, "portfolio": portfolio})


@router.get("/portfolio/real")
def get_real_portfolio(services: ServicesDep) -> Response:
    source = services.portfolio_source
    if source is None:
        return json_response({"kind": "REAL", "available": False, "reason": PHASE_0_PENDING, "source": None,
                              "positions": []})
    try:
        positions = source.list_positions()
    except Exception as exc:  # noqa: BLE001 - display-only data: never a 500 and never the exception text (D46)
        return json_response({"kind": "REAL", "available": False, "reason": SOURCE_UNAVAILABLE,
                              "source": source.name, "error": type(exc).__name__, "positions": []})
    return json_response({"kind": "REAL", "available": True, "reason": None, "source": source.name,
                          "positions": positions})
```

Em `src/virtual_orders/api/app.py`, o import dos routers passa a `from virtual_orders.api.routes import health, market, metrics, orders, portfolio, replay, signals, watchlist`; acrescente `app.include_router(portfolio.router)` depois de `app.include_router(market.router)`.

`docs/superpowers/roadmap/2026-09-14-robinhood-phase0-feasibility-report.md`:

```markdown
# Robinhood MCP (somente leitura) — Relatório de viabilidade da Fase 0 (modelo)

- **Estado:** modelo; **nenhum item verificado**. Preenchido só com evidência do MCP oficial (roadmap `2026-09-12-robinhood-mcp-read-only.md`).
- **Consumidor aguardando:** slot `Services.portfolio_source` (Plano 3C, D46), hoje sempre `None`; `GET /portfolio/real` responde `PHASE_0_PENDING`.
- **Regra:** sem evidência, nada de adapter, credencial, chamada MCP ou variável de ambiente. Itens 1–4, 12, 14, 16 e 17 bloqueiam o portfólio real.

| # | Verificação | Bloqueia o portfólio real | Status | Evidência (link, data, versão do MCP) | Observações |
|---|---|---|---|---|---|
| 1 | Autenticação para serviço backend | sim | não verificado | — | — |
| 2 | Persistência e renovação da autenticação | sim | não verificado | — | — |
| 3 | Uso em VPS sem sessão interativa do Codex/Claude | sim | não verificado | — | — |
| 4 | Schema real das tools | sim | não verificado | — | — |
| 5 | Quotes disponíveis | não | não verificado | — | — |
| 6 | Historical OHLCV | não | não verificado | — | — |
| 7 | Timeframes | não | não verificado | — | — |
| 8 | Profundidade histórica | não | não verificado | — | — |
| 9 | Level 2 / order book | não | não verificado | — | — |
| 10 | Fundamentals | não | não verificado | — | — |
| 11 | Earnings | não | não verificado | — | — |
| 12 | Positions/watchlists | sim | não verificado | — | — |
| 13 | Paginação | não | não verificado | — | — |
| 14 | Rate limits | sim | não verificado | — | — |
| 15 | Freshness | não | não verificado | — | — |
| 16 | Semântica de erros | sim | não verificado | — | — |
| 17 | Storage, retenção e licenciamento dos dados | sim | não verificado | — | — |

## Allowlist proposta (preencher só com tools confirmadas)

| Tool MCP | Método tipado | Permitida | Evidência |
|---|---|---|---|
| — | `list_positions()` | pendente | — |

Tools de ordem (place, cancel, modify, options, crypto, equity) e qualquer tool desconhecida: **negadas**.

## Conclusão

Pendente. Só com todos os itens bloqueantes verificados um plano de implementação do adapter pode ser escrito.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/integration/api/test_portfolio_api.py tests/integration/api/test_signals_api.py tests/test_bootstrap.py tests/test_bootstrap_worker.py tests/test_import_boundaries.py && uv run ruff check src tests migrations && uv run mypy`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/virtual_orders/portfolio src/virtual_orders/services.py src/virtual_orders/bootstrap.py src/virtual_orders/readmodels/portfolio.py src/virtual_orders/api/routes/portfolio.py src/virtual_orders/api/app.py docs/superpowers/roadmap/2026-09-14-robinhood-phase0-feasibility-report.md tests/integration/api/test_portfolio_api.py tests/test_import_boundaries.py
git commit -m "feat(portfolio): per-1R virtual portfolio and a disabled read-only real portfolio slot"
```

---

### Task 9: Rotas de observabilidade — log de saúde, outbox de alertas e cobertura de dados (entradas 4, 8; D42)

**Files:**
- Create: `src/virtual_orders/readmodels/observability.py`
- Create: `src/virtual_orders/api/routes/observability.py`
- Modify: `src/virtual_orders/api/app.py`
- Create: `tests/integration/api/test_observability_api.py`

**Interfaces:**
- Consumes: tabelas `health_state_log`, `alert_outbox`, `alert_delivery_attempts`, `order_events`; `enqueue_alert`, `AlertKind` (testes).
- Produces:
  - `virtual_orders.readmodels.observability`:
    - `COVERAGE_WINDOW = timedelta(days=14)`, `class OutboxOutcome(StrEnum)` com `DELIVERED`, `FAILED`, `EXPIRED` e `PENDING`;
    - `health_log(conn, *, limit) -> list[dict[str, Any]]`;
    - `alert_outbox_view(conn, *, outcome, limit) -> list[dict[str, Any]]`;
    - `quality_overview(conn, *, limit) -> dict[str, Any]`;
  - rotas `GET /health/log`, `GET /alert-outbox` e `GET /quality/overview`.

- [ ] **Step 1: Write the failing tests**

`tests/integration/api/test_observability_api.py`:

```python
from datetime import date, datetime

from sqlalchemy import select

from tests.integration.support import CODE_VERSION, DAY, FakeReference, flat_raw, post_json, signal_body
from tests.support import et
from virtual_orders.alerts.outbox import AlertKind, enqueue_alert
from virtual_orders.evaluator.quality import run_end_of_day
from virtual_orders.storage import tables


def get(api, path, **params):
    response = api.client.get(path, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_health_log_lists_transitions_newest_first(api):
    with api.services.engine.begin() as conn:
        conn.execute(tables.health_state_log.insert().values(state="DEGRADED", cause_codes=["LIVE_CYCLE_STALE"]))
        conn.execute(tables.health_state_log.insert().values(state="HEALTHY", cause_codes=[]))

    entries = get(api, "/health/log")["entries"]
    assert [(e["state"], e["cause_codes"]) for e in entries] == [("HEALTHY", []), ("DEGRADED", ["LIVE_CYCLE_STALE"])]
    assert datetime.fromisoformat(entries[0]["observed_at"]).tzinfo is not None
    assert [e["state"] for e in get(api, "/health/log", limit=1)["entries"]] == ["HEALTHY"]
    assert api.client.get("/health/log", params={"limit": 0}).json()["error"]["code"] == "REQUEST_INVALID"


def test_alert_outbox_lists_delivery_state_without_documents(api):
    engine = api.services.engine
    with engine.begin() as conn:
        for key in ("A", "B", "C"):
            enqueue_alert(conn, alert_key=key, kind=AlertKind.PRICE_CROSS, document={"secret": "document-value"},
                          subject="rule")
        ids = dict(conn.execute(select(tables.alert_outbox.c.alert_key, tables.alert_outbox.c.id)).all())
        conn.execute(tables.alert_delivery_attempts.insert(), [
            {"alert_id": ids["A"], "outcome": "DELIVERED", "status_code": 200, "error_type": None},
            {"alert_id": ids["B"], "outcome": "FAILED", "status_code": None, "error_type": "ConnectTimeout"},
            {"alert_id": ids["B"], "outcome": "EXPIRED", "status_code": None, "error_type": None},
        ])

    response = api.client.get("/alert-outbox")
    alerts = response.json()["alerts"]
    assert [(a["alert_key"], a["kind"], a["last_outcome"], a["failures"]) for a in alerts] == [
        ("C", "PRICE_CROSS", None, 0), ("B", "PRICE_CROSS", "EXPIRED", 1), ("A", "PRICE_CROSS", "DELIVERED", 0),
    ]
    assert "document-value" not in response.text and "document" not in alerts[0]
    for outcome, keys in (("EXPIRED", ["B"]), ("PENDING", ["C"]), ("DELIVERED", ["A"]), ("FAILED", [])):
        assert [a["alert_key"] for a in get(api, "/alert-outbox", outcome=outcome)["alerts"]] == keys, outcome
    invalid = api.client.get("/alert-outbox", params={"outcome": "LOST"})
    assert invalid.status_code == 422 and invalid.json()["error"]["code"] == "REQUEST_INVALID"


def test_quality_overview_reports_coverage_and_data_gaps(api):
    order_id = post_json(api.client, "/signals", signal_body()).json()["auto_order_id"]
    bars = [b for b in flat_raw(DAY, "09:30", "16:00", 105) if not et(DAY, "10:10") <= b.ts < et(DAY, "10:45")]
    api.bars.load(bars)  # above the zone: never fills; 35 minutes missing
    run_end_of_day(api.services.engine, gateway=api.services.gateway, reference=FakeReference(),
                   session_day=date(2025, 11, 25), code_version=CODE_VERSION, market_now=et(DAY, "16:30"))

    overview = get(api, "/quality/overview")

    assert overview["window_days"] == 14
    assert overview["coverage"] == [{"session_date": "2025-11-25", "orders": 1, "expected_bars": 390,
                                     "missing_bars": 35, "coverage_pct": "91.03"}]
    (gap,) = overview["data_gaps"]
    assert (gap["order_id"], gap["ticker"], gap["minutes"]) == (order_id, "AAPL", 35)
    assert datetime.fromisoformat(gap["gap_start_ts"]) == et(DAY, "10:10")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/integration/api/test_observability_api.py`
Expected: FAIL — `404 NOT_FOUND` nas três rotas.

- [ ] **Step 3: Implement**

`src/virtual_orders/readmodels/observability.py`:

```python
"""Operator views for the Health page (D42): health transitions, alert delivery and data coverage. Reads only.

Alert documents are never returned (they can carry event payloads); error columns already hold exception types only.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import Connection, func, or_, select, text, true

from virtual_orders.storage.tables import alert_delivery_attempts, alert_outbox, health_state_log

COVERAGE_WINDOW = timedelta(days=14)
PCT_QUANTUM = Decimal("0.01")


class OutboxOutcome(StrEnum):
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"
    PENDING = "PENDING"  # no attempt yet, or the latest attempt failed


_COVERAGE = text(
    """
    SELECT split_part(e.event_key, ':', 2) AS session_date, COUNT(DISTINCT e.order_id) AS orders,
           SUM((e.payload->>'expected_bars')::int) AS expected_bars,
           SUM((e.payload->>'missing_bars')::int) AS missing_bars
    FROM order_events e
    JOIN orders o ON o.id = e.order_id
    WHERE NOT o.replay AND e.type = 'DATA_QUALITY' AND e.recorded_at >= clock_timestamp() - :window
    GROUP BY 1
    ORDER BY 1 DESC
    """
)

_DATA_GAPS = text(
    """
    SELECT e.order_id, g.ticker, e.event_key, e.payload->>'gap_start_ts' AS gap_start_ts,
           (e.payload->>'minutes')::int AS minutes, e.recorded_at
    FROM order_events e
    JOIN orders o ON o.id = e.order_id
    JOIN signals g ON g.id = o.signal_id
    WHERE NOT o.replay AND e.type = 'DATA_GAP' AND e.recorded_at >= clock_timestamp() - :window
    ORDER BY e.id DESC
    LIMIT :limit
    """
)


def health_log(conn: Connection, *, limit: int) -> list[dict[str, Any]]:
    rows = conn.execute(select(health_state_log).order_by(health_state_log.c.id.desc()).limit(limit))
    return [{"id": row.id, "state": row.state, "cause_codes": list(row.cause_codes), "observed_at": row.observed_at}
            for row in rows]


def alert_outbox_view(conn: Connection, *, outcome: OutboxOutcome | None, limit: int) -> list[dict[str, Any]]:
    attempts = alert_delivery_attempts
    last = (
        select(attempts.c.outcome, attempts.c.status_code, attempts.c.error_type, attempts.c.attempted_at)
        .where(attempts.c.alert_id == alert_outbox.c.id)
        .order_by(attempts.c.id.desc())
        .limit(1)
        .lateral("last_attempt")
    )
    failures = (
        select(func.count()).select_from(attempts)
        .where(attempts.c.alert_id == alert_outbox.c.id, attempts.c.outcome == OutboxOutcome.FAILED.value)
        .scalar_subquery()
    )
    query = select(
        alert_outbox.c.id, alert_outbox.c.alert_key, alert_outbox.c.kind, alert_outbox.c.subject,
        alert_outbox.c.subject_ts, alert_outbox.c.created_at, failures.label("failures"),
        last.c.outcome.label("last_outcome"), last.c.status_code.label("last_status_code"),
        last.c.error_type.label("last_error_type"), last.c.attempted_at.label("last_attempted_at"),
    ).select_from(alert_outbox.outerjoin(last, true()))
    if outcome is OutboxOutcome.PENDING:
        query = query.where(or_(last.c.outcome.is_(None), last.c.outcome == OutboxOutcome.FAILED.value))
    elif outcome is not None:
        query = query.where(last.c.outcome == outcome.value)
    return [dict(row) for row in conn.execute(query.order_by(alert_outbox.c.id.desc()).limit(limit)).mappings()]


def _coverage_pct(expected: int, missing: int) -> Decimal | None:
    if expected <= 0:
        return None
    return (Decimal(expected - missing) / Decimal(expected) * 100).quantize(PCT_QUANTUM, rounding=ROUND_HALF_EVEN)


def quality_overview(conn: Connection, *, limit: int) -> dict[str, Any]:
    coverage = [
        {"session_date": row.session_date, "orders": int(row.orders), "expected_bars": int(row.expected_bars),
         "missing_bars": int(row.missing_bars),
         "coverage_pct": _coverage_pct(int(row.expected_bars), int(row.missing_bars))}
        for row in conn.execute(_COVERAGE, {"window": COVERAGE_WINDOW})
    ]
    gaps = [dict(row) for row in conn.execute(_DATA_GAPS, {"window": COVERAGE_WINDOW, "limit": limit}).mappings()]
    return {"window_days": COVERAGE_WINDOW.days, "coverage": coverage, "data_gaps": gaps}
```

`src/virtual_orders/api/routes/observability.py`:

```python
"""Health page reads (D42): health_state_log, alert delivery state (never documents), coverage and DATA_GAP."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Response

from virtual_orders.api.deps import ServicesDep
from virtual_orders.api.encoding import json_response
from virtual_orders.readmodels.observability import OutboxOutcome, alert_outbox_view, health_log, quality_overview

router = APIRouter()
MAX_LIMIT = 500


@router.get("/health/log")
def get_health_log(services: ServicesDep, limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 20) -> Response:
    with services.engine.connect() as conn:
        return json_response({"entries": health_log(conn, limit=limit)})


@router.get("/alert-outbox")
def get_alert_outbox(
    services: ServicesDep,
    outcome: OutboxOutcome | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 100,
) -> Response:
    with services.engine.connect() as conn:
        return json_response({"alerts": alert_outbox_view(conn, outcome=outcome, limit=limit)})


@router.get("/quality/overview")
def get_quality_overview(services: ServicesDep, limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 100) -> Response:
    with services.engine.connect() as conn:
        return json_response(quality_overview(conn, limit=limit))
```

Em `src/virtual_orders/api/app.py`, o import dos routers passa a `from virtual_orders.api.routes import health, market, metrics, observability, orders, portfolio, replay, signals, watchlist`; acrescente `app.include_router(observability.router)` depois de `app.include_router(portfolio.router)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/integration/api/test_observability_api.py tests/integration/api/test_signals_api.py tests/integration/api/test_health_api.py && uv run ruff check src tests migrations && uv run mypy`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/virtual_orders/readmodels/observability.py src/virtual_orders/api/routes/observability.py src/virtual_orders/api/app.py tests/integration/api/test_observability_api.py
git commit -m "feat(api): health log, alert delivery state and data coverage reads for the Health page"
```

---

### Task 10: Ponta a ponta pela API (spec 7; D54)

**Files:**
- Create: `tests/integration/api/test_end_to_end_api.py`

**Interfaces:**
- Consumes: todas as rotas (3A, 3B, Tasks 7–9), `WorkerJobs` e `JobResult` (3B), harness `api`, `scenario_bars`, `signal_body`.
- Produces: o teste `test_recorded_session_end_to_end_through_the_api`, citado nos critérios de aceite e na Task 15.

- [ ] **Step 1: Write the test**

`tests/integration/api/test_end_to_end_api.py`:

```python
"""Spec 7 end to end through the HTTP API (D54): recorded session (fake feed) -> signal -> AUTO and MANUAL orders ->
events -> projection -> portfolio -> metrics -> REPRODUCE identical. Real jobs, controlled clock, no network, no sleep."""

from uuid import UUID

from tests.integration.support import DAY, post_json, scenario_bars, signal_body
from tests.support import et
from virtual_orders.worker.jobs import JobResult, WorkerJobs


def get(api, path, **params):
    response = api.client.get(path, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def identities(api, order_id):
    return [(e["event_key"], e["payload_hash"]) for e in get(api, f"/orders/{order_id}")["events"]]


def test_recorded_session_end_to_end_through_the_api(api):
    jobs = WorkerJobs(api.services)
    api.bars.load(scenario_bars())  # fill 10:05 @101, TARGET1 11:00 @106, TARGET2 12:50 @110 -> r = 1.75

    created = post_json(api.client, "/signals", signal_body())
    assert created.status_code == 201, created.text
    signal_id, auto_id = created.json()["signal_id"], UUID(created.json()["auto_order_id"])
    assert [(s["signal_id"], s["auto_order_status"]) for s in get(api, "/signals", date=DAY)["signals"]] == [
        (signal_id, "PENDING")]

    api.clock.set(et(DAY, "10:00", 30))
    manual = api.client.post(f"/signals/{signal_id}/orders")
    assert manual.status_code == 201, manual.text
    assert manual.json()["partial_bar_skipped"] is True
    manual_id = UUID(manual.json()["order_id"])

    api.clock.set(et(DAY, "10:30"))
    assert jobs.live_cycle() == JobResult("live_cycle", True, "COMPLETED")
    portfolio = get(api, "/portfolio/virtual")["portfolio"]
    assert [(p["position"]["order_id"], p["unrealized_r"]) for p in portfolio["positions"]] == sorted(
        [(str(auto_id), "0.5"), (str(manual_id), "0.5")])
    assert portfolio["totals"]["unrealized_r"] == "1"

    for hm in ("11:30", "13:00"):
        api.clock.set(et(DAY, hm))
        assert jobs.live_cycle() == JobResult("live_cycle", True, "COMPLETED")
    api.clock.set(et(DAY, "16:30"))
    assert jobs.end_of_day() == JobResult("end_of_day", True, "COMPLETED")

    for order_id, expected_bars in ((auto_id, 201), (manual_id, 170)):
        detail = get(api, f"/orders/{order_id}")
        assert [e["type"] for e in detail["events"]] == [
            "ORDER_CREATED", "FILLED", "TARGET1_HIT", "TARGET2_HIT", "DATA_QUALITY"]
        assert (detail["order"]["status"], detail["order"]["r_multiple"]) == ("CLOSED", "1.75")
        assert detail["data_quality"]["events"][0]["payload"]["expected_bars"] == expected_bars
        chart = get(api, f"/orders/{order_id}/chart")
        assert [m["type"] for m in chart["markers"]] == ["FILLED", "TARGET1_HIT", "TARGET2_HIT"]
        bars = get(api, "/market/bars", ticker=chart["ticker"], source=chart["price_source"],
                   **{"from": chart["window"]["from"], "to": chart["window"]["to"]})
        assert len(bars["bars"]) == len(bars["vwap"]) == 201

    summary = get(api, "/metrics")["groups"][0]["summary"]
    assert (summary["trades"], summary["win_rate"], summary["expectancy_r"], summary["execution_rate"]) == (
        2, 1.0, 1.75, 1.0)
    assert "INSUFFICIENT_SAMPLE" in summary["warnings"]
    by_origin = {g["key"]: g["summary"]["trades"] for g in get(api, "/metrics", group_by="origin")["groups"]}
    assert by_origin == {"AUTO_STRATEGY": 1, "MANUAL_USER": 1}
    assert get(api, "/portfolio/virtual")["portfolio"]["positions"] == []

    replay = post_json(api.client, "/replay", {"mode": "REPRODUCE", "from": et(DAY, "08:00").isoformat(),
                                               "to": et(DAY, "23:00").isoformat()})
    assert replay.status_code == 200, replay.text
    report = replay.json()
    assert report["failures"] == {} and set(report["created"]) == {str(auto_id), str(manual_id)}
    for source_id, replay_id in report["created"].items():
        assert identities(api, replay_id) == identities(api, source_id)
    replays = get(api, "/metrics", replay="true")["groups"][0]["summary"]
    assert (replays["trades"], replays["expectancy_r"], replays["win_rate"]) == (2, 1.75, 1.0)
```

- [ ] **Step 2: Run the test**

Run: `uv run pytest tests/integration/api/test_end_to_end_api.py`
Expected: PASS de primeira, porque o teste só integra o que as Tasks 7–9 e os Planos 2–3B já entregaram. Se falhar, **não** mude a asserção para caber no resultado: investigue com `superpowers:systematic-debugging`. Valores esperados divergentes do teste de ponta a ponta existente (`tests/integration/test_end_to_end.py`: mesmos eventos, `expected_bars` 201/170, `r = 1.75`) são defeito, não ajuste de teste.

- [ ] **Step 3: Run the neighbours and the static checks**

Run: `uv run pytest tests/integration/api tests/integration/test_end_to_end.py && uv run ruff check src tests migrations && uv run mypy`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/integration/api/test_end_to_end_api.py
git commit -m "test(e2e): recorded session through the HTTP API with jobs, portfolio, metrics and REPRODUCE"
```

---

### Task 11: Cliente HTTP do dashboard (D40)

**Files:**
- Create: `dashboard/dashboard/client.py`
- Create: `dashboard/tests/test_client.py`

**Interfaces:**
- Consumes: as rotas da API (3A, 3B, Tasks 7–9), só por HTTP.
- Produces (`dashboard.client`):
  - `API_URL_VARIABLE = "DASHBOARD_API_URL"`, `API_KEY_VARIABLE = "API_KEY"`, `JsonObject = dict[str, Any]`;
  - `DashboardConfigError(missing: list[str])`, `ApiRequestFailed(status_code, code, reason=None, detail=None)`, `ApiUnreachable(error_type: str)`;
  - `json_text(body: Any) -> str`;
  - `class ApiClient(base_url, api_key, *, transport=None, timeout=15.0)` com `from_environment(environ)`, `close()` e os métodos: `health() -> JsonObject`, `health_log(*, limit=20) -> list[JsonObject]`, `alert_outbox(*, outcome=None, limit=100) -> list[JsonObject]`, `quality_overview() -> JsonObject`, `metrics(*, group_by=None, include_needs_review=False, replay=False, start=None, end=None) -> JsonObject`, `signals(*, day=None, strategy=None, limit=200) -> list[JsonObject]`, `create_manual_order(signal_id: str) -> JsonObject`, `orders(*, status=None, origin=None, strategy=None, replay=False, needs_review=None, limit=200) -> list[JsonObject]`, `order_detail(order_id: str) -> JsonObject`, `order_chart(order_id: str) -> JsonObject`, `market_bars(ticker, start, end, *, source=None) -> JsonObject`, `pressure(ticker, *, window_bars=30, cmf_threshold=None) -> JsonObject`, `watchlist() -> list[JsonObject]`, `add_ticker(ticker) -> JsonObject`, `remove_ticker(ticker) -> JsonObject`, `create_rule(ticker, body) -> JsonObject`, `delete_rule(rule_id) -> JsonObject`, `virtual_portfolio() -> JsonObject`, `real_portfolio() -> JsonObject`.

- [ ] **Step 1: Write the failing tests**

`dashboard/tests/test_client.py`:

```python
import json
from datetime import UTC, date, datetime
from decimal import Decimal

import httpx
import pytest

from dashboard.client import ApiClient, ApiRequestFailed, ApiUnreachable, DashboardConfigError


def client_for(handler):
    return ApiClient("http://api.test", "secret-key", transport=httpx.MockTransport(handler))


def test_requests_carry_the_key_and_numbers_come_back_as_decimal():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, text='{"orders": [{"r_multiple": "1.75", "win_rate": 0.5}]}')

    orders = client_for(handler).orders(status="CLOSED", needs_review=False)
    assert orders == [{"r_multiple": "1.75", "win_rate": Decimal("0.5")}]
    request = seen[0]
    assert request.headers["X-API-Key"] == "secret-key"
    assert request.url.path == "/orders"
    assert dict(request.url.params) == {"status": "CLOSED", "replay": "false", "needs_review": "false", "limit": "200"}


def test_query_values_are_serialized_and_none_is_dropped():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"signals": [], "groups": []})

    api = client_for(handler)
    api.metrics(include_needs_review=True, start=datetime(2025, 11, 25, 14, 30, tzinfo=UTC))
    api.signals(day=date(2025, 11, 25))
    assert dict(seen[0].url.params) == {"include_needs_review": "true", "replay": "false",
                                        "from": "2025-11-25T14:30:00+00:00"}
    assert dict(seen[1].url.params) == {"date": "2025-11-25", "limit": "200"}
    with pytest.raises(ValueError, match="naive datetime"):
        api.metrics(start=datetime(2025, 11, 25, 14, 30))  # noqa: DTZ001


def test_bodies_send_decimals_as_exact_json_numbers_and_path_segments_are_quoted():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(201, json={"rule": {"id": "r-1"}, "ticker": "BRK/B", "status": "CREATED"})

    api = client_for(handler)
    rule = api.create_rule("MSFT", {"kind": "PRICE_CROSS", "level": Decimal("101.50"), "direction": "ABOVE"})
    assert rule == {"id": "r-1"}
    assert b"101.50" in seen[0].content
    assert json.loads(seen[0].content, parse_float=Decimal)["level"] == Decimal("101.50")
    assert seen[0].headers["Content-Type"] == "application/json"
    api.add_ticker("BRK/B")
    assert seen[1].method == "PUT" and seen[1].url.raw_path == b"/watchlist/BRK%2FB"


def test_error_envelopes_become_api_request_failed_with_fixed_fields():
    def handler(request):
        return httpx.Response(422, json={"error": {"code": "SIGNAL_NO_LONGER_ACTIONABLE",
                                                   "reason": "ENTRY_OPPORTUNITY_ALREADY_OCCURRED", "detail": {}}})

    with pytest.raises(ApiRequestFailed) as caught:
        client_for(handler).create_manual_order("s-1")
    error = caught.value
    assert (error.status_code, error.code, error.reason, error.detail) == (
        422, "SIGNAL_NO_LONGER_ACTIONABLE", "ENTRY_OPPORTUNITY_ALREADY_OCCURRED", {})


def test_non_envelope_errors_and_invalid_bodies_use_fixed_codes():
    with pytest.raises(ApiRequestFailed) as bad_gateway:
        client_for(lambda request: httpx.Response(502, text="<html>proxy secret</html>")).watchlist()
    assert (bad_gateway.value.code, str(bad_gateway.value)) == ("HTTP_502", "HTTP_502")
    with pytest.raises(ApiRequestFailed) as not_json:
        client_for(lambda request: httpx.Response(200, text="not json")).watchlist()
    assert not_json.value.code == "INVALID_RESPONSE"


def test_transport_failures_keep_only_the_exception_type():
    def handler(request):
        raise httpx.ConnectError("connection refused to http://api.test with secret-key")

    with pytest.raises(ApiUnreachable) as caught:
        client_for(handler).virtual_portfolio()
    assert (caught.value.error_type, str(caught.value)) == ("ConnectError", "ConnectError")


def test_health_returns_the_unhealthy_body_instead_of_raising():
    body = {"state": "UNHEALTHY", "causes": [{"code": "DATABASE_UNAVAILABLE"}], "facts": None}
    assert client_for(lambda request: httpx.Response(503, json=body)).health() == body


def test_configuration_comes_from_the_environment_and_never_shows_the_key():
    with pytest.raises(DashboardConfigError) as caught:
        ApiClient.from_environment({"API_KEY": "  "})
    assert caught.value.missing == ["MISSING:DASHBOARD_API_URL", "MISSING:API_KEY"]
    api = ApiClient.from_environment({"DASHBOARD_API_URL": "http://api:8000", "API_KEY": "secret-key"})
    assert "secret-key" not in repr(api) and "api:8000" in repr(api)
    api.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --directory dashboard pytest tests/test_client.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'dashboard.client'`.

- [ ] **Step 3: Implement**

`dashboard/dashboard/client.py`:

```python
"""HTTP client for the Virtual Order Engine API. The dashboard talks only to the API (spec 2.1, D40)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import quote

import httpx

API_URL_VARIABLE = "DASHBOARD_API_URL"
API_KEY_VARIABLE = "API_KEY"
API_KEY_HEADER = "X-API-Key"
TIMEOUT_SECONDS = 15.0

JsonObject = dict[str, Any]


class DashboardConfigError(Exception):
    """Missing dashboard settings, named by variable, never by value."""

    def __init__(self, missing: list[str]) -> None:
        super().__init__(", ".join(missing))
        self.missing = missing


class ApiRequestFailed(Exception):
    """A non-2xx answer: HTTP status and the envelope's fixed code, reason and detail (spec 5.1, D19)."""

    def __init__(
        self, status_code: int, code: str, reason: str | None = None, detail: Mapping[str, Any] | None = None
    ) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.reason = reason
        self.detail: dict[str, Any] = dict(detail or {})


class ApiUnreachable(Exception):
    """Transport failure. Only the httpx exception type is kept: its message can carry the URL."""

    def __init__(self, error_type: str) -> None:
        super().__init__(error_type)
        self.error_type = error_type


def json_text(body: Any) -> str:
    """JSON with Decimal written as exact number tokens (the API reads numbers as Decimal, never float)."""
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


def _param(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("naive datetime in an API query")
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal | int | str):
        return str(value)
    raise TypeError(f"unsupported query value: {type(value).__name__}")


def _params(params: Mapping[str, Any]) -> dict[str, str]:
    return {name: _param(value) for name, value in params.items() if value is not None}


def _segment(value: str) -> str:
    return quote(value, safe="")


class ApiClient:
    def __init__(
        self, base_url: str, api_key: str, *, transport: httpx.BaseTransport | None = None,
        timeout: float = TIMEOUT_SECONDS,
    ) -> None:
        self._client = httpx.Client(base_url=base_url, headers={API_KEY_HEADER: api_key}, timeout=timeout,
                                    transport=transport)

    def __repr__(self) -> str:
        return f"ApiClient(base_url={str(self._client.base_url)!r})"

    @classmethod
    def from_environment(cls, environ: Mapping[str, str]) -> ApiClient:
        url = environ.get(API_URL_VARIABLE, "").strip()
        key = environ.get(API_KEY_VARIABLE, "").strip()
        missing = [f"MISSING:{name}" for name, value in ((API_URL_VARIABLE, url), (API_KEY_VARIABLE, key)) if not value]
        if missing:
            raise DashboardConfigError(missing)
        return cls(url, key)

    def close(self) -> None:
        self._client.close()

    def _request(
        self, method: str, path: str, *, params: Mapping[str, Any] | None = None, body: Any = None,
        accepted: tuple[int, ...] = (),
    ) -> JsonObject:
        content = None if body is None else json_text(body)
        headers = None if body is None else {"Content-Type": "application/json"}
        query = _params(params or {})
        try:
            response = self._client.request(method, path, params=query, content=content, headers=headers)
        except httpx.HTTPError as exc:
            raise ApiUnreachable(type(exc).__name__) from None
        try:
            payload: Any = json.loads(response.text, parse_float=Decimal) if response.content else {}
        except ValueError:
            payload = None
        status = response.status_code
        if 200 <= status < 300 or status in accepted:
            if isinstance(payload, dict):
                return payload
            raise ApiRequestFailed(status, "INVALID_RESPONSE")
        error = payload.get("error") if isinstance(payload, dict) else None
        if not isinstance(error, dict):
            raise ApiRequestFailed(status, f"HTTP_{status}")
        reason, detail = error.get("reason"), error.get("detail")
        raise ApiRequestFailed(status, str(error.get("code") or f"HTTP_{status}"),
                               reason if isinstance(reason, str) else None, detail if isinstance(detail, dict) else None)

    def health(self) -> JsonObject:
        return self._request("GET", "/health", accepted=(503,))  # UNHEALTHY still carries state and causes (D17)

    def health_log(self, *, limit: int = 20) -> list[JsonObject]:
        return list(self._request("GET", "/health/log", params={"limit": limit})["entries"])

    def alert_outbox(self, *, outcome: str | None = None, limit: int = 100) -> list[JsonObject]:
        return list(self._request("GET", "/alert-outbox", params={"outcome": outcome, "limit": limit})["alerts"])

    def quality_overview(self) -> JsonObject:
        return self._request("GET", "/quality/overview")

    def metrics(
        self, *, group_by: str | None = None, include_needs_review: bool = False, replay: bool = False,
        start: datetime | None = None, end: datetime | None = None,
    ) -> JsonObject:
        return self._request("GET", "/metrics", params={
            "group_by": group_by, "include_needs_review": include_needs_review, "replay": replay, "from": start,
            "to": end,
        })

    def signals(self, *, day: date | None = None, strategy: str | None = None, limit: int = 200) -> list[JsonObject]:
        return list(self._request("GET", "/signals", params={"date": day, "strategy": strategy,
                                                             "limit": limit})["signals"])

    def create_manual_order(self, signal_id: str) -> JsonObject:
        return self._request("POST", f"/signals/{_segment(signal_id)}/orders")

    def orders(
        self, *, status: str | None = None, origin: str | None = None, strategy: str | None = None,
        replay: bool = False, needs_review: bool | None = None, limit: int = 200,
    ) -> list[JsonObject]:
        return list(self._request("GET", "/orders", params={
            "status": status, "origin": origin, "strategy": strategy, "replay": replay, "needs_review": needs_review,
            "limit": limit,
        })["orders"])

    def order_detail(self, order_id: str) -> JsonObject:
        return self._request("GET", f"/orders/{_segment(order_id)}")

    def order_chart(self, order_id: str) -> JsonObject:
        return self._request("GET", f"/orders/{_segment(order_id)}/chart")

    def market_bars(
        self, ticker: str, start: datetime | str, end: datetime | str, *, source: str | None = None
    ) -> JsonObject:
        return self._request("GET", "/market/bars", params={"ticker": ticker, "from": start, "to": end,
                                                            "source": source})

    def pressure(self, ticker: str, *, window_bars: int = 30, cmf_threshold: Decimal | None = None) -> JsonObject:
        return self._request("GET", "/market/pressure", params={"ticker": ticker, "window_bars": window_bars,
                                                                "cmf_threshold": cmf_threshold})

    def watchlist(self) -> list[JsonObject]:
        return list(self._request("GET", "/watchlist")["watchlist"])

    def add_ticker(self, ticker: str) -> JsonObject:
        return self._request("PUT", f"/watchlist/{_segment(ticker)}")

    def remove_ticker(self, ticker: str) -> JsonObject:
        return self._request("DELETE", f"/watchlist/{_segment(ticker)}")

    def create_rule(self, ticker: str, body: Mapping[str, Any]) -> JsonObject:
        return dict(self._request("POST", f"/watchlist/{_segment(ticker)}/alerts", body=dict(body))["rule"])

    def delete_rule(self, rule_id: str) -> JsonObject:
        return self._request("DELETE", f"/alerts/{_segment(rule_id)}")

    def virtual_portfolio(self) -> JsonObject:
        return self._request("GET", "/portfolio/virtual")

    def real_portfolio(self) -> JsonObject:
        return self._request("GET", "/portfolio/real")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --directory dashboard pytest && uv run --directory dashboard ruff check . && uv run --directory dashboard mypy && uv run pytest tests/test_import_boundaries.py`
Expected: PASS. Se `request.url.raw_path` do httpx instalado normalizar `%2F`, troque a asserção por `seen[1].url.raw_path.endswith(b"BRK%2FB")` só se o valor bruto ainda trouxer `%2F`. Se o `/` chegar decodificado, é defeito do cliente: a rota receberia dois segmentos.

- [ ] **Step 5: Commit**

```bash
git add dashboard/dashboard/client.py dashboard/tests/test_client.py
git commit -m "feat(dashboard): HTTP API client with Decimal JSON, fixed error codes and hidden key"
```

---

### Task 12: View-models e figuras Plotly puros (entradas 2, 4, 7, 8; D40, D43–D46)

**Files:**
- Create: `dashboard/dashboard/viewmodels.py`
- Create: `dashboard/dashboard/charts.py`
- Create: `dashboard/tests/test_viewmodels.py`, `dashboard/tests/test_charts.py`
- Create: `tests/integration/api/test_dashboard_contract.py` (raiz, D57)
- Create: `dashboard/tests/fixtures/api/*.json` (17 arquivos gravados pelo teste da raiz), `dashboard/tests/test_api_contract.py`

**Interfaces:**
- Consumes: `ApiClient`, `ApiRequestFailed`, `ApiUnreachable` (Task 11); formatos JSON das rotas (D42 e 3A/3B); na raiz, `WorkerJobs`/`JobResult`, `post_json`, `scenario_bars`, `flat_raw`, `signal_body`, `ROOT`, `enqueue_alert`, `AlertKind` e o harness `api`.
- Produces (`dashboard.viewmodels`):
  - constantes: `EMPTY = "—"`, `SHORT_NOTICE`, `REAL_PHASE_0_MESSAGE`, `PRESSURE_TITLE`;
  - formatação: `fmt_price`, `fmt_decimal`, `fmt_pct`, `fmt_r`, `fmt_money`, `fmt_ts`, `today_et() -> date`, `market_day_window(day: date) -> tuple[datetime, datetime]`;
  - erros: `describe_api_error(exc: Exception) -> str`;
  - visão geral: `MetricCard(label, value, interval)`, `metric_cards(summary) -> list[MetricCard]`, `summary_warnings(summary) -> list[str]`, `review_exclusion_text(summary) -> str`, `RPoint(closed_at, cumulative_r, order_id)`, `cumulative_r(orders, *, include_needs_review) -> list[RPoint]`;
  - tabelas: `signal_rows`, `order_rows`, `order_label(order) -> str`, `event_log_rows`, `comparison_rows`, `replay_pairs`, `outbox_rows`, `health_log_rows`, `rule_rows`, `market_tickers(watchlist, orders) -> list[str]`;
  - detalhe e saúde: `QualityView(expected_bars, missing_bars, coverage, events, rechecks)` com `quality_view(detail)`; `HealthView(state, causes, info_notices, last_cycle, frozen_orders, incidents_total, review_queue, incidents, missing_runs)` com `health_view(report)`; `coverage_rows(overview)`, `gap_rows(overview)`;
  - portfólio e pressão: `VirtualPortfolioView(disclaimer, rows, totals)` com `virtual_portfolio_view(payload)`; `real_portfolio_message(payload) -> str | None`, `real_portfolio_rows(payload)`; `PressureDisplay(title, lines, method, disclaimer)` com `pressure_display(payload)`;
  - regras: `InputError(code)`, `rule_body(*, kind, level, direction, cmf_threshold, window_bars, cooldown_minutes) -> dict[str, Any]`, `watchlist_ticker(text: str) -> str` (`InputError("TICKER_INVALID")` para vazio, espaço ou `/`, M7);
  - limites e legendas: `ORDER_LIST_LIMIT = 1000`, `CURVE_LIMIT_NOTICE`, `curve_limit_notice(fetched: int) -> str | None` (D59), `WINDOW_TRUNCATED_NOTICE` (M10).
- Produces (`dashboard.charts`): `candlestick_figure(bars, vwap, *, title, levels=None, markers=(), evaluation_start_ts=None)` e `cumulative_r_figure(points)`, ambos devolvendo `plotly.graph_objects.Figure`.

- [ ] **Step 1: Write the failing tests**

`dashboard/tests/test_viewmodels.py`:

```python
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from dashboard.client import ApiRequestFailed, ApiUnreachable
from dashboard.viewmodels import (
    CURVE_LIMIT_NOTICE,
    EMPTY,
    InputError,
    comparison_rows,
    coverage_rows,
    cumulative_r,
    curve_limit_notice,
    describe_api_error,
    health_view,
    market_day_window,
    market_tickers,
    metric_cards,
    order_rows,
    pressure_display,
    quality_view,
    real_portfolio_message,
    replay_pairs,
    review_exclusion_text,
    rule_body,
    signal_rows,
    summary_warnings,
    virtual_portfolio_view,
    watchlist_ticker,
)

ET = ZoneInfo("America/New_York")
SUMMARY = {
    "trades": 12, "win_rate": Decimal("0.5833"), "avg_r": Decimal("0.42"), "expectancy_r": Decimal("0.42"),
    "profit_factor": Decimal("1.8"), "max_drawdown_r": Decimal("2.5"), "avg_duration_seconds": Decimal("3600"),
    "avg_mfe_r": Decimal("1.1"), "avg_mae_r": Decimal("-0.6"), "execution_rate": Decimal("0.75"),
    "win_rate_ci": [Decimal("0.3333"), Decimal("0.8333")], "expectancy_ci": [Decimal("-0.1"), Decimal("0.95")],
    "drawdown_sequence_risk": {"p5": Decimal("1.2"), "p50": Decimal("2.4"), "p95": Decimal("4.75")},
    "excluded_needs_review": {"count": 2, "reasons": {"MISSING_BAR_UNVERIFIABLE": 1, "MANUAL": 1}},
    "included_needs_review": {"count": 0, "reasons": {}},
    "warnings": ["INSUFFICIENT_SAMPLE", "SHORT_BORROW_NOT_SIMULATED"],
}


def test_api_errors_are_described_with_fixed_messages_only():
    no_longer = ApiRequestFailed(422, "SIGNAL_NO_LONGER_ACTIONABLE", "ENTRY_OPPORTUNITY_ALREADY_OCCURRED")
    assert describe_api_error(no_longer) == (
        "Sinal não é mais acionável: a ordem virtual não foi criada. "
        "Motivo: a entrada hipotética já ocorreu antes do clique.")
    assert describe_api_error(ApiRequestFailed(503, "ACTIONABILITY_UNVERIFIABLE", None, {"ingest_error": "x"})) == (
        "Não foi possível verificar a actionability (dados indisponíveis). Tente de novo mais tarde.")
    assert describe_api_error(ApiRequestFailed(422, "ALERT_RULE_INVALID", None,
                                               {"errors": ["MISSING_FIELD:level", {"loc": ["x"]}]})) == (
        "Regra de alerta inválida. Códigos: MISSING_FIELD:level.")
    assert describe_api_error(ApiRequestFailed(418, "TEAPOT")) == "Erro da API: TEAPOT (HTTP 418)."
    assert describe_api_error(ApiUnreachable("ConnectError")) == "API indisponível (ConnectError)."
    assert describe_api_error(RuntimeError("secret text")) == "Erro inesperado (RuntimeError)."


def test_metric_cards_show_values_and_confidence_intervals():
    cards = {card.label: (card.value, card.interval) for card in metric_cards(SUMMARY)}
    assert cards["Trades"] == ("12", None)
    assert cards["Win rate"] == ("58.3%", "IC 95%: 33.3% a 83.3%")
    assert cards["Expectância (R)"] == ("+0.42R", "IC 95%: -0.10R a +0.95R")
    assert cards["Profit factor"] == ("1.80", None)
    assert cards["Drawdown máx. (R)"] == ("2.50R", None)
    assert cards["Taxa de execução"] == ("75.0%", None)
    assert cards["MAE médio (R)"] == ("-0.60R", None)
    assert cards["Risco de sequência (DD p5/p50/p95)"][0] == "1.20 / 2.40 / 4.75 R"
    assert summary_warnings(SUMMARY) == ["Amostra insuficiente (menos de 30 trades).",
                                         "SHORT: borrow, locate e custo de empréstimo não são simulados."]
    assert review_exclusion_text(SUMMARY) == "Excluídas por revisão: 2 (MANUAL: 1, MISSING_BAR_UNVERIFIABLE: 1)"
    empty = {**SUMMARY, "win_rate": None, "win_rate_ci": None, "drawdown_sequence_risk": None}
    cards = {card.label: (card.value, card.interval) for card in metric_cards(empty)}
    assert cards["Win rate"] == (EMPTY, None) and cards["Risco de sequência (DD p5/p50/p95)"][0] == EMPTY


def test_cumulative_r_follows_the_metrics_review_policy_and_order():
    orders = [
        {"order_id": "a", "status": "CLOSED", "closed_at": "2025-11-25T18:00:00+00:00", "r_multiple": "1.75",
         "needs_review": False},
        {"order_id": "b", "status": "CLOSED", "closed_at": "2025-11-25T17:00:00+00:00", "r_multiple": "-1",
         "needs_review": False},
        {"order_id": "c", "status": "CLOSED", "closed_at": "2025-11-25T17:30:00+00:00", "r_multiple": "0.5",
         "needs_review": True},
        {"order_id": "d", "status": "OPEN", "closed_at": None, "r_multiple": "0", "needs_review": False},
    ]
    assert [(p.order_id, p.cumulative_r) for p in cumulative_r(orders, include_needs_review=False)] == [
        ("b", Decimal("-1")), ("a", Decimal("0.75"))]
    assert [(p.order_id, p.cumulative_r) for p in cumulative_r(orders, include_needs_review=True)] == [
        ("b", Decimal("-1")), ("c", Decimal("-0.5")), ("a", Decimal("1.25"))]


def test_signal_and_order_rows_are_display_strings():
    signal = {"signal_id": "s-1", "ticker": "AAPL", "direction": "LONG", "strategy": "REXSHARE",
              "strategy_version": "1.0", "entry_zone_low": "100", "entry_zone_high": "102", "stop": "97",
              "target1": "106", "target2": "110", "trigger_price": None, "auto_order_status": None,
              "created_at": "2025-11-25T14:00:00+00:00", "valid_until_ts": "2025-11-27T21:00:00+00:00"}
    (row,) = signal_rows([signal])
    assert row == {"Ticker": "AAPL", "Direção": "LONG", "Estratégia": "REXSHARE 1.0", "Zona": "100–102",
                   "Stop": "97", "Alvos": "106 / 110", "Gatilho": EMPTY, "Status AUTO": "sem ordem AUTO",
                   "Criado": "2025-11-25 09:00 ET", "Válido até": "2025-11-27 16:00 ET"}
    order = {"order_id": "0123456789ab", "ticker": "AAPL", "direction": "LONG", "strategy": "REXSHARE",
             "origin": "MANUAL_USER", "status": "PENDING", "entry_path": None, "avg_entry": None, "r_multiple": "0",
             "needs_review": True, "frozen": False, "replay": False, "created_at": "2025-11-25T15:00:30+00:00"}
    (row,) = order_rows([order])
    assert (row["Ordem"], row["Status"], row["R"], row["Revisão"], row["entry_path"], row["Criada"]) == (
        "01234567", "PENDING", EMPTY, "sim", EMPTY, "2025-11-25 10:00 ET")


def test_quality_view_shows_coverage_events_and_rechecks():
    detail = {"data_quality": {
        "expected_bars": 390, "missing_bars": 35,
        "events": [{"type": "DATA_GAP", "event_key": "DATA_GAP:x",
                    "payload": {"gap_start_ts": "2025-11-25T15:10:00+00:00", "minutes": 35}}],
        "rechecks": [{"session_date": "2025-11-25", "data_as_of": "2025-11-26T21:45:00+00:00",
                      "payload": {"status": "PROVIDER_FAILURE_FINAL", "terminal_reason": "PROVIDER_FAILURE",
                                  "level_touch_check": None, "review_flags": []}}],
    }}
    view = quality_view(detail)
    assert (view.expected_bars, view.missing_bars, view.coverage) == (390, 35, "91.03%")
    assert view.events == [{"Tipo": "DATA_GAP", "event_key": "DATA_GAP:x",
                            "Detalhe": "lacuna de 35 min desde 2025-11-25 10:10 ET"}]
    assert view.rechecks == [{"Pregão": "2025-11-25", "Status": "PROVIDER_FAILURE_FINAL",
                              "Motivo terminal": "PROVIDER_FAILURE", "Toque de nível": EMPTY, "Revisões": "0",
                              "data_as_of": "2025-11-26 16:45 ET"}]
    assert quality_view({"data_quality": {"expected_bars": 0, "missing_bars": 0, "events": [],
                                          "rechecks": []}}).coverage == EMPTY


def test_health_view_surfaces_info_causes_review_queue_and_missing_runs():
    report = {"state": "DEGRADED", "causes": [
        {"code": "END_OF_DAY_MISSING", "severity": "DEGRADED", "detail": {"session_days": ["2025-11-26"]}},
        {"code": "UNDELIVERABLE_ALERTS", "severity": "INFO", "detail": {"count": 2}},
        {"code": "ORDER_EVENT_ALERTS_BEHIND", "severity": "INFO", "detail": {"count": 1}},
    ], "facts": {
        "live_runs": [{"status": "COMPLETED", "started_at": "2025-11-25T15:30:05+00:00",
                       "detail": {"market_now": "2025-11-25T15:30:00+00:00"}}],
        "frozen_orders": 1, "incidents_total": 3, "needs_review": {"MANUAL": 2, "DATA_GAP": 1},
        "incident_groups": [{"kind": "PROJECTION_INTEGRITY_ERROR", "reason": None, "occurrences": 2,
                             "affected_count": 1, "last_recorded_at": "2025-11-25T16:00:00+00:00"}],
        "missing_runs": {"END_OF_DAY": ["2025-11-26"]},
    }}
    view = health_view(report)
    assert view.state == "DEGRADED"
    assert view.causes[0] == {"Código": "END_OF_DAY_MISSING", "Severidade": "DEGRADED"}
    assert view.info_notices == [
        "2 alerta(s) expirado(s) sem entrega nos últimos 7 dias (n8n fora do caminho crítico).",
        "1 evento(s) de ordem ficaram para trás do lookback de alertas.",
    ]
    assert view.last_cycle == "COMPLETED · market_now 2025-11-25 10:30 ET · iniciado 2025-11-25 10:30 ET"
    assert (view.frozen_orders, view.incidents_total) == (1, 3)
    assert view.review_queue == [{"Motivo": "DATA_GAP", "Ordens": "1"}, {"Motivo": "MANUAL", "Ordens": "2"}]
    assert view.incidents[0]["Tipo"] == "PROJECTION_INTEGRITY_ERROR" and view.incidents[0]["Motivo"] == EMPTY
    assert view.missing_runs == ["END_OF_DAY: 2025-11-26"]
    down = health_view({"state": "UNHEALTHY", "causes": [
        {"code": "DATABASE_UNAVAILABLE", "severity": "UNHEALTHY", "detail": {"error": "OperationalError"}}],
        "facts": None})
    assert (down.last_cycle, down.frozen_orders, down.review_queue) == (
        "sem dados (banco ou schema indisponível)", None, [])


def test_comparison_and_replay_pairs():
    (row,) = comparison_rows([{"key": None, "summary": SUMMARY}])
    assert (row["Grupo"], row["Trades"], row["Expectância"], row["Excluídas (revisão)"]) == (
        "(sem valor)", "12", "+0.42R", "2")
    originals = [{"order_id": "o-1", "status": "CLOSED", "r_multiple": "1.75", "fill_model_version": "v1"}]
    replays = [
        {"order_id": "r-1", "replay_of_order_id": "o-1", "replay_mode": "RECALCULATE", "status": "CLOSED",
         "r_multiple": "1.5", "fill_model_version": "v1", "created_at": "2025-11-26T10:00:00+00:00"},
        {"order_id": "r-2", "replay_of_order_id": "gone", "replay_mode": "REPRODUCE", "status": "CLOSED",
         "r_multiple": "1", "fill_model_version": "v1", "created_at": "2025-11-26T11:00:00+00:00"},
    ]
    assert replay_pairs(originals, replays) == [{
        "Original": "o-1", "Replay": "r-1", "Modo": "RECALCULATE", "Status original": "CLOSED",
        "Status replay": "CLOSED", "R original": "+1.75R", "R replay": "+1.50R", "Diferença (R)": "-0.25R",
        "Fill model": "v1 → v1",
    }]


def test_portfolio_views_never_mix_real_and_virtual():
    virtual = {"portfolio": {"basis": "PER_1R_NORMALIZED", "disclaimer": "per 1R", "positions": [{
        "position": {"ticker": "AAPL", "direction": "LONG", "strategy": "REXSHARE", "origin": "AUTO_STRATEGY",
                     "qty_open": "25", "avg_entry": "101", "last_close": "103",
                     "last_close_ts": "2025-11-25T15:29:00+00:00", "frozen": False},
        "notional": "2575", "unrealized_pnl": "50", "unrealized_r": "0.5", "open_r": "0.5", "allocation_pct": "100",
    }], "totals": {"positions": 1, "marked": 1, "unmarked": 0, "notional": "2575", "unrealized_pnl": "50",
                   "unrealized_r": "0.5", "risk_amount": "100"}}}
    view = virtual_portfolio_view(virtual)
    assert view.disclaimer == "per 1R"
    assert (view.rows[0]["P&L não realizado"], view.rows[0]["R não realizado"], view.rows[0]["Alocação"]) == (
        "$50.00", "+0.50R", "100.00%")
    assert view.totals == [("Posições", "1"), ("Sem marcação", "0"), ("Notional (por 1R)", "$2,575.00"),
                           ("P&L não realizado (por 1R)", "$50.00"), ("R não realizado", "+0.50R")]
    assert real_portfolio_message({"available": False, "reason": "PHASE_0_PENDING"}) == (
        "Portfólio real indisponível (Fase 0 pendente).")
    assert real_portfolio_message({"available": False, "reason": "SOURCE_UNAVAILABLE", "error": "RuntimeError"}) == (
        "Portfólio real indisponível (SOURCE_UNAVAILABLE: RuntimeError).")
    assert real_portfolio_message({"available": True, "positions": []}) is None


def test_pressure_display_always_carries_method_and_disclaimer():
    unavailable = pressure_display({"estimate": True, "method": "OHLCV_PRESSURE_ESTIMATE_V1",
                                    "disclaimer": "not order flow", "available": False,
                                    "reason": "INSUFFICIENT_BARS"})
    assert (unavailable.method, unavailable.disclaimer, unavailable.lines) == (
        "OHLCV_PRESSURE_ESTIMATE_V1", "not order flow", ["Indisponível: INSUFFICIENT_BARS"])
    assert "estimativa" in unavailable.title.lower()
    shown = pressure_display({"method": "OHLCV_PRESSURE_ESTIMATE_V1", "disclaimer": "d", "available": True,
                              "side": "BUY", "cmf_threshold": "0.01", "values": {
                                  "bars": 30, "last_bar_ts": "2025-11-25T17:50:00+00:00",
                                  "chaikin_money_flow": "0.0137", "obv_slope": "0.0345", "vwap": "107.0922",
                                  "vwap_distance_pct": "2.7152", "close_location_value": "0.4118"}})
    assert shown.lines == [
        "Janela: 30 candles até 2025-11-25 12:50 ET", "CMF: 0.0137", "Inclinação do OBV: 0.0345",
        "VWAP da janela: 107.0922 (distância 2.7152%)", "CLV do último candle: 0.4118",
        "Pressão forte estimada: BUY (limiar CMF 0.01)",
    ]
    assert pressure_display({"available": False, "reason": "NO_BARS"}).method == "método não informado"


def test_rule_bodies_parse_decimal_text_and_report_codes():
    assert rule_body(kind="PRICE_CROSS", level=" 101,50 ", direction="ABOVE", cmf_threshold="", window_bars=30,
                     cooldown_minutes=15) == {"kind": "PRICE_CROSS", "cooldown_minutes": 15,
                                              "level": Decimal("101.50"), "direction": "ABOVE"}
    assert rule_body(kind="PRESSURE", level="", direction="ABOVE", cmf_threshold="0.3", window_bars=20,
                     cooldown_minutes=30) == {"kind": "PRESSURE", "cooldown_minutes": 30,
                                              "cmf_threshold": Decimal("0.3"), "window_bars": 20}
    with pytest.raises(InputError) as caught:
        rule_body(kind="PRICE_CROSS", level="abc", direction="ABOVE", cmf_threshold="", window_bars=30,
                  cooldown_minutes=30)
    assert caught.value.code == "INVALID_DECIMAL:level"


def test_market_helpers():
    start, end = market_day_window(date(2025, 11, 25))
    assert (start, end) == (datetime(2025, 11, 25, tzinfo=ET), datetime(2025, 11, 26, tzinfo=ET))
    assert market_tickers([{"ticker": "MSFT"}], [{"ticker": "AAPL"}, {"ticker": "MSFT"}]) == ["AAPL", "MSFT"]


def test_owner_facing_limits_and_labels():
    assert curve_limit_notice(999) is None
    assert curve_limit_notice(1000) == CURVE_LIMIT_NOTICE  # D59: the oldest closed orders are the ones dropped
    assert watchlist_ticker(" brk.b ") == "BRK.B"
    for bad in ("", "  ", "BRK/B", "BR K"):  # M7: "/" would be decoded by the server before routing
        with pytest.raises(InputError) as caught:
            watchlist_ticker(bad)
        assert caught.value.code == "TICKER_INVALID"
    (row,) = coverage_rows({"coverage": [{"session_date": "2025-11-25", "orders": 2, "expected_bars": 780,
                                          "missing_bars": 35, "coverage_pct": "95.51"}]})
    assert row == {"Pregão": "2025-11-25", "Ordens": "2", "Minutos-ordem esperados": "780",
                   "Minutos-ordem ausentes": "35", "Cobertura": "95.51%"}  # M8: summed per order
```

`dashboard/tests/test_charts.py`:

```python
from decimal import Decimal

from dashboard.charts import candlestick_figure, cumulative_r_figure
from dashboard.viewmodels import RPoint

BARS = [
    {"ts": "2025-11-25T14:30:00+00:00", "open": "100", "high": "101", "low": "99.5", "close": "100.5",
     "volume": "1000"},
    {"ts": "2025-11-25T14:31:00+00:00", "open": "100.5", "high": "102", "low": "100", "close": "101.5",
     "volume": "3000"},
]
VWAP = [{"ts": BARS[0]["ts"], "value": "100.3333"}, {"ts": BARS[1]["ts"], "value": "100.9583"}]


def test_candlestick_figure_has_price_vwap_and_volume_rows_in_et():
    figure = candlestick_figure(BARS, VWAP, title="AAPL")
    assert [(trace.type, trace.name) for trace in figure.data] == [
        ("candlestick", "Candles"), ("scatter", "VWAP (sessão)"), ("bar", "Volume")]
    assert list(figure.data[0].open) == [100.0, 100.5] and list(figure.data[2].y) == [1000.0, 3000.0]
    assert list(figure.data[0].x) == ["2025-11-25 09:30", "2025-11-25 09:31"]
    assert figure.data[2].yaxis == "y2"
    assert figure.layout.xaxis.rangeslider.visible is False
    assert figure.layout.title.text == "AAPL"


def test_levels_zone_evaluation_start_and_markers_are_overlaid():
    figure = candlestick_figure(
        BARS, VWAP, title="AAPL",
        levels={"entry_zone_low": "100", "entry_zone_high": "102", "stop": "97", "target1": "106", "target2": None,
                "trigger_price": None, "avg_entry": None, "stop_current": None},
        markers=[{"type": "FILLED", "ts": BARS[1]["ts"], "price": "101"},
                 {"type": "DATA_GAP", "ts": BARS[0]["ts"], "price": None}],
        evaluation_start_ts=BARS[0]["ts"],
    )
    assert [shape.name for shape in figure.layout.shapes] == [
        "Zona de entrada", "Stop", "Alvo 1", "evaluation_start_ts", "DATA_GAP"]
    zone, stop = figure.layout.shapes[0], figure.layout.shapes[1]
    assert (zone.y0, zone.y1, stop.y0, stop.y1) == (100.0, 102.0, 97.0, 97.0)
    assert figure.layout.shapes[3].x0 == "2025-11-25 09:30"
    markers = [(trace.name, list(trace.y)) for trace in figure.data if getattr(trace, "mode", None) == "markers"]
    assert markers == [("FILLED", [101.0])]


def test_cumulative_r_figure():
    figure = cumulative_r_figure([RPoint("2025-11-25T17:00:00+00:00", Decimal("-1"), "b"),
                                  RPoint("2025-11-25T18:00:00+00:00", Decimal("0.75"), "a")])
    (trace,) = figure.data
    assert (trace.name, list(trace.x), list(trace.y)) == (
        "R acumulado", ["2025-11-25 12:00", "2025-11-25 13:00"], [-1.0, 0.75])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --directory dashboard pytest tests/test_viewmodels.py tests/test_charts.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'dashboard.viewmodels'`.

- [ ] **Step 3: Implement the view-models**

`dashboard/dashboard/viewmodels.py`:

```python
"""Pure mappers from API JSON to display values (D40). No Streamlit here, so every rule is unit-tested."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from dashboard.client import ApiRequestFailed, ApiUnreachable

ET = ZoneInfo("America/New_York")
EMPTY = "—"
SHORT_NOTICE = "SHORT: borrow, locate e custo de empréstimo não são simulados (spec 4.7)."
REAL_PHASE_0_MESSAGE = "Portfólio real indisponível (Fase 0 pendente)."
PRESSURE_TITLE = "Estimativa de pressão (OHLCV) — não é fluxo de ordens"
ORDER_LIST_LIMIT = 1000  # GET /orders maximum page size (D59)
CURVE_LIMIT_NOTICE = (
    "Curva limitada às 1000 ordens fechadas mais recentes (limite de GET /orders): as mais antigas ficaram de fora.")
WINDOW_TRUNCATED_NOTICE = "Janela do gráfico limitada a 7 dias a partir do dia de evaluation_start_ts (D42)."

ERROR_MESSAGES = {
    "SIGNAL_EXPIRED": "Sinal expirado: a ordem virtual não foi criada.",
    "SIGNAL_NO_LONGER_ACTIONABLE": "Sinal não é mais acionável: a ordem virtual não foi criada.",
    "ACTIONABILITY_UNVERIFIABLE": (
        "Não foi possível verificar a actionability (dados indisponíveis). Tente de novo mais tarde."),
    "SIGNAL_NOT_FOUND": "Sinal não encontrado.",
    "ORDER_NOT_FOUND": "Ordem não encontrada.",
    "TICKER_INVALID": "Ticker inválido.",
    "TICKER_NOT_TRADABLE": "Ticker não negociável.",
    "TICKER_UNVERIFIABLE": "Não foi possível verificar o ticker agora.",
    "ALERT_RULE_INVALID": "Regra de alerta inválida.",
    "WATCHLIST_TICKER_NOT_FOUND": "Ticker fora da watchlist.",
    "ALERT_RULE_NOT_FOUND": "Regra não encontrada.",
    "MARKET_REQUEST_INVALID": "Pedido de mercado inválido.",
    "REQUEST_INVALID": "Parâmetros inválidos.",
    "UNAUTHORIZED": "Chave da API recusada.",
    "INTERNAL_ERROR": "Erro interno da API.",
}
REASON_MESSAGES = {
    "INVALIDATED": "a tese foi invalidada (stop sem posição ou zona perdida com CANCEL)",
    "ENTRY_OPPORTUNITY_ALREADY_OCCURRED": "a entrada hipotética já ocorreu antes do clique",
    "STOPPED": "a ordem hipotética já foi estopada",
    "TARGET_REACHED": "a ordem hipotética já atingiu o alvo final",
}
WARNING_MESSAGES = {
    "INSUFFICIENT_SAMPLE": "Amostra insuficiente (menos de 30 trades).",
    "SHORT_BORROW_NOT_SIMULATED": "SHORT: borrow, locate e custo de empréstimo não são simulados.",
}
INFO_NOTICES = {
    "UNDELIVERABLE_ALERTS": "{count} alerta(s) expirado(s) sem entrega nos últimos 7 dias (n8n fora do caminho crítico).",
    "ORDER_EVENT_ALERTS_BEHIND": "{count} evento(s) de ordem ficaram para trás do lookback de alertas.",
}


def _decimal(value: Any) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def fmt_price(value: Any) -> str:
    return EMPTY if value is None else str(value)


def fmt_decimal(value: Any, places: int = 2) -> str:
    return EMPTY if value is None else f"{_decimal(value):.{places}f}"


def fmt_pct(value: Any) -> str:
    return EMPTY if value is None else f"{_decimal(value) * 100:.1f}%"


def fmt_r(value: Any) -> str:
    return EMPTY if value is None else f"{_decimal(value):+.2f}R"


def fmt_money(value: Any) -> str:
    return EMPTY if value is None else f"${_decimal(value):,.2f}"


def fmt_ts(value: Any) -> str:
    if value is None:
        return EMPTY
    moment = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return moment.astimezone(ET).strftime("%Y-%m-%d %H:%M ET")


def today_et() -> date:
    return datetime.now(ET).date()


def market_day_window(day: date) -> tuple[datetime, datetime]:
    return datetime.combine(day, time(0), tzinfo=ET), datetime.combine(day + timedelta(days=1), time(0), tzinfo=ET)


def describe_api_error(exc: Exception) -> str:
    """Fixed Portuguese messages by code; reasons and detail.errors codes appended. Never exception text (D40)."""
    if isinstance(exc, ApiUnreachable):
        return f"API indisponível ({exc.error_type})."
    if not isinstance(exc, ApiRequestFailed):
        return f"Erro inesperado ({type(exc).__name__})."
    parts = [ERROR_MESSAGES.get(exc.code, f"Erro da API: {exc.code} (HTTP {exc.status_code}).")]
    if exc.reason:
        parts.append(f"Motivo: {REASON_MESSAGES.get(exc.reason, exc.reason)}.")
    errors = exc.detail.get("errors")
    codes = [item for item in errors if isinstance(item, str)] if isinstance(errors, list) else []
    if codes:
        parts.append(f"Códigos: {', '.join(codes)}.")
    return " ".join(parts)


@dataclass(frozen=True)
class MetricCard:
    label: str
    value: str
    interval: str | None


def _interval(bounds: Any, formatter: Any) -> str | None:
    if not bounds:
        return None
    low, high = bounds
    return f"IC 95%: {formatter(low)} a {formatter(high)}"


def metric_cards(summary: Mapping[str, Any]) -> list[MetricCard]:
    risk = summary.get("drawdown_sequence_risk")
    sequence = EMPTY if not risk else (
        f"{fmt_decimal(risk['p5'])} / {fmt_decimal(risk['p50'])} / {fmt_decimal(risk['p95'])} R")
    drawdown = summary.get("max_drawdown_r")
    return [
        MetricCard("Trades", str(summary["trades"]), None),
        MetricCard("Win rate", fmt_pct(summary.get("win_rate")), _interval(summary.get("win_rate_ci"), fmt_pct)),
        MetricCard("Expectância (R)", fmt_r(summary.get("expectancy_r")),
                   _interval(summary.get("expectancy_ci"), fmt_r)),
        MetricCard("R médio", fmt_r(summary.get("avg_r")), None),
        MetricCard("Profit factor", fmt_decimal(summary.get("profit_factor")), None),
        MetricCard("Drawdown máx. (R)", EMPTY if drawdown is None else f"{fmt_decimal(drawdown)}R", None),
        MetricCard("Taxa de execução", fmt_pct(summary.get("execution_rate")), None),
        MetricCard("MFE médio (R)", fmt_r(summary.get("avg_mfe_r")), None),
        MetricCard("MAE médio (R)", fmt_r(summary.get("avg_mae_r")), None),
        MetricCard("Risco de sequência (DD p5/p50/p95)", sequence,
                   "Permutação da ordem dos trades: risco de sequência, não IC do drawdown histórico."),
    ]


def summary_warnings(summary: Mapping[str, Any]) -> list[str]:
    return [WARNING_MESSAGES.get(code, code) for code in summary.get("warnings", [])]


def review_exclusion_text(summary: Mapping[str, Any]) -> str:
    excluded, included = summary["excluded_needs_review"], summary["included_needs_review"]
    text = f"Excluídas por revisão: {excluded['count']}"
    if excluded["reasons"]:
        text += " (" + ", ".join(f"{name}: {count}" for name, count in sorted(excluded["reasons"].items())) + ")"
    if included["count"]:
        text += f" · incluídas em revisão: {included['count']}"
    return text


@dataclass(frozen=True)
class RPoint:
    closed_at: str
    cumulative_r: Decimal
    order_id: str


def cumulative_r(orders: Sequence[Mapping[str, Any]], *, include_needs_review: bool) -> list[RPoint]:
    """Same order and review policy as GET /metrics (spec 5.4): closed_at then order_id; flagged excluded by default."""
    closed = [
        order for order in orders
        if order.get("status") == "CLOSED" and order.get("closed_at") and order.get("r_multiple") is not None
        and (include_needs_review or not order.get("needs_review"))
    ]
    closed.sort(key=lambda order: (datetime.fromisoformat(str(order["closed_at"])), str(order["order_id"])))
    total = Decimal(0)
    points: list[RPoint] = []
    for order in closed:
        total += _decimal(order["r_multiple"])
        points.append(RPoint(str(order["closed_at"]), total, str(order["order_id"])))
    return points


def signal_rows(signals: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    return [{
        "Ticker": str(s["ticker"]), "Direção": str(s["direction"]),
        "Estratégia": f"{s['strategy']} {s['strategy_version']}",
        "Zona": f"{fmt_price(s['entry_zone_low'])}–{fmt_price(s['entry_zone_high'])}",
        "Stop": fmt_price(s["stop"]),
        "Alvos": fmt_price(s["target1"]) if s.get("target2") is None else f"{s['target1']} / {s['target2']}",
        "Gatilho": fmt_price(s.get("trigger_price")),
        "Status AUTO": str(s["auto_order_status"]) if s.get("auto_order_status") else "sem ordem AUTO",
        "Criado": fmt_ts(s["created_at"]), "Válido até": fmt_ts(s["valid_until_ts"]),
    } for s in signals]


def order_rows(orders: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    return [{
        "Ordem": str(o["order_id"])[:8], "Ticker": str(o["ticker"]), "Direção": str(o["direction"]),
        "Estratégia": str(o["strategy"]), "Origem": str(o["origin"]), "Status": str(o["status"]),
        "entry_path": fmt_price(o.get("entry_path")), "Entrada média": fmt_price(o.get("avg_entry")),
        "R": fmt_r(o.get("r_multiple")) if o.get("status") == "CLOSED" else EMPTY,
        "Revisão": "sim" if o.get("needs_review") else "não", "Frozen": "sim" if o.get("frozen") else "não",
        "Replay": "sim" if o.get("replay") else "não", "Criada": fmt_ts(o.get("created_at")),
    } for o in orders]


def order_label(order: Mapping[str, Any]) -> str:
    return f"{order['ticker']} · {order['status']} · {order['origin']} · {str(order['order_id'])[:8]}"


def event_log_rows(events: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    return [{
        "seq": str(e["seq"]), "Tipo": str(e["type"]), "event_key": str(e["event_key"]),
        "Candle": fmt_ts(e.get("bar_ts")), "Preço": fmt_price(e.get("price")), "Qtd": fmt_price(e.get("qty")),
    } for e in events]


@dataclass(frozen=True)
class QualityView:
    expected_bars: int
    missing_bars: int
    coverage: str
    events: list[dict[str, str]]
    rechecks: list[dict[str, str]]


def _quality_detail(event: Mapping[str, Any]) -> str:
    payload = event.get("payload") or {}
    if event.get("type") == "DATA_GAP":
        return f"lacuna de {payload.get('minutes')} min desde {fmt_ts(payload.get('gap_start_ts'))}"
    return f"{payload.get('expected_bars')} esperados, {payload.get('missing_bars')} ausentes"


def quality_view(detail: Mapping[str, Any]) -> QualityView:
    quality = detail["data_quality"]
    expected, missing = int(quality["expected_bars"]), int(quality["missing_bars"])
    coverage = EMPTY if expected == 0 else f"{Decimal(expected - missing) / Decimal(expected) * 100:.2f}%"
    events = [{"Tipo": str(e["type"]), "event_key": str(e["event_key"]), "Detalhe": _quality_detail(e)}
              for e in quality["events"]]
    rechecks = [{
        "Pregão": str(r["session_date"]), "Status": str(r["payload"].get("status") or EMPTY),
        "Motivo terminal": str(r["payload"].get("terminal_reason") or EMPTY),
        "Toque de nível": str(r["payload"].get("level_touch_check") or EMPTY),
        "Revisões": str(len(r["payload"].get("review_flags") or [])), "data_as_of": fmt_ts(r.get("data_as_of")),
    } for r in quality["rechecks"]]
    return QualityView(expected, missing, coverage, events, rechecks)


@dataclass(frozen=True)
class HealthView:
    state: str
    causes: list[dict[str, str]]
    info_notices: list[str]
    last_cycle: str
    frozen_orders: int | None
    incidents_total: int | None
    review_queue: list[dict[str, str]]
    incidents: list[dict[str, str]]
    missing_runs: list[str]


def health_view(report: Mapping[str, Any]) -> HealthView:
    causes = list(report.get("causes") or [])
    rows = [{"Código": str(c["code"]), "Severidade": str(c["severity"])} for c in causes]
    notices = [INFO_NOTICES[c["code"]].format(count=(c.get("detail") or {}).get("count", 0))
               for c in causes if c["code"] in INFO_NOTICES]
    facts = report.get("facts")
    if not facts:
        return HealthView(str(report["state"]), rows, notices, "sem dados (banco ou schema indisponível)", None, None,
                          [], [], [])
    live = facts.get("live_runs") or []
    last_cycle = "nenhum ciclo registrado" if not live else (
        f"{live[0]['status']} · market_now {fmt_ts((live[0].get('detail') or {}).get('market_now'))} · "
        f"iniciado {fmt_ts(live[0].get('started_at'))}")
    review_queue = [{"Motivo": str(reason), "Ordens": str(count)}
                    for reason, count in sorted((facts.get("needs_review") or {}).items())]
    incidents = [{
        "Tipo": str(g["kind"]), "Motivo": str(g.get("reason") or EMPTY), "Ocorrências": str(g["occurrences"]),
        "Ordens afetadas": str(g["affected_count"]), "Último": fmt_ts(g.get("last_recorded_at")),
    } for g in facts.get("incident_groups") or []]
    missing = [f"{kind}: {', '.join(days)}" for kind, days in sorted((facts.get("missing_runs") or {}).items())]
    return HealthView(str(report["state"]), rows, notices, last_cycle, int(facts["frozen_orders"]),
                      int(facts["incidents_total"]), review_queue, incidents, missing)


def coverage_rows(overview: Mapping[str, Any]) -> list[dict[str, str]]:
    """M8: the API sums expected/missing bars per order, so the columns are order-minutes, not session minutes."""
    return [{
        "Pregão": str(c["session_date"]), "Ordens": str(c["orders"]),
        "Minutos-ordem esperados": str(c["expected_bars"]), "Minutos-ordem ausentes": str(c["missing_bars"]),
        "Cobertura": EMPTY if c.get("coverage_pct") is None else f"{c['coverage_pct']}%",
    } for c in overview.get("coverage") or []]


def gap_rows(overview: Mapping[str, Any]) -> list[dict[str, str]]:
    return [{
        "Ordem": str(g["order_id"])[:8], "Ticker": str(g["ticker"]), "Início": fmt_ts(g.get("gap_start_ts")),
        "Minutos": str(g["minutes"]), "Gravado": fmt_ts(g.get("recorded_at")),
    } for g in overview.get("data_gaps") or []]


def comparison_rows(groups: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for group in groups:
        s = group["summary"]
        rows.append({
            "Grupo": "(sem valor)" if group.get("key") is None else str(group["key"]), "Trades": str(s["trades"]),
            "Win rate": fmt_pct(s.get("win_rate")), "Expectância": fmt_r(s.get("expectancy_r")),
            "IC expectância": _interval(s.get("expectancy_ci"), fmt_r) or EMPTY,
            "Profit factor": fmt_decimal(s.get("profit_factor")), "Taxa de execução": fmt_pct(s.get("execution_rate")),
            "Excluídas (revisão)": str(s["excluded_needs_review"]["count"]),
        })
    return rows


def _closed_r(order: Mapping[str, Any]) -> Decimal | None:
    return _decimal(order["r_multiple"]) if order.get("status") == "CLOSED" and order.get("r_multiple") is not None \
        else None


def replay_pairs(originals: Sequence[Mapping[str, Any]], replays: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    by_id = {str(o["order_id"]): o for o in originals}
    rows: list[dict[str, str]] = []
    for replay in sorted(replays, key=lambda item: (str(item.get("created_at") or ""), str(item["order_id"]))):
        source = by_id.get(str(replay.get("replay_of_order_id")))
        if source is None:
            continue
        original_r, replay_r = _closed_r(source), _closed_r(replay)
        rows.append({
            "Original": str(source["order_id"]), "Replay": str(replay["order_id"]),
            "Modo": str(replay.get("replay_mode") or EMPTY), "Status original": str(source["status"]),
            "Status replay": str(replay["status"]), "R original": fmt_r(original_r), "R replay": fmt_r(replay_r),
            "Diferença (R)": EMPTY if original_r is None or replay_r is None else fmt_r(replay_r - original_r),
            "Fill model": f"{source['fill_model_version']} → {replay['fill_model_version']}",
        })
    return rows


def outbox_rows(alerts: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    return [{
        "alert_key": str(a["alert_key"]), "Tipo": str(a["kind"]), "Criado": fmt_ts(a.get("created_at")),
        "Falhas": str(a["failures"]), "Último resultado": str(a.get("last_outcome") or "PENDENTE"),
        "HTTP": fmt_price(a.get("last_status_code")), "Erro": str(a.get("last_error_type") or EMPTY),
    } for a in alerts]


def health_log_rows(entries: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    return [{"Estado": str(e["state"]), "Causas": ", ".join(e.get("cause_codes") or []) or EMPTY,
             "Observado": fmt_ts(e.get("observed_at"))} for e in entries]


def rule_rows(rules: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    return [{
        "Tipo": str(r["kind"]), "Nível": fmt_price(r.get("level")), "Direção": str(r.get("direction") or EMPTY),
        "Limiar CMF": fmt_price(r.get("cmf_threshold")), "Janela": fmt_price(r.get("window_bars")),
        "Cooldown (min)": str(r["cooldown_minutes"]), "Criada": fmt_ts(r.get("created_at")),
    } for r in rules]


def market_tickers(watchlist: Sequence[Mapping[str, Any]], orders: Sequence[Mapping[str, Any]]) -> list[str]:
    return sorted({str(item["ticker"]) for item in watchlist} | {str(item["ticker"]) for item in orders})


@dataclass(frozen=True)
class VirtualPortfolioView:
    disclaimer: str
    rows: list[dict[str, str]]
    totals: list[tuple[str, str]]


def virtual_portfolio_view(payload: Mapping[str, Any]) -> VirtualPortfolioView:
    portfolio = payload["portfolio"]
    rows = [{
        "Ticker": str(m["position"]["ticker"]), "Direção": str(m["position"]["direction"]),
        "Estratégia": str(m["position"]["strategy"]), "Origem": str(m["position"]["origin"]),
        "Qtd (por 1R)": fmt_price(m["position"]["qty_open"]), "Entrada média": fmt_price(m["position"]["avg_entry"]),
        "Último fechamento": fmt_price(m["position"].get("last_close")),
        "Candle": fmt_ts(m["position"].get("last_close_ts")), "P&L não realizado": fmt_money(m.get("unrealized_pnl")),
        "R não realizado": fmt_r(m.get("unrealized_r")), "R marcado (aberto)": fmt_r(m.get("open_r")),
        "Alocação": EMPTY if m.get("allocation_pct") is None else f"{fmt_decimal(m['allocation_pct'])}%",
        "Frozen": "sim" if m["position"].get("frozen") else "não",
    } for m in portfolio["positions"]]
    totals = portfolio["totals"]
    return VirtualPortfolioView(str(portfolio["disclaimer"]), rows, [
        ("Posições", str(totals["positions"])), ("Sem marcação", str(totals["unmarked"])),
        ("Notional (por 1R)", fmt_money(totals["notional"])),
        ("P&L não realizado (por 1R)", fmt_money(totals["unrealized_pnl"])),  # M9: sums different risk_amounts
        ("R não realizado", fmt_r(totals["unrealized_r"])),
    ])


def real_portfolio_message(payload: Mapping[str, Any]) -> str | None:
    if payload.get("available"):
        return None
    reason = payload.get("reason")
    if reason == "PHASE_0_PENDING":
        return REAL_PHASE_0_MESSAGE
    error = payload.get("error")
    return f"Portfólio real indisponível ({reason}{': ' + str(error) if error else ''})."


def real_portfolio_rows(payload: Mapping[str, Any]) -> list[dict[str, str]]:
    return [{"Ticker": str(p["ticker"]), "Quantidade": fmt_price(p["quantity"]),
             "Custo médio": fmt_price(p.get("average_cost")), "Valor de mercado": fmt_money(p.get("market_value")),
             "as_of": fmt_ts(p.get("as_of"))} for p in payload.get("positions") or []]


@dataclass(frozen=True)
class PressureDisplay:
    title: str
    lines: list[str]
    method: str
    disclaimer: str


def pressure_display(payload: Mapping[str, Any]) -> PressureDisplay:
    """D44: method and disclaimer are always present, even when the estimate is unavailable."""
    method = str(payload.get("method") or "método não informado")
    disclaimer = str(payload.get("disclaimer") or "Estimativa derivada de candles; não é fluxo de ordens.")
    if not payload.get("available"):
        return PressureDisplay(PRESSURE_TITLE, [f"Indisponível: {payload.get('reason')}"], method, disclaimer)
    values = payload["values"]
    lines = [
        f"Janela: {values['bars']} candles até {fmt_ts(values['last_bar_ts'])}",
        f"CMF: {values['chaikin_money_flow']}", f"Inclinação do OBV: {values['obv_slope']}",
        f"VWAP da janela: {values['vwap']} (distância {values['vwap_distance_pct']}%)",
        f"CLV do último candle: {values['close_location_value']}",
    ]
    if payload.get("side"):
        lines.append(f"Pressão forte estimada: {payload['side']} (limiar CMF {payload['cmf_threshold']})")
    return PressureDisplay(PRESSURE_TITLE, lines, method, disclaimer)


class InputError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _decimal_input(text: str, name: str) -> Decimal:
    try:
        value = Decimal(text.strip().replace(",", "."))
    except InvalidOperation:
        raise InputError(f"INVALID_DECIMAL:{name}") from None
    if not value.is_finite():
        raise InputError(f"INVALID_DECIMAL:{name}")
    return value


def rule_body(
    *, kind: str, level: str, direction: str, cmf_threshold: str, window_bars: int, cooldown_minutes: int
) -> dict[str, Any]:
    body: dict[str, Any] = {"kind": kind, "cooldown_minutes": cooldown_minutes}
    if kind == "PRICE_CROSS":
        body["level"] = _decimal_input(level, "level")
        body["direction"] = direction
    else:
        body["cmf_threshold"] = _decimal_input(cmf_threshold, "cmf_threshold")
        body["window_bars"] = window_bars
    return body


def watchlist_ticker(text: str) -> str:
    """M7: the server decodes %2F before routing, so a ticker with "/" could never reach /watchlist/{ticker}."""
    ticker = text.strip().upper()
    if not ticker or any(character.isspace() or character == "/" for character in ticker):
        raise InputError("TICKER_INVALID")
    return ticker


def curve_limit_notice(fetched: int) -> str | None:
    """D59: GET /orders returns at most 1000 rows, newest first; a full page means older trades were left out."""
    return CURVE_LIMIT_NOTICE if fetched >= ORDER_LIST_LIMIT else None
```

- [ ] **Step 4: Implement the figures**

`dashboard/dashboard/charts.py`:

```python
"""Pure Plotly figure builders (D40, D43). Floats appear only here, to hand values to Plotly for drawing."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from dashboard.viewmodels import RPoint

ET = ZoneInfo("America/New_York")
LEVELS = (("stop", "Stop"), ("target1", "Alvo 1"), ("target2", "Alvo 2"), ("trigger_price", "Gatilho"),
          ("avg_entry", "Entrada média"), ("stop_current", "Stop vigente"))
MARKER_SYMBOLS = {
    "FILLED": "triangle-up", "TARGET1_HIT": "star", "TARGET2_HIT": "star", "STOPPED": "x", "TIME_EXIT": "square",
    "INVALIDATED": "x-open", "ZONE_LOST": "triangle-down-open", "ZONE_RECLAIMED": "triangle-up-open",
    "TRIGGER_HIT": "diamond",
}


def _et_label(value: Any) -> str:
    moment = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return moment.astimezone(ET).strftime("%Y-%m-%d %H:%M")


def _number(value: Any) -> float:
    return float(value if isinstance(value, Decimal) else Decimal(str(value)))


def candlestick_figure(
    bars: Sequence[Mapping[str, Any]],
    vwap: Sequence[Mapping[str, Any]],
    *,
    title: str,
    levels: Mapping[str, Any] | None = None,
    markers: Sequence[Mapping[str, Any]] = (),
    evaluation_start_ts: str | None = None,
) -> go.Figure:
    figure = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.75, 0.25], vertical_spacing=0.03)
    x = [_et_label(bar["ts"]) for bar in bars]
    figure.add_trace(go.Candlestick(
        x=x, open=[_number(b["open"]) for b in bars], high=[_number(b["high"]) for b in bars],
        low=[_number(b["low"]) for b in bars], close=[_number(b["close"]) for b in bars], name="Candles",
    ), row=1, col=1)
    figure.add_trace(go.Scatter(x=[_et_label(p["ts"]) for p in vwap], y=[_number(p["value"]) for p in vwap],
                                mode="lines", name="VWAP (sessão)"), row=1, col=1)
    figure.add_trace(go.Bar(x=x, y=[_number(b["volume"]) for b in bars], name="Volume"), row=2, col=1)
    levels = levels or {}
    if levels.get("entry_zone_low") is not None and levels.get("entry_zone_high") is not None:
        figure.add_shape(type="rect", xref="x domain", x0=0, x1=1, yref="y", y0=_number(levels["entry_zone_low"]),
                         y1=_number(levels["entry_zone_high"]), name="Zona de entrada",
                         fillcolor="rgba(46, 125, 50, 0.12)", line={"width": 0}, layer="below")
    for key, label in LEVELS:
        if levels.get(key) is None:
            continue
        value = _number(levels[key])
        figure.add_shape(type="line", xref="x domain", x0=0, x1=1, yref="y", y0=value, y1=value, name=label,
                         line={"dash": "dash", "width": 1})
        figure.add_annotation(xref="x domain", x=1, yref="y", y=value, text=label, showarrow=False, xanchor="left")
    if evaluation_start_ts is not None:
        start = _et_label(evaluation_start_ts)
        figure.add_shape(type="line", xref="x", x0=start, x1=start, yref="y domain", y0=0, y1=1,
                         name="evaluation_start_ts", line={"dash": "dot", "width": 1})
    priced: dict[str, list[Mapping[str, Any]]] = {}
    for marker in markers:
        if marker.get("price") is None:
            at = _et_label(marker["ts"])
            figure.add_shape(type="line", xref="x", x0=at, x1=at, yref="y domain", y0=0, y1=1,
                             name=str(marker["type"]), line={"dash": "dot", "width": 1, "color": "gray"})
        else:
            priced.setdefault(str(marker["type"]), []).append(marker)
    for kind, items in priced.items():
        figure.add_trace(go.Scatter(
            x=[_et_label(m["ts"]) for m in items], y=[_number(m["price"]) for m in items], mode="markers", name=kind,
            marker={"symbol": MARKER_SYMBOLS.get(kind, "circle"), "size": 11},
        ), row=1, col=1)
    figure.update_layout(title={"text": title}, height=640, legend={"orientation": "h"},
                         xaxis_rangeslider_visible=False)
    figure.update_xaxes(rangebreaks=[{"bounds": ["sat", "mon"]}, {"bounds": [16, 9.5], "pattern": "hour"}])
    return figure


def cumulative_r_figure(points: Sequence[RPoint]) -> go.Figure:
    figure = go.Figure(go.Scatter(x=[_et_label(p.closed_at) for p in points],
                                  y=[float(p.cumulative_r) for p in points], mode="lines+markers", name="R acumulado"))
    figure.update_layout(title={"text": "R acumulado (ordens fechadas)"}, yaxis_title="R", height=360)
    return figure
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run --directory dashboard pytest && uv run --directory dashboard ruff check . && uv run --directory dashboard mypy && uv run pytest tests/test_import_boundaries.py`
Expected: PASS. Se o Plotly instalado guardar `x`/`x0` de outra forma (por exemplo, convertendo as strings de data), ajuste só a forma da asserção estrutural, com a mesma informação. Registre a diferença no ledger, como no Step 9 da Task 1.

- [ ] **Step 6: Commit**

```bash
git add dashboard/dashboard/viewmodels.py dashboard/dashboard/charts.py dashboard/tests/test_viewmodels.py dashboard/tests/test_charts.py
git commit -m "feat(dashboard): pure view-models and Plotly figures for metrics, orders, health, market and portfolio"
```

- [ ] **Step 7: Write the root contract recorder (D57)**

`tests/integration/api/test_dashboard_contract.py`:

```python
"""D57: the dashboard's view of the API. A recorded session is walked through the real routes, and the JSON shape of
every response the dashboard reads is compared with dashboard/tests/fixtures/api/<name>.json. With
DASHBOARD_CONTRACT_RECORD=1 the responses are written as those fixtures instead; the dashboard project then asserts
its client, view-models and figures against them. The engine never imports the dashboard (D56)."""

import json
import os
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

    def check(self, name: str, response: Any) -> Any:
        assert response.status_code in (200, 503), (name, response.status_code, response.text)
        self.names.append(name)
        body = json.loads(response.text, parse_float=Decimal)
        path = FIXTURES / f"{name}.json"
        if RECORD:
            FIXTURES.mkdir(parents=True, exist_ok=True)
            pretty = json.dumps(json.loads(response.text), indent=2, sort_keys=True, ensure_ascii=False)
            path.write_text(pretty + "\n", encoding="utf-8")
            return body
        assert path.exists(), f"missing {path}: record it with DASHBOARD_CONTRACT_RECORD=1 (D57)"
        expected = shape(json.loads(path.read_text(encoding="utf-8"), parse_float=Decimal))
        assert differences(expected, shape(body)) == [], name
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
```

- [ ] **Step 8: Record the fixtures and check them in compare mode**

```bash
DASHBOARD_CONTRACT_RECORD=1 uv run pytest tests/integration/api/test_dashboard_contract.py
uv run pytest tests/integration/api/test_dashboard_contract.py
ls dashboard/tests/fixtures/api | wc -l
grep -rl "test-api-key" dashboard/tests/fixtures/api || echo "no key in fixtures"
```

Expected: as duas execuções PASSAM; 17 arquivos; `no key in fixtures`. Sem fixtures, o modo de comparação falha com `missing …: record it with DASHBOARD_CONTRACT_RECORD=1`. Uma asserção de fluxo que falhe aqui (status, `JobResult`) é defeito a investigar com `superpowers:systematic-debugging`, nunca motivo para relaxar o teste.

- [ ] **Step 9: Write the dashboard contract test**

`dashboard/tests/test_api_contract.py`:

```python
"""D57: the client's envelope keys, the view-models and the figures against responses recorded from the real API
(tests/integration/api/test_dashboard_contract.py in the engine project). No server, no network."""

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from dashboard.charts import candlestick_figure, cumulative_r_figure
from dashboard.client import ApiClient
from dashboard.viewmodels import (
    REAL_PHASE_0_MESSAGE,
    comparison_rows,
    coverage_rows,
    cumulative_r,
    event_log_rows,
    gap_rows,
    health_log_rows,
    health_view,
    market_tickers,
    metric_cards,
    order_rows,
    outbox_rows,
    pressure_display,
    quality_view,
    real_portfolio_message,
    replay_pairs,
    review_exclusion_text,
    rule_rows,
    signal_rows,
    summary_warnings,
    virtual_portfolio_view,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "api"


def raw(name: str) -> str:
    return (FIXTURES / f"{name}.json").read_text(encoding="utf-8")


def load(name: str) -> Any:
    return json.loads(raw(name), parse_float=Decimal)


def serving(name: str) -> ApiClient:
    body = raw(name)
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=body))
    return ApiClient("http://api.test", "key", transport=transport)


def test_the_client_unwraps_every_recorded_list_envelope():
    assert sorted(order["ticker"] for order in serving("orders").orders()) == ["AAPL", "AAPL", "MSFT"]
    assert [signal["ticker"] for signal in serving("signals").signals()] == ["AAPL"]
    assert [entry["ticker"] for entry in serving("watchlist").watchlist()] == ["MSFT"]
    assert "DEGRADED" in [entry["state"] for entry in serving("health_log").health_log()]
    assert [alert["alert_key"] for alert in serving("alert_outbox").alert_outbox()] == ["contract-alert"]


def test_overview_cards_curve_and_comparison_from_recorded_metrics():
    summary = load("metrics")["groups"][0]["summary"]
    cards = {card.label: card.value for card in metric_cards(summary)}
    assert (cards["Trades"], cards["Win rate"], cards["Expectância (R)"]) == ("2", "100.0%", "+1.75R")
    assert isinstance(summary_warnings(summary), list)
    assert review_exclusion_text(summary).startswith("Excluídas por revisão: ")
    by_origin = {row["Grupo"]: row["Trades"] for row in comparison_rows(load("metrics_by_origin")["groups"])}
    assert by_origin == {"AUTO_STRATEGY": "1", "MANUAL_USER": "1"}
    points = cumulative_r(load("orders_closed")["orders"], include_needs_review=False)
    assert [point.cumulative_r for point in points] == [Decimal("1.75"), Decimal("3.50")]
    assert list(cumulative_r_figure(points).data[0].y) == [1.75, 3.5]


def test_order_tables_detail_quality_and_chart_from_recorded_orders():
    assert sorted(row["Ticker"] for row in order_rows(load("orders")["orders"])) == ["AAPL", "AAPL", "MSFT"]
    (signal,) = signal_rows(load("signals")["signals"])
    assert (signal["Ticker"], signal["Zona"], signal["Alvos"]) == ("AAPL", "100–102", "106 / 110")
    detail = load("order_detail")
    quality = quality_view(detail)
    assert (quality.expected_bars, quality.missing_bars, quality.coverage) == (201, 0, "100.00%")
    assert [row["Tipo"] for row in event_log_rows(detail["events"])] == [
        "ORDER_CREATED", "FILLED", "TARGET1_HIT", "TARGET2_HIT", "DATA_QUALITY"]
    chart, bars = load("order_chart"), load("market_bars")
    figure = candlestick_figure(bars["bars"], bars["vwap"], title=chart["ticker"], levels=chart["levels"],
                                markers=chart["markers"], evaluation_start_ts=chart["evaluation_start_ts"])
    assert len(figure.data[0].open) == 201
    assert [trace.name for trace in figure.data if getattr(trace, "mode", None) == "markers"] == [
        "FILLED", "TARGET1_HIT", "TARGET2_HIT"]
    assert {"Zona de entrada", "Stop", "Alvo 1", "Alvo 2"} <= {shape.name for shape in figure.layout.shapes}
    assert chart["window_truncated"] is False


def test_market_portfolio_watchlist_comparison_and_health_from_recorded_responses():
    display = pressure_display(load("pressure"))
    assert display.method == "OHLCV_PRESSURE_ESTIMATE_V1" and "CMF: 0.0137" in display.lines
    assert display.lines[-1] == "Pressão forte estimada: BUY (limiar CMF 0.01)"
    portfolio = virtual_portfolio_view(load("portfolio_virtual"))
    assert [row["R não realizado"] for row in portfolio.rows] == ["+0.50R", "+0.50R"]
    assert dict(portfolio.totals)["R não realizado"] == "+1.00R"
    assert real_portfolio_message(load("portfolio_real")) == REAL_PHASE_0_MESSAGE
    watchlist, orders = load("watchlist")["watchlist"], load("orders")["orders"]
    assert [(row["Tipo"], row["Nível"], row["Direção"]) for row in rule_rows(watchlist[0]["rules"])] == [
        ("PRICE_CROSS", "101.5", "ABOVE")]
    assert market_tickers(watchlist, orders) == ["AAPL", "MSFT"]
    pairs = replay_pairs(orders, load("orders_replay")["orders"])
    assert len(pairs) == 2 and {pair["Diferença (R)"] for pair in pairs} == {"+0.00R"}
    view = health_view(load("health"))
    assert view.state in {"HEALTHY", "DEGRADED", "UNHEALTHY"} and view.frozen_orders == 0
    overview = load("quality_overview")
    assert {"2025-11-25", "2025-11-26"} <= {row["Pregão"] for row in coverage_rows(overview)}
    assert [(row["Ticker"], row["Minutos"]) for row in gap_rows(overview)] == [("MSFT", "35")]
    assert [row["Último resultado"] for row in outbox_rows(load("alert_outbox")["alerts"])] == ["EXPIRED"]
    log = [(row["Estado"], row["Causas"]) for row in health_log_rows(load("health_log")["entries"])]
    assert ("DEGRADED", "LIVE_CYCLE_STALE") in log
```

- [ ] **Step 10: Run the contract on both sides**

```bash
uv run --directory dashboard pytest tests/test_api_contract.py
uv run pytest tests/integration/api/test_dashboard_contract.py tests/test_import_boundaries.py
uv run --directory dashboard pytest && uv run --directory dashboard ruff check . && uv run --directory dashboard mypy
uv run ruff check src tests migrations && uv run mypy
```

Expected: PASS. Estes testes de contrato passam de primeira por construção (integram o que as Tasks 7–12 já entregaram; M15). Se um valor do dashboard divergir, o view-model ou o cliente discorda da API real: corrija o view-model e o teste unitário dele, **nunca** a fixture à mão. Se o teste da raiz falhar depois de uma mudança futura de rota, regrave as fixtures (Step 8) e deixe o teste do dashboard mostrar qual tela quebrou.

- [ ] **Step 11: Commit**

```bash
git add tests/integration/api/test_dashboard_contract.py dashboard/tests/fixtures/api dashboard/tests/test_api_contract.py
git commit -m "test(contract): record real API responses and assert dashboard view-models against them"
```

---

### Task 13: Views Streamlit, entrada do app e smoke com `AppTest` (spec 5.5; entradas 2, 3, 4, 7, 8; D40)

**Files:**
- Create: `dashboard/dashboard/app.py`
- Create: `dashboard/dashboard/views/__init__.py`, `common.py`, `overview.py`, `signals.py`, `orders.py`, `comparison.py`, `health.py`, `watchlist.py`, `market.py`, `portfolio.py` (todos em `dashboard/dashboard/views/`)
- Create: `dashboard/tests/test_app_smoke.py`

**Interfaces:**
- Consumes: `ApiClient`, `DashboardConfigError` (Task 11); todos os view-models e `candlestick_figure`/`cumulative_r_figure` (Task 12).
- Produces:
  - `dashboard.views.common.guarded[T](action: Callable[[], T]) -> T | None`;
  - uma função `render(client: ApiClient) -> None` por view;
  - `dashboard.app.CLIENT_KEY = "api_client"` e `PAGES: dict[str, Callable[[ApiClient], None]]`, com a chave de widget `"page"` no `st.sidebar.radio`.

- [ ] **Step 1: Write the failing smoke tests**

`dashboard/tests/test_app_smoke.py`:

```python
"""AppTest smoke over the real entry point with a fake API client (D40). No server, no network."""

from decimal import Decimal
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from dashboard.client import ApiRequestFailed, ApiUnreachable

APP = str(Path(__file__).resolve().parents[1] / "dashboard" / "app.py")  # dashboard/dashboard/app.py
TS = "2025-11-25T15:30:00+00:00"
SUMMARY = {
    "trades": 12, "win_rate": Decimal("0.5833"), "avg_r": Decimal("0.42"), "expectancy_r": Decimal("0.42"),
    "profit_factor": Decimal("1.8"), "max_drawdown_r": Decimal("2.5"), "avg_duration_seconds": Decimal("3600"),
    "avg_mfe_r": Decimal("1.1"), "avg_mae_r": Decimal("-0.6"), "execution_rate": Decimal("0.75"),
    "win_rate_ci": [Decimal("0.3333"), Decimal("0.8333")], "expectancy_ci": [Decimal("-0.1"), Decimal("0.95")],
    "drawdown_sequence_risk": None, "excluded_needs_review": {"count": 1, "reasons": {"MANUAL": 1}},
    "included_needs_review": {"count": 0, "reasons": {}}, "warnings": ["INSUFFICIENT_SAMPLE"],
}
SIGNAL = {
    "signal_id": "s-1", "ticker": "AAPL", "direction": "LONG", "strategy": "REXSHARE", "strategy_version": "1.0",
    "entry_zone_low": "100", "entry_zone_high": "102", "stop": "97", "target1": "106", "target2": "110",
    "trigger_price": None, "auto_order_id": "o-auto", "auto_order_status": "PENDING", "created_at": TS,
    "valid_until_ts": "2025-11-27T21:00:00+00:00",
}
PRESSURE = {
    "ticker": "MSFT", "price_source": "alpaca_iex", "data_as_of": TS, "window_bars": 30, "estimate": True,
    "method": "OHLCV_PRESSURE_ESTIMATE_V1",
    "disclaimer": "Estimate derived from 1-minute OHLCV bars; it is not order-flow or trade-side data.",
    "available": False, "reason": "INSUFFICIENT_BARS", "values": None, "cmf_threshold": None, "side": None,
}


class FakeApi:
    def __init__(self) -> None:
        self.manual_calls: list[str] = []
        self.manual_error: Exception | None = None

    def metrics(self, **kwargs):
        return {"groups": [{"key": None, "summary": SUMMARY}]}

    def orders(self, **kwargs):
        return []

    def signals(self, **kwargs):
        return [SIGNAL]

    def create_manual_order(self, signal_id):
        self.manual_calls.append(signal_id)
        if self.manual_error is not None:
            raise self.manual_error
        return {"order_id": "o-manual-1", "actionability_run_id": "run", "data_as_of": TS,
                "partial_bar_skipped": False}

    def health(self):
        return {"state": "HEALTHY", "causes": [
            {"code": "UNDELIVERABLE_ALERTS", "severity": "INFO", "detail": {"count": 2}},
            {"code": "ORDER_EVENT_ALERTS_BEHIND", "severity": "INFO", "detail": {"count": 1}},
        ], "facts": {"live_runs": [], "frozen_orders": 0, "incidents_total": 0, "incident_groups": [],
                     "needs_review": {"MANUAL": 1}, "missing_runs": {}}}

    def quality_overview(self):
        return {"window_days": 14, "coverage": [], "data_gaps": []}

    def alert_outbox(self, **kwargs):
        return [{"alert_key": "HEALTH:9", "kind": "HEALTH", "created_at": TS, "failures": 3,
                 "last_outcome": "EXPIRED", "last_status_code": None, "last_error_type": "ConnectTimeout"}]

    def health_log(self, **kwargs):
        return [{"id": 3, "state": "DEGRADED", "cause_codes": ["LIVE_CYCLE_STALE"], "observed_at": TS}]

    def watchlist(self):
        return [{"ticker": "MSFT", "added_at": TS, "rules": []}]

    def market_bars(self, ticker, start, end, *, source=None):
        return {"ticker": ticker, "price_source": "alpaca_iex", "data_as_of": TS, "vwap_method": "SESSION_VWAP",
                "bars": [], "vwap": []}

    def pressure(self, ticker, **kwargs):
        return PRESSURE

    def virtual_portfolio(self):
        return {"kind": "VIRTUAL", "data_as_of": TS, "portfolio": {
            "basis": "PER_1R_NORMALIZED", "disclaimer": "Virtual positions sized per 1R of risk.", "positions": [],
            "totals": {"positions": 0, "marked": 0, "unmarked": 0, "notional": "0", "unrealized_pnl": "0",
                       "unrealized_r": "0", "risk_amount": "0"}}}

    def real_portfolio(self):
        return {"kind": "REAL", "available": False, "reason": "PHASE_0_PENDING", "source": None, "positions": []}


def open_page(fake, page):
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["api_client"] = fake
    at.run()
    assert not at.exception
    if page != "Visão geral":
        at.radio(key="page").set_value(page).run()
        assert not at.exception
    return at


def values(elements):
    return [element.value for element in elements]


def test_overview_shows_cards_warnings_and_review_exclusions():
    at = open_page(FakeApi(), "Visão geral")
    assert at.title[0].value == "Visão geral"
    assert {metric.label for metric in at.metric} >= {"Trades", "Win rate", "Expectância (R)", "Taxa de execução"}
    assert "Amostra insuficiente (menos de 30 trades)." in values(at.warning)
    assert "Excluídas por revisão: 1 (MANUAL: 1)" in values(at.caption)


def test_buy_virtual_creates_a_manual_order():
    fake = FakeApi()
    at = open_page(fake, "Sinais do dia")
    at.button(key="buy-s-1").click().run()
    assert fake.manual_calls == ["s-1"]
    assert any("o-manual-1" in value for value in values(at.success))


@pytest.mark.parametrize("error, message", [
    (ApiRequestFailed(422, "SIGNAL_EXPIRED"), "Sinal expirado: a ordem virtual não foi criada."),
    (ApiRequestFailed(422, "SIGNAL_NO_LONGER_ACTIONABLE", "STOPPED"),
     "Sinal não é mais acionável: a ordem virtual não foi criada. Motivo: a ordem hipotética já foi estopada."),
    (ApiRequestFailed(503, "ACTIONABILITY_UNVERIFIABLE"),
     "Não foi possível verificar a actionability (dados indisponíveis). Tente de novo mais tarde."),
    (ApiUnreachable("ConnectError"), "API indisponível (ConnectError)."),
])
def test_buy_virtual_errors_are_shown_explicitly_without_a_traceback(error, message):
    fake = FakeApi()
    fake.manual_error = error
    at = open_page(fake, "Sinais do dia")
    at.button(key="buy-s-1").click().run()
    assert not at.exception and values(at.error) == [message]


def test_health_page_shows_info_causes_expired_alerts_and_the_last_health_log():
    at = open_page(FakeApi(), "Saúde")
    assert "Estado: HEALTHY" in values(at.success)
    assert "2 alerta(s) expirado(s) sem entrega nos últimos 7 dias (n8n fora do caminho crítico)." in values(at.info)
    assert "1 evento(s) de ordem ficaram para trás do lookback de alertas." in values(at.info)
    assert any("DEGRADED" in value and "LIVE_CYCLE_STALE" in value for value in values(at.markdown))


def test_portfolio_page_shows_the_phase_0_message_and_never_a_combined_total():
    at = open_page(FakeApi(), "Portfólio")
    assert "Portfólio real indisponível (Fase 0 pendente)." in values(at.info)
    assert "Totais do portfólio real e do virtual nunca são somados." in values(at.caption)


def test_market_page_always_labels_pressure_as_an_estimate():
    at = open_page(FakeApi(), "Mercado")
    captions = values(at.caption)
    assert "Método: OHLCV_PRESSURE_ESTIMATE_V1" in captions
    assert PRESSURE["disclaimer"] in captions


def test_watchlist_page_lists_tickers():
    at = open_page(FakeApi(), "Watchlist e alertas")
    assert at.title[0].value == "Watchlist e regras de alerta" and not at.exception


def test_missing_configuration_names_the_variables_only(monkeypatch):
    monkeypatch.delenv("DASHBOARD_API_URL", raising=False)
    monkeypatch.setenv("API_KEY", "secret-value")
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    assert values(at.error) == ["Configuração do dashboard incompleta: MISSING:DASHBOARD_API_URL"]
    assert "secret-value" not in str(values(at.error))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --directory dashboard pytest tests/test_app_smoke.py`
Expected: FAIL — o `AppTest` não encontra `dashboard/dashboard/app.py`.

- [ ] **Step 3: Implement the shared view helpers and the entry point**

`dashboard/dashboard/views/__init__.py`:

```python
"""Thin Streamlit views (D40). Named `views`, not `pages`: a `pages/` folder next to app.py turns multipage on."""
```

`dashboard/dashboard/views/common.py`:

```python
"""Helpers shared by every view (D40)."""

from __future__ import annotations

from collections.abc import Callable

import streamlit as st

from dashboard.viewmodels import describe_api_error


def guarded[T](action: Callable[[], T]) -> T | None:
    """Runs one API call; any failure becomes a fixed message on screen, never a traceback or exception text."""
    try:
        return action()
    except Exception as exc:  # noqa: BLE001 - the owner's screen shows codes or exception types only (D40)
        st.error(describe_api_error(exc))
        return None
```

`dashboard/dashboard/app.py`:

```python
"""Streamlit entry point: `streamlit run dashboard/app.py` from the dashboard project. API only (spec 2.1, D40)."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import cast

import streamlit as st

from dashboard.client import ApiClient, DashboardConfigError
from dashboard.views import comparison, health, market, orders, overview, portfolio, signals, watchlist

CLIENT_KEY = "api_client"
PAGES: dict[str, Callable[[ApiClient], None]] = {
    "Visão geral": overview.render,
    "Sinais do dia": signals.render,
    "Ordens": orders.render,
    "Comparação": comparison.render,
    "Saúde": health.render,
    "Watchlist e alertas": watchlist.render,
    "Mercado": market.render,
    "Portfólio": portfolio.render,
}


def _client() -> ApiClient | None:
    """One httpx client per browser session, never closed explicitly: accepted for a single owner (D40, M13)."""
    existing = st.session_state.get(CLIENT_KEY)
    if existing is not None:
        return cast(ApiClient, existing)  # injected by tests, or built on a previous rerun
    try:
        client = ApiClient.from_environment(os.environ)
    except DashboardConfigError as exc:
        st.error(f"Configuração do dashboard incompleta: {', '.join(exc.missing)}")
        return None
    st.session_state[CLIENT_KEY] = client
    return client


def main() -> None:
    st.set_page_config(page_title="Virtual Order Engine", layout="wide")
    client = _client()
    if client is None:
        return
    page = st.sidebar.radio("Página", list(PAGES), key="page")
    PAGES[str(page)](client)


main()
```

- [ ] **Step 4: Implement the views**

`dashboard/dashboard/views/overview.py`:

```python
from __future__ import annotations

import streamlit as st

from dashboard.charts import cumulative_r_figure
from dashboard.client import ApiClient
from dashboard.viewmodels import (
    ORDER_LIST_LIMIT,
    cumulative_r,
    curve_limit_notice,
    metric_cards,
    review_exclusion_text,
    summary_warnings,
)
from dashboard.views.common import guarded


def render(client: ApiClient) -> None:
    st.title("Visão geral")
    include = st.toggle("Incluir ordens em revisão", value=False, key="overview-include-review")
    metrics = guarded(lambda: client.metrics(include_needs_review=include))
    closed = guarded(lambda: client.orders(status="CLOSED", limit=ORDER_LIST_LIMIT))
    if metrics is None or closed is None:
        return
    if not metrics["groups"]:
        st.info("Sem métricas.")
        return
    summary = metrics["groups"][0]["summary"]
    for warning in summary_warnings(summary):
        st.warning(warning)
    st.caption(review_exclusion_text(summary))
    columns = st.columns(3)
    for index, card in enumerate(metric_cards(summary)):
        with columns[index % 3]:
            st.metric(card.label, card.value, help=card.interval)
    points = cumulative_r(closed, include_needs_review=include)
    notice = curve_limit_notice(len(closed))
    if notice is not None:
        st.caption(notice)  # D59: a full page of GET /orders drops the oldest closed trades
    if points:
        st.plotly_chart(cumulative_r_figure(points))
    else:
        st.info("Nenhuma ordem fechada ainda.")
```

`dashboard/dashboard/views/signals.py`:

```python
from __future__ import annotations

from datetime import date

import streamlit as st

from dashboard.client import ApiClient
from dashboard.viewmodels import signal_rows, today_et
from dashboard.views.common import guarded


def render(client: ApiClient) -> None:
    st.title("Sinais do dia")
    day = st.date_input("Data (ET)", value=today_et(), key="signals-day")
    if not isinstance(day, date):
        return
    rows = guarded(lambda: client.signals(day=day))
    if rows is None:
        return
    if not rows:
        st.info("Nenhum sinal nesta data.")
        return
    st.dataframe(signal_rows(rows), hide_index=True)
    st.caption("Comprar virtual cria uma ordem MANUAL_USER; a API recusa sinais vencidos ou já decididos (spec 3.4.1).")
    for signal in rows:
        signal_id = str(signal["signal_id"])
        if st.button(f"Comprar virtual — {signal['ticker']} · {signal['strategy']}", key=f"buy-{signal_id}"):
            created = guarded(lambda sid=signal_id: client.create_manual_order(sid))  # default arg: ruff B023
            if created is not None:
                st.success(f"Ordem virtual MANUAL_USER criada: {created['order_id']}")
```

`dashboard/dashboard/views/orders.py`:

```python
from __future__ import annotations

import streamlit as st

from dashboard.charts import candlestick_figure
from dashboard.client import ApiClient
from dashboard.viewmodels import (
    ORDER_LIST_LIMIT,
    SHORT_NOTICE,
    WINDOW_TRUNCATED_NOTICE,
    event_log_rows,
    order_label,
    order_rows,
    quality_view,
)
from dashboard.views.common import guarded

STATUSES = ["PENDING", "OPEN", "PARTIAL", "CLOSED", "EXPIRED", "INVALIDATED", "CANCELED"]
ALL = "(todos)"
REVIEW = {"em revisão": True, "sem revisão": False}


def render(client: ApiClient) -> None:
    st.title("Ordens")
    status_col, origin_col, strategy_col, review_col = st.columns(4)
    status = status_col.selectbox("Status", [ALL, *STATUSES], key="orders-status")
    origin = origin_col.selectbox("Origem", [ALL, "AUTO_STRATEGY", "MANUAL_USER"], key="orders-origin")
    strategy = strategy_col.text_input("Estratégia", key="orders-strategy")
    review = review_col.selectbox("Revisão", [ALL, *REVIEW], key="orders-review")
    replay = st.checkbox("Mostrar replays", key="orders-replay")
    rows = guarded(lambda: client.orders(
        status=None if status == ALL else status, origin=None if origin == ALL else origin,
        strategy=strategy.strip() or None, replay=replay, needs_review=REVIEW.get(str(review)), limit=ORDER_LIST_LIMIT,
    ))
    if rows is None:
        return
    st.dataframe(order_rows(rows), hide_index=True)
    if not rows:
        return
    by_id = {str(row["order_id"]): row for row in rows}
    selected = st.selectbox("Detalhe da ordem", list(by_id), format_func=lambda oid: order_label(by_id[oid]),
                            key="orders-detail")
    if selected is None:
        return
    order_id = str(selected)
    detail = guarded(lambda: client.order_detail(order_id))
    chart = guarded(lambda: client.order_chart(order_id))
    if detail is None or chart is None:
        return
    if chart["direction"] == "SHORT":
        st.warning(SHORT_NOTICE)
    bars = guarded(lambda: client.market_bars(chart["ticker"], chart["window"]["from"], chart["window"]["to"],
                                              source=chart["price_source"]))
    if bars is not None:
        st.plotly_chart(candlestick_figure(
            bars["bars"], bars["vwap"], title=f"{chart['ticker']} · {chart['status']}", levels=chart["levels"],
            markers=chart["markers"], evaluation_start_ts=chart["evaluation_start_ts"],
        ))
        st.caption(f"Candles armazenados lidos as-of {bars['data_as_of']} (fonte {bars['price_source']}); "
                   f"evaluation_start_ts {chart['evaluation_start_ts']}.")
        if chart.get("window_truncated"):
            st.caption(WINDOW_TRUNCATED_NOTICE)  # M10: later markers and bars fall outside the 7-day window
    quality = quality_view(detail)
    st.subheader("Qualidade de dados")
    st.write(f"Minutos esperados: {quality.expected_bars} · ausentes: {quality.missing_bars} · "
             f"cobertura: {quality.coverage}")
    if quality.events:
        st.dataframe(quality.events, hide_index=True)
    st.markdown("**Rechecks (DATA_QUALITY_RECHECK)**")
    if quality.rechecks:
        st.dataframe(quality.rechecks, hide_index=True)
    else:
        st.caption("Nenhum recheck.")
    st.subheader("Log de eventos")
    st.dataframe(event_log_rows(detail["events"]), hide_index=True)
```

`dashboard/dashboard/views/comparison.py`:

```python
from __future__ import annotations

import streamlit as st

from dashboard.client import ApiClient
from dashboard.viewmodels import comparison_rows, replay_pairs
from dashboard.views.common import guarded

COMPARISONS = (("origin", "AUTO × MANUAL"), ("strategy", "Estratégia"),
               ("fill_model_version", "Versão do fill model"), ("entry_path", "entry_path"))


def render(client: ApiClient) -> None:
    st.title("Comparação")
    include = st.toggle("Incluir ordens em revisão", value=False, key="comparison-include-review")
    for group_by, title in COMPARISONS:
        payload = guarded(lambda group=group_by: client.metrics(group_by=group, include_needs_review=include))
        if payload is not None:
            st.subheader(title)
            st.dataframe(comparison_rows(payload["groups"]), hide_index=True)
    st.subheader("Original × replay")
    originals = guarded(lambda: client.orders(limit=1000))
    replays = guarded(lambda: client.orders(replay=True, limit=1000))
    if originals is None or replays is None:
        return
    pairs = replay_pairs(originals, replays)
    if not pairs:
        st.info("Nenhum replay.")
        return
    st.dataframe(pairs, hide_index=True)
    selected = st.selectbox("Configuração do fill model", [pair["Replay"] for pair in pairs], key="comparison-replay")
    pair = next((item for item in pairs if item["Replay"] == selected), None)
    if pair is None:
        return
    source_chart = guarded(lambda: client.order_chart(pair["Original"]))
    replay_chart = guarded(lambda: client.order_chart(pair["Replay"]))
    left, right = st.columns(2)
    for column, label, chart in ((left, "Original", source_chart), (right, "Replay", replay_chart)):
        if chart is not None:
            column.markdown(f"**{label}** · {chart['fill_model_version']}")
            column.json(chart["config_snapshot"])
```

`dashboard/dashboard/views/health.py`:

```python
from __future__ import annotations

import streamlit as st

from dashboard.client import ApiClient
from dashboard.viewmodels import coverage_rows, fmt_ts, gap_rows, health_log_rows, health_view, outbox_rows
from dashboard.views.common import guarded


def render(client: ApiClient) -> None:
    st.title("Saúde")
    report = guarded(client.health)
    if report is not None:
        view = health_view(report)
        if view.state == "HEALTHY":
            st.success(f"Estado: {view.state}")
        elif view.state == "DEGRADED":
            st.warning(f"Estado: {view.state}")
        else:
            st.error(f"Estado: {view.state}")
        for notice in view.info_notices:
            st.info(notice)
        st.subheader("Causas")
        st.dataframe(view.causes, hide_index=True)
        st.subheader("Último ciclo")
        st.write(view.last_cycle)
        if view.frozen_orders is not None:
            frozen_col, incidents_col = st.columns(2)
            frozen_col.metric("Ordens frozen", str(view.frozen_orders))
            incidents_col.metric("Incidentes de integridade (total)", str(view.incidents_total))
        for missing in view.missing_runs:
            st.warning(f"Job ausente — {missing}")
        st.subheader("Fila NEEDS_REVIEW por motivo")
        st.dataframe(view.review_queue, hide_index=True)
        st.subheader("Incidentes de integridade (24 h)")
        st.dataframe(view.incidents, hide_index=True)
    overview = guarded(client.quality_overview)
    if overview is not None:
        st.subheader(f"Cobertura de dados em minutos-ordem (últimos {overview['window_days']} dias)")
        st.caption("Somada por ordem: duas ordens do mesmo ticker contam o mesmo minuto duas vezes (M8).")
        st.dataframe(coverage_rows(overview), hide_index=True)
        st.subheader("DATA_GAP")
        st.dataframe(gap_rows(overview), hide_index=True)
    expired = guarded(lambda: client.alert_outbox(outcome="EXPIRED"))
    if expired is not None:
        st.subheader("Alertas EXPIRED")
        st.dataframe(outbox_rows(expired), hide_index=True)
    log = guarded(lambda: client.health_log(limit=20))
    if log:
        last = log[0]
        causes = ", ".join(last.get("cause_codes") or []) or "sem causas"
        st.markdown(f"**Último health_state_log:** {last['state']} · {causes} · {fmt_ts(last.get('observed_at'))}")
        st.dataframe(health_log_rows(log), hide_index=True)
```

`dashboard/dashboard/views/watchlist.py`:

```python
from __future__ import annotations

import streamlit as st

from dashboard.client import ApiClient
from dashboard.viewmodels import InputError, rule_body, rule_rows, watchlist_ticker
from dashboard.views.common import guarded


def render(client: ApiClient) -> None:
    st.title("Watchlist e regras de alerta")
    st.caption("Alertas saem só pelo webhook n8n; pressão é estimativa derivada de OHLCV (D27).")
    with st.form("watchlist-add"):
        ticker = st.text_input("Ticker", key="watchlist-ticker")
        if st.form_submit_button("Adicionar à watchlist"):
            try:
                new_ticker = watchlist_ticker(ticker)  # M7: "/" never reaches /watchlist/{ticker}
            except InputError as exc:
                st.error(f"Entrada inválida: {exc.code}")
            else:
                added = guarded(lambda: client.add_ticker(new_ticker))
                if added is not None:
                    st.success(f"{added['ticker']}: {added['status']}")
    entries = guarded(client.watchlist)
    if not entries:
        st.info("Watchlist vazia.")
        return
    for entry in entries:
        symbol = str(entry["ticker"])
        with st.expander(f"{symbol} — {len(entry['rules'])} regra(s)"):
            st.dataframe(rule_rows(entry["rules"]), hide_index=True)
            for rule in entry["rules"]:
                rule_id = str(rule["id"])
                if st.button(f"Remover regra {rule['kind']} {rule_id[:8]}", key=f"rule-delete-{rule_id}"):
                    if guarded(lambda rid=rule_id: client.delete_rule(rid)) is not None:  # default args: ruff B023
                        st.success("Regra removida.")
            if st.button(f"Remover {symbol} da watchlist", key=f"watch-delete-{symbol}"):
                if guarded(lambda name=symbol: client.remove_ticker(name)) is not None:
                    st.success(f"{symbol} removido.")
            with st.form(f"rule-add-{symbol}"):
                kind = st.selectbox("Tipo", ["PRICE_CROSS", "PRESSURE"], key=f"rule-kind-{symbol}")
                level = st.text_input("Nível (PRICE_CROSS)", key=f"rule-level-{symbol}")
                direction = st.selectbox("Direção", ["ABOVE", "BELOW"], key=f"rule-direction-{symbol}")
                threshold = st.text_input("Limiar CMF (PRESSURE, entre 0 e 1)", key=f"rule-cmf-{symbol}")
                window = st.number_input("Janela (PRESSURE, candles)", min_value=5, max_value=390, value=30,
                                         key=f"rule-window-{symbol}")
                cooldown = st.number_input("Cooldown (min)", min_value=0, max_value=1440, value=30,
                                           key=f"rule-cooldown-{symbol}")
                if st.form_submit_button("Criar regra"):
                    try:
                        body = rule_body(kind=str(kind), level=level, direction=str(direction),
                                         cmf_threshold=threshold, window_bars=int(window),
                                         cooldown_minutes=int(cooldown))
                    except InputError as exc:
                        st.error(f"Entrada inválida: {exc.code}")
                    else:
                        if guarded(lambda name=symbol, payload=body: client.create_rule(name, payload)) is not None:
                            st.success("Regra criada.")
```

`dashboard/dashboard/views/market.py`:

```python
from __future__ import annotations

from datetime import date

import streamlit as st

from dashboard.charts import candlestick_figure
from dashboard.client import ApiClient
from dashboard.viewmodels import (
    ORDER_LIST_LIMIT,
    market_day_window,
    market_tickers,
    order_label,
    pressure_display,
    today_et,
)
from dashboard.views.common import guarded

NO_ORDER = "(nenhuma)"


def render(client: ApiClient) -> None:
    st.title("Mercado")
    watched = guarded(client.watchlist) or []
    orders = guarded(lambda: client.orders(limit=ORDER_LIST_LIMIT)) or []
    options = market_tickers(watched, orders)
    if not options:
        st.info("Adicione um ticker à watchlist ou crie uma ordem para ver o mercado.")
        return
    ticker = str(st.selectbox("Ticker", options, key="market-ticker"))
    day = st.date_input("Pregão (ET)", value=today_et(), key="market-day")
    if not isinstance(day, date):
        return
    start, end = market_day_window(day)
    related = {str(o["order_id"]): o for o in orders if o["ticker"] == ticker}
    overlay = st.selectbox("Sobrepor ordem", [NO_ORDER, *related], key="market-overlay",
                           format_func=lambda oid: oid if oid == NO_ORDER else order_label(related[oid]))
    chart = None if overlay in (None, NO_ORDER) else guarded(lambda: client.order_chart(str(overlay)))
    source = None if chart is None else str(chart["price_source"])  # M6: the overlaid order's own feed
    bars = guarded(lambda: client.market_bars(ticker, start, end, source=source))
    if bars is not None:
        if bars["bars"]:
            st.plotly_chart(candlestick_figure(
                bars["bars"], bars["vwap"], title=f"{ticker} · {day.isoformat()}",
                levels=None if chart is None else chart["levels"], markers=() if chart is None else chart["markers"],
                evaluation_start_ts=None if chart is None else chart["evaluation_start_ts"],
            ))
        else:
            st.info("Nenhum candle armazenado neste pregão.")
        st.caption(f"Candles armazenados lidos as-of {bars['data_as_of']}; VWAP: {bars['vwap_method']}.")
    window = st.number_input("Janela da estimativa (candles)", min_value=5, max_value=390, value=30,
                             key="market-window")
    payload = guarded(lambda: client.pressure(ticker, window_bars=int(window)))
    if payload is not None:
        display = pressure_display(payload)
        st.subheader(display.title)
        for line in display.lines:
            st.write(line)
        st.caption(f"Método: {display.method}")
        st.caption(display.disclaimer)
```

`dashboard/dashboard/views/portfolio.py`:

```python
from __future__ import annotations

import streamlit as st

from dashboard.client import ApiClient
from dashboard.viewmodels import real_portfolio_message, real_portfolio_rows, virtual_portfolio_view
from dashboard.views.common import guarded


def render(client: ApiClient) -> None:
    st.title("Portfólio")
    st.subheader("Portfólio virtual (por 1R normalizado)")
    virtual = guarded(client.virtual_portfolio)
    if virtual is not None:
        view = virtual_portfolio_view(virtual)
        st.caption(view.disclaimer)
        columns = st.columns(len(view.totals))
        for column, (label, value) in zip(columns, view.totals, strict=True):
            column.metric(label, value)
        if view.rows:
            st.dataframe(view.rows, hide_index=True)
        else:
            st.info("Nenhuma posição virtual aberta.")
    st.subheader("Portfólio real (somente leitura)")
    real = guarded(client.real_portfolio)
    if real is not None:
        message = real_portfolio_message(real)
        if message is not None:
            st.info(message)
        else:
            st.dataframe(real_portfolio_rows(real), hide_index=True)
    st.caption("Totais do portfólio real e do virtual nunca são somados.")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run --directory dashboard pytest && uv run --directory dashboard ruff check . && uv run --directory dashboard mypy && uv run pytest tests/test_import_boundaries.py`
Expected: PASS, sem warnings. Se o `AppTest` da versão instalada diferir no acesso a elementos (`at.radio(key=...)`, `at.metric`, `at.caption`, `button(key=...).click()`), adapte só a forma do acesso nos testes, preservando cada asserção de conteúdo, e registre no ledger. Se o Streamlit emitir aviso de depreciação para algum parâmetro usado aqui (por exemplo, `hide_index`), troque pelo parâmetro atual da versão instalada, sem silenciar o aviso.

- [ ] **Step 6: Commit**

```bash
git add dashboard/dashboard/app.py dashboard/dashboard/views dashboard/tests/test_app_smoke.py
git commit -m "feat(dashboard): Streamlit views for overview, signals, orders, comparison, health, watchlist, market and portfolio"
```

---

### Task 14: Imagens, Docker Compose de produção, `.env.example` e runbook (entradas 1, 5; D47, D48, D53, D56, D58, D59)

**Files:**
- Create: `Dockerfile`, `.dockerignore`, `docker-compose.yml`, `.env.example` (imagem do motor)
- Create: `dashboard/Dockerfile`, `dashboard/.dockerignore` (imagem do dashboard, do `dashboard/uv.lock`)
- Create: `docs/superpowers/runbooks/2026-09-14-compose-producao.md`
- Create: `tests/deploy/__init__.py`, `tests/deploy/test_compose.py`
- Modify: `pyproject.toml`, `uv.lock` (só `pyyaml` no grupo `dev`, com diff do export)

**Interfaces:**
- Consumes:
  - `virtual_orders.config.ENV_VARIABLES`, `load_settings`;
  - os comandos `uvicorn virtual_orders.bootstrap:app_from_environment --factory`, `python -m virtual_orders.worker run` e `alembic upgrade head`;
  - `streamlit run dashboard/app.py` (dentro do projeto `dashboard/`);
  - os códigos de saída do worker 2, 3 e 4;
  - o valor de `client.showErrorDetails` registrado na Task 1 (`none`, salvo `Ruling:` em contrário).
- Produces: os arquivos de deploy validados estaticamente; o comando de smoke manual documentado no runbook.

- [ ] **Step 1: Add PyYAML to the engine's dev group without moving any locked version**

```bash
SCRATCH="$(mktemp -d)"
uv export --frozen --no-hashes --all-groups > "$SCRATCH/lock-before.txt"
uv add --dev "pyyaml>=6.0"
uv export --frozen --no-hashes --all-groups > "$SCRATCH/lock-after.txt"
diff "$SCRATCH/lock-before.txt" "$SCRATCH/lock-after.txt" | grep '^[<>]' || echo "no change"
```

Expected: só linhas `>` com `pyyaml==…` (adição). **Se aparecer qualquer linha `<`** (um pacote existente mudou de versão, por exemplo pandas, numpy, fastapi, starlette, httpx, sqlalchemy, psycopg ou apscheduler), pare e reporte ao controlador sem commitar (D56). Registre a versão do PyYAML no ledger.

- [ ] **Step 2: Write the failing tests**

Crie `tests/deploy/__init__.py` vazio e `tests/deploy/test_compose.py`:

```python
"""Static checks of the production deployment files (D47, D48, D56, D58). No docker binary, no network."""

import re
from pathlib import Path

import yaml

from virtual_orders.config import ENV_VARIABLES, load_settings

ROOT = Path(__file__).resolve().parents[2]
SECRET_NAMES = ("API_KEY", "SECRET", "PASSWORD", "DATABASE_URL", "WEBHOOK")
ERROR_DETAILS = "none"  # D58: probed in Task 1; a boolean Streamlit would make this "false" through a recorded Ruling


def compose() -> dict:
    return yaml.safe_load((ROOT / "docker-compose.yml").read_text())


def services() -> dict:
    return compose()["services"]


def seconds(duration: str) -> int:
    match = re.fullmatch(r"(?:(\d+)m)?(?:(\d+)s)?", duration)
    assert match and duration, duration
    return int(match.group(1) or 0) * 60 + int(match.group(2) or 0)


def env_example() -> dict[str, str]:
    pairs = {}
    for line in (ROOT / ".env.example").read_text().splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            name, _, value = line.partition("=")
            pairs[name.strip()] = value.strip()
    return pairs


def dockerfile_lines(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text().splitlines() if line.strip() and not line.strip().startswith("#")]


def test_the_production_stack_has_exactly_the_spec_services_plus_migrations():
    assert set(services()) == {"postgres", "migrate", "api", "worker", "dashboard"}
    assert "pgdata" in compose()["volumes"]


def test_the_worker_is_one_restarting_instance_with_a_long_grace_period():
    worker = services()["worker"]
    assert worker["command"] == ["python", "-m", "virtual_orders.worker", "run"]
    assert worker["restart"] == "unless-stopped"
    assert seconds(worker["stop_grace_period"]) >= 120
    assert not {"deploy", "scale", "ports"} & set(worker)


def test_migrations_run_once_before_the_api_and_the_worker_with_only_the_database_url():
    stack = services()
    migrate = stack["migrate"]
    assert migrate["command"] == ["alembic", "upgrade", "head"] and migrate["restart"] == "no"
    assert migrate["depends_on"] == {"postgres": {"condition": "service_healthy"}}
    assert set(migrate["environment"]) == {"DATABASE_URL"} and "env_file" not in migrate  # M12: least privilege
    for name in ("api", "worker"):
        assert stack[name]["depends_on"] == {"migrate": {"condition": "service_completed_successfully"}}


def test_the_engine_image_is_built_once_and_the_dashboard_has_its_own_build():
    stack = services()
    assert stack["migrate"]["build"]["context"] == "."
    assert stack["dashboard"]["build"]["context"] == "./dashboard"  # D56: built from dashboard/uv.lock
    for name in ("api", "worker"):
        assert "build" not in stack[name], name  # M11: one build of the engine image
        assert stack[name]["image"] == stack["migrate"]["image"] and stack[name]["pull_policy"] == "never", name
    assert stack["dashboard"]["image"] != stack["migrate"]["image"]


def test_the_api_runs_the_app_factory_and_its_healthcheck_reads_the_key_from_the_environment():
    api = services()["api"]
    assert api["command"][:3] == ["uvicorn", "virtual_orders.bootstrap:app_from_environment", "--factory"]
    check = " ".join(api["healthcheck"]["test"])
    assert "/health" in check and "os.environ['API_KEY']" in check and "/livez" not in check


def test_the_dashboard_gets_only_the_api_url_and_key_and_hides_error_details():
    dashboard = services()["dashboard"]
    assert set(dashboard["environment"]) == {"DASHBOARD_API_URL", "API_KEY"} and "env_file" not in dashboard
    assert dashboard["environment"]["DASHBOARD_API_URL"] == "http://api:8000"
    assert dashboard["depends_on"] == {"api": {"condition": "service_started"}}  # D58
    command = dashboard["command"]
    assert command[:3] == ["streamlit", "run", "dashboard/app.py"]
    assert command[command.index("--client.showErrorDetails") + 1] == ERROR_DETAILS


def test_ports_bind_to_localhost_only_and_postgres_is_not_published():
    for name, service in services().items():
        for port in service.get("ports", []):
            assert str(port).startswith("127.0.0.1:"), name
    assert "ports" not in services()["postgres"]


def test_no_secret_value_is_written_in_the_compose_file():
    for name, service in services().items():
        for key, value in (service.get("environment") or {}).items():
            if any(part in key for part in SECRET_NAMES) and key != "DASHBOARD_API_URL":
                assert str(value).startswith("${"), (name, key)
    assert "change-me" not in (ROOT / "docker-compose.yml").read_text()


def test_both_images_are_built_from_their_own_lock_without_dev_dependencies_as_a_non_root_user():
    uv_images = set()
    for dockerfile in (ROOT / "Dockerfile", ROOT / "dashboard" / "Dockerfile"):
        lines = dockerfile_lines(dockerfile)
        assert lines[0].startswith("FROM python:3.12"), dockerfile
        assert "COPY pyproject.toml uv.lock ./" in lines, dockerfile
        assert any(line.startswith("RUN uv sync --frozen --no-dev") for line in lines), dockerfile
        users = [line for line in lines if line.startswith("USER ")]
        assert users and users[-1] != "USER root", dockerfile
        copies = [line for line in lines if line.startswith("COPY ")]
        assert not any(".env" in line or "tests" in line for line in copies), dockerfile
        uv_images |= {line.split()[1] for line in copies if line.startswith("COPY --from=ghcr.io/astral-sh/uv:")}
    assert len(uv_images) == 1  # the same pinned uv in both images (D58)
    assert not any("dashboard" in line for line in dockerfile_lines(ROOT / "Dockerfile"))  # D56
    assert {".env", ".git", ".venv", "tests", "dashboard"} <= set((ROOT / ".dockerignore").read_text().split())
    assert {".venv", "tests"} <= set((ROOT / "dashboard" / ".dockerignore").read_text().split())


def test_env_example_lists_every_setting_with_placeholders_only_and_loads():
    values = env_example()
    assert set(ENV_VARIABLES) - {"GIT_SHA"} <= set(values)
    assert "GIT_SHA" not in values  # the image build arg wins; an env_file value would override code_version
    assert {"POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"} <= set(values)
    for name in ("API_KEY", "ALPACA_API_KEY", "ALPACA_SECRET_KEY", "FMP_API_KEY", "POSTGRES_PASSWORD"):
        assert values[name] == "change-me", name
    assert "change-me" in values["DATABASE_URL"] and values["N8N_WEBHOOK_URL"] == ""
    load_settings(values)  # a copied .env.example is a valid configuration shape
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/deploy/test_compose.py`
Expected: FAIL — `FileNotFoundError` para `docker-compose.yml`, `Dockerfile`, `dashboard/Dockerfile` e `.env.example`.

- [ ] **Step 4: Create the deployment files**

Antes de fixar a imagem do `uv`, rode `uv --version` e use essa mesma versão nas duas linhas `COPY --from=ghcr.io/astral-sh/uv:<versão>`. Na escrita deste plano era `0.12.3`. Se o `docker` estiver disponível, confirme também `docker run --rm ghcr.io/astral-sh/uv:<versão> uv --version` (D58). Os dois `uv.lock` precisam ser legíveis por essa versão. Registre a versão usada no ledger.

`Dockerfile` (motor):

```dockerfile
# Engine image for migrate, api and worker (Plan 3C, D47, D56). Built from uv.lock without dev dependencies.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.3 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY alembic.ini ./
COPY migrations ./migrations
COPY src ./src
RUN uv sync --frozen --no-dev

ARG GIT_SHA=unknown
ENV GIT_SHA=${GIT_SHA} \
    PATH=/app/.venv/bin:${PATH} \
    PYTHONPATH=/app/src

RUN useradd --system --uid 10001 --home-dir /app vo && chown -R vo /app
USER vo
```

`.dockerignore` (raiz):

```
.git
.venv
.env
.hypothesis
.ruff_cache
.mypy_cache
.pytest_cache
.superpowers
**/__pycache__
dashboard
docs
tests
scratchpad
```

`dashboard/Dockerfile`:

```dockerfile
# Dashboard image (Plan 3C, D56): built from dashboard/uv.lock only; it never contains the engine or its lock.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.3 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY dashboard ./dashboard
RUN uv sync --frozen --no-dev --no-editable

ARG GIT_SHA=unknown
ENV GIT_SHA=${GIT_SHA} \
    PATH=/app/.venv/bin:${PATH}

RUN useradd --system --uid 10001 --home-dir /app vo && chown -R vo /app
USER vo
```

`dashboard/.dockerignore`:

```
.venv
.pytest_cache
.ruff_cache
.mypy_cache
**/__pycache__
tests
```

`docker-compose.yml`:

```yaml
# Production stack (spec 2.1, Plan 3C D47/D48/D56/D58). Copy .env.example to .env first; never commit .env.
# Build and start with GIT_SHA exported (runbook): the engine image is built once, by migrate.
name: virtual-order-engine

services:
  postgres:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_USER: ${POSTGRES_USER:?POSTGRES_USER is required}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}
      POSTGRES_DB: ${POSTGRES_DB:?POSTGRES_DB is required}
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U \"$${POSTGRES_USER}\" -d \"$${POSTGRES_DB}\""]
      interval: 5s
      timeout: 3s
      retries: 30

  migrate:
    build:
      context: .
      args:
        GIT_SHA: ${GIT_SHA:-unknown}
    image: virtual-order-engine:${GIT_SHA:-local}
    command: ["alembic", "upgrade", "head"]
    environment:
      DATABASE_URL: ${DATABASE_URL:?DATABASE_URL is required}  # M12: migrations need nothing else
    restart: "no"
    depends_on:
      postgres:
        condition: service_healthy

  api:
    image: virtual-order-engine:${GIT_SHA:-local}
    pull_policy: never
    command: ["uvicorn", "virtual_orders.bootstrap:app_from_environment", "--factory", "--host", "0.0.0.0", "--port", "8000"]
    env_file: .env
    restart: unless-stopped
    ports:
      - "127.0.0.1:8000:8000"
    depends_on:
      migrate:
        condition: service_completed_successfully
    healthcheck:
      # D48: GET /health with the key read inside the container; 503 (database or schema) fails the check.
      test: ["CMD", "python", "-c", "import os, urllib.request; urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8000/health', headers={'X-API-Key': os.environ['API_KEY']}), timeout=8)"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 20s

  worker:
    image: virtual-order-engine:${GIT_SHA:-local}
    pull_policy: never
    command: ["python", "-m", "virtual_orders.worker", "run"]
    env_file: .env
    restart: unless-stopped  # exit 3 (lock lost, D39) and 4 (database down at start, D49) are restarted
    stop_signal: SIGTERM
    stop_grace_period: 150s  # an end-of-day or recheck in progress finishes on SIGTERM (D20)
    depends_on:
      migrate:
        condition: service_completed_successfully

  dashboard:
    build:
      context: ./dashboard
      args:
        GIT_SHA: ${GIT_SHA:-unknown}
    image: virtual-order-engine-dashboard:${GIT_SHA:-local}
    command: ["streamlit", "run", "dashboard/app.py", "--server.address", "0.0.0.0", "--server.port", "8501", "--server.headless", "true", "--browser.gatherUsageStats", "false", "--client.showErrorDetails", "none"]
    environment:
      DASHBOARD_API_URL: http://api:8000
      API_KEY: ${API_KEY:?API_KEY is required}
    restart: unless-stopped
    ports:
      - "127.0.0.1:8501:8501"  # D59: no Streamlit auth; reach it through an SSH tunnel
    depends_on:
      api:
        condition: service_started  # D58: an API outage shows "API indisponível", never a dashboard that never starts

volumes:
  pgdata:
```

`.env.example`:

```
# Copy to .env and replace every placeholder. Never commit .env. GIT_SHA comes from the image build arg.
# The password inside DATABASE_URL must be the same value as POSTGRES_PASSWORD.
POSTGRES_USER=vo
POSTGRES_PASSWORD=change-me
POSTGRES_DB=vo
API_KEY=change-me
DATABASE_URL=postgresql+psycopg://vo:change-me@postgres:5432/vo
ALPACA_API_KEY=change-me
ALPACA_SECRET_KEY=change-me
FMP_API_KEY=change-me
EVAL_INTERVAL_MINUTES=2
DEFAULT_RISK_AMOUNT=100
ENTRY_SLIPPAGE_BPS=0
STOP_SLIPPAGE_BPS=5
COMMISSION_PER_EXECUTION=0
SEC_TAF_FEES_ENABLED=false
SEC_FEE_RATE=
TAF_FEE_PER_SHARE=
TAF_FEE_MAX=
ZONE_LOST_POLICY=RECLAIM
TARGET1_SCALE_OUT_PCT=50
DATA_GAP_MINUTES=30
CROSSCHECK_TOLERANCE_PCT=0.5
DIVIDEND_TOLERANCE=0.001
BOOTSTRAP_RESAMPLES=2000
BOOTSTRAP_SEED=42
N8N_WEBHOOK_URL=
PRICE_SOURCE=alpaca_iex
ALPACA_TRADING_URL=https://paper-api.alpaca.markets
```

`docs/superpowers/runbooks/2026-09-14-compose-producao.md`:

````markdown
# Runbook — Compose de produção (Plano 3C)

## Subir

```bash
cp .env.example .env            # troque todos os change-me; a senha de DATABASE_URL = POSTGRES_PASSWORD
docker compose config --quiet   # valida o arquivo e a interpolação, sem rede
export GIT_SHA="$(git rev-parse --short HEAD)"   # o mesmo valor para build e up: a imagem não é reconstruída
docker compose build            # imagem do motor (uma vez, pelo migrate) e imagem do dashboard (dashboard/uv.lock)
docker compose up -d
docker compose ps               # migrate "exited (0)"; api "healthy"; worker e dashboard "running"
```

## Smoke manual

```bash
set -a; . ./.env; set +a
curl -fsS -H "X-API-Key: $API_KEY" http://127.0.0.1:8000/health | python -m json.tool
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/health    # 401 sem chave
docker compose logs --tail=50 worker                                       # jobs registrados, lock obtido
```

Abra `http://127.0.0.1:8501`. Em VPS, só por túnel SSH: `ssh -L 8501:127.0.0.1:8501 <host>`.

## Decisões operacionais

- Healthcheck do `api` = `GET /health` com a chave do ambiente (D48): `DEGRADED` continua saudável; `503` (banco ou schema fora da head) não. O `dashboard` sobe assim que o `api` inicia (D58) e mostra "API indisponível" enquanto ele não responde.
- Um único `worker` (D20). Códigos de saída: `2` outro worker tem o lock; `3` lock perdido (D39); `4` banco inacessível na subida (D49). `restart: unless-stopped` reinicia 3 e 4; um `2` persistente indica dois stacks no mesmo banco.
- `docker compose stop worker` espera até 150 s: um fim de dia ou recheck em andamento termina.
- Reconstruir projeções: `docker compose run --rm worker python -m virtual_orders.worker rebuild-projections` (não disputa o lock do worker).

## Segurança — limitações conhecidas

- **Dashboard sem autenticação (D59):** o Streamlit não autentica; a porta fica só em `127.0.0.1` e o acesso remoto é por túnel SSH. Nunca publique a porta fora de `127.0.0.1`. Autenticação e proxy TLS ficam para o endurecimento de VPS.
- **Desvio registrado da spec 6 (D55):** `api` e `worker` conectam com o dono do banco, não com uma role sem `UPDATE`/`DELETE`. Os triggers de append-only rejeitam mutação do histórico para qualquer role (`test_app_role_cannot_update_history`); a role separada fica para o endurecimento de VPS.
- `migrate` recebe só `DATABASE_URL` (M12); o `dashboard` recebe só `DASHBOARD_API_URL` e `API_KEY`, e esconde detalhes de erro (`--client.showErrorDetails none`, D58).

## Jobs perdidos (D53 — recuperação manual)

- `OPENING_MISSING`: não rode a abertura depois do horário. Dividendos com ex-date do dia sem validação ficam para revisão; confira as ordens do ticker e registre `NEEDS_REVIEW` manual se preciso.
- `END_OF_DAY_MISSING`: não há comando para rodar o fim de dia de um pregão passado (D4/D12(b)). A validade é finalizada no fim de dia seguinte, e a qualidade do pregão entra em `QUALITY_NOT_EVALUATED` e é coberta pelo `DATA_QUALITY_RECHECK` (D22, D52). Se o feed continuar falhando por 5 pregões, a pendência termina em `PROVIDER_FAILURE_FINAL` com `NEEDS_REVIEW` `DATA_QUALITY_UNVERIFIED`.
- A causa some de `/health` quando o próximo job daquele tipo roda para o pregão seguinte e o lookback de 14 dias passa; ela nunca é apagada à mão.
````

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/deploy/test_compose.py tests/test_import_boundaries.py && uv run ruff check src tests migrations && uv run mypy`
Expected: PASS.

Opcional, fora da suíte e só se o `docker` estiver disponível: `cp .env.example .env && docker compose config --quiet; rm .env`. Deve sair com 0 (o `env_file: .env` referenciado precisa existir para o `config`). Nunca versione `.env`.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock Dockerfile .dockerignore docker-compose.yml .env.example dashboard/Dockerfile dashboard/.dockerignore docs/superpowers/runbooks/2026-09-14-compose-producao.md tests/deploy/__init__.py tests/deploy/test_compose.py
git commit -m "build(deploy): production Compose with separate engine and dashboard images, one-shot migrations and keyed healthcheck"
```

---

### Task 15: Fechamento — fronteiras fixadas, verificação do zero, smoke sem rede, revisão do branch inteiro e nota de encerramento

**Files:**
- Modify: `tests/test_import_boundaries.py` (`test_boundary_scan_covers_the_3c_modules`)
- Create: `docs/superpowers/notes/2026-09-14-plan3c-closeout.md`
- Modify: `docs/superpowers/plans/2026-09-14-virtual-order-engine-dashboard.md` (seção final "Encerramento do controlador")

**Interfaces:**
- Consumes: tudo o que as Tasks 1–14 produziram.
- Produces:
  - nota de encerramento no formato das notas dos Planos 3A e 3B;
  - tag local `plan/virtual-order-engine-dashboard-complete`, criada **somente** se nenhuma revisão estiver aberta.

- [ ] **Step 1: Pin the boundary coverage to the real files**

Acrescente ao final de `tests/test_import_boundaries.py`:

```python
def test_boundary_scan_covers_the_3c_modules() -> None:
    dashboard = {_dashboard_rel(p) for p in _dashboard_files()}
    package = "dashboard/dashboard"
    assert {
        f"{package}/app.py", f"{package}/client.py", f"{package}/viewmodels.py", f"{package}/charts.py",
        f"{package}/views/common.py", f"{package}/views/overview.py", f"{package}/views/signals.py",
        f"{package}/views/orders.py", f"{package}/views/comparison.py", f"{package}/views/health.py",
        f"{package}/views/watchlist.py", f"{package}/views/market.py", f"{package}/views/portfolio.py",
        "dashboard/tests/test_api_contract.py",
    } <= dashboard
    assert not (DASHBOARD_PROJECT / "dashboard" / "pages").exists()  # a pages/ folder would switch on multipage (D40)
    assert len(list((DASHBOARD_PROJECT / "tests" / "fixtures" / "api").glob("*.json"))) == 17  # D57
    assert (ROOT / "tests/integration/api/test_dashboard_contract.py").exists()
    neutral = {_rel(p) for p in _neutral_files()}
    assert {
        "virtual_orders/readmodels/market.py", "virtual_orders/readmodels/portfolio.py",
        "virtual_orders/readmodels/observability.py", "virtual_orders/portfolio/sources.py",
        "virtual_orders/analytics/vwap.py", "virtual_orders/analytics/portfolio.py",
    } <= neutral
    assert {"virtual_orders/analytics/vwap.py", "virtual_orders/analytics/portfolio.py"} <= set(PLATFORM_PURE_MODULES)
    api = {_rel(p) for p in _api_files()}
    assert {
        "virtual_orders/api/tickers.py", "virtual_orders/api/routes/market.py",
        "virtual_orders/api/routes/portfolio.py", "virtual_orders/api/routes/observability.py",
    } <= api
    assert "virtual_orders.marketdata.alpaca" in _imported_modules(SRC / COMPOSITION_ROOT)
```

Run: `uv run pytest tests/test_import_boundaries.py`
Expected: PASS.

Commit:

```bash
git add tests/test_import_boundaries.py
git commit -m "test(boundaries): pin dashboard, market, portfolio and observability coverage to the real modules"
```

- [ ] **Step 2: Final verification from zero**

Run, em sequência e na mesma sessão de shell (o `BASE` precisa sobreviver entre os comandos), e registre as saídas no ledger:

```bash
docker compose -f docker-compose.test.yml down -v
docker compose -f docker-compose.test.yml up -d --wait
uv sync --locked
uv sync --directory dashboard --locked
uv run pytest -p no:cacheprovider -o addopts="" -q
uv run ruff check src tests migrations
uv run mypy
uv run --directory dashboard pytest -p no:cacheprovider -o addopts="" -q
uv run --directory dashboard ruff check .
uv run --directory dashboard mypy
uv run pytest -p no:cacheprovider tests/test_import_boundaries.py tests/integration/api/test_health_api.py tests/integration/api/test_dashboard_contract.py tests/integration/worker/test_runner.py tests/integration/test_quality_recheck.py tests/evaluator tests/deploy
grep -cE '^name = "(streamlit|plotly)"' uv.lock || echo "engine lock has no UI libraries"
git diff --stat plan-3b-worker -- uv.lock
git diff --stat plan/virtual-order-engine-core-complete -- src/core
git status --porcelain -- src/core
if git merge-base --is-ancestor plan-3b-worker HEAD && ! git merge-base --is-ancestor plan-3b-worker main; then
  BASE=plan-3b-worker
else
  BASE=$(git merge-base main HEAD)
fi
echo "BASE=$BASE"
git log --format='%an <%ae>%n%B' "$BASE"..HEAD | grep -E '^(Co-Authored-By|Claude-Session):' || echo "no AI trailers"
git log --format='%an <%ae>' "$BASE"..HEAD | sort -u
```

Expected:
- Suíte inteira verde, sem skips e sem warnings, com os `N_BASE` testes herdados (Task 0) e os novos.
- Os dois `uv sync --locked` sem mudanças.
- ruff e mypy limpos nos dois projetos; o mypy do dashboard cobre `dashboard/dashboard/`.
- Suíte do dashboard verde, sem skips e sem warnings (sonda, cliente, view-models, figuras, contrato, smoke).
- A chamada única que mistura raiz, `api`, `worker`, `integration`, `evaluator` e `deploy` passa (regressão da entrada 20), inclusive o contrato em modo de comparação (D57).
- `engine lock has no UI libraries`; o diff do `uv.lock` da raiz contra `plan-3b-worker` só acrescenta `pyyaml` (Task 14).
- Os dois comandos de `src/core` vazios.
- `no AI trailers`.
- Um único autor: `Ricardo Carneiro <132141856+ricdomingues@users.noreply.github.com>`.
- As migrations sobem do zero (template recém-criado roda `alembic upgrade head` até `0004`), e `test_migrations_run_from_zero_and_back`, `test_downgrade_to_0002_and_back` e `test_downgrade_to_0003_and_back` passam.

- [ ] **Step 3: Smoke the composition, the dashboard and the deployment files without network**

O `env -i` garante que nenhuma variável do shell do host entre. Nada aqui abre conexão de rede: `build_services` não conecta, o import do dashboard não chama a API e `yaml.safe_load` só lê arquivos.

```bash
env -i PATH="$PATH" HOME="$HOME" \
  API_KEY=x DATABASE_URL=postgresql+psycopg://vo:vo@127.0.0.1:55432/postgres ALPACA_API_KEY=a ALPACA_SECRET_KEY=s \
  FMP_API_KEY=f \
  uv run python -c "from fastapi.routing import iter_route_contexts; from virtual_orders.bootstrap import app_from_environment; app = app_from_environment(); print(sorted((sorted(c.route.methods)[0], c.route.path) for c in iter_route_contexts(app.routes))); print(app.state.services.portfolio_source); app.state.services.close()"
env -i PATH="$PATH" HOME="$HOME" uv run --directory dashboard python -c "from dashboard.client import ApiClient, DashboardConfigError
try:
    ApiClient.from_environment({})
except DashboardConfigError as exc:
    print(exc.missing)"
env -i PATH="$PATH" HOME="$HOME" uv run python -c "import yaml; stack = yaml.safe_load(open('docker-compose.yml')); print(sorted(stack['services']), stack['services']['worker']['stop_grace_period'])"
env -i PATH="$PATH" HOME="$HOME" uv run python -m virtual_orders.worker run; echo "exit=$?"
env -i PATH="$PATH" HOME="$HOME" uv run --directory dashboard streamlit version
```

Expected:
1. As rotas:

```
[('DELETE', '/alerts/{rule_id}'), ('DELETE', '/watchlist/{ticker}'), ('GET', '/alert-outbox'), ('GET', '/health'), ('GET', '/health/log'), ('GET', '/market/bars'), ('GET', '/market/pressure'), ('GET', '/metrics'), ('GET', '/orders'), ('GET', '/orders/{order_id}'), ('GET', '/orders/{order_id}/chart'), ('GET', '/portfolio/real'), ('GET', '/portfolio/virtual'), ('GET', '/quality/overview'), ('GET', '/signals'), ('GET', '/watchlist'), ('POST', '/orders/{order_id}/cancel'), ('POST', '/replay'), ('POST', '/signals'), ('POST', '/signals/{signal_id}/orders'), ('POST', '/watchlist/{ticker}/alerts'), ('PUT', '/watchlist/{ticker}')]
```

   seguidas de `None` (portfólio real desligado, D46).
2. `['MISSING:DASHBOARD_API_URL', 'MISSING:API_KEY']`.
3. `['api', 'dashboard', 'migrate', 'postgres', 'worker'] 150s`.
4. Uma linha JSON `{"error": "CONFIG_INVALID", "errors": ["MISSING:API_KEY", …]}` sem nenhum valor e `exit=2`.
5. A versão do Streamlit registrada na Task 1.

- [ ] **Step 4: Whole-branch review**

- Despache a revisão final de branch inteiro (`requesting-code-review`), com o modelo mais capaz, sobre `$BASE..HEAD` (o `BASE` do Step 2).
- Entregue ao revisor: a spec, este plano, as Global Constraints e a nota de encerramento do 3B.
- Peça verificação explícita de:
  - (a) D40–D59 e a tabela de rastreio das entradas 1–20 do 3B (itens cobertos e descartados, com motivo);
  - (b) nenhum segredo nem texto de exceção em respostas HTTP (inclusive `/portfolio/real` e `/alert-outbox` sem `document`), erros renderizados no dashboard, `docker-compose.yml`, `Dockerfile` e `.env.example`;
  - (c) fronteiras: `dashboard` só por HTTP; `src` sem `dashboard`/`streamlit`/`plotly`; só `bootstrap.py` importa adapters; nenhum adapter Robinhood/MCP;
  - (d) nenhuma mudança de semântica de fill e `src/core` intocado;
  - (e) rotas novas: autenticação no nível do app, envelope, `Decimal`, SQL parametrizado, nenhuma chamada a provider no caminho da requisição, leituras as-of com `data_as_of` devolvido (D41);
  - (f) pressão sempre com `estimate`/`method`/`disclaimer` na API e na tela (D44); VWAP ancorado na sessão (D43);
  - (g) portfólio virtual por 1R (D45) e real nunca somado ao virtual (D46);
  - (h) Compose: worker único com `restart` e `stop_grace_period` ≥ 120 s, `migrate` one-shot, healthcheck com a chave (D48), portas só em `127.0.0.1`, dashboard só com `DASHBOARD_API_URL`/`API_KEY`;
  - (i) endurecimentos D49–D52: código 4, `ShutdownGuard`, keepalives, janela do `ORDER_EVENT_ALERTS_BEHIND`, `_JOB_RUNS` limitado com semântica idêntica, isolamento da watchlist, migration `0004`, `PROVIDER_FAILURE_FINAL`;
  - (j) ponta a ponta pela API (D54) sem rede nem espera real, e testes do dashboard sem servidor;
  - (k) D56: o `uv.lock` da raiz sem Streamlit/Plotly e sem mudança de versão de pacote existente; imagem do dashboard só do `dashboard/uv.lock`;
  - (l) D57: fixtures do contrato gravadas das rotas reais, sem segredo, e view-models afirmados contra elas;
  - (m) D52: `PROVIDER_FAILURE_FINAL` com `NEEDS_REVIEW` `DATA_QUALITY_UNVERIFIED` idempotente e `NO_OBSERVATIONS_FINAL` sem revisão; entrada 20 corrigida (nenhum import de `conftest`).
- Achados viram **uma** rodada de correção seguida de uma re-revisão com escopo restrito. Resíduos são adjudicados no ledger (`Ruling: … — … — …`). Rode o Step 2 de novo depois da última correção.

- [ ] **Step 5: Write the close-out note**

`docs/superpowers/notes/2026-09-14-plan3c-closeout.md`, com as seções:
- **Cabeçalho:**
  - Plano e Spec (v1.2 + D5–D59);
  - Intervalo: `BASE` do Step 2 até a ponta do branch, nomeando qual base valeu;
  - Testes: contagem do Step 2, com os `N_BASE` herdados;
  - Núcleo congelado: comando e resultado;
  - versões de Streamlit/Plotly/uv usadas.
- **Critérios de aceite** (tabela # | Critério | Teste | Resultado), cobrindo cada linha da tabela "Critérios de aceite do Plano 3C" abaixo com os nomes reais dos testes (confirmados por grep).
- **Rastreio das entradas 1–20 do 3B:** entrada → commit(s) → teste(s), ou "descartada — motivo (D55)".
- **Decisões D40–D59:** uma linha cada (mantida/alterada — motivo — custo se estiver errada).
- **Rulings durante a execução** (inclusive as adaptações à API instalada do `AppTest`/Plotly das Tasks 1, 12 e 13).
- **Resultado da revisão final** e **achados menores adiados**.
- **Entradas para o próximo plano**, no mínimo:
  1. Executar a Fase 0 da Robinhood e preencher `docs/superpowers/roadmap/2026-09-14-robinhood-phase0-feasibility-report.md`; só então planejar o adapter de `PortfolioSource`.
  2. Observação em paper: D33 (colapso de revisões), burst de cruzamentos ao ligar o webhook (D26), itens do T12.
  3. Endurecimento de VPS: role de banco sem `UPDATE`/`DELETE` para `api`/`worker` (**desvio registrado da spec 6**, D55), TLS/proxy e autenticação na frente do dashboard (hoje limitação conhecida: só `127.0.0.1` + túnel SSH, D59), backup do volume `pgdata`.
  4. Curva de R acumulado limitada a 1000 ordens fechadas (limite de `GET /orders`, legenda na tela, D59); paginar ou criar rota agregada quando passar disso.
  5. Revisar em paper quantas ordens saem das métricas por `DATA_QUALITY_UNVERIFIED` (D52) e se `NO_OBSERVATIONS_FINAL` deveria marcar revisão.

Commit:

```bash
git add docs/superpowers/notes/2026-09-14-plan3c-closeout.md docs/superpowers/plans/2026-09-14-virtual-order-engine-dashboard.md
git commit -m "docs: Plan 3C close-out"
```

- [ ] **Step 6: Tag (only with no open review)**

Só se o ledger não tiver nenhuma revisão aberta, nenhum achado bloqueante sem ruling e o Step 2 estiver verde **depois** da última correção:

```bash
git tag -a plan/virtual-order-engine-dashboard-complete -m "Plan 3C — dashboard, portfolio, production compose and end to end COMPLETE"
```

**Não crie a tag se houver qualquer revisão aberta.** Não faça push da tag nem do branch sem pedido explícito do responsável.

---

## Critérios de aceite do Plano 3C

| # | Critério | Onde |
|---|---|---|
| 1 | Dashboard só por HTTP com `X-API-Key`, em projeto `uv` próprio; nenhum import de `core`/`virtual_orders`; `src` e `tests` da raiz não importam o dashboard; lock da raiz sem Streamlit/Plotly | Tasks 1, 11, 15 (`test_import_boundaries.py`, `dashboard/tests/test_client.py`) |
| 2 | Visão geral: curva de R acumulado, cards com IC, risco de sequência, avisos e excluídas por revisão | Tasks 12, 13 (`test_viewmodels.py`, `test_app_smoke.py`) |
| 3 | Sinais do dia com "Comprar virtual" e exibição explícita de 422 (`SIGNAL_EXPIRED`, `SIGNAL_NO_LONGER_ACTIONABLE` com motivo) e 503 (`ACTIONABILITY_UNVERIFIABLE`), sem traceback nem texto de exceção | Tasks 12, 13 |
| 4 | Ordens: tabela filtrável; detalhe com candles, volume, VWAP, zona, stop, alvos, `evaluation_start_ts`, marcadores de eventos, qualidade (inclusive rechecks) e log | Tasks 7, 12, 13 (`test_market_api.py`, `test_charts.py`, `test_viewmodels.py`) |
| 5 | Comparação: AUTO × MANUAL, estratégia, versão/configuração do fill model, `entry_path`, original × replay | Tasks 7, 12, 13 |
| 6 | Saúde: último ciclo, cobertura, `DATA_GAP`, fila `NEEDS_REVIEW` por motivo, incidentes, frozen, causas `INFO` `UNDELIVERABLE_ALERTS`/`ORDER_EVENT_ALERTS_BEHIND`, alertas `EXPIRED`, último `health_state_log` | Tasks 9, 12, 13 (`test_observability_api.py`, `test_app_smoke.py`) |
| 7 | Watchlist e regras sobre as rotas do 3B | Task 13 |
| 8 | Painel de mercado: candles as-of com volume e VWAP de sessão, sobreposição de ordem, pressão sempre com `method` e `disclaimer`, sem chamada a provider | Tasks 6, 7, 12, 13 |
| 9 | Portfólio virtual por 1R (qty, entrada média, último fechamento, P&L e R não realizados, alocação) | Tasks 6, 8, 12, 13 (`test_portfolio.py`, `test_portfolio_api.py`) |
| 10 | Portfólio real: só contrato e slot desligado, `PHASE_0_PENDING`, modelo da Fase 0 com 17 itens "não verificado", nunca somado ao virtual | Tasks 8, 12, 13 |
| 11 | Compose de produção: `postgres`, `migrate` one-shot, `api` com healthcheck com chave, `worker` único com `restart` e `stop_grace_period` ≥ 120 s, `dashboard`; `.env.example` só com placeholders; validação estática sem docker | Task 14 (`test_compose.py`) |
| 12 | Ponta a ponta pela API: sinal → ordens → eventos → projeção → portfólio → métricas esperadas → `REPRODUCE` idêntico | Task 10 (`test_end_to_end_api.py`) |
| 13 | Endurecimentos do 3B: M4 (código 4), sincronização da flag `stopping`, M2 (keepalives), ramo do lock liberado, M1 (janela), T9 (leitura limitada), M3 (paridade), T13 (isolamento), T10 (`0004` e as duas lacunas de teste), `PROVIDER_FAILURE_FINAL` com `NEEDS_REVIEW`, entrada 20 (nenhum import de `conftest`) | Tasks 1–5 |
| 14 | `src/core` intocado; `N_BASE` testes herdados verdes; migrations verificadas do zero; ruff e mypy strict limpos com `dashboard` | Tasks 0, 5, 15 |
| 15 | Commits com identidade noreply e sem trailers de IA; nenhuma tag com revisão aberta; nenhum push | Task 15 |
| 16 | Contrato API × dashboard: respostas reais gravadas e comparadas por forma; view-models, cliente e figuras afirmados contra elas | Task 12 (`test_dashboard_contract.py`, `dashboard/tests/test_api_contract.py`) |
| 17 | Streamlit sem detalhes de erro (`showErrorDetails none` sondado), dashboard em `127.0.0.1` com túnel SSH, imagens separadas por lock | Tasks 1, 14 (`test_toolkit.py`, `test_compose.py`) |

## Encerramento do controlador

- **Modelo de execução por task:** implementadores em haiku para T4, T10 e T15 Step 1 (código pequeno e completo); sonnet para T1–T3, T5–T9, T11–T14. Revisores sonnet por padrão; opus para T1 (dois projetos + mudança de helpers), T7 (rotas de mercado as-of), T12 (maior task, view-models + fixtures de contrato), T14 (Dockerfiles/Compose) e para a revisão final de branch inteiro. Re-revisões em haiku/sonnet conforme o tamanho do achado.
- **Rodadas de correção:** T11 — 1 rodada (rejeição de NaN/Infinity, `commits c03d912..fdf07aa`); T12 — 1 rodada (fixtures determinísticas, Decimal exato, asserções mais estritas, `commit 8c0d0fd`); T14 — 1 rodada (código de saída 2 duplo, `--locked`, `USER vo`, `.dockerignore` do dashboard, chave do runbook sem argv, `commit a1a2984`); revisão final de branch inteiro — 1 rodada de correção (5 itens: Minor 1, Minor 2, Minor 7, Minor 8 e o T8 adiado; `commits db9a883..1e2d6de`).
- **Verificação final do zero** (commit `1e2d6de`): 1128 testes passando na raiz (908 herdados de `N_BASE`, medidos em `8da0f24`, o encerramento do Plano 3B), 47 no dashboard, ambos 0 skips e 0 warnings; a chamada mista de raiz/`api`/`worker`/`integration`/`evaluator`/`deploy` passa com 451 testes; `ruff` e `mypy` limpos nos dois projetos (110 arquivos na raiz, 15 no dashboard); os dois `uv sync --locked` sem mudanças; `git diff plan/virtual-order-engine-core-complete -- src/core` e `git status --porcelain -- src/core` vazios; `BASE=plan-3b-worker` (`0fd2707`); 24 commits de implementação/correção antes deste encerramento (25 com este commit); nenhum trailer `Co-Authored-By`/`Claude-Session`; autor único `Ricardo Carneiro <132141856+ricdomingues@users.noreply.github.com>`.
- **Saída do smoke (sem rede):** 22 rotas na lista esperada, seguidas de `portfolio_source: None` (portfólio real desligado, D46); configuração do dashboard faltando `['MISSING:DASHBOARD_API_URL', 'MISSING:API_KEY']`; `docker-compose.yml` com os serviços `['api', 'dashboard', 'migrate', 'postgres', 'worker']` e `stop_grace_period` de `150s`; `python -m virtual_orders.worker run` sem configuração sai com `exit=2` e uma linha `CONFIG_INVALID`; Streamlit na versão sondada na Task 1 (1.63.0). As duas imagens (motor e dashboard) foram construídas com `docker compose build` durante a Task 14; nenhuma foi executada com `up` até agora (entrada 10 da nota de encerramento).
- **Estado da tag:** `plan/virtual-order-engine-dashboard-complete` — a ser criada localmente após verificação, sem push, e só depois de confirmado que nenhuma revisão segue aberta (a re-revisão da rodada de correção final marcou os 5 achados como ADDRESSED, sem achado bloqueante pendente).
- **Nota de encerramento:** `docs/superpowers/notes/2026-09-14-plan3c-closeout.md`.
