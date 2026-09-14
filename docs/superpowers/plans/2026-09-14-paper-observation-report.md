# Virtual Order Engine — Plano 4: Relatório Diário de Observação em Paper (somente leitura)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Entregar um relatório diário, somente leitura e calculado só a partir de dados já gravados, para 7–14 dias de observação em paper/shadow do motor de ordens virtuais: falhas de provider, candles ausentes, 503 de actionability, `DATA_QUALITY_RECHECK`, falhas de entrega de alertas, reinícios do worker, transições de saúde, trades virtuais, MFE/MAE, latência sinal → fill e pressão estimada × resultado — pela API, por CLI, numa tela do dashboard e num alerta n8n opcional de fim de dia.

**Architecture:** O núcleo (`src/core/`) continua congelado e nenhuma regra de fill muda. Na plataforma entram: um módulo puro de estatística (`analytics/observation.py`), um read model neutro dividido em janela (`readmodels/observation_window.py`), seções operacionais (`readmodels/observation_operations.py`), seções de trades (`readmodels/observation_trades.py`) e montagem (`readmodels/observation.py`); duas rotas autenticadas (`api/routes/observation.py`); uma CLI só leitura (`virtual_orders/observation/`); um alerta `OBSERVATION_DAILY` enfileirado pelo job de fim de dia. A única escrita nova é a tabela append-only `worker_sessions` (migration `0005`), gravada pelo runner do worker em melhor esforço, sem mudar seus códigos de saída. O dashboard ganha a página "Observação" (cliente, view-model, figuras, view, smoke `AppTest`) sobre fixtures gravadas das rotas reais.

**Tech Stack:** Python 3.12, `uv`, FastAPI, SQLAlchemy 2 Core + psycopg 3, PostgreSQL 16, Alembic, APScheduler 3.x, httpx, Streamlit/Plotly só no projeto `dashboard/`, pytest, Hypothesis (herdado), ruff 0.8.6, mypy 1.13 strict.

**Spec:** `docs/superpowers/specs/2026-09-12-virtual-order-engine-design.md` (SPEC v1.2 FROZEN), mais D5–D59 (Planos 2, 3A, 3B, 3C) e D60–D73 abaixo.
- **Entradas:** `docs/superpowers/notes/2026-09-14-plan3c-closeout.md`, entrada 2 ("Observação em paper"): este plano entrega o **instrumento** de observação; as decisões que dependem dos dados (D33, burst do webhook, T12, entradas 5–7) continuam para depois dos 7–14 dias.
- **Convenções:** `docs/superpowers/plans/2026-09-14-virtual-order-engine-dashboard.md` (Plano 3C).
- **CI:** `.github/workflows/ci.yml` — job `engine` (ruff, mypy, núcleo congelado, migrations do zero com `upgrade`/`downgrade base`/`upgrade`, `uv run pytest -p no:cacheprovider -o addopts="" -q` com Postgres 16) e job `dashboard` (ruff, mypy, pytest). O plano mantém os dois verdes; nenhuma mudança no workflow.

## Objetivo do responsável (vinculante)

Relatório diário para 7–14 dias de observação medindo: falhas de provider; candles ausentes; 503 de actionability; `DATA_QUALITY_RECHECK`; falhas de entrega de alertas; reinícios do worker; transições de saúde; trades virtuais; MFE/MAE; latência sinal → fill; pressure score × resultado subsequente. **Sem** estratégias novas, **sem** funcionalidade de trading, **sem** mudança de semântica de fill, **sem** chamadas novas a provider, `src/core` congelado.

## Fora deste plano

- Estratégias, execução, Robinhood/Fase 0 (entrada 1 do 3C), endurecimento de VPS (entrada 3), paginação da curva de R (entrada 4), Minors 3–6 do 3C (entradas 6–9), ordenação do `/portfolio/virtual` (entrada 11), `docker compose up` real (entrada 10).
- Decisões que só os dados de paper resolvem (D33, D26 burst, T12, D52 `NO_OBSERVATIONS_FINAL`): o relatório mede; a decisão vem no próximo plano.
- Nenhuma mudança em `src/core/`, `fill_model v1`, `EventType`, D4, D12, D22, D52 nem nos contratos das 22 rotas existentes.
- A pressão recalculada **nunca** é gravada, nunca alimenta avaliação, alerta de regra ou métrica da spec 5.4.

Base: branch `plan-4-observation`, criado em `main` `09f2eea` (contém os Planos 1–3C). Intervalo de commits do plano: `09f2eea..HEAD`.

## Global Constraints

### Núcleo, plataforma e dados

- Python **3.12**. Nenhuma dependência nova em nenhum dos dois projetos: `uv.lock` e `dashboard/uv.lock` não mudam (verificado na Task 13 com `git diff --stat 09f2eea -- uv.lock dashboard/uv.lock` vazio).
- **`src/core/` está congelado.** Nenhum arquivo criado, editado ou removido. Se um teste exigir mudar o núcleo, **pare e reporte ao responsável**. Encerramento exige `git diff plan/virtual-order-engine-core-complete -- src/core` vazio.
- Continuam valendo as Global Constraints dos Planos 2–3C: `Decimal` para preço, volume, dinheiro e R; `datetime` timezone-aware em UTC; leitura as-of; nenhum teste acessa a rede nem dorme; Postgres real em `127.0.0.1:55432` (`docker compose -f docker-compose.test.yml up -d --wait`).
- **Relatório só leitura (D60):** nenhuma rota, CLI ou job de relatório grava run, evento, snapshot, bar ou projeção; nenhum chama provider (`api.bars.calls` inalterado nos testes). A única escrita nova do plano é `worker_sessions` (D62) e a linha `OBSERVATION_DAILY` do outbox (D70).
- **As-of (D61):** `as_of = acquire_data_as_of(engine)` no instante do pedido (relógio do banco); toda linha lida é filtrada por sua coluna de relógio do banco `<= as_of`.
- **Um snapshot por pedido (D61):** depois de `acquire_data_as_of`, o relatório inteiro (e o resumo inteiro) é lido numa **única** transação `REPEATABLE READ` somente leitura (`observation_snapshot(engine)`), na rota, na CLI e no alerta; `build_observation_report` recusa qualquer outra conexão. O alerta grava o outbox numa transação de escrita **separada**, depois da leitura.
- **Ordens replay** ficam fora de toda métrica de trade, latência e pressão; aparecem só na contagem rotulada `replay_closed` (D64).
- **SQL parametrizado:** `text()` com binds (listas com `CAST(:x AS uuid[])`/`CAST(:x AS text[])`) ou SQLAlchemy Core. Nenhum valor de requisição interpolado.
- Migrations verificadas do zero: `test_migrations_run_from_zero_and_back`, `test_downgrade_to_0004_and_back` (novo), `test_downgrade_to_0003_and_back`, `test_downgrade_to_0002_and_back`, e o passo "Migrations from an empty database" do CI.

### Fronteiras de import (verificadas por `tests/test_import_boundaries.py`)

- Só `virtual_orders/bootstrap.py` importa adapters. A CLI `virtual_orders/observation/` **não** usa o bootstrap nem `config` (lê só `DATABASE_URL`, D73) e nunca importa adapters, provider libs, `virtual_orders.api`, `virtual_orders.worker`, `fastapi`, `starlette`, `uvicorn`.
- `readmodels/observation*.py` são neutros (mesmas regras de `readmodels/`); `analytics/observation.py` é puro (sem I/O nem infraestrutura).
- **Avaliação nunca lê o relatório:** `virtual_orders/evaluator/**`, `virtual_orders/ledger/**`, `virtual_orders/marketdata/**` e `src/core/**` nunca importam `virtual_orders.readmodels.observation*` nem `virtual_orders.analytics.observation`.
- Dashboard só por HTTP (D40); `src`/`tests` da raiz não importam `dashboard`/`streamlit`/`plotly`; nenhum módulo importa um `conftest`.

### Segurança, HTTP e UI

- Segredos (`API_KEY`, `ALPACA_*`, `FMP_API_KEY`, `DATABASE_URL`, `N8N_WEBHOOK_URL`) nunca aparecem em respostas, saída da CLI, logs novos, documentos de alerta ou erros renderizados.
- **Nenhum texto de exceção nem mensagem de provider** em respostas, CLI, alertas ou dashboard: mensagens gravadas viram códigos fixos (`failure_code`), erros de run viram só o nome do tipo (`^[A-Z][A-Za-z0-9_]*(?=\()`, senão `UNKNOWN`); a escrita falha de `worker_sessions` e a falha do alerta `OBSERVATION_DAILY` são logadas só com `type(exc).__name__` (sem `exc_info`, sem mensagem).
- Rotas novas usam o app, a autenticação `X-API-Key` no nível do app, o envelope de erro e `json_response` do 3A.
- **Pressão sempre rotulada:** toda resposta, saída da CLI e tela com pressão trazem `estimate: true`, `method`, `disclaimer` e `association_note`.
- Host do worker nunca gravado em claro: só `host_fingerprint` (16 hex de SHA-256) ou `NULL`.

### Qualidade e commits

- Imports acrescentados a arquivos existentes são fundidos e ordenados com os já presentes (`uv run ruff check --fix --select I <arquivo>`, ou `uv run --directory dashboard ruff check --fix --select I <arquivo>`).
- Motor: `uv run ruff check src tests migrations` e `uv run mypy` limpos. Dashboard: `uv run --directory dashboard ruff check .` e `uv run --directory dashboard mypy` limpos. Sem `per-file-ignores` novos.
- Testes com Postgres ficam em `tests/integration/` (marcados automaticamente); testes puros fora dele.
- Identificadores de código e textos de API/alerta em inglês; prosa do plano, Markdown da CLI e textos da UI em português; mensagens de commit em inglês.
- **Commits:** identidade repo-local `Ricardo Carneiro <132141856+ricdomingues@users.noreply.github.com>`. **Nenhum** trailer `Co-Authored-By:` ou `Claude-Session:` (também para subagentes). Nunca alterar configuração global do git. **Nenhum push**: o responsável abre e mescla o PR pelo CI obrigatório.

Comandos de verificação usados nas tasks:

```bash
# motor (raiz)
uv run pytest -p no:cacheprovider -o addopts="" -q <paths> && uv run ruff check src tests migrations && uv run mypy
# dashboard
uv run --directory dashboard pytest -p no:cacheprovider -o addopts="" -q && uv run --directory dashboard ruff check . && uv run --directory dashboard mypy
```

## Decisões deste plano (a spec v1.2 não é alterada)

- **D60 — Relatório neutro, só leitura, derivado de dados gravados (ruling 1).**
  - `ObservationReport` para um pregão NYSE e `ObservationSummary` para um intervalo, montados por `readmodels/observation.py` a partir de `evaluation_runs`/`evaluation_run_status`, `order_events`, `order_state`, `orders`, `signals`, `bars_1m`/`bar_batches`, `data_quality_rechecks`, `alert_outbox`/`alert_delivery_attempts`, `health_state_log` e `worker_sessions`.
  - Nada é recalculado por provider; nenhuma escrita. Cada seção leva a definição em `DEFINITIONS` (texto fixo em inglês) e na tabela "Definição das métricas" abaixo.
  - Custo se estiver errado: o relatório poderia criar efeito colateral (run, evento) só por ser lido — impedido por teste de contagem antes/depois nas Tasks 7 e 8.
- **D61 — Janela e as-of.**
  - Janela do pregão `D`: `[00:00 ET do dia seguinte ao pregão NYSE anterior, 00:00 ET de D+1)`. Dias sem pregão (fim de semana, feriado) pertencem ao relatório do pregão seguinte: nenhum fato fica sem dono.
  - **Pertinência pelo instante de mercado** para fatos de avaliação: runs `LIVE`/`WATCHLIST`/`QUALITY_RECHECK` por `detail.market_now`; `ACTIONABILITY` por `detail.created_at` (clique) do primeiro status; `OPENING`/`END_OF_DAY` por `detail.session_day = D`; eventos por `bar_ts` (fills) ou pela data do `event_key` (`DATA_QUALITY:D`) ou por `gap_start_ts` dentro do pregão (`DATA_GAP`); trades por `order_state.closed_at`; ordens criadas por `orders.created_at`. Sem instante de mercado, `evaluation_runs.started_at`.
  - **Pertinência pelo relógio do banco** para fatos de infraestrutura sem instante de mercado: `health_state_log.observed_at`, `alert_outbox.created_at`, `alert_delivery_attempts.attempted_at`, `worker_sessions.recorded_at`. Em produção os dois relógios coincidem (D21); nos testes o relógio de mercado é falso, então esses fatos são inseridos com instantes explícitos.
  - Um run ainda `RUNNING` sem instante de mercado no `detail` (por exemplo um `LIVE` em andamento, que só grava `market_now` no status final) cai para `evaluation_runs.started_at`, o relógio do banco. Em produção coincide com o instante de mercado (D21); nos testes, runs em andamento recebem `market_now` explícito no início quando a janela importa.
  - **As-of:** `as_of = acquire_data_as_of(engine)` (relógio do banco, sob o lock de ingestão). Status de run lido é o último com `recorded_at <= as_of`; eventos, rechecks, tentativas, log de saúde e sessões com a própria coluna `<= as_of`; candles com `ingested_at <= as_of`. `complete = as_of >= fim da janela`; `effective_end = min(fim, as_of)`.
  - **Um snapshot REPEATABLE READ por pedido.** O filtro `<= as_of` não basta: `recorded_at` é carimbado no insert, não no commit, então um ciclo que commita entre duas consultas deixaria seções em desacordo (`trades.filled` ≠ `latency.bar.count`). Por isso o relatório inteiro, e o resumo inteiro (até 45 relatórios), é lido numa única transação `REPEATABLE READ` somente leitura aberta por `observation_snapshot(engine)` **depois** de `acquire_data_as_of`; tudo o que foi commitado até `as_of` é visível e nada commitado depois muda a leitura. `build_observation_report` confere `transaction_isolation = 'repeatable read'` e `transaction_read_only = 'on'` e recusa outra conexão (`ValueError`). O alerta `OBSERVATION_DAILY` lê nesse snapshot e só depois grava o outbox em `engine.begin()` separado.
  - Exceção registrada: `order_state` é projeção atual. Um trade só entra se o evento que o fechou (`bar_ts = closed_at`, tipo `TARGET1_HIT`/`TARGET2_HIT`/`STOPPED`/`TIME_EXIT`) tem `recorded_at <= as_of`; `r_multiple`, `mfe_r`, `mae_r` de uma ordem fechada não mudam depois, mas `needs_review` pode virar `true` retroativamente (D34) — o relatório de um dia passado pode perder um trade para a coluna "excluídas por revisão".
  - Custo se estiver errado: um fato de fim de semana some ou conta duas vezes; testes de janela (Task 4) fixam segunda-feira depois do feriado de Ação de Graças.
- **D62 — `worker_sessions` (ruling 2).**
  - Migration `0005`: tabela append-only `worker_sessions(id bigserial, session_id uuid, event STARTED|STOPPED, recorded_at DEFAULT clock_timestamp(), code_version, host_fingerprint, exit_code, reason)`, `UNIQUE(session_id, event)`, `CHECK` que separa campos de início e de parada, `host_fingerprint ~ '^[0-9a-f]{16}$'`, triggers `reject_history_mutation` e `GRANT SELECT, INSERT` a `vo_app` iguais a 0003. `EXPECTED_SCHEMA_REVISION = "0005"`.
  - O runner grava `STARTED` logo depois de obter o lock (processos que saem com `2`/`4` não são sessão) e `STOPPED` no `finally`, **antes** de soltar o lock: `(0, SIGNAL)`, `(0, SCHEDULER_STOPPED)`, `(3, LOCK_LOST)` ou `(1, UNCAUGHT_EXCEPTION)` quando qualquer exceção sobe depois do lock (scheduler, `WorkerJobs`, `build_schedule`, `scheduler_factory`, `KeyboardInterrupt`, `SystemExit`; a exceção continua propagando; `1` é o status do interpretador). Os códigos de retorno de `run_worker` não mudam.
  - Toda escrita é melhor esforço: exceção vira `logger.warning("worker session … not recorded: <Tipo>")` e o worker segue. Sem `STARTED` gravado, não se grava `STOPPED`.
  - `host_fingerprint = sha256(hostname)[:16]`: distingue instâncias de contêiner/host sem guardar o nome. No contêiner o hostname é o id do contêiner, que muda a cada recriação (não só quando o host muda).
  - Métricas: `starts`, `restarts` (inícios na janela precedidos por qualquer sessão anterior), `unclean_ends`, `stops` por motivo, `exit_codes`, `open_session_at_end`.
  - **Atribuição exata.** Uma sessão sem linha `STOPPED` gravada as-of está **aberta**: o processo está rodando ou terminou sem linha de parada, e as duas coisas são indistinguíveis até o próximo início. `open_session_at_end = true` quando a última sessão iniciada até `effective_end` não tem `STOPPED` antes de `effective_end`. Um fim sem parada (SIGKILL, OOM, queda do host) só é detectável quando o **próximo** `STARTED` aparece, e é atribuído à janela em que esse próximo início ocorre, mesmo que seja dias depois. Uma sessão ainda aberta sem início posterior aparece como aberta, nunca como reinício nem como fim sem parada. Um `STOPPED` com motivo `LOCK_LOST` é sempre fim limpo, mesmo gravado depois do `STARTED` do sucessor (o sucessor pode obter o lock antes do nosso `finally`).
  - Custo se estiver errado: uma escrita que travasse o worker derrubaria a avaliação — impedido pelo teste de falha de escrita (Task 2).
- **D63 — Validação dos pedidos.**
  - `GET /observation/report?day=YYYY-MM-DD`: `day` precisa ser pregão NYSE e não posterior à data ET de `as_of`. Erros: `422 OBSERVATION_REQUEST_INVALID` com `detail.errors` em `DAY_IN_FUTURE:<dia>`, `NOT_A_SESSION:<dia>` (nessa ordem). Formato inválido: `422 REQUEST_INVALID` do FastAPI.
  - `GET /observation/summary?from&to`: `EMPTY_RANGE` (`to < from`), `RANGE_TOO_LARGE` (mais de 45 dias corridos, `MAX_SUMMARY_DAYS`), `DAY_IN_FUTURE:<to>`, `NO_SESSIONS` (nenhum pregão no intervalo). Extremos podem ser dias sem pregão. Hoje é aceito, com `complete: false`.
  - CLI usa as mesmas funções (`session_window`, `summary_sessions`) e o mesmo código de erro, com saída `3`.
- **D64 — Trades virtuais e MFE/MAE.**
  - População: ordens não replay. `created` por origem (`orders.created_at` na janela, com `ORDER_CREATED` gravado as-of); `filled` = eventos `FILLED` com `bar_ts` na janela; `closed` = `status CLOSED`, `closed_at` na janela, evento de fechamento as-of (D61).
  - Estatísticas (`TradeStats`: trades, wins `r > 0`, losses `r < 0`, win rate, soma e média de R, média e mediana de `mfe_r`/`mae_r`) seguem a política de revisão da spec 5.4: **excluem** `needs_review` e informam `excluded_needs_review`. As linhas (`rows`) listam todos os fechados com a flag.
  - MFE/MAE vêm da projeção (`excursion_r`, spec D3: MFE sem o candle de stop). Mediana de Decimal = média dos dois centrais quando `n` é par; R em 4 casas `ROUND_HALF_EVEN`.
  - Replay: só `replay_closed` (contagem rotulada), nunca misturado.
- **D65 — Latência sinal → fill (ruling 4).**
  - Por evento `FILLED` de ordem não replay com `bar_ts` na janela e `recorded_at <= as_of`: `bar_latency_seconds = floor(bar_ts − signals.created_at)` e `recorded_latency_seconds = floor(recorded_at − signals.created_at)`, em segundos inteiros. `bar_ts` é o início do candle do fill; como `evaluation_start_ts` é o primeiro minuto em/depois de `created_at`, `bar_latency_seconds >= 0`.
  - Estatísticas: `count`, mediana e p90 por **nearest-rank** (`ceil(p/100·n)`-ésimo menor, sem interpolação), `max`; também mediana/p90 da latência de candle por origem.
  - Ordens sem fill: ordens não replay criadas na janela sem `FILLED` as-of, contadas por desfecho as-of: `EXPIRED`, `INVALIDATED`, `CANCELED` (primeiro desses eventos gravado) ou `NOT_FILLED_YET`. Ordens criadas antes e preenchidas na janela contam só na latência.
  - `unfilled` é fotografia **as-of do relatório**: uma ordem criada na janela e preenchida numa janela posterior sai do bucket quando o relatório é refeito depois do fill, e ordens `FROZEN` sem desfecho caem em `NOT_FILLED_YET`. Por isso `created ≠ filled_na_janela + unfilled` em geral.
  - Para `MANUAL_USER` a latência também parte do `created_at` do sinal (ruling), não do clique.
- **D66 — Pressão estimada × resultado (ruling 3).**
  - Para cada trade fechado não replay da janela: `pressure_before` lê os candles gravados **as-of do relatório** no `price_source` da ordem e usa os **últimos 30** (`OBSERVATION_PRESSURE_WINDOW_BARS`, o padrão de `/market/pressure`) com `ts < floor_minute(signals.created_at)`, **atravessando pregões** (sem corte na abertura e sem limite de dias). A regra "data ET do último candle" da D44 não é usada aqui: com ela, todo sinal entre 09:31 e 09:59 ET teria menos de 30 candles no pregão e ficaria `INSUFFICIENT_BARS` por construção, enviesando os buckets pela hora do sinal. Cada trade grava `pressure_window_start`/`pressure_window_end` (primeiro e último candle usados) e `pressure_spans_sessions` (datas ET diferentes; `null` sem estimativa). Nenhum candle: `NO_BARS`; de 1 a 29 candles em todo o histórico as-of: `INSUFFICIENT_BARS`.
  - `estimate_pressure` (D27) sem mudança. Alinhamento com a direção do trade: `strong_pressure(estimate, 0.05)` BUY/SELL → `ALIGNED` (BUY em LONG, SELL em SHORT) ou `OPPOSED`; sem lado → `NEUTRAL`; sem estimativa → `UNAVAILABLE`. Força por `|CMF|`: `WEAK < 0.05 <= MODERATE < 0.15 <= STRONG`.
  - Buckets `(alignment, strength)` com trades, wins, soma e média de R sobre os trades sem revisão; `unavailable_reasons` na mesma população. A média de R de um bucket só é calculada com **pelo menos 5 trades** (`MIN_TRADES_FOR_BUCKET_MEAN`); abaixo disso `mean_r = null`. Toda superfície mostra `n` ao lado da média ("R médio (n)").
  - Limiares CMF `0.05`/`0.15` e janela de 30 candles são **convenções rotuladas**, não calibradas; rever só com amostra suficiente.
  - Toda saída: `estimate: true`, `method = OHLCV_PRESSURE_ESTIMATE_V1`, `disclaimer` (D27) e `association_note` ("Descriptive counts over a small paper sample: not causal evidence, not a trading signal, and never used by any evaluation.").
  - Nunca gravado; nenhum módulo de avaliação importa o relatório (teste de fronteira, Task 6).
  - Custo se estiver errado: candles corrigidos pelo vendor depois do sinal mudam a estimativa; aceito e rotulado ("as-of do relatório, não do sinal").
- **D67 — Falhas de provider.**
  - Por tipo de run atribuído à janela, com o último status as-of: `LIVE`/`WATCHLIST` `detail.ingest_failures`; `OPENING` `detail.source_failures`; `END_OF_DAY` `detail.unavailable`; `QUALITY_RECHECK` `detail.ingest_failures` + `detail.unavailable`; `ACTIONABILITY` `detail.ingest_error` (chave `price_source:ticker`).
  - Por tipo: `runs`, `failed_runs` (status `FAILED` com `error`) e `failed_run_errors` (só o nome da classe de exceção: `^[A-Z][A-Za-z0-9_]*(?=\()`, ou seja, começa com maiúscula, sem pontos e seguido imediatamente de `(`; qualquer outra coisa, inclusive `db.internal(host)` ou `password(...)`, vira `UNKNOWN` — nunca o texto), `runs_with_failures`, `failures` (pares run×feed), `codes` (`UNKNOWN_DATA_SOURCE` se a mensagem começa assim; `UNEXPECTED_ERROR` se começa com `ERROR:` (D51); senão `SOURCE_ERROR`), `feeds` (chaves distintas, até 50) e `feeds_total`. Total geral e `consecutive_live_max`: maior sequência de runs `LIVE` `COMPLETED` consecutivos **dentro da janela** com o mesmo feed falhando; reinicia na borda da janela e ignora runs `FAILED`. É descritiva e **não** é a regra do `/health` (que olha os ciclos mais recentes).
  - Mensagens gravadas nunca saem do banco.
- **D68 — Saúde, actionability, qualidade, recheck e alertas.**
  - **Transições de saúde:** linhas de `health_state_log` com `observed_at` na janela (até `effective_end`); `state_at_start` = última linha antes da janela (`UNKNOWN` sem linha); `entered` = linhas cujo estado difere do da linha anterior (a primeira compara com `state_at_start`); **`transitions` = mudanças de estado = `sum(entered.values())`**; `log_rows` = linhas do log na janela (o `health_watch` grava uma linha a cada mudança de assinatura, inclusive só de causas, então `DEGRADED → DEGRADED` conta em `log_rows` e não em `transitions`); `cause_codes` por linha; `seconds_by_state` = tempo em cada estado dentro de `[início, effective_end)`; `state_at_end`.
  - **503 de actionability:** runs `ACTIONABILITY` atribuídos pelo clique. `requests`, `results` (`ACTIONABLE`, `SIGNAL_EXPIRED`, `SIGNAL_NO_LONGER_ACTIONABLE`, `ACTIONABILITY_UNVERIFIABLE`), `unverifiable` (as respostas `503`), causas (`PROVIDER_FAILURE` com `ingest_error`, `POLICY_CONTRACT_VIOLATION`, `MISSING_MINUTES`), `unverifiable_rate = unverifiable / requests` (4 casas) e `unfinished` (sem status final as-of).
  - **Candles ausentes:** eventos `DATA_QUALITY:D` de ordens não replay (minutos-ordem, como a M8): `orders_measured`, `expected_bars`, `missing_bars`, `coverage_pct` (2 casas), `orders_with_missing`; `DATA_GAP` com `gap_start_ts` dentro do pregão: `gaps`, `gap_minutes`; `not_evaluated` por motivo do último run `END_OF_DAY` `COMPLETED` do pregão (D12).
  - **`DATA_QUALITY_RECHECK`:** runs `QUALITY_RECHECK` atribuídos à janela; linhas `data_quality_rechecks` desses runs as-of: `rows`, `statuses`, `terminal_reasons`, `sessions` (pregões rechecados); `about_this_session` = linhas sobre o pregão `D` gravadas em qualquer run até `as_of` (fecha a Minor 3 do 3C dentro do relatório; essas linhas também aparecem em `rows` da janela do run que as gravou, então nunca se somam os dois); `not_evaluated` por motivo nos runs da janela, **sem** os pulos `ALREADY_RECHECKED`/`ALREADY_EVALUATED` (recheck.py: a pendência já foi tratada), que vão para `skipped` por motivo.
  - **Entrega de alertas:** `created` por tipo, `attempts` por desfecho, `failed_alerts` (alertas distintos com `FAILED`), `failure_types` (`HTTP_<status>` quando há status, senão o tipo de exceção gravado), `pending_at_end` (criados na janela sem `DELIVERED`/`EXPIRED` até o fim da janela).
- **D69 — Resumo de vários pregões.** `summary_sessions` lista os pregões do intervalo; o resumo recalcula cada relatório (sem cache) no **mesmo** snapshot (D61) e devolve uma linha diária de contagens (`DailyRow`), `TradeStats`, latências e buckets de pressão agregados sobre todas as linhas. Cada relatório varre `evaluation_runs` **uma vez** (`window_runs` com todos os tipos, repassado às seções). Custo aceito: 45 relatórios por pedido no pior caso; a observação é de 7–14 dias. `window_runs` não tem limite inferior (o instante de mercado vive no `detail` e os testes usam relógio falso): aceito por semanas; um instante de mercado em coluna própria fica para o próximo plano.
- **D70 — Superfícies de entrega (ruling 5).**
  - (a) `GET /observation/report?day` e `GET /observation/summary?from&to`, aditivas (24 rotas no total).
  - (b) CLI `python -m virtual_orders.observation report --day YYYY-MM-DD [--format markdown|json]` e `summary --from --to [--format]`, saída em stdout. Códigos de saída: `0` ok; `1` erro interno (só o tipo no stderr); `2` uso inválido (argparse) ou `CONFIG_INVALID` (`MISSING:DATABASE_URL`); `3` `OBSERVATION_REQUEST_INVALID`; `4` `DATABASE_UNAVAILABLE` (só o tipo) **somente** para `OperationalError`, `InterfaceError` e o `TimeoutError` do pool do SQLAlchemy (o mesmo mapeamento do `/health`); qualquer outro `SQLAlchemyError` (`ProgrammingError`, `DataError`, …) é defeito: `1` com o código fixo `DATABASE_ERROR` e o tipo.
  - (c) Página "Observação" no dashboard.
  - (d) **Incluído (é pequeno):** `AlertKind.OBSERVATION_DAILY`, chave `OBSERVATION_DAILY:<pregão>` (idempotente, uma por pregão), enfileirado pelo job `end_of_day` só com sink configurado, contido em `try/except` como o resumo de fim de dia (spec 1.2 item 7). Documento só com contagens e códigos (`OBSERVATION_ALERT_FIELDS`), nunca tickers, ids ou mensagens. É a fotografia do primeiro fim de dia que roda (16:30 ou 18:30); `complete: false` e `as_of` ficam no documento porque a janela termina à meia-noite. **O primeiro enfileiramento vence:** se o primeiro fim de dia levantar ao montar o alerta e o pregão depois ficar resolvido (`ALREADY_SETTLED`), nenhum `OBSERVATION_DAILY` é enviado para esse pregão; o relatório continua disponível pela API/CLI (aceito e documentado no runbook). A falha é logada só com o tipo (`logger.error("observation alert failed: %s", type(exc).__name__)`, sem `exc_info`). O relatório do alerta é lido no snapshot da D61 e o outbox é gravado em transação separada. O `END_OF_DAY_SUMMARY` existente não muda. Migration `0005` acrescenta o tipo ao `CHECK` de `alert_outbox.kind`.
- **D71 — Página "Observação".** `ApiClient.observation_report/observation_summary`; view-models puros (`observation_view`, `observation_day_rows`); figuras `daily_r_figure` e `pressure_buckets_figure`; view `views/observation.py`; entrada no `st.sidebar.radio`. Mensagem fixa para `OBSERVATION_REQUEST_INVALID` com os códigos.
- **D72 — Contrato gravado.** `tests/integration/api/test_dashboard_contract.py` grava `observation_report.json` e `observation_summary.json` **depois** de todas as fixtures existentes (que ficam byte-idênticas), com linhas explícitas de `health_state_log` e `worker_sessions` no pregão gravado. A latência por `recorded_at` depende do relógio real e é fixada em `0` na canonicalização (como `*_hash`). Contagem de fixtures: 17 → 19 (`test_boundary_scan_covers_the_3c_modules`).
- **D73 — CLI sem composição.** A CLI lê só `DATABASE_URL` e constrói `make_engine`; não usa `load_settings`/`build_services` (que exigiriam chaves de provider para uma leitura). Nada de provider é importado.

## Definição das métricas (fonte → regra)

| Métrica do responsável | Campo | Fonte | Regra |
|---|---|---|---|
| Falhas de provider | `provider_failures` | `evaluation_run_status.detail` (último as-of) | D67 |
| Candles ausentes | `data_quality` | eventos `DATA_QUALITY:D`, `DATA_GAP`, run `END_OF_DAY` | D68 |
| 503 de actionability | `actionability` | runs `ACTIONABILITY` | D68 |
| `DATA_QUALITY_RECHECK` | `rechecks` | runs `QUALITY_RECHECK`, `data_quality_rechecks` | D68 |
| Falhas de entrega de alertas | `alerts` | `alert_outbox`, `alert_delivery_attempts` | D68 |
| Reinícios do worker | `worker` | `worker_sessions` | D62 |
| Transições de saúde | `health` | `health_state_log` | D68 |
| Trades virtuais | `trades` | `orders`, `order_events`, `order_state` | D64 |
| MFE / MAE | `trades.stats`, `trades.rows` | `order_state.mfe_r`/`mae_r` | D64 |
| Latência sinal → fill | `latency` | `signals.created_at`, eventos `FILLED` | D65 |
| Pressão × resultado | `pressure`, `trades.rows[].pressure_*` | `bars_1m` as-of, `order_state.r_multiple` | D66 |

## Conflitos e lacunas da spec resolvidos acima

1. **Spec 3.1 (lista de tabelas) × reinícios do worker sem registro:** tabela append-only aditiva `worker_sessions`, como as da 0003 (D62).
2. **Spec 6 ("worker cai no meio do ciclo: retoma pelo cursor") × nenhuma medida de quedas:** `STARTED`/`STOPPED` e `unclean_ends`, sem mudar códigos de saída nem o caminho crítico (D62).
3. **Spec 5.1 (lista fechada de rotas) × relatório pela API:** rotas aditivas no padrão da D42 (D70).
4. **Spec 3.6 (leitura atual só para `RECALCULATE` e dashboard) × pressão recalculada:** leitura as-of do relatório, rotulada como estimativa, nunca gravada nem usada em avaliação (D66).
5. **Spec 5.4 (política de revisão) × trades do dia:** mesma política; excluídas contadas (D64).
6. **Spec 5.3 (webhook opcional com resumo) × alerta de observação:** tipo novo aditivo, só contagens, idempotente por pregão; o resumo existente não muda (D70).
7. **Spec 1.2 item 7 (n8n fora do caminho crítico):** o alerta é enfileirado dentro de `try/except` depois do fim de dia e só com sink (D70).
8. **Spec 5.5 (cinco telas) × página "Observação":** tela extra, só pela API (D71).
9. **D21 (`Services.clock` só para o instante de mercado) × as-of do relatório:** o relatório usa o relógio do banco (`acquire_data_as_of`), nunca `Services.clock`; o instante de mercado vem do que os runs gravaram (D61).
10. **D12/D22 (pendências de qualidade saem de `/quality/overview`) × Minor 3 do 3C:** o relatório mostra `about_this_session` dos rechecks; `/quality/overview` não muda (D68).
11. **Leitura as-of (D61) × commits concorrentes:** `recorded_at` é carimbado no insert, não no commit; o relatório é lido num único snapshot `REPEATABLE READ` somente leitura (D61).
12. **"Transições de saúde" do responsável × linhas do `health_state_log`:** transição = mudança de estado; linhas do log contadas à parte em `log_rows` (D68).
13. **Janela de pressão da D44 (data ET do último candle) × sinais do início do pregão:** a observação usa os últimos 30 candles antes do minuto do sinal atravessando pregões, com a janela gravada por trade (D66).

## Questões do autor decididas na revisão do plano

1. **CMF `0.05`/`0.15` e janela de 30 candles:** aceitos como convenções rotuladas; `n` aparece ao lado de cada média de bucket e nenhuma média é calculada com menos de 5 trades (D66).
2. **`OBSERVATION_DAILY` como fotografia das 16:30/18:30:** aceito; `complete: false` e `as_of` no documento; um job às 00:05 ET só se o responsável pedir (D70).
3. **`window_runs` sem limite inferior:** aceito para 7–14 dias; uma varredura por relatório agora (D69); coluna de instante de mercado no próximo plano.
4. **`needs_review` retroativo:** aceito e documentado (D61); `excluded_needs_review` torna a mudança visível.
5. **Latência `MANUAL_USER` a partir do sinal:** mantida pelo ruling; `bar_by_origin` separa as populações (D65).
6. **Tag `plan/paper-observation-report-complete`:** aceita; sem push (Task 13).

## Estrutura de arquivos

```
docs/superpowers/plans/2026-09-14-paper-observation-report.md          # Task 0  — este plano, versionado pelo controlador
migrations/versions/0005_worker_sessions_observation_alert.py          # Task 1
src/virtual_orders/
  storage/tables.py                     # Task 1  — worker_sessions, APPEND_ONLY_TABLES
  readmodels/health.py                  # Task 1  — EXPECTED_SCHEMA_REVISION = "0005"
  ledger/worker_sessions.py             # Task 2  — StopReason, host_fingerprint, record_worker_start/stop
  worker/runner.py                      # Task 2  — sessões em melhor esforço, códigos de saída inalterados
  analytics/observation.py              # Task 3  — estatística pura
  readmodels/observation_window.py      # Task 4  — ObservationWindow, session_window, summary_sessions
  readmodels/observation_operations.py  # Task 4  — provider, qualidade, actionability, recheck, alertas, worker, saúde
  readmodels/observation_trades.py      # Task 5  — trades, MFE/MAE, latência, pressão × resultado
  readmodels/observation.py             # Task 6  — ObservationReport, ObservationSummary, DEFINITIONS
  api/routes/observation.py             # Task 7
  api/app.py                            # Task 7  — router novo
  observation/__init__.py               # Task 8
  observation/__main__.py               # Task 8  — CLI
  observation/render.py                 # Task 8  — Markdown e JSON
  alerts/outbox.py                      # Task 9  — AlertKind.OBSERVATION_DAILY
  alerts/observation.py                 # Task 9  — documento e enfileiramento
  worker/jobs.py                        # Task 9  — gancho no end_of_day
docs/superpowers/runbooks/2026-09-14-compose-producao.md                # Task 8  — seção "Observação diária"
dashboard/dashboard/client.py                                           # Task 10
dashboard/tests/fixtures/api/observation_report.json, observation_summary.json  # Task 10 (gravadas)
dashboard/dashboard/viewmodels.py, charts.py                            # Task 11
dashboard/dashboard/views/observation.py, app.py                        # Task 12
docs/superpowers/notes/2026-09-14-plan4-closeout.md                     # Task 13
tests/integration/test_schema_0005.py                                   # Task 1
tests/integration/api/test_health_api.py                                # Task 1  — head esperado 0005
tests/integration/worker/test_runner.py                                 # Task 2  — testes acrescentados
tests/analytics/test_observation.py                                     # Task 3
tests/readmodels/test_observation_window.py                             # Task 4
tests/integration/observation_support.py                                # Task 4  — helpers (não é conftest)
tests/integration/test_observation_operations.py                        # Task 4
tests/integration/test_observation_trades.py                            # Task 5
tests/integration/test_observation_report.py                            # Task 6
tests/integration/api/test_observation_api.py                           # Task 7
tests/integration/test_observation_cli.py                               # Task 8
tests/integration/worker/test_jobs.py                                   # Task 9  — testes acrescentados
tests/integration/api/test_dashboard_contract.py                        # Task 10
tests/test_import_boundaries.py                                         # Tasks 6, 8, 10, 13
dashboard/tests/test_client.py, test_api_contract.py                    # Tasks 10, 11
dashboard/tests/test_viewmodels.py, test_charts.py                      # Task 11
dashboard/tests/test_app_smoke.py                                       # Task 12
```

---

### Task 0: Conferir o plano versionado e medir a linha de base

**Files:**
- Nenhum arquivo novo. O controlador versiona este plano em `docs/superpowers/plans/2026-09-14-paper-observation-report.md` no branch `plan-4-observation` antes de despachar a Task 1.

**Interfaces:**
- Produces: `N_BASE` (esperado: raiz 1128 passando, dashboard 47 passando, 0 skips, 0 warnings), registrado no ledger e usado nas Tasks 1 e 13.

- [ ] **Step 1: Confirm the plan is committed on the branch**

```bash
git switch plan-4-observation
git log --oneline -1 -- docs/superpowers/plans/2026-09-14-paper-observation-report.md
git status --short
git merge-base --is-ancestor 09f2eea HEAD && echo "based on main 09f2eea"
```

Expected: um commit listado, árvore limpa e `based on main 09f2eea`. Se o plano não estiver versionado, pare e reporte ao controlador; não copie de outro lugar.

- [ ] **Step 2: Measure the inherited baseline**

```bash
docker compose -f docker-compose.test.yml down -v
docker compose -f docker-compose.test.yml up -d --wait
uv sync --locked
uv sync --directory dashboard --locked
uv run pytest -p no:cacheprovider -o addopts="" -q 2>&1 | tail -3
uv run ruff check src tests migrations
uv run mypy
uv run --directory dashboard pytest -p no:cacheprovider -o addopts="" -q 2>&1 | tail -3
uv run --directory dashboard ruff check .
uv run --directory dashboard mypy
```

Expected: `1128 passed` na raiz e `47 passed` no dashboard, sem `skipped` nem `warnings`; ruff e mypy limpos nos dois projetos. Registre as linhas finais como `N_BASE`. Se o número diferir, registre o valor medido e siga (nenhuma task presume número fixo). A Task 0 não gera commit.

---

### Task 1: Migration `0005` — `worker_sessions` e o tipo de alerta `OBSERVATION_DAILY` (D62, D70)

**Files:**
- Create: `migrations/versions/0005_worker_sessions_observation_alert.py`
- Modify: `src/virtual_orders/storage/tables.py` (tabela `worker_sessions`; `APPEND_ONLY_TABLES`)
- Modify: `src/virtual_orders/readmodels/health.py:35` (`EXPECTED_SCHEMA_REVISION = "0005"`)
- Create: `tests/integration/test_schema_0005.py`
- Modify: `tests/integration/api/test_health_api.py` (`test_schema_behind_the_migration_head_is_503_without_facts`: `"expected": "0005"`)

**Interfaces:**
- Produces: `virtual_orders.storage.tables.worker_sessions` (colunas `id`, `session_id`, `event`, `recorded_at`, `code_version`, `host_fingerprint`, `exit_code`, `reason`); `"worker_sessions" in APPEND_ONLY_TABLES`; `alert_outbox.kind` aceita `'OBSERVATION_DAILY'`; alembic head `0005`.

- [ ] **Step 1: Write the failing schema tests**

`tests/integration/test_schema_0005.py`:

```python
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from tests.integration.support import alembic_config, count
from virtual_orders.storage import tables

FINGERPRINT = "0123456789abcdef"


def started(session_id, **overrides):
    values = dict(session_id=session_id, event="STARTED", code_version="sha", host_fingerprint=FINGERPRINT)
    values.update(overrides)
    return values


def stopped(session_id, **overrides):
    values = dict(session_id=session_id, event="STOPPED", exit_code=0, reason="SIGNAL")
    values.update(overrides)
    return values


def test_a_session_has_at_most_one_start_and_one_stop_row(engine):
    session_id = uuid4()
    with engine.begin() as conn:
        conn.execute(tables.worker_sessions.insert().values(**started(session_id)))
        conn.execute(tables.worker_sessions.insert().values(**stopped(session_id)))
        conn.execute(tables.worker_sessions.insert().values(**started(uuid4(), host_fingerprint=None)))
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(tables.worker_sessions.insert().values(**stopped(session_id, exit_code=3, reason="LOCK_LOST")))
    assert count(engine, "worker_sessions") == 3


@pytest.mark.parametrize("row", [
    started(uuid4(), code_version=None),
    started(uuid4(), host_fingerprint="worker-host"),
    started(uuid4(), exit_code=0),
    stopped(uuid4(), exit_code=None),
    stopped(uuid4(), reason="OOM"),
    stopped(uuid4(), code_version="sha"),
    {"session_id": uuid4(), "event": "RESTARTED", "code_version": "sha"},
], ids=["start-without-version", "plain-hostname", "start-with-exit-code", "stop-without-exit-code",
        "unknown-reason", "stop-with-version", "unknown-event"])
def test_rows_that_mix_start_and_stop_fields_are_rejected(engine, row):
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(tables.worker_sessions.insert().values(**row))


def test_worker_sessions_are_append_only_and_the_app_role_can_insert(engine):
    with engine.begin() as conn:
        conn.execute(text("SET LOCAL ROLE vo_app"))
        conn.execute(tables.worker_sessions.insert().values(**started(uuid4())))
    for statement in ("UPDATE worker_sessions SET code_version = 'x'", "DELETE FROM worker_sessions",
                      "TRUNCATE worker_sessions"):
        with pytest.raises(DBAPIError, match="append-only"):
            with engine.begin() as conn:
                conn.execute(text(statement))
    assert "worker_sessions" in tables.APPEND_ONLY_TABLES


def test_the_outbox_accepts_the_observation_alert_kind(engine):
    with engine.begin() as conn:
        conn.execute(tables.alert_outbox.insert().values(
            alert_key="OBSERVATION_DAILY:2025-11-25", kind="OBSERVATION_DAILY", document={}))
    assert count(engine, "alert_outbox") == 1


def test_downgrade_to_0004_and_back(database_url, engine):
    config = alembic_config(database_url)
    command.downgrade(config, "0004")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT to_regclass('worker_sessions')::text")).scalar_one() is None
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(tables.alert_outbox.insert().values(alert_key="k", kind="OBSERVATION_DAILY", document={}))
    command.upgrade(config, "head")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT to_regclass('worker_sessions')::text")).scalar_one() == "worker_sessions"
```

Em `tests/integration/api/test_health_api.py`, no teste `test_schema_behind_the_migration_head_is_503_without_facts`, troque `"detail": {"expected": "0004", "found": "0001"}` por `"detail": {"expected": "0005", "found": "0001"}`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest -p no:cacheprovider -o addopts="" -q tests/integration/test_schema_0005.py tests/readmodels/test_health_rules.py tests/integration/api/test_health_api.py`
Expected: FAIL — `AttributeError: module 'virtual_orders.storage.tables' has no attribute 'worker_sessions'` e o `expected` do `/health` ainda `0004`.

- [ ] **Step 3: Write the migration, the table mirror and the head**

`migrations/versions/0005_worker_sessions_observation_alert.py`:

```python
"""Worker sessions and the daily observation alert kind (Plan 4: D62, D70).

Revision ID: 0005
Revises: 0004
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

_KINDS_BEFORE = (
    "'ORDER_EVENT', 'ORDER_REVIEW', 'INTEGRITY_INCIDENT', 'PRICE_CROSS', 'PRESSURE', 'HEALTH', 'END_OF_DAY_SUMMARY'"
)
APPEND_ONLY = ("worker_sessions",)

SCHEMA = """
CREATE TABLE worker_sessions (
  id bigserial PRIMARY KEY,
  session_id uuid NOT NULL,
  event text NOT NULL CHECK (event IN ('STARTED', 'STOPPED')),
  recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  code_version text NULL,
  host_fingerprint text NULL,
  exit_code int NULL,
  reason text NULL,
  UNIQUE (session_id, event),
  CHECK (
    (event = 'STARTED' AND code_version IS NOT NULL AND exit_code IS NULL AND reason IS NULL
      AND (host_fingerprint IS NULL OR host_fingerprint ~ '^[0-9a-f]{16}$'))
    OR (event = 'STOPPED' AND code_version IS NULL AND host_fingerprint IS NULL AND exit_code IS NOT NULL
      AND reason IN ('SIGNAL', 'LOCK_LOST', 'SCHEDULER_STOPPED', 'UNCAUGHT_EXCEPTION'))
  )
);
CREATE INDEX worker_sessions_recorded_idx ON worker_sessions (event, recorded_at, id);
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
    op.execute(f"GRANT SELECT, INSERT ON {', '.join(APPEND_ONLY)} TO vo_app")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO vo_app")
    op.execute("ALTER TABLE alert_outbox DROP CONSTRAINT alert_outbox_kind_check")
    op.execute(
        "ALTER TABLE alert_outbox ADD CONSTRAINT alert_outbox_kind_check "
        f"CHECK (kind IN ({_KINDS_BEFORE}, 'OBSERVATION_DAILY'))"
    )


def downgrade() -> None:
    # Fails loudly if OBSERVATION_DAILY alerts exist: append-only history is never rewritten for a downgrade.
    # Dropping worker_sessions discards its append-only history, as the 0003 downgrade does for its tables.
    op.execute("ALTER TABLE alert_outbox DROP CONSTRAINT alert_outbox_kind_check")
    op.execute(f"ALTER TABLE alert_outbox ADD CONSTRAINT alert_outbox_kind_check CHECK (kind IN ({_KINDS_BEFORE}))")
    op.execute("DROP TABLE IF EXISTS worker_sessions CASCADE")
```

Em `src/virtual_orders/storage/tables.py`, antes de `APPEND_ONLY_TABLES`:

```python
worker_sessions = Table(
    "worker_sessions", metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("session_id", UUID(as_uuid=True), nullable=False),
    Column("event", Text, nullable=False),
    _ts("recorded_at"),
    Column("code_version", Text),
    Column("host_fingerprint", Text),
    Column("exit_code", Integer),
    Column("reason", Text),
)
```

e substitua `APPEND_ONLY_TABLES` por:

```python
APPEND_ONLY_TABLES = (
    "signals", "orders", "order_events", "evaluation_runs", "evaluation_run_status",
    "order_eval_segments", "bar_batches", "bars_1m", "market_data_snapshots", "integrity_incidents",
    "data_quality_rechecks", "alert_outbox", "alert_delivery_attempts", "alert_event_marks", "health_state_log",
    "worker_sessions",
)
```

Em `src/virtual_orders/readmodels/health.py`:

```python
EXPECTED_SCHEMA_REVISION = "0005"  # alembic head; pinned by test_expected_schema_revision_is_the_migration_head
```

O nome `alert_outbox_kind_check` é o gerado pelo Postgres para o `CHECK` de coluna da 0003 (mesma convenção de `evaluation_runs_kind_check` nas 0002/0003). Se o `upgrade` falhar com `constraint "alert_outbox_kind_check" does not exist`, confira com `SELECT conname FROM pg_constraint WHERE conrelid = 'alert_outbox'::regclass`, use o nome real nas duas funções e registre `Ruling:` no ledger.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest -p no:cacheprovider -o addopts="" -q tests/integration/test_schema_0005.py tests/integration/test_schema.py tests/integration/test_schema_0003.py tests/integration/test_schema_0004.py tests/readmodels/test_health_rules.py tests/integration/api/test_health_api.py && uv run ruff check src tests migrations && uv run mypy`
Expected: PASS (inclui `test_metadata_matches_migrated_columns`, `test_every_append_only_table_has_triggers`, `test_migrations_run_from_zero_and_back`, `test_expected_schema_revision_is_the_migration_head`); ruff e mypy limpos.

- [ ] **Step 5: Commit**

```bash
git add migrations/versions/0005_worker_sessions_observation_alert.py src/virtual_orders/storage/tables.py \
  src/virtual_orders/readmodels/health.py tests/integration/test_schema_0005.py tests/integration/api/test_health_api.py
git commit -m "feat(schema): add append-only worker_sessions and the OBSERVATION_DAILY alert kind (0005)"
```

---

### Task 2: Runner grava sessões do worker em melhor esforço (D62)

**Files:**
- Create: `src/virtual_orders/ledger/worker_sessions.py`
- Modify: `src/virtual_orders/worker/runner.py` (`run_worker`, helpers novos)
- Modify: `tests/integration/worker/test_runner.py` (testes acrescentados)

**Interfaces:**
- Consumes: `tables.worker_sessions` (Task 1).
- Produces:
  - `virtual_orders.ledger.worker_sessions.StopReason` (`SIGNAL`, `LOCK_LOST`, `SCHEDULER_STOPPED`, `UNCAUGHT_EXCEPTION`);
  - `host_fingerprint(hostname: str | None) -> str | None`;
  - `record_worker_start(engine: Engine, *, session_id: UUID, code_version: str, fingerprint: str | None) -> None`;
  - `record_worker_stop(engine: Engine, *, session_id: UUID, exit_code: int, reason: StopReason) -> None`;
  - `run_worker(services, *, scheduler_factory=..., install_signal_handlers=True, hostname: Callable[[], str | None] = _hostname) -> int` (códigos inalterados); `EXIT_UNCAUGHT = 1` (só gravado).

- [ ] **Step 1: Write the failing tests**

Acrescente aos imports de `tests/integration/worker/test_runner.py` (fundidos e ordenados com os existentes, por exemplo `from sqlalchemy import select, text`; `uv run ruff check --fix --select I tests/integration/worker/test_runner.py` organiza):

```python
import logging

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from tests.integration.support import CODE_VERSION
from virtual_orders.ledger.worker_sessions import host_fingerprint
from virtual_orders.storage import tables
```

e ao final do arquivo:

```python
def session_rows(engine):
    with engine.connect() as conn:
        return [dict(row) for row in conn.execute(
            select(tables.worker_sessions).order_by(tables.worker_sessions.c.id)).mappings()]


def test_host_fingerprint_is_a_short_hash_and_never_the_name():
    fingerprint = host_fingerprint("worker-host")
    assert fingerprint is not None and len(fingerprint) == 16 and "worker" not in fingerprint
    assert fingerprint == host_fingerprint("worker-host") != host_fingerprint("other-host")
    assert host_fingerprint(None) is None and host_fingerprint("") is None


def test_a_session_records_its_start_and_its_signalled_stop(worker, monkeypatch):
    installed: dict = {}
    monkeypatch.setattr(runner_module.signal, "signal", lambda signum, handler: installed.__setitem__(signum, handler))

    class SignalledWhileRunning(FakeScheduler):
        def start(self) -> None:
            installed[signal.SIGTERM](signal.SIGTERM, None)

    assert run_worker(worker.services, scheduler_factory=SignalledWhileRunning, hostname=lambda: "worker-host") == 0
    start, stop = session_rows(worker.services.engine)
    assert (start["event"], start["code_version"], start["host_fingerprint"]) == (
        "STARTED", CODE_VERSION, host_fingerprint("worker-host"))
    assert (stop["session_id"], stop["event"], stop["exit_code"], stop["reason"]) == (
        start["session_id"], "STOPPED", 0, "SIGNAL")
    assert start["recorded_at"] <= stop["recorded_at"]
    assert worker.closed == [1]


def test_a_scheduler_that_returns_on_its_own_is_recorded_as_stopped(worker):
    assert run_worker(worker.services, scheduler_factory=FakeScheduler, install_signal_handlers=False,
                      hostname=lambda: None) == 0
    start, stop = session_rows(worker.services.engine)
    assert start["host_fingerprint"] is None
    assert (stop["exit_code"], stop["reason"]) == (0, "SCHEDULER_STOPPED")


def test_a_lost_lock_is_recorded_with_exit_code_3(worker):
    engine = worker.services.engine

    class LosesTheLock(FakeScheduler):
        def start(self) -> None:
            watch = {job[0]: job[1] for job in self.jobs}["worker_lock"]
            with engine.begin() as conn:
                conn.execute(text(
                    "SELECT pg_terminate_backend(pid) FROM pg_locks "
                    "WHERE locktype = 'advisory' AND objid = CAST(:key AS oid) AND pid <> pg_backend_pid()"
                ), {"key": WORKER_LOCK_KEY})
            assert watch() == JobResult("worker_lock", False, "LOCK_LOST")

    assert run_worker(worker.services, scheduler_factory=LosesTheLock, install_signal_handlers=False) == 3
    _, stop = session_rows(engine)
    assert (stop["exit_code"], stop["reason"]) == (3, "LOCK_LOST")
    assert_lock_is_free(engine)


def test_a_failing_scheduler_is_recorded_and_still_raises(worker):
    class Broken(FakeScheduler):
        def start(self) -> None:
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        run_worker(worker.services, scheduler_factory=Broken, install_signal_handlers=False)
    _, stop = session_rows(worker.services.engine)
    assert (stop["exit_code"], stop["reason"]) == (1, "UNCAUGHT_EXCEPTION")
    assert worker.closed == [1]


def test_a_worker_that_never_gets_the_lock_is_not_a_session(worker):
    held = acquire_worker_lock(worker.services.engine)
    assert held is not None
    try:
        assert run_worker(worker.services, scheduler_factory=FakeScheduler, install_signal_handlers=False) == 2
    finally:
        release_worker_lock(held)
    assert session_rows(worker.services.engine) == []


def test_a_failing_session_write_never_stops_the_worker_and_logs_only_the_type(worker, monkeypatch, caplog):
    def failing(*args, **kwargs):
        raise SQLAlchemyError("postgresql+psycopg://vo:hunter2@db/vo")

    monkeypatch.setattr(runner_module, "record_worker_start", failing)
    scheduler = FakeScheduler()
    with caplog.at_level(logging.WARNING, logger="virtual_orders.worker"):
        code = run_worker(worker.services, scheduler_factory=lambda: scheduler, install_signal_handlers=False)
    assert code == 0 and scheduler.started and worker.closed == [1]
    assert "worker session start not recorded: SQLAlchemyError" in caplog.text
    assert "hunter2" not in caplog.text
    assert session_rows(worker.services.engine) == []  # no stop row without a recorded start
    assert_lock_is_free(worker.services.engine)


def test_a_failing_stop_write_still_releases_the_lock_and_closes(worker, monkeypatch, caplog):
    def failing(*args, **kwargs):
        raise SQLAlchemyError("down")

    monkeypatch.setattr(runner_module, "record_worker_stop", failing)
    with caplog.at_level(logging.WARNING, logger="virtual_orders.worker"):
        assert run_worker(worker.services, scheduler_factory=FakeScheduler, install_signal_handlers=False) == 0
    assert "worker session stop not recorded: SQLAlchemyError" in caplog.text
    assert [row["event"] for row in session_rows(worker.services.engine)] == ["STARTED"]
    assert worker.closed == [1]
    assert_lock_is_free(worker.services.engine)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest -p no:cacheprovider -o addopts="" -q tests/integration/worker/test_runner.py`
Expected: FAIL com `ModuleNotFoundError: No module named 'virtual_orders.ledger.worker_sessions'`.

- [ ] **Step 3: Implement the ledger module and the runner change**

`src/virtual_orders/ledger/worker_sessions.py`:

```python
"""Worker process sessions (Plan 4, D62): an append-only start row and a best-effort stop row. Never secrets."""

from __future__ import annotations

import hashlib
from enum import StrEnum
from uuid import UUID

from sqlalchemy import Engine, func

from virtual_orders.storage.tables import worker_sessions

FINGERPRINT_LENGTH = 16


class StopReason(StrEnum):
    SIGNAL = "SIGNAL"
    LOCK_LOST = "LOCK_LOST"
    SCHEDULER_STOPPED = "SCHEDULER_STOPPED"
    UNCAUGHT_EXCEPTION = "UNCAUGHT_EXCEPTION"  # any exception after the lock: scheduler, jobs, schedule, factory, exit


def host_fingerprint(hostname: str | None) -> str | None:
    """First 16 hex digits of SHA-256 of the host name: tells container/host instances apart without the name.

    Inside a container the host name is the container id, which changes on every recreate."""
    if not hostname:
        return None
    return hashlib.sha256(hostname.encode("utf-8")).hexdigest()[:FINGERPRINT_LENGTH]


def record_worker_start(engine: Engine, *, session_id: UUID, code_version: str, fingerprint: str | None) -> None:
    with engine.begin() as conn:
        conn.execute(worker_sessions.insert().values(
            session_id=session_id, event="STARTED", code_version=code_version, host_fingerprint=fingerprint,
            recorded_at=func.clock_timestamp(),
        ))


def record_worker_stop(engine: Engine, *, session_id: UUID, exit_code: int, reason: StopReason) -> None:
    with engine.begin() as conn:
        conn.execute(worker_sessions.insert().values(
            session_id=session_id, event="STOPPED", exit_code=exit_code, reason=reason.value,
            recorded_at=func.clock_timestamp(),
        ))
```

Em `src/virtual_orders/worker/runner.py`, acrescente aos imports:

```python
import socket
from uuid import UUID, uuid4

from virtual_orders.ledger.worker_sessions import StopReason, host_fingerprint, record_worker_start, record_worker_stop
```

depois de `EXIT_DATABASE_UNAVAILABLE`:

```python
EXIT_UNCAUGHT = 1  # D62: the interpreter's status for an uncaught exception; recorded, never returned
```

antes de `run_worker`:

```python
def _hostname() -> str | None:
    try:
        return socket.gethostname()
    except OSError:
        return None


def _start_session(services: Services, hostname: Callable[[], str | None]) -> UUID | None:
    """D62: best effort. A failed write is logged by type only and never stops the worker."""
    session_id = uuid4()
    try:
        record_worker_start(services.engine, session_id=session_id, code_version=services.code_version,
                            fingerprint=host_fingerprint(hostname()))
    except Exception as exc:  # noqa: BLE001 - observation data is never in the critical path
        logger.warning("worker session start not recorded: %s", type(exc).__name__)
        return None
    return session_id


def _stop_session(services: Services, session_id: UUID, exit_code: int, reason: StopReason) -> None:
    try:
        record_worker_stop(services.engine, session_id=session_id, exit_code=exit_code, reason=reason)
    except Exception as exc:  # noqa: BLE001 - the lock is still released and the services still closed
        logger.warning("worker session stop not recorded: %s", type(exc).__name__)
```

e substitua `run_worker` inteiro por:

```python
def run_worker(
    services: Services,
    *,
    scheduler_factory: Callable[[], Scheduler] = blocking_scheduler,
    install_signal_handlers: bool = True,
    hostname: Callable[[], str | None] = _hostname,
) -> int:
    lock: Connection | None = None
    lost = False
    signalled = False
    session: UUID | None = None
    ended: tuple[int, StopReason] | None = None
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
        session = _start_session(services, hostname)  # D62: only a process holding the lock is a session
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
                nonlocal signalled
                if not guard.claim():
                    logger.info("signal %s received again; already shutting down", signum)
                    return  # a second signal, or one racing the lock-loss shutdown: never shut down twice
                signalled = True
                logger.info("signal %s received; waiting for running jobs and shutting down", signum)
                scheduler.shutdown(wait=True)

            signal.signal(signal.SIGTERM, stop)
            signal.signal(signal.SIGINT, stop)
        scheduler.start()
        code = EXIT_LOCK_LOST if lost else EXIT_OK
        reason = StopReason.LOCK_LOST if lost else StopReason.SIGNAL if signalled else StopReason.SCHEDULER_STOPPED
        ended = (code, reason)
        return code
    except BaseException:
        ended = (EXIT_UNCAUGHT, StopReason.UNCAUGHT_EXCEPTION)  # also KeyboardInterrupt/SystemExit after the lock
        raise
    finally:
        if session is not None and ended is not None:
            _stop_session(services, session, *ended)  # before the lock is released: the next start sorts after it
        if lock is not None:
            try:
                release_worker_lock(lock)
            except Exception as exc:  # noqa: BLE001 - closing the services below must still happen
                logger.warning("could not release the worker lock: %s", type(exc).__name__)
        services.close()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest -p no:cacheprovider -o addopts="" -q tests/integration/worker tests/worker && uv run ruff check src tests migrations && uv run mypy`
Expected: PASS, inclusive todos os testes pré-existentes do runner (códigos `0`, `2`, `3`, `4` e `test_a_signal_after_the_lock_is_lost_does_not_shut_down_twice`); ruff e mypy limpos.

- [ ] **Step 5: Commit**

```bash
git add src/virtual_orders/ledger/worker_sessions.py src/virtual_orders/worker/runner.py tests/integration/worker/test_runner.py
git commit -m "feat(worker): record worker sessions best-effort without changing exit codes"
```

---

### Task 3: Estatística pura da observação (D64–D68)

**Files:**
- Create: `src/virtual_orders/analytics/observation.py`
- Create: `tests/analytics/test_observation.py`
- Modify: `tests/test_import_boundaries.py` (`PLATFORM_PURE_MODULES` inclui `virtual_orders/analytics/observation.py`)

**Interfaces:**
- Consumes: `virtual_orders.analytics.pressure.PressureEstimate`, `PressureSide`, `strong_pressure`.
- Produces (todos puros):
  - constantes `R_QUANTUM = Decimal("0.0001")`, `RATE_QUANTUM = Decimal("0.0001")`, `PCT_QUANTUM = Decimal("0.01")`, `OBSERVATION_CMF_THRESHOLD = Decimal("0.05")`, `STRONG_CMF = Decimal("0.15")`, `MIN_TRADES_FOR_BUCKET_MEAN = 5`, `UNKNOWN_STATE = "UNKNOWN"`, `ASSOCIATION_NOTE: str`;
  - `whole_seconds(start: datetime, end: datetime) -> int`;
  - `nearest_rank(values: Sequence[int], percentile: int) -> int | None`;
  - `LatencyStats(count: int, median_seconds: int | None, p90_seconds: int | None, max_seconds: int | None)`; `latency_stats(values: Sequence[int]) -> LatencyStats`;
  - `quantize_r(value: Decimal) -> Decimal`; `ratio(numerator: int, denominator: int) -> Decimal | None`; `coverage_pct(expected: int, missing: int) -> Decimal | None`;
  - `TradeStats(trades, wins, losses, win_rate, sum_r, mean_r, mean_mfe_r, median_mfe_r, mean_mae_r, median_mae_r)`; `trade_stats(results: Sequence[tuple[Decimal, Decimal | None, Decimal | None]]) -> TradeStats`;
  - `StateChange(at: datetime, state: str)`; `seconds_by_state(initial: str | None, changes: Sequence[StateChange], start: datetime, end: datetime) -> dict[str, int]`;
  - `Alignment` (`ALIGNED`, `OPPOSED`, `NEUTRAL`, `UNAVAILABLE`), `Strength` (`WEAK`, `MODERATE`, `STRONG`); `classify_pressure(estimate: PressureEstimate | None, direction: str) -> tuple[Alignment, Strength | None]`;
  - `PressureBucket(alignment, strength, trades, wins, sum_r, mean_r)` (`mean_r` é `None` com menos de `MIN_TRADES_FOR_BUCKET_MEAN` trades); `pressure_buckets(items: Iterable[tuple[Alignment, Strength | None, Decimal]]) -> list[PressureBucket]`;
  - `failure_code(message: object) -> str`.

- [ ] **Step 1: Write the failing tests**

`tests/analytics/test_observation.py`:

```python
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from virtual_orders.analytics.observation import (
    ASSOCIATION_NOTE,
    MIN_TRADES_FOR_BUCKET_MEAN,
    UNKNOWN_STATE,
    Alignment,
    LatencyStats,
    PressureBucket,
    StateChange,
    Strength,
    TradeStats,
    classify_pressure,
    coverage_pct,
    failure_code,
    latency_stats,
    nearest_rank,
    pressure_buckets,
    ratio,
    seconds_by_state,
    trade_stats,
    whole_seconds,
)
from virtual_orders.analytics.pressure import METHOD, PressureEstimate

T0 = datetime(2025, 11, 25, 5, 0, tzinfo=UTC)


def estimate(cmf: str, obv: str = "1", distance: str = "0.5") -> PressureEstimate:
    return PressureEstimate(
        method=METHOD, bars=30, first_bar_ts=T0, last_bar_ts=T0 + timedelta(minutes=29),
        close_location_value=Decimal("0"), chaikin_money_flow=Decimal(cmf), obv_slope=Decimal(obv),
        vwap=Decimal("100"), vwap_distance_pct=Decimal(distance),
    )


def test_whole_seconds_floors_and_rejects_naive_datetimes():
    assert whole_seconds(T0, T0 + timedelta(minutes=65, microseconds=999_999)) == 3900
    assert whole_seconds(T0, T0 - timedelta(microseconds=1)) == -1
    with pytest.raises(ValueError, match="naive"):
        whole_seconds(datetime(2025, 11, 25), T0)  # noqa: DTZ001


def test_nearest_rank_percentiles_never_interpolate():
    assert nearest_rank([], 50) is None
    assert nearest_rank([30], 90) == 30
    assert nearest_rank([40, 10, 30, 20], 50) == 20  # ceil(0.5 * 4) = 2nd smallest
    assert nearest_rank(list(range(1, 11)), 90) == 9
    assert nearest_rank(list(range(1, 11)), 100) == 10
    with pytest.raises(ValueError):
        nearest_rank([1], 0)
    assert latency_stats([3900, 60, 600]) == LatencyStats(count=3, median_seconds=600, p90_seconds=3900,
                                                          max_seconds=3900)
    assert latency_stats([]) == LatencyStats(0, None, None, None)


def test_ratios_and_coverage_are_quantized_decimals():
    assert ratio(1, 3) == Decimal("0.3333") and ratio(0, 0) is None
    assert coverage_pct(390, 35) == Decimal("91.03") and coverage_pct(0, 0) is None


def test_trade_stats_follow_the_r_sign_and_median_rules():
    stats = trade_stats([
        (Decimal("1.75"), Decimal("2.375"), Decimal("-0.1")),
        (Decimal("-1"), Decimal("0.5"), Decimal("-1.2")),
        (Decimal("0"), None, None),
        (Decimal("0.5"), Decimal("1"), Decimal("-0.3")),
    ])
    assert stats == TradeStats(
        trades=4, wins=2, losses=1, win_rate=Decimal("0.5000"), sum_r=Decimal("1.2500"), mean_r=Decimal("0.3125"),
        mean_mfe_r=Decimal("1.2917"), median_mfe_r=Decimal("1.0000"), mean_mae_r=Decimal("-0.5333"),
        median_mae_r=Decimal("-0.3000"),
    )
    empty = trade_stats([])
    assert (empty.trades, empty.win_rate, empty.sum_r, empty.mean_r, empty.median_mfe_r) == (
        0, None, Decimal("0.0000"), None, None)
    assert str(trade_stats([(Decimal("0"), None, None)]).sum_r) == "0.0000"  # never "-0.0000"


def test_seconds_by_state_splits_the_window_at_each_change():
    start, end = T0, T0 + timedelta(hours=24)
    changes = [StateChange(T0 + timedelta(hours=10), "DEGRADED"), StateChange(T0 + timedelta(hours=10, minutes=30),
                                                                               "DEGRADED"),
               StateChange(T0 + timedelta(hours=11), "HEALTHY")]
    assert seconds_by_state("HEALTHY", changes, start, end) == {"DEGRADED": 3600, "HEALTHY": 82800}
    assert seconds_by_state(None, [StateChange(T0, "HEALTHY")], start, end) == {"HEALTHY": 86400}
    assert seconds_by_state(None, [], start, end) == {UNKNOWN_STATE: 86400}
    assert seconds_by_state("HEALTHY", [], end, start) == {}
    with pytest.raises(ValueError, match="outside"):
        seconds_by_state("HEALTHY", [StateChange(end, "DEGRADED")], start, end)


@pytest.mark.parametrize("value, direction, expected", [
    (estimate("0.2"), "LONG", (Alignment.ALIGNED, Strength.STRONG)),
    (estimate("0.2"), "SHORT", (Alignment.OPPOSED, Strength.STRONG)),
    (estimate("-0.08", obv="-1", distance="-0.2"), "SHORT", (Alignment.ALIGNED, Strength.MODERATE)),
    (estimate("0.08", obv="-1"), "LONG", (Alignment.NEUTRAL, Strength.MODERATE)),  # OBV falling: no strong side
    (estimate("0.0137"), "LONG", (Alignment.NEUTRAL, Strength.WEAK)),
    (None, "LONG", (Alignment.UNAVAILABLE, None)),
])
def test_pressure_is_classified_relative_to_the_trade_direction(value, direction, expected):
    assert classify_pressure(value, direction) == expected


def test_an_unknown_direction_is_rejected():
    with pytest.raises(ValueError, match="direction"):
        classify_pressure(estimate("0.2"), "FLAT")


def test_buckets_group_results_in_a_fixed_order_and_need_five_trades_for_a_mean():
    buckets = pressure_buckets([
        (Alignment.UNAVAILABLE, None, Decimal("-1")),
        (Alignment.ALIGNED, Strength.STRONG, Decimal("1.75")),
        (Alignment.ALIGNED, Strength.WEAK, Decimal("0.5")),
        (Alignment.ALIGNED, Strength.STRONG, Decimal("-1")),
        *((Alignment.NEUTRAL, Strength.WEAK, Decimal(r)) for r in ("1", "-1", "0.5", "0.5", "1")),
    ])
    assert buckets == [
        PressureBucket(Alignment.ALIGNED, Strength.WEAK, 1, 1, Decimal("0.5000"), None),
        PressureBucket(Alignment.ALIGNED, Strength.STRONG, 2, 1, Decimal("0.7500"), None),
        PressureBucket(Alignment.NEUTRAL, Strength.WEAK, 5, 4, Decimal("2.0000"), Decimal("0.4000")),
        PressureBucket(Alignment.UNAVAILABLE, None, 1, 0, Decimal("-1.0000"), None),
    ]
    assert MIN_TRADES_FOR_BUCKET_MEAN == 5
    assert "not causal" in ASSOCIATION_NOTE and "never used by any evaluation" in ASSOCIATION_NOTE
    assert "at least 5 trades" in ASSOCIATION_NOTE


@pytest.mark.parametrize("message, code", [
    ("UNKNOWN_DATA_SOURCE: no bar source registered for nope", "UNKNOWN_DATA_SOURCE"),
    ("ERROR:KeyError", "UNEXPECTED_ERROR"),
    ("HTTP 429 from https://data.alpaca.markets?key=secret", "SOURCE_ERROR"),
    ("SourceUnavailable", "SOURCE_ERROR"),
])
def test_failure_messages_become_fixed_codes(message, code):
    assert failure_code(message) == code
```

Em `tests/test_import_boundaries.py`, troque `PLATFORM_PURE_MODULES` por:

```python
PLATFORM_PURE_MODULES = [
    "virtual_orders/analytics/pressure.py", "virtual_orders/alerts/rules.py",
    "virtual_orders/analytics/vwap.py", "virtual_orders/analytics/portfolio.py",
    "virtual_orders/analytics/observation.py",
]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest -p no:cacheprovider -o addopts="" -q tests/analytics/test_observation.py tests/test_import_boundaries.py`
Expected: FAIL com `ModuleNotFoundError: No module named 'virtual_orders.analytics.observation'` (e `test_boundary_scan_covers_the_worker_and_alert_packages` falha porque o arquivo listado ainda não existe).

- [ ] **Step 3: Implement the module**

`src/virtual_orders/analytics/observation.py`:

```python
"""Pure statistics for the daily paper-observation report (Plan 4, D64-D68). No I/O; Decimal only, never float."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from enum import StrEnum

from core.domain.hashing import CANONICAL_CONTEXT
from virtual_orders.analytics.pressure import PressureEstimate, PressureSide, strong_pressure

R_QUANTUM = Decimal("0.0001")
RATE_QUANTUM = Decimal("0.0001")
PCT_QUANTUM = Decimal("0.01")
OBSERVATION_CMF_THRESHOLD = Decimal("0.05")
STRONG_CMF = Decimal("0.15")
MIN_TRADES_FOR_BUCKET_MEAN = 5
UNKNOWN_STATE = "UNKNOWN"
ASSOCIATION_NOTE = (
    "Descriptive counts over a small paper sample: not causal evidence, not a trading signal, and never used by any "
    "evaluation. A bucket mean is shown only with at least 5 trades; n is always shown beside it."
)
_DIRECTIONS = frozenset({"LONG", "SHORT"})


def whole_seconds(start: datetime, end: datetime) -> int:
    """Floor of (end - start) in whole seconds."""
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("naive datetime is not allowed")
    delta = end - start
    return delta.days * 86400 + delta.seconds


def nearest_rank(values: Sequence[int], percentile: int) -> int | None:
    """The ceil(p/100 * n)-th smallest value, without interpolation (D65); None when there is no value."""
    if not 0 < percentile <= 100:
        raise ValueError("percentile must be in (0, 100]")
    if not values:
        return None
    ordered = sorted(values)
    rank = -(-percentile * len(ordered) // 100)
    return ordered[rank - 1]


@dataclass(frozen=True)
class LatencyStats:
    count: int
    median_seconds: int | None
    p90_seconds: int | None
    max_seconds: int | None


def latency_stats(values: Sequence[int]) -> LatencyStats:
    return LatencyStats(len(values), nearest_rank(values, 50), nearest_rank(values, 90),
                        max(values) if values else None)


def _quantize(value: Decimal, quantum: Decimal) -> Decimal:
    with localcontext(CANONICAL_CONTEXT):
        result = value.quantize(quantum, rounding=ROUND_HALF_EVEN)
    return result if result != 0 else Decimal(0).quantize(quantum)  # never "-0.0000"


def quantize_r(value: Decimal) -> Decimal:
    return _quantize(value, R_QUANTUM)


def ratio(numerator: int, denominator: int) -> Decimal | None:
    if denominator <= 0:
        return None
    with localcontext(CANONICAL_CONTEXT):
        return _quantize(Decimal(numerator) / Decimal(denominator), RATE_QUANTUM)


def coverage_pct(expected: int, missing: int) -> Decimal | None:
    if expected <= 0:
        return None
    with localcontext(CANONICAL_CONTEXT):
        return _quantize(Decimal(expected - missing) * 100 / Decimal(expected), PCT_QUANTUM)


def _mean(values: Sequence[Decimal]) -> Decimal | None:
    if not values:
        return None
    with localcontext(CANONICAL_CONTEXT):
        return quantize_r(sum(values, Decimal(0)) / len(values))


def _median(values: Sequence[Decimal]) -> Decimal | None:
    """Middle value; the mean of the two middle values when the count is even (D64)."""
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return quantize_r(ordered[middle])
    with localcontext(CANONICAL_CONTEXT):
        return quantize_r((ordered[middle - 1] + ordered[middle]) / 2)


@dataclass(frozen=True)
class TradeStats:
    trades: int
    wins: int
    losses: int
    win_rate: Decimal | None
    sum_r: Decimal
    mean_r: Decimal | None
    mean_mfe_r: Decimal | None
    median_mfe_r: Decimal | None
    mean_mae_r: Decimal | None
    median_mae_r: Decimal | None


def trade_stats(results: Sequence[tuple[Decimal, Decimal | None, Decimal | None]]) -> TradeStats:
    """`results` = (r_multiple, mfe_r, mae_r) per closed trade. Wins are r > 0, losses r < 0 (spec 5.4)."""
    r_values = [r for r, _, _ in results]
    mfe = [value for _, value, _ in results if value is not None]
    mae = [value for _, _, value in results if value is not None]
    wins = sum(1 for r in r_values if r > 0)
    with localcontext(CANONICAL_CONTEXT):
        total = sum(r_values, Decimal(0))
    return TradeStats(
        trades=len(r_values), wins=wins, losses=sum(1 for r in r_values if r < 0),
        win_rate=ratio(wins, len(r_values)), sum_r=quantize_r(total), mean_r=_mean(r_values),
        mean_mfe_r=_mean(mfe), median_mfe_r=_median(mfe), mean_mae_r=_mean(mae), median_mae_r=_median(mae),
    )


@dataclass(frozen=True)
class StateChange:
    at: datetime
    state: str


def seconds_by_state(
    initial: str | None, changes: Sequence[StateChange], start: datetime, end: datetime
) -> dict[str, int]:
    """Seconds spent in each health state inside [start, end); `initial` is the state in force at `start` (D68)."""
    if end <= start:
        return {}
    totals: dict[str, int] = {}
    current, since = initial or UNKNOWN_STATE, start
    for change in sorted(changes, key=lambda item: item.at):
        if not start <= change.at < end:
            raise ValueError("state change outside the window")
        totals[current] = totals.get(current, 0) + whole_seconds(since, change.at)
        current, since = change.state, change.at
    totals[current] = totals.get(current, 0) + whole_seconds(since, end)
    return {state: seconds for state, seconds in sorted(totals.items()) if seconds > 0}


class Alignment(StrEnum):
    ALIGNED = "ALIGNED"
    OPPOSED = "OPPOSED"
    NEUTRAL = "NEUTRAL"
    UNAVAILABLE = "UNAVAILABLE"


class Strength(StrEnum):
    WEAK = "WEAK"
    MODERATE = "MODERATE"
    STRONG = "STRONG"


def classify_pressure(estimate: PressureEstimate | None, direction: str) -> tuple[Alignment, Strength | None]:
    """D66: the strong side (D37 rule at CMF 0.05) against the trade direction, and |CMF| as the strength."""
    if direction not in _DIRECTIONS:
        raise ValueError(f"unknown direction {direction!r}")
    if estimate is None:
        return Alignment.UNAVAILABLE, None
    magnitude = abs(estimate.chaikin_money_flow)
    if magnitude >= STRONG_CMF:
        strength = Strength.STRONG
    elif magnitude >= OBSERVATION_CMF_THRESHOLD:
        strength = Strength.MODERATE
    else:
        strength = Strength.WEAK
    side = strong_pressure(estimate, OBSERVATION_CMF_THRESHOLD)
    if side is None:
        return Alignment.NEUTRAL, strength
    favourable = PressureSide.BUY if direction == "LONG" else PressureSide.SELL
    return (Alignment.ALIGNED if side is favourable else Alignment.OPPOSED), strength


@dataclass(frozen=True)
class PressureBucket:
    alignment: Alignment
    strength: Strength | None
    trades: int
    wins: int
    sum_r: Decimal
    mean_r: Decimal | None


_ALIGNMENT_ORDER = {item: index for index, item in enumerate(Alignment)}
_STRENGTH_ORDER: dict[Strength | None, int] = {None: -1}
_STRENGTH_ORDER.update({item: index for index, item in enumerate(Strength)})


def pressure_buckets(items: Iterable[tuple[Alignment, Strength | None, Decimal]]) -> list[PressureBucket]:
    """D66: counts and sum of R per bucket; the mean only with at least MIN_TRADES_FOR_BUCKET_MEAN trades."""
    groups: dict[tuple[Alignment, Strength | None], list[Decimal]] = {}
    for alignment, strength, r_multiple in items:
        groups.setdefault((alignment, strength), []).append(r_multiple)
    buckets: list[PressureBucket] = []
    for (alignment, strength), values in sorted(
        groups.items(), key=lambda entry: (_ALIGNMENT_ORDER[entry[0][0]], _STRENGTH_ORDER[entry[0][1]])
    ):
        with localcontext(CANONICAL_CONTEXT):
            total = sum(values, Decimal(0))
        mean = _mean(values) if len(values) >= MIN_TRADES_FOR_BUCKET_MEAN else None
        buckets.append(PressureBucket(alignment, strength, len(values), sum(1 for r in values if r > 0),
                                      quantize_r(total), mean))
    return buckets


def failure_code(message: object) -> str:
    """D67: a stored failure message reduced to a fixed code; the message itself never leaves the database."""
    text = str(message)
    if text.startswith("UNKNOWN_DATA_SOURCE"):
        return "UNKNOWN_DATA_SOURCE"
    if text.startswith("ERROR:"):
        return "UNEXPECTED_ERROR"
    return "SOURCE_ERROR"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest -p no:cacheprovider -o addopts="" -q tests/analytics tests/test_import_boundaries.py && uv run ruff check src tests migrations && uv run mypy`
Expected: PASS; ruff e mypy limpos. Se o valor esperado de uma média/mediana divergir por arredondamento, recalcule à mão com `ROUND_HALF_EVEN` em 4 casas e corrija **o teste** somente se a conta manual confirmar o valor da implementação; registre `Ruling:` no ledger.

- [ ] **Step 5: Commit**

```bash
git add src/virtual_orders/analytics/observation.py tests/analytics/test_observation.py tests/test_import_boundaries.py
git commit -m "feat(analytics): pure observation statistics for latency, trades, health time and pressure buckets"
```

---

### Task 4: Read model — janela do pregão e seções operacionais (D61, D63, D67, D68, D62)

**Files:**
- Create: `src/virtual_orders/readmodels/observation_window.py`
- Create: `src/virtual_orders/readmodels/observation_operations.py`
- Create: `tests/readmodels/test_observation_window.py`
- Create: `tests/integration/observation_support.py`
- Create: `tests/integration/test_observation_operations.py`

**Interfaces:**
- Consumes: `analytics.observation` (Task 3): `StateChange`, `UNKNOWN_STATE`, `coverage_pct`, `failure_code`, `ratio`, `seconds_by_state`; `tables.worker_sessions` (Task 1); `evaluator.manual.ACTIONABILITY_UNVERIFIABLE`; `marketdata.calendars.calendar_for_window`.
- Produces:
  - `observation_window`: `MARKET_TZ`, `MAX_SUMMARY_DAYS = 45`; `ObservationRequestInvalid(codes: list[str])`; `ObservationWindow(session_day: date, session_open_utc: datetime, session_close_utc: datetime, start: datetime, end: datetime, as_of: datetime)` com propriedades `effective_end: datetime` e `complete: bool`; `session_window(day: date, as_of: datetime) -> ObservationWindow`; `summary_sessions(first: date, last: date, as_of: datetime) -> list[ObservationWindow]`.
  - `observation_operations`: `RUN_KIND_ORDER: tuple[str, ...]` (os seis tipos de run lidos), `RECHECK_SKIP_REASONS`; `RunFacts(run_id, kind, status, detail, instant)`; `window_runs(conn, window, kinds: Sequence[str]) -> list[RunFacts]`;
    as quatro seções que leem runs aceitam `*, runs: Sequence[RunFacts] | None = None` (a montagem passa uma única varredura com `RUN_KIND_ORDER`; `None` faz a própria leitura);
    `FeedFailures(run_kind, runs, failed_runs, runs_with_failures, failures, codes, feeds, feeds_total, failed_run_errors)`, `ProviderFailures(by_run_kind, total_failures, consecutive_live_max)`, `provider_failures(conn, window, *, runs=None) -> ProviderFailures`;
    `ActionabilitySection(requests, results, unverifiable, unverifiable_causes, unverifiable_rate, unfinished)`, `actionability(conn, window, *, runs=None)`;
    `DataQualitySection(orders_measured, expected_bars, missing_bars, coverage_pct, orders_with_missing, gaps, gap_minutes, not_evaluated)`, `data_quality(conn, window, *, runs=None)`;
    `RecheckSection(runs, rows, statuses, terminal_reasons, sessions, about_this_session, not_evaluated, skipped)`, `recheck_activity(conn, window, *, runs=None)`;
    `AlertDeliverySection(created, attempts, failed_alerts, failure_types, pending_at_end)`, `alert_deliveries(conn, window)`;
    `WorkerSessionView(session_id, started_at, code_version, stopped_at, exit_code, reason)`, `WorkerSection(starts, restarts, unclean_ends, stops, exit_codes, code_versions, open_session_at_end, sessions)`, `worker_activity(conn, window)`;
    `HealthSection(state_at_start, transitions, log_rows, entered, cause_codes, seconds_by_state, state_at_end)`, `health_transitions(conn, window)`.
  - `tests/integration/observation_support.py`: `recorded_run(engine, kind, *, start_detail, status, detail) -> UUID`, `window_for(engine, day: date) -> ObservationWindow`.

- [ ] **Step 1: Write the failing window tests**

`tests/readmodels/test_observation_window.py`:

```python
from datetime import date

import pytest

from tests.support import et
from virtual_orders.readmodels.observation_window import (
    MAX_SUMMARY_DAYS,
    ObservationRequestInvalid,
    session_window,
    summary_sessions,
)

AS_OF = et("2025-12-02", "12:00")  # a Tuesday; 2025-11-27 is Thanksgiving and 2025-11-28 closes at 13:00 ET


def test_a_session_window_owns_the_days_without_a_session_before_it():
    monday = session_window(date(2025, 12, 1), AS_OF)
    assert (monday.start, monday.end) == (et("2025-11-29", "00:00"), et("2025-12-02", "00:00"))
    assert (monday.session_open_utc, monday.session_close_utc) == (
        et("2025-12-01", "09:30"), et("2025-12-01", "16:00"))
    assert monday.complete and monday.effective_end == monday.end
    friday = session_window(date(2025, 11, 28), AS_OF)
    assert friday.start == et("2025-11-27", "00:00") and friday.session_close_utc == et("2025-11-28", "13:00")
    today = session_window(date(2025, 12, 2), AS_OF)
    assert not today.complete and today.effective_end == AS_OF


@pytest.mark.parametrize("day, codes", [
    (date(2025, 11, 27), ["NOT_A_SESSION:2025-11-27"]),
    (date(2025, 11, 29), ["NOT_A_SESSION:2025-11-29"]),
    (date(2025, 12, 3), ["DAY_IN_FUTURE:2025-12-03"]),
    (date(2025, 12, 6), ["DAY_IN_FUTURE:2025-12-06", "NOT_A_SESSION:2025-12-06"]),
])
def test_days_without_a_session_and_future_days_are_rejected_with_codes(day, codes):
    with pytest.raises(ObservationRequestInvalid) as caught:
        session_window(day, AS_OF)
    assert caught.value.codes == codes


def test_a_naive_as_of_is_a_programming_error():
    with pytest.raises(ValueError, match="timezone-aware"):
        session_window(date(2025, 12, 1), AS_OF.replace(tzinfo=None))


def test_a_summary_lists_the_sessions_of_the_range():
    windows = summary_sessions(date(2025, 11, 22), date(2025, 12, 1), AS_OF)
    assert [window.session_day for window in windows] == [
        date(2025, 11, 24), date(2025, 11, 25), date(2025, 11, 26), date(2025, 11, 28), date(2025, 12, 1)]
    assert MAX_SUMMARY_DAYS == 45


@pytest.mark.parametrize("first, last, codes", [
    (date(2025, 12, 1), date(2025, 11, 24), ["EMPTY_RANGE"]),
    (date(2025, 10, 1), date(2025, 11, 25), ["RANGE_TOO_LARGE"]),
    (date(2025, 11, 29), date(2025, 11, 30), ["NO_SESSIONS"]),
    (date(2025, 12, 1), date(2025, 12, 5), ["DAY_IN_FUTURE:2025-12-05"]),
])
def test_invalid_summary_ranges_are_rejected_with_codes(first, last, codes):
    with pytest.raises(ObservationRequestInvalid) as caught:
        summary_sessions(first, last, AS_OF)
    assert caught.value.codes == codes
```

- [ ] **Step 2: Run the window tests to verify they fail**

Run: `uv run pytest -p no:cacheprovider -o addopts="" -q tests/readmodels/test_observation_window.py`
Expected: FAIL com `ModuleNotFoundError: No module named 'virtual_orders.readmodels.observation_window'`.

- [ ] **Step 3: Implement the window module**

`src/virtual_orders/readmodels/observation_window.py`:

```python
"""The session window of the daily observation report (Plan 4, D61, D63): NYSE sessions only, never a provider."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from core.domain.calendar import Session
from virtual_orders.marketdata.calendars import calendar_for_window

MARKET_TZ = ZoneInfo("America/New_York")
MAX_SUMMARY_DAYS = 45
NOT_A_SESSION = "NOT_A_SESSION"
DAY_IN_FUTURE = "DAY_IN_FUTURE"
EMPTY_RANGE = "EMPTY_RANGE"
RANGE_TOO_LARGE = "RANGE_TOO_LARGE"
NO_SESSIONS = "NO_SESSIONS"


class ObservationRequestInvalid(Exception):
    """Fixed codes only (D63); never a caller-supplied value beyond an ISO date."""

    def __init__(self, codes: list[str]) -> None:
        super().__init__(", ".join(codes))
        self.codes = codes


@dataclass(frozen=True)
class ObservationWindow:
    session_day: date
    session_open_utc: datetime
    session_close_utc: datetime
    start: datetime  # 00:00 ET of the day after the previous NYSE session (D61)
    end: datetime  # 00:00 ET of the day after session_day
    as_of: datetime  # database clock at the request (acquire_data_as_of)

    @property
    def effective_end(self) -> datetime:
        return min(self.end, self.as_of)

    @property
    def complete(self) -> bool:
        return self.as_of >= self.end


def _midnight_et(day: date) -> datetime:
    return datetime.combine(day, time(0), tzinfo=MARKET_TZ).astimezone(UTC)


def _probe(day: date) -> datetime:
    return datetime.combine(day, time(12), tzinfo=UTC)


def _sessions(first: date, last: date) -> tuple[Session, ...]:
    return calendar_for_window(_probe(first), _probe(last)).sessions  # padded: one session before and after


def session_window(day: date, as_of: datetime) -> ObservationWindow:
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    codes: list[str] = []
    if day > as_of.astimezone(MARKET_TZ).date():
        codes.append(f"{DAY_IN_FUTURE}:{day.isoformat()}")
    sessions = _sessions(day, day)
    session = next((item for item in sessions if item.day == day), None)
    if session is None:
        codes.append(f"{NOT_A_SESSION}:{day.isoformat()}")
    if codes or session is None:
        raise ObservationRequestInvalid(codes)
    previous = max(item.day for item in sessions if item.day < day)
    return ObservationWindow(
        session_day=day, session_open_utc=session.open_utc, session_close_utc=session.close_utc,
        start=_midnight_et(previous + timedelta(days=1)), end=_midnight_et(day + timedelta(days=1)),
        as_of=as_of.astimezone(UTC),
    )


def summary_sessions(first: date, last: date, as_of: datetime) -> list[ObservationWindow]:
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    codes: list[str] = []
    if last < first:
        codes.append(EMPTY_RANGE)
    elif (last - first).days + 1 > MAX_SUMMARY_DAYS:
        codes.append(RANGE_TOO_LARGE)
    if last > as_of.astimezone(MARKET_TZ).date():
        codes.append(f"{DAY_IN_FUTURE}:{last.isoformat()}")
    if codes:
        raise ObservationRequestInvalid(codes)
    days = [item.day for item in _sessions(first, last) if first <= item.day <= last]
    if not days:
        raise ObservationRequestInvalid([NO_SESSIONS])
    return [session_window(day, as_of) for day in days]
```

- [ ] **Step 4: Run the window tests to verify they pass**

Run: `uv run pytest -p no:cacheprovider -o addopts="" -q tests/readmodels/test_observation_window.py`
Expected: PASS.

- [ ] **Step 5: Write the failing operational tests**

`tests/integration/observation_support.py`:

```python
"""Helpers for the observation read-model tests. Never a conftest (Plan 3B close-out entry 20)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import Engine

from tests.integration.support import CODE_VERSION
from virtual_orders.ledger.runs import RunKind, RunStatus, finish_run, start_run
from virtual_orders.marketdata.asof import acquire_data_as_of
from virtual_orders.readmodels.observation_window import ObservationWindow, session_window


def recorded_run(
    engine: Engine,
    kind: RunKind,
    *,
    start_detail: Mapping[str, Any],
    status: RunStatus | None,
    detail: Mapping[str, Any] | None = None,
) -> UUID:
    """A run as its writer records it: the start detail, then (unless `status` is None) one final status."""
    as_of = acquire_data_as_of(engine)
    with engine.begin() as conn:
        run = start_run(conn, kind, as_of, CODE_VERSION, detail=start_detail)
        if status is not None:
            finish_run(conn, run.run_id, status, detail)
    return run.run_id


def window_for(engine: Engine, day: date) -> ObservationWindow:
    return session_window(day, acquire_data_as_of(engine))
```

`tests/integration/test_observation_operations.py`:

```python
from datetime import date
from decimal import Decimal
from uuid import uuid4

from tests.integration.observation_support import recorded_run, window_for
from tests.integration.support import (
    CODE_VERSION,
    DAY,
    FakeBarSource,
    FakeReference,
    feeds,
    flat_raw,
    submit_default,
)
from tests.support import et
from virtual_orders.evaluator.quality import run_end_of_day
from virtual_orders.ledger.runs import RunKind, RunStatus
from virtual_orders.readmodels.observation_operations import (
    RUN_KIND_ORDER,
    ActionabilitySection,
    AlertDeliverySection,
    DataQualitySection,
    FeedFailures,
    HealthSection,
    RecheckSection,
    actionability,
    alert_deliveries,
    data_quality,
    health_transitions,
    provider_failures,
    recheck_activity,
    window_runs,
    worker_activity,
)
from virtual_orders.readmodels.observation_window import session_window
from virtual_orders.storage import tables

SESSION = date(2025, 11, 25)
NEXT = "2025-11-26"
COMPLETED, FAILED = RunStatus.COMPLETED, RunStatus.FAILED


def test_provider_failures_are_counted_by_run_kind_with_codes_and_never_messages(engine):
    for hm, failures in (
        ("10:00", {"fake_feed:AAPL": "timeout talking to https://secret-host"}),
        ("10:02", {"fake_feed:AAPL": "timeout", "fake_feed:MSFT": "UNKNOWN_DATA_SOURCE: nope"}),
        ("10:04", {"fake_feed:AAPL": "timeout"}),
    ):
        recorded_run(engine, RunKind.LIVE, start_detail={}, status=COMPLETED,
                     detail={"orders": 1, "ingest_failures": failures, "market_now": et(DAY, hm)})
    recorded_run(engine, RunKind.LIVE, start_detail={}, status=FAILED,
                 detail={"error": "OperationalError('password=hunter2')", "market_now": et(DAY, "10:06")})
    recorded_run(engine, RunKind.LIVE, start_detail={}, status=FAILED,  # no "Type(" prefix: never the text itself
                 detail={"error": "password=hunter2 host=db", "market_now": et(DAY, "10:07")})
    for hm, stored in (("10:08", "db.internal(host)"), ("10:09", "password(hunter2)")):  # dotted or lowercase: UNKNOWN
        recorded_run(engine, RunKind.LIVE, start_detail={}, status=FAILED,
                     detail={"error": stored, "market_now": et(DAY, hm)})
    recorded_run(engine, RunKind.LIVE, start_detail={}, status=COMPLETED,
                 detail={"ingest_failures": {"fake_feed:AAPL": "x"}, "market_now": et(NEXT, "10:00")})
    recorded_run(engine, RunKind.WATCHLIST, start_detail={"market_now": et(DAY, "10:00")}, status=COMPLETED,
                 detail={"ingest_failures": {"fake_feed:NVDA": "ERROR:KeyError"}, "market_now": et(DAY, "10:00")})
    recorded_run(engine, RunKind.OPENING, start_detail={"session_day": DAY}, status=COMPLETED,
                 detail={"session_day": DAY, "source_failures": {"fmp": "HTTP 429"}})
    recorded_run(engine, RunKind.END_OF_DAY, start_detail={"session_day": DAY}, status=COMPLETED,
                 detail={"session_day": DAY, "unavailable": {"AAPL:1m": "yfinance down"}, "not_evaluated": {}})

    with engine.connect() as conn:
        section = provider_failures(conn, window_for(engine, SESSION))

    by_kind = {item.run_kind: item for item in section.by_run_kind}
    assert list(by_kind) == ["LIVE", "WATCHLIST", "OPENING", "END_OF_DAY"]
    assert by_kind["LIVE"] == FeedFailures(
        run_kind="LIVE", runs=7, failed_runs=4, runs_with_failures=3, failures=4,
        codes={"SOURCE_ERROR": 3, "UNKNOWN_DATA_SOURCE": 1}, feeds=("fake_feed:AAPL", "fake_feed:MSFT"),
        feeds_total=2, failed_run_errors={"OperationalError": 1, "UNKNOWN": 3},
    )
    assert by_kind["WATCHLIST"].codes == {"UNEXPECTED_ERROR": 1}
    assert by_kind["OPENING"].feeds == ("fmp",) and by_kind["END_OF_DAY"].feeds == ("AAPL:1m",)
    assert section.total_failures == 7 and section.consecutive_live_max == 3
    for leaked in ("secret-host", "hunter2", "host=db", "password", "db.internal", "internal", "429"):
        assert leaked not in repr(section), leaked
    with engine.connect() as conn:  # the report assembly passes one scan of every run kind: same result
        window = window_for(engine, SESSION)
        assert provider_failures(conn, window, runs=window_runs(conn, window, RUN_KIND_ORDER)) == section


def test_actionability_answers_are_counted_with_the_503_causes(engine):
    base = {"signal_id": str(uuid4()), "ticker": "AAPL", "price_source": "fake_feed",
            "coverage_policy": "STRICT_PRIMARY_COVERAGE"}

    def click(day, hm, status, **final):
        recorded_run(engine, RunKind.ACTIONABILITY, start_detail={**base, "created_at": et(day, hm)}, status=status,
                     detail={**base, "reason": None, **final})

    click(DAY, "10:00", COMPLETED, result="ACTIONABLE", order_id=str(uuid4()))
    click(DAY, "10:01", FAILED, result="ACTIONABILITY_UNVERIFIABLE", ingest_error="SourceUnavailable: host")
    click(DAY, "10:02", FAILED, result="ACTIONABILITY_UNVERIFIABLE", missing_count=3, missing_minutes=[])
    click(DAY, "10:03", FAILED, result="ACTIONABILITY_UNVERIFIABLE", missing_count=1, missing_minutes=[],
          policy_contract_violation=True)
    click(DAY, "10:04", COMPLETED, result="SIGNAL_EXPIRED")
    click(DAY, "10:05", None)  # no final status as of the report
    click(NEXT, "10:00", FAILED, result="ACTIONABILITY_UNVERIFIABLE", ingest_error="x")

    with engine.connect() as conn:
        section = actionability(conn, window_for(engine, SESSION))

    assert section == ActionabilitySection(
        requests=6, results={"ACTIONABILITY_UNVERIFIABLE": 3, "ACTIONABLE": 1, "SIGNAL_EXPIRED": 1}, unverifiable=3,
        unverifiable_causes={"MISSING_MINUTES": 1, "POLICY_CONTRACT_VIOLATION": 1, "PROVIDER_FAILURE": 1},
        unverifiable_rate=Decimal("0.5000"), unfinished=1,
    )


def test_missing_bars_gaps_and_unevaluated_orders_come_from_the_session_quality(engine):
    submit_default(engine)
    submit_default(engine, client_signal_id="msft", ticker="MSFT")
    source = FakeBarSource([b for b in flat_raw(DAY, "09:30", "16:00", 105)
                            if not et(DAY, "10:10") <= b.ts < et(DAY, "10:45")])
    source.failing.add("MSFT")
    run_end_of_day(engine, gateway=feeds(source), reference=FakeReference(), session_day=SESSION,
                   code_version=CODE_VERSION, market_now=et(DAY, "16:30"))

    with engine.connect() as conn:
        section = data_quality(conn, window_for(engine, SESSION))
        next_day = data_quality(conn, window_for(engine, date(2025, 11, 26)))

    assert section == DataQualitySection(
        orders_measured=1, expected_bars=390, missing_bars=35, coverage_pct=Decimal("91.03"), orders_with_missing=1,
        gaps=1, gap_minutes=35, not_evaluated={"PROVIDER_FAILURE": 1},
    )
    assert (next_day.orders_measured, next_day.gaps, next_day.not_evaluated) == (0, 0, {})


def test_recheck_rows_are_attributed_to_the_recheck_runs_of_the_window(engine):
    order_id = submit_default(engine).auto_order_id
    in_window = recorded_run(
        engine, RunKind.QUALITY_RECHECK, start_detail={"sessions": ["2025-11-21"], "market_now": et(DAY, "16:30")},
        status=COMPLETED, detail={"market_now": et(DAY, "16:30"),
                                  "not_evaluated": {f"{order_id}:2025-11-20": "PROVIDER_FAILURE",
                                                    f"{order_id}:2025-11-19": "ALREADY_RECHECKED"}},
    )
    later = recorded_run(engine, RunKind.QUALITY_RECHECK, start_detail={"market_now": et(NEXT, "16:30")},
                         status=COMPLETED, detail={"market_now": et(NEXT, "16:30"), "not_evaluated": {}})
    with engine.begin() as conn:
        for session_date, run_id, payload in (
            (date(2025, 11, 24), in_window, {"status": "EVALUATED"}),
            (date(2025, 11, 21), in_window,
             {"status": "PROVIDER_FAILURE_FINAL", "terminal_reason": "PROVIDER_FAILURE"}),
            (SESSION, later, {"status": "EVALUATED"}),
        ):
            conn.execute(tables.data_quality_rechecks.insert().values(
                order_id=order_id, session_date=session_date, recheck_key=f"DATA_QUALITY_RECHECK:{session_date}:x",
                run_id=run_id, source_run_id=run_id, data_as_of=et(DAY, "16:30"), payload=payload,
            ))

    with engine.connect() as conn:
        section = recheck_activity(conn, window_for(engine, SESSION))

    assert section == RecheckSection(
        runs=1, rows=2, statuses={"EVALUATED": 1, "PROVIDER_FAILURE_FINAL": 1},
        terminal_reasons={"PROVIDER_FAILURE": 1}, sessions=(date(2025, 11, 21), date(2025, 11, 24)),
        about_this_session={"EVALUATED": 1}, not_evaluated={"PROVIDER_FAILURE": 1}, skipped={"ALREADY_RECHECKED": 1},
    )


def test_alert_deliveries_are_counted_by_the_database_clock_inside_the_window(engine):
    with engine.begin() as conn:
        ids = {}
        for key, kind, created in (("A", "ORDER_EVENT", et(DAY, "10:00")), ("B", "HEALTH", et(DAY, "11:00")),
                                   ("C", "PRICE_CROSS", et(DAY, "12:00")), ("D", "HEALTH", et(NEXT, "10:00"))):
            ids[key] = conn.execute(tables.alert_outbox.insert().values(
                alert_key=key, kind=kind, document={"secret": "document-value"}, created_at=created,
            ).returning(tables.alert_outbox.c.id)).scalar_one()
        conn.execute(tables.alert_delivery_attempts.insert(), [
            {"alert_id": ids["A"], "outcome": "FAILED", "status_code": 502, "error_type": "AlertDeliveryFailed",
             "attempted_at": et(DAY, "10:01")},
            {"alert_id": ids["A"], "outcome": "DELIVERED", "status_code": 200, "error_type": None,
             "attempted_at": et(DAY, "10:03")},
            {"alert_id": ids["B"], "outcome": "FAILED", "status_code": None, "error_type": "ConnectTimeout",
             "attempted_at": et(DAY, "11:01")},
            {"alert_id": ids["B"], "outcome": "FAILED", "status_code": None, "error_type": "ConnectTimeout",
             "attempted_at": et(DAY, "11:03")},
            {"alert_id": ids["D"], "outcome": "EXPIRED", "status_code": None, "error_type": None,
             "attempted_at": et(NEXT, "11:00")},
        ])

    with engine.connect() as conn:
        section = alert_deliveries(conn, window_for(engine, SESSION))

    assert section == AlertDeliverySection(
        created={"HEALTH": 1, "ORDER_EVENT": 1, "PRICE_CROSS": 1}, attempts={"DELIVERED": 1, "FAILED": 3},
        failed_alerts=2, failure_types={"ConnectTimeout": 2, "HTTP_502": 1}, pending_at_end=2,
    )
    assert "document-value" not in repr(section)


def worker_row(conn, session_id, event, at, **fields):
    conn.execute(tables.worker_sessions.insert().values(session_id=session_id, event=event, recorded_at=at, **fields))


def test_worker_restarts_unclean_ends_and_open_sessions(engine):
    s0, s1, s2, s3, s4 = (uuid4() for _ in range(5))
    with engine.begin() as conn:
        worker_row(conn, s0, "STARTED", et("2025-11-24", "08:00"), code_version="sha-a")
        worker_row(conn, s0, "STOPPED", et("2025-11-24", "20:00"), exit_code=0, reason="SIGNAL")
        worker_row(conn, s1, "STARTED", et(DAY, "08:00"), code_version="sha-b")  # clean restart after s0
        worker_row(conn, s2, "STARTED", et(DAY, "12:00"), code_version="sha-b")  # s1 has no stop row: unclean
        worker_row(conn, s3, "STARTED", et(DAY, "13:05"), code_version="sha-b")  # took the lock s2 lost
        worker_row(conn, s2, "STOPPED", et(DAY, "13:06"), exit_code=3, reason="LOCK_LOST")  # after s3: still clean
        worker_row(conn, s4, "STARTED", et(NEXT, "08:00"), code_version="sha-c")  # s3 has no stop row

    with engine.connect() as conn:
        section = worker_activity(conn, window_for(engine, SESSION))
        first_ever = worker_activity(conn, window_for(engine, date(2025, 11, 24)))
        next_day = worker_activity(conn, window_for(engine, date(2025, 11, 26)))

    assert (section.starts, section.restarts, section.unclean_ends) == (3, 3, 1)
    assert (section.stops, section.exit_codes) == ({"LOCK_LOST": 1}, {"3": 1})
    # s3 has no stop row at the window end: open (running, or ended without a stop row), never a restart here
    assert section.code_versions == ("sha-b",) and section.open_session_at_end is True
    assert [(view.session_id, view.exit_code, view.reason) for view in section.sessions] == [
        (s1, None, None), (s2, 3, "LOCK_LOST"), (s3, None, None)]
    assert (first_ever.starts, first_ever.restarts, first_ever.unclean_ends) == (1, 0, 0)
    assert first_ever.stops == {"SIGNAL": 1} and first_ever.open_session_at_end is False
    # s3's unclean end is attributed to the window of the next start (s4); s4 itself is still open
    assert (next_day.starts, next_day.restarts, next_day.unclean_ends, next_day.open_session_at_end) == (1, 1, 1, True)
    assert next_day.stops == {} and next_day.code_versions == ("sha-c",)


def insert_health(conn, state, codes, at):
    conn.execute(tables.health_state_log.insert().values(state=state, cause_codes=codes, observed_at=at))


def test_health_transitions_and_time_in_each_state(engine):
    with engine.begin() as conn:
        insert_health(conn, "HEALTHY", [], et("2025-11-24", "15:00"))
        insert_health(conn, "DEGRADED", ["LIVE_CYCLE_STALE"], et(DAY, "10:00"))
        insert_health(conn, "DEGRADED", ["INGEST_FAILURES_CONSECUTIVE", "LIVE_CYCLE_STALE"], et(DAY, "10:30"))
        insert_health(conn, "HEALTHY", [], et(DAY, "11:00"))
        insert_health(conn, "DEGRADED", ["LIVE_CYCLE_STALE"], et(NEXT, "09:00"))

    with engine.connect() as conn:
        section = health_transitions(conn, window_for(engine, SESSION))
        early = health_transitions(conn, session_window(SESSION, et(DAY, "10:15")))  # as of 10:15 ET

    # Three log rows, two state changes: DEGRADED -> DEGRADED (cause codes only) is a log row, not a transition.
    assert section == HealthSection(
        state_at_start="HEALTHY", transitions=2, log_rows=3, entered={"DEGRADED": 1, "HEALTHY": 1},
        cause_codes={"INGEST_FAILURES_CONSECUTIVE": 1, "LIVE_CYCLE_STALE": 2},
        seconds_by_state={"DEGRADED": 3600, "HEALTHY": 82800}, state_at_end="HEALTHY",
    )
    assert (early.transitions, early.log_rows, early.seconds_by_state, early.state_at_end) == (
        1, 1, {"DEGRADED": 900, "HEALTHY": 36000}, "DEGRADED")
```

- [ ] **Step 6: Run the operational tests to verify they fail**

Run: `uv run pytest -p no:cacheprovider -o addopts="" -q tests/integration/test_observation_operations.py`
Expected: FAIL com `ModuleNotFoundError: No module named 'virtual_orders.readmodels.observation_operations'`.

- [ ] **Step 7: Implement the operational sections**

`src/virtual_orders/readmodels/observation_operations.py`:

```python
"""Operational sections of the daily observation report (Plan 4, D61, D62, D67, D68). Reads only, as of the report.

Stored failure messages and exception text never leave this module: they become fixed codes or type names (D19).
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, text

from virtual_orders.analytics.observation import (
    UNKNOWN_STATE,
    StateChange,
    coverage_pct,
    failure_code,
    ratio,
    seconds_by_state,
)
from virtual_orders.evaluator.manual import ACTIONABILITY_UNVERIFIABLE
from virtual_orders.readmodels.observation_window import ObservationWindow

MAX_LISTED_FEEDS = 50
# D67: where each run kind records its provider failures (feed key -> stored message).
FAILURE_MAPS: dict[str, tuple[str, ...]] = {
    "LIVE": ("ingest_failures",),
    "WATCHLIST": ("ingest_failures",),
    "OPENING": ("source_failures",),
    "END_OF_DAY": ("unavailable",),
    "QUALITY_RECHECK": ("ingest_failures", "unavailable"),
    "ACTIONABILITY": (),
}
RUN_KIND_ORDER = tuple(FAILURE_MAPS)
# D68: recheck skips that mean "already handled", never a data-quality problem (recheck.py `skip`).
RECHECK_SKIP_REASONS = frozenset({"ALREADY_RECHECKED", "ALREADY_EVALUATED"})
_ERROR_TYPE = re.compile(r"^[A-Z][A-Za-z0-9_]*(?=\()")  # a class name: uppercase first, no dots, then "("

_WINDOW_RUNS = text(
    """
    SELECT runs.run_id, runs.kind, runs.status, runs.detail, runs.instant
    FROM (
        SELECT r.run_id, r.kind, latest.status, latest.detail,
               (first.detail->>'session_day')::date AS session_day,
               COALESCE((latest.detail->>'market_now')::timestamptz, (first.detail->>'market_now')::timestamptz,
                        (first.detail->>'created_at')::timestamptz, r.started_at) AS instant
        FROM evaluation_runs r
        JOIN LATERAL (
            SELECT s.status, s.detail FROM evaluation_run_status s
            WHERE s.run_id = r.run_id AND s.recorded_at <= :as_of
            ORDER BY s.id DESC LIMIT 1
        ) latest ON true
        JOIN LATERAL (
            SELECT s.detail FROM evaluation_run_status s WHERE s.run_id = r.run_id ORDER BY s.id LIMIT 1
        ) first ON true
        WHERE r.kind = ANY(CAST(:kinds AS text[])) AND r.started_at <= :as_of
    ) runs
    WHERE CASE WHEN runs.kind IN ('OPENING', 'END_OF_DAY') THEN runs.session_day = :session_day
               ELSE runs.instant >= :start AND runs.instant < :end END
    ORDER BY runs.instant, runs.run_id
    """
)


@dataclass(frozen=True)
class RunFacts:
    run_id: UUID
    kind: str
    status: str
    detail: dict[str, Any]
    instant: datetime


def window_runs(conn: Connection, window: ObservationWindow, kinds: Sequence[str]) -> list[RunFacts]:
    """D61: runs attributed by their market instant (or session day), with the latest status recorded as of."""
    rows = conn.execute(_WINDOW_RUNS, {
        "kinds": list(kinds), "as_of": window.as_of, "start": window.start, "end": window.end,
        "session_day": window.session_day,
    })
    return [RunFacts(row.run_id, row.kind, row.status, dict(row.detail), row.instant) for row in rows]


def _runs_of(
    conn: Connection, window: ObservationWindow, kinds: Sequence[str], runs: Sequence[RunFacts] | None
) -> list[RunFacts]:
    """D69: the report passes one scan of every run kind; a section called on its own reads its kinds."""
    found = window_runs(conn, window, kinds) if runs is None else runs
    return [run for run in found if run.kind in kinds]


def _counts(counter: Counter[str]) -> dict[str, int]:
    return dict(sorted(counter.items()))


def _error_type(value: object) -> str:
    """`repr(exc)` -> the exception type name only (D19). Anything that does not start with `Type(` is UNKNOWN:
    the stored text itself never leaves this module, whatever a writer stored."""
    match = _ERROR_TYPE.match(str(value))
    return match.group(0) if match else "UNKNOWN"


# -- provider failures (D67) -------------------------------------------------------------------------------------

@dataclass(frozen=True)
class FeedFailures:
    run_kind: str
    runs: int
    failed_runs: int
    runs_with_failures: int
    failures: int
    codes: dict[str, int]
    feeds: tuple[str, ...]
    feeds_total: int
    failed_run_errors: dict[str, int]


@dataclass(frozen=True)
class ProviderFailures:
    by_run_kind: tuple[FeedFailures, ...]
    total_failures: int
    consecutive_live_max: int


def _failures(run: RunFacts) -> dict[str, str]:
    if run.kind == "ACTIONABILITY":
        raw = run.detail.get("ingest_error")
        if raw is None:
            return {}
        return {f"{run.detail.get('price_source')}:{run.detail.get('ticker')}": str(raw)}
    found: dict[str, str] = {}
    for key in FAILURE_MAPS.get(run.kind, ()):
        value = run.detail.get(key)
        if isinstance(value, Mapping):
            found.update({str(name): str(message) for name, message in value.items()})
    return found


def _longest_streak(failing_per_run: Sequence[set[str]]) -> int:
    best = 0
    streaks: dict[str, int] = {}
    for failing in failing_per_run:
        streaks = {feed: streaks.get(feed, 0) + 1 for feed in failing}
        best = max([best, *streaks.values()])
    return best


def provider_failures(
    conn: Connection, window: ObservationWindow, *, runs: Sequence[RunFacts] | None = None
) -> ProviderFailures:
    found_runs = _runs_of(conn, window, RUN_KIND_ORDER, runs)
    sections: list[FeedFailures] = []
    for kind in RUN_KIND_ORDER:
        of_kind = [run for run in found_runs if run.kind == kind]
        if not of_kind:
            continue
        found = [_failures(run) for run in of_kind]
        feeds = sorted({feed for failures in found for feed in failures})
        failed = [run for run in of_kind if run.status == "FAILED" and "error" in run.detail]
        sections.append(FeedFailures(
            run_kind=kind, runs=len(of_kind), failed_runs=len(failed),
            runs_with_failures=sum(1 for failures in found if failures),
            failures=sum(len(failures) for failures in found),
            codes=_counts(Counter(failure_code(message) for failures in found for message in failures.values())),
            feeds=tuple(feeds[:MAX_LISTED_FEEDS]), feeds_total=len(feeds),
            failed_run_errors=_counts(Counter(_error_type(run.detail["error"]) for run in failed)),
        ))
    # Descriptive: consecutive completed LIVE cycles inside this window only; not the /health rule (D67).
    live = [set(_failures(run)) for run in found_runs if run.kind == "LIVE" and run.status == "COMPLETED"]
    return ProviderFailures(tuple(sections), sum(item.failures for item in sections), _longest_streak(live))


# -- actionability (D68) -------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class ActionabilitySection:
    requests: int
    results: dict[str, int]
    unverifiable: int
    unverifiable_causes: dict[str, int]
    unverifiable_rate: Decimal | None
    unfinished: int


def _unverifiable_cause(detail: Mapping[str, Any]) -> str:
    if "ingest_error" in detail:
        return "PROVIDER_FAILURE"
    if detail.get("policy_contract_violation"):
        return "POLICY_CONTRACT_VIOLATION"
    return "MISSING_MINUTES"


def actionability(
    conn: Connection, window: ObservationWindow, *, runs: Sequence[RunFacts] | None = None
) -> ActionabilitySection:
    clicks = _runs_of(conn, window, ("ACTIONABILITY",), runs)
    finished = [run for run in clicks if run.status != "RUNNING"]
    unverifiable = [run for run in finished if run.detail.get("result") == ACTIONABILITY_UNVERIFIABLE]
    return ActionabilitySection(
        requests=len(clicks),
        results=_counts(Counter(str(run.detail.get("result")) for run in finished)),
        unverifiable=len(unverifiable),
        unverifiable_causes=_counts(Counter(_unverifiable_cause(run.detail) for run in unverifiable)),
        unverifiable_rate=ratio(len(unverifiable), len(clicks)),
        unfinished=len(clicks) - len(finished),
    )


# -- missing bars (D68) --------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class DataQualitySection:
    orders_measured: int
    expected_bars: int
    missing_bars: int
    coverage_pct: Decimal | None
    orders_with_missing: int
    gaps: int
    gap_minutes: int
    not_evaluated: dict[str, int]


_SESSION_QUALITY = text(
    """
    SELECT COUNT(DISTINCT e.order_id) AS orders,
           COALESCE(SUM((e.payload->>'expected_bars')::int), 0) AS expected_bars,
           COALESCE(SUM((e.payload->>'missing_bars')::int), 0) AS missing_bars,
           COUNT(*) FILTER (WHERE (e.payload->>'missing_bars')::int > 0) AS orders_with_missing
    FROM order_events e
    JOIN orders o ON o.id = e.order_id
    WHERE NOT o.replay AND e.type = 'DATA_QUALITY' AND e.event_key = :event_key AND e.recorded_at <= :as_of
    """
)

_SESSION_GAPS = text(
    """
    SELECT COUNT(*) AS gaps, COALESCE(SUM((e.payload->>'minutes')::int), 0) AS minutes
    FROM order_events e
    JOIN orders o ON o.id = e.order_id
    WHERE NOT o.replay AND e.type = 'DATA_GAP' AND e.recorded_at <= :as_of
      AND (e.payload->>'gap_start_ts')::timestamptz >= :open
      AND (e.payload->>'gap_start_ts')::timestamptz < :close
    """
)


def data_quality(
    conn: Connection, window: ObservationWindow, *, runs: Sequence[RunFacts] | None = None
) -> DataQualitySection:
    quality = conn.execute(_SESSION_QUALITY, {
        "event_key": f"DATA_QUALITY:{window.session_day.isoformat()}", "as_of": window.as_of,
    }).one()
    gaps = conn.execute(_SESSION_GAPS, {
        "as_of": window.as_of, "open": window.session_open_utc, "close": window.session_close_utc,
    }).one()
    completed = [run for run in _runs_of(conn, window, ("END_OF_DAY",), runs) if run.status == "COMPLETED"]
    pending = completed[-1].detail.get("not_evaluated") if completed else None  # the latest run supersedes (D12)
    reasons = Counter(str(reason) for reason in pending.values()) if isinstance(pending, Mapping) else Counter()
    expected, missing = int(quality.expected_bars), int(quality.missing_bars)
    return DataQualitySection(
        orders_measured=int(quality.orders), expected_bars=expected, missing_bars=missing,
        coverage_pct=coverage_pct(expected, missing), orders_with_missing=int(quality.orders_with_missing),
        gaps=int(gaps.gaps), gap_minutes=int(gaps.minutes), not_evaluated=_counts(reasons),
    )


# -- DATA_QUALITY_RECHECK (D68) ------------------------------------------------------------------------------------

@dataclass(frozen=True)
class RecheckSection:
    runs: int
    rows: int
    statuses: dict[str, int]
    terminal_reasons: dict[str, int]
    sessions: tuple[date, ...]
    about_this_session: dict[str, int]
    not_evaluated: dict[str, int]
    skipped: dict[str, int]


_RECHECK_ROWS = text(
    """
    SELECT q.run_id, q.session_date, q.payload
    FROM data_quality_rechecks q
    WHERE q.recorded_at <= :as_of AND (q.run_id = ANY(CAST(:run_ids AS uuid[])) OR q.session_date = :session_day)
    """
)


def recheck_activity(
    conn: Connection, window: ObservationWindow, *, runs: Sequence[RunFacts] | None = None
) -> RecheckSection:
    recheck_runs = _runs_of(conn, window, ("QUALITY_RECHECK",), runs)
    run_ids = {run.run_id for run in recheck_runs}
    rows = conn.execute(_RECHECK_ROWS, {
        "as_of": window.as_of, "run_ids": list(run_ids), "session_day": window.session_day,
    }).all()
    mine = [row for row in rows if row.run_id in run_ids]
    not_evaluated: Counter[str] = Counter()
    skipped: Counter[str] = Counter()
    for run in recheck_runs:
        pending = run.detail.get("not_evaluated")
        if isinstance(pending, Mapping):
            for reason in (str(value) for value in pending.values()):
                (skipped if reason in RECHECK_SKIP_REASONS else not_evaluated)[reason] += 1
    return RecheckSection(
        runs=len(recheck_runs), rows=len(mine),
        statuses=_counts(Counter(str(row.payload.get("status")) for row in mine)),
        terminal_reasons=_counts(Counter(str(row.payload["terminal_reason"]) for row in mine
                                         if row.payload.get("terminal_reason") is not None)),
        sessions=tuple(sorted({row.session_date for row in mine})),
        # Rows about this session also count in `rows` of the window whose run recorded them: never sum the two.
        about_this_session=_counts(Counter(str(row.payload.get("status")) for row in rows
                                           if row.session_date == window.session_day)),
        not_evaluated=_counts(not_evaluated), skipped=_counts(skipped),
    )


# -- alert delivery (D68) ------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class AlertDeliverySection:
    created: dict[str, int]
    attempts: dict[str, int]
    failed_alerts: int
    failure_types: dict[str, int]
    pending_at_end: int


_ALERTS_CREATED = text(
    """
    SELECT kind, COUNT(*) AS alerts FROM alert_outbox
    WHERE created_at >= :start AND created_at < :end AND created_at <= :as_of
    GROUP BY kind
    """
)

_ATTEMPTS = text(
    """
    SELECT alert_id, outcome, status_code, error_type FROM alert_delivery_attempts
    WHERE attempted_at >= :start AND attempted_at < :end AND attempted_at <= :as_of
    """
)

_PENDING_AT_END = text(
    """
    SELECT COUNT(*) FROM alert_outbox a
    WHERE a.created_at >= :start AND a.created_at < :end AND a.created_at <= :as_of
      AND NOT EXISTS (
          SELECT 1 FROM alert_delivery_attempts t
          WHERE t.alert_id = a.id AND t.outcome IN ('DELIVERED', 'EXPIRED')
            AND t.attempted_at < :end AND t.attempted_at <= :as_of
      )
    """
)


def alert_deliveries(conn: Connection, window: ObservationWindow) -> AlertDeliverySection:
    params = {"start": window.start, "end": window.end, "as_of": window.as_of}
    attempts = conn.execute(_ATTEMPTS, params).all()
    failed = [row for row in attempts if row.outcome == "FAILED"]
    return AlertDeliverySection(
        created=_counts(Counter({row.kind: int(row.alerts) for row in conn.execute(_ALERTS_CREATED, params)})),
        attempts=_counts(Counter(row.outcome for row in attempts)),
        failed_alerts=len({row.alert_id for row in failed}),
        failure_types=_counts(Counter(
            f"HTTP_{row.status_code}" if row.status_code is not None else str(row.error_type or "UNKNOWN")
            for row in failed
        )),
        pending_at_end=int(conn.execute(_PENDING_AT_END, params).scalar_one()),
    )


# -- worker sessions (D62) -----------------------------------------------------------------------------------------

@dataclass(frozen=True)
class WorkerSessionView:
    session_id: UUID
    started_at: datetime
    code_version: str
    stopped_at: datetime | None
    exit_code: int | None
    reason: str | None


@dataclass(frozen=True)
class WorkerSection:
    starts: int
    restarts: int
    unclean_ends: int
    stops: dict[str, int]
    exit_codes: dict[str, int]
    code_versions: tuple[str, ...]
    open_session_at_end: bool  # no stop row yet at the window end: running, or ended without a stop row
    sessions: tuple[WorkerSessionView, ...]


_STARTS = text(
    """
    SELECT s.session_id, s.recorded_at, s.code_version FROM worker_sessions s
    WHERE s.event = 'STARTED' AND s.recorded_at < :end AND s.recorded_at <= :as_of
      AND s.recorded_at >= COALESCE((
          SELECT max(p.recorded_at) FROM worker_sessions p
          WHERE p.event = 'STARTED' AND p.recorded_at < :start AND p.recorded_at <= :as_of
      ), :start)
    ORDER BY s.recorded_at, s.id
    """
)

_STOPS = text(
    """
    SELECT session_id, recorded_at, exit_code, reason FROM worker_sessions
    WHERE event = 'STOPPED' AND recorded_at <= :as_of AND session_id = ANY(CAST(:ids AS uuid[]))
    """
)


def worker_activity(conn: Connection, window: ObservationWindow) -> WorkerSection:
    """D62: a session without a stop row is open. An unclean end is only detectable when the NEXT start appears and
    belongs to the window of that start; a still-open session with no later start is open, never a restart."""
    params = {"start": window.start, "end": window.end, "as_of": window.as_of}
    starts = conn.execute(_STARTS, params).all()
    stops = {row.session_id: row for row in conn.execute(_STOPS, {
        "as_of": window.as_of, "ids": [row.session_id for row in starts]})}
    restarts = unclean = 0
    for index, row in enumerate(starts):
        if row.recorded_at < window.start or index == 0:
            continue  # the session in force at the start, or the first session ever recorded
        restarts += 1
        before = stops.get(starts[index - 1].session_id)
        if before is None:
            unclean += 1  # the previous process ended without a stop row: killed, crashed or host lost
        elif before.recorded_at > row.recorded_at and before.reason != "LOCK_LOST":
            unclean += 1  # a stop written after the next start, other than a successor taking a lost lock
    inside = [row for row in starts if row.recorded_at >= window.start]
    window_stops = [stop for stop in stops.values() if window.start <= stop.recorded_at < window.end]
    last_stop = stops.get(starts[-1].session_id) if starts else None
    return WorkerSection(
        starts=len(inside), restarts=restarts, unclean_ends=unclean,
        stops=_counts(Counter(str(stop.reason) for stop in window_stops)),
        exit_codes=_counts(Counter(str(stop.exit_code) for stop in window_stops)),
        code_versions=tuple(sorted({row.code_version for row in inside})),
        open_session_at_end=bool(starts) and (last_stop is None or last_stop.recorded_at >= window.effective_end),
        sessions=tuple(
            WorkerSessionView(
                row.session_id, row.recorded_at, row.code_version,
                None if stops.get(row.session_id) is None else stops[row.session_id].recorded_at,
                None if stops.get(row.session_id) is None else stops[row.session_id].exit_code,
                None if stops.get(row.session_id) is None else stops[row.session_id].reason,
            )
            for row in inside
        ),
    )


# -- health transitions (D68) --------------------------------------------------------------------------------------

@dataclass(frozen=True)
class HealthSection:
    state_at_start: str
    transitions: int  # state changes: sum(entered.values())
    log_rows: int  # health_state_log rows in the window, including cause-code-only changes
    entered: dict[str, int]
    cause_codes: dict[str, int]
    seconds_by_state: dict[str, int]
    state_at_end: str


_HEALTH_BEFORE = text(
    """
    SELECT state FROM health_state_log
    WHERE observed_at < :start AND observed_at <= :as_of
    ORDER BY observed_at DESC, id DESC LIMIT 1
    """
)

_HEALTH_ROWS = text(
    """
    SELECT state, cause_codes, observed_at FROM health_state_log
    WHERE observed_at >= :start AND observed_at < :stop
    ORDER BY observed_at, id
    """
)


def health_transitions(conn: Connection, window: ObservationWindow) -> HealthSection:
    before = conn.execute(_HEALTH_BEFORE, {"start": window.start, "as_of": window.as_of}).scalar_one_or_none()
    rows = conn.execute(_HEALTH_ROWS, {"start": window.start, "stop": window.effective_end}).all()
    initial = before or UNKNOWN_STATE
    entered: Counter[str] = Counter()
    current = initial
    for row in rows:
        if row.state != current:
            entered[row.state] += 1
        current = row.state
    return HealthSection(
        state_at_start=initial, transitions=sum(entered.values()), log_rows=len(rows), entered=_counts(entered),
        cause_codes=_counts(Counter(code for row in rows for code in row.cause_codes)),
        seconds_by_state=seconds_by_state(initial, [StateChange(row.observed_at, row.state) for row in rows],
                                          window.start, window.effective_end),
        state_at_end=current,
    )
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `uv run pytest -p no:cacheprovider -o addopts="" -q tests/readmodels/test_observation_window.py tests/integration/test_observation_operations.py tests/test_import_boundaries.py && uv run ruff check src tests migrations && uv run mypy`
Expected: PASS (as fronteiras neutras já cobrem os novos arquivos de `readmodels/`); ruff e mypy limpos.

- [ ] **Step 9: Commit**

```bash
git add src/virtual_orders/readmodels/observation_window.py src/virtual_orders/readmodels/observation_operations.py \
  tests/readmodels/test_observation_window.py tests/integration/observation_support.py \
  tests/integration/test_observation_operations.py
git commit -m "feat(readmodels): observation window and operational sections as of the report"
```

---

### Task 5: Read model — trades, MFE/MAE, latência sinal → fill e pressão × resultado (D64–D66)

**Files:**
- Create: `src/virtual_orders/readmodels/observation_trades.py`
- Create: `tests/integration/test_observation_trades.py`

**Interfaces:**
- Consumes: `analytics.observation` (Task 3, inclusive `MIN_TRADES_FOR_BUCKET_MEAN`); `analytics.pressure.DISCLAIMER`, `METHOD`, `PressureEstimate`, `estimate_pressure`; `core.domain.models.Bar`; `marketdata.asof.floor_minute`; `readmodels.market.INSUFFICIENT_BARS`, `NO_BARS`, `market_day_start`; `ObservationWindow` (Task 4).
- Produces:
  - `OBSERVATION_PRESSURE_WINDOW_BARS = 30`, `CLOSING_EVENT_TYPES`;
  - `PressureBefore(estimate: PressureEstimate | None, unavailable_reason: str | None, window_start: datetime | None, window_end: datetime | None, spans_sessions: bool | None)`;
  - `pressure_before(conn, *, ticker: str, price_source: str, decided_at: datetime, as_of: datetime) -> PressureBefore` (últimos 30 candles as-of antes do minuto do sinal, atravessando pregões);
  - `TradeRow(order_id, ticker, direction, origin, strategy, opened_at, closed_at, r_multiple, mfe_r, mae_r, needs_review, pressure_alignment: Alignment, pressure_strength: Strength | None, pressure_cmf: Decimal | None, pressure_unavailable_reason: str | None, pressure_window_start: datetime | None, pressure_window_end: datetime | None, pressure_spans_sessions: bool | None)`;
  - `TradesSection(created, filled, closed, excluded_needs_review, stats: TradeStats, stats_by_origin: dict[str, TradeStats], replay_closed, rows: tuple[TradeRow, ...])`;
  - `FillLatencyRow(order_id, ticker, origin, signal_created_at, fill_bar_ts, recorded_at, bar_latency_seconds, recorded_latency_seconds)`;
  - `LatencySection(bar: LatencyStats, recorded: LatencyStats, bar_by_origin: dict[str, LatencyStats], unfilled: dict[str, int], rows: tuple[FillLatencyRow, ...])`;
  - `PressureResultSection(estimate: bool, method, disclaimer, association_note, window_bars, cmf_threshold, strong_cmf, min_trades_for_mean: int, buckets: tuple[PressureBucket, ...], unavailable_reasons: dict[str, int])`;
  - `TradeFacts(trades, latency, pressure)`; `trade_sections(conn, window) -> TradeFacts`;
  - `pressure_section(rows: list[TradeRow]) -> PressureResultSection` e `latency_section(fills: list[FillLatencyRow], unfilled: dict[str, int]) -> LatencySection` (reusadas pelo resumo da Task 6).

- [ ] **Step 1: Write the failing tests**

`tests/integration/test_observation_trades.py`:

```python
from datetime import date
from decimal import Decimal

from sqlalchemy import select

from core.domain.models import FillConfig
from tests.integration.observation_support import window_for
from tests.integration.support import (
    CODE_VERSION,
    DAY,
    PRICE_SOURCE,
    TICKER,
    FakeBarSource,
    FakeReference,
    backdated_batch,
    feeds,
    flat_raw,
    scenario_bars,
    signal_body,
    submit_default,
)
from tests.support import et
from virtual_orders.analytics.observation import Alignment, LatencyStats, PressureBucket, Strength, whole_seconds
from virtual_orders.analytics.pressure import DISCLAIMER, METHOD
from virtual_orders.evaluator.commands import flag_order_review
from virtual_orders.evaluator.cycle import run_live_cycle
from virtual_orders.evaluator.quality import run_end_of_day
from virtual_orders.evaluator.signals import submit_signal
from virtual_orders.marketdata.asof import acquire_data_as_of
from virtual_orders.readmodels.observation_trades import PressureBefore, pressure_before, trade_sections
from virtual_orders.storage import tables

SESSION = date(2025, 11, 25)
NEXT = "2025-11-26"


def projection(engine, order_id):
    with engine.connect() as conn:
        return conn.execute(select(tables.order_state).where(tables.order_state.c.order_id == order_id)).one()


def test_trades_latency_and_unfilled_orders_of_a_session(engine):
    source = FakeBarSource(scenario_bars() + flat_raw(DAY, "09:30", "16:00", 105, "MSFT"))
    kept = submit_default(engine).auto_order_id
    reviewed = submit_default(engine, client_signal_id="rex-2").auto_order_id
    submit_default(engine, client_signal_id="msft-1", ticker="MSFT", valid_sessions=1)  # never fills: expires
    submit_default(engine, client_signal_id="msft-3", ticker="MSFT")  # never fills: still waiting
    for hm in ("10:30", "11:30", "13:00"):
        run_live_cycle(engine, feeds(source), code_version=CODE_VERSION, market_now=et(DAY, hm))
    run_end_of_day(engine, gateway=feeds(source), reference=FakeReference(), session_day=SESSION,
                   code_version=CODE_VERSION, market_now=et(DAY, "16:30"))
    flag_order_review(engine, reviewed, reason="MANUAL", ref=DAY)

    with engine.connect() as conn:
        facts = trade_sections(conn, window_for(engine, SESSION))

    trades, latency, pressure = facts.trades, facts.latency, facts.pressure
    assert (trades.created, trades.filled, trades.closed, trades.excluded_needs_review, trades.replay_closed) == (
        {"AUTO_STRATEGY": 4}, 2, 2, 1, 0)
    assert [(row.order_id, row.needs_review) for row in trades.rows] == sorted(
        [(kept, False), (reviewed, True)], key=lambda item: str(item[0]))
    state = projection(engine, kept)
    row = next(item for item in trades.rows if item.order_id == kept)
    assert (row.ticker, row.direction, row.origin, row.closed_at) == (TICKER, "LONG", "AUTO_STRATEGY", et(DAY, "12:50"))
    assert (row.r_multiple, row.mfe_r, row.mae_r) == (state.r_multiple, state.mfe_r, state.mae_r)
    assert (trades.stats.trades, trades.stats.wins, trades.stats.sum_r) == (1, 1, Decimal("1.7500"))
    assert trades.stats.mean_mfe_r == state.mfe_r.quantize(Decimal("0.0001"))
    assert list(trades.stats_by_origin) == ["AUTO_STRATEGY"]

    assert latency.bar == LatencyStats(count=2, median_seconds=3900, p90_seconds=3900, max_seconds=3900)
    assert latency.bar_by_origin == {"AUTO_STRATEGY": latency.bar}
    assert latency.unfilled == {"EXPIRED": 1, "NOT_FILLED_YET": 1}
    fill = latency.rows[0]
    assert (fill.signal_created_at, fill.fill_bar_ts, fill.bar_latency_seconds) == (
        et(DAY, "09:00"), et(DAY, "10:05"), 3900)
    assert fill.recorded_latency_seconds == whole_seconds(fill.signal_created_at, fill.recorded_at)
    assert latency.recorded.count == 2

    assert (pressure.estimate, pressure.method, pressure.disclaimer) == (True, METHOD, DISCLAIMER)
    assert pressure.buckets == (  # one trade: no mean below MIN_TRADES_FOR_BUCKET_MEAN
        PressureBucket(Alignment.UNAVAILABLE, None, 1, 1, Decimal("1.7500"), None),)
    assert pressure.min_trades_for_mean == 5
    assert pressure.unavailable_reasons == {"NO_BARS": 1}  # the 09:00 signal had no stored bar before it
    assert row.pressure_unavailable_reason == "NO_BARS" and row.pressure_cmf is None
    assert (row.pressure_window_start, row.pressure_window_end, row.pressure_spans_sessions) == (None, None, None)


def test_pressure_is_recomputed_from_bars_before_the_signal_and_paired_with_the_result(engine):
    backdated_batch(engine, TICKER, scenario_bars(), ingested_at=et(DAY, "13:00"))  # the previous session, 09:30-12:50
    backdated_batch(engine, "MSFT", flat_raw(DAY, "15:50", "16:00", 50, "MSFT"), ingested_at=et(DAY, "16:05"))
    source = FakeBarSource(scenario_bars(NEXT))
    order_id = submit_signal(engine, signal_body(client_signal_id="next-day"), config=FillConfig(),
                             code_version=CODE_VERSION, price_source=PRICE_SOURCE, now=et(NEXT, "09:00")).auto_order_id
    for hm in ("10:30", "11:30", "13:00"):
        run_live_cycle(engine, feeds(source), code_version=CODE_VERSION, market_now=et(NEXT, hm))

    as_of = acquire_data_as_of(engine)
    with engine.connect() as conn:
        facts = trade_sections(conn, window_for(engine, date(2025, 11, 26)))
        insufficient = pressure_before(conn, ticker="MSFT", price_source=PRICE_SOURCE, decided_at=et(NEXT, "09:00"),
                                       as_of=as_of)
        nothing = pressure_before(conn, ticker="NVDA", price_source=PRICE_SOURCE, decided_at=et(NEXT, "09:00"),
                                  as_of=as_of)

    (row,) = facts.trades.rows
    # Last 30 stored bars before 09:00 ET: 12:21-12:50 of the previous session. 29 flat bars and the 12:50 bar
    # (H 110.5, L 108.8, C 110): CMF = 0.4118 * 1000 / 30000 = 0.0137, below 0.05 -> no strong side.
    assert (row.order_id, row.pressure_cmf, row.pressure_alignment, row.pressure_strength) == (
        order_id, Decimal("0.0137"), Alignment.NEUTRAL, Strength.WEAK)
    assert (row.pressure_window_start, row.pressure_window_end, row.pressure_spans_sessions) == (
        et(DAY, "12:21"), et(DAY, "12:50"), False)
    assert facts.pressure.buckets == (
        PressureBucket(Alignment.NEUTRAL, Strength.WEAK, 1, 1, Decimal("1.7500"), None),)
    assert facts.pressure.unavailable_reasons == {}
    # MSFT has only 10 stored bars in its whole history as of the report; NVDA has none.
    assert insufficient == PressureBefore(None, "INSUFFICIENT_BARS", None, None, None)
    assert nothing == PressureBefore(None, "NO_BARS", None, None, None)


def test_an_early_session_signal_reads_the_last_bars_of_the_previous_session(engine):
    backdated_batch(engine, TICKER, scenario_bars(), ingested_at=et(DAY, "13:00"))  # 09:30-12:50 of the previous session
    backdated_batch(engine, TICKER, flat_raw(NEXT, "09:30", "09:45", 107), ingested_at=et(NEXT, "09:45"))  # 15 bars
    as_of = acquire_data_as_of(engine)

    with engine.connect() as conn:
        early = pressure_before(conn, ticker=TICKER, price_source=PRICE_SOURCE, decided_at=et(NEXT, "09:45", 30),
                                as_of=as_of)
        before_open_bars = pressure_before(conn, ticker=TICKER, price_source=PRICE_SOURCE,
                                           decided_at=et(NEXT, "09:45", 30), as_of=et(NEXT, "09:40"))

    # D66: a 09:45 ET signal is never INSUFFICIENT_BARS by construction. The last 30 bars before 09:45 are
    # 12:36-12:50 of the previous session and 09:30-09:44 of this one.
    assert early.unavailable_reason is None and early.estimate is not None and early.estimate.bars == 30
    assert (early.window_start, early.window_end, early.spans_sessions) == (et(DAY, "12:36"), et(NEXT, "09:44"), True)
    assert (early.estimate.first_bar_ts, early.estimate.last_bar_ts) == (early.window_start, early.window_end)
    # As of 09:40 the 09:30-09:44 batch was not ingested yet: only the previous session's bars count.
    assert (before_open_bars.window_start, before_open_bars.window_end, before_open_bars.spans_sessions) == (
        et(DAY, "12:21"), et(DAY, "12:50"), False)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest -p no:cacheprovider -o addopts="" -q tests/integration/test_observation_trades.py`
Expected: FAIL com `ModuleNotFoundError: No module named 'virtual_orders.readmodels.observation_trades'`.

- [ ] **Step 3: Implement the trade sections**

`src/virtual_orders/readmodels/observation_trades.py`:

```python
"""Trade sections of the daily observation report (Plan 4, D64-D66): virtual trades, MFE/MAE, signal -> fill latency
and a pressure ESTIMATE recomputed from stored bars before each signal. Reads only; the estimate is never stored and
never feeds any evaluation."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Connection, text

from core.domain.models import Bar
from virtual_orders.analytics.observation import (
    ASSOCIATION_NOTE,
    MIN_TRADES_FOR_BUCKET_MEAN,
    OBSERVATION_CMF_THRESHOLD,
    STRONG_CMF,
    Alignment,
    LatencyStats,
    PressureBucket,
    Strength,
    TradeStats,
    classify_pressure,
    latency_stats,
    pressure_buckets,
    trade_stats,
    whole_seconds,
)
from virtual_orders.analytics.pressure import DISCLAIMER, METHOD, PressureEstimate, estimate_pressure
from virtual_orders.marketdata.asof import floor_minute
from virtual_orders.readmodels.market import INSUFFICIENT_BARS, NO_BARS, market_day_start
from virtual_orders.readmodels.observation_window import ObservationWindow

OBSERVATION_PRESSURE_WINDOW_BARS = 30  # the /market/pressure default (D44); a labelled convention (D66)
CLOSING_EVENT_TYPES = ("TARGET1_HIT", "TARGET2_HIT", "STOPPED", "TIME_EXIT")

# D66: the last :bars stored minutes strictly before the signal minute, across sessions, each at the version known as
# of the report (the same version rule as read_bars_as_of: latest ingested_at, then batch_id).
_LAST_BARS_BEFORE = text(
    """
    SELECT DISTINCT ON (b.ts) b.ts, b.open, b.high, b.low, b.close, b.volume, b.batch_id
    FROM bars_1m b
    JOIN bar_batches bb ON bb.batch_id = b.batch_id
    WHERE b.ticker = :ticker AND b.source = :source AND b.ts < :cutoff AND bb.ingested_at <= :as_of
    ORDER BY b.ts DESC, bb.ingested_at DESC, b.batch_id DESC
    LIMIT :bars
    """
)

_CREATED = text(
    """
    SELECT o.origin, COUNT(*) AS orders FROM orders o
    WHERE NOT o.replay AND o.created_at >= :start AND o.created_at < :end
      AND EXISTS (SELECT 1 FROM order_events e
                  WHERE e.order_id = o.id AND e.type = 'ORDER_CREATED' AND e.recorded_at <= :as_of)
    GROUP BY o.origin
    """
)

_FILLS = text(
    """
    SELECT o.id AS order_id, o.origin, g.ticker, g.created_at AS signal_created_at, e.bar_ts, e.recorded_at
    FROM order_events e
    JOIN orders o ON o.id = e.order_id
    JOIN signals g ON g.id = o.signal_id
    WHERE NOT o.replay AND e.type = 'FILLED' AND e.bar_ts >= :start AND e.bar_ts < :end AND e.recorded_at <= :as_of
    ORDER BY e.bar_ts, o.origin, o.id
    """
)

_UNFILLED = text(
    """
    SELECT COALESCE(outcome.type, 'NOT_FILLED_YET') AS bucket, COUNT(*) AS orders
    FROM orders o
    LEFT JOIN LATERAL (
        SELECT e.type FROM order_events e
        WHERE e.order_id = o.id AND e.type IN ('EXPIRED', 'INVALIDATED', 'CANCELED') AND e.recorded_at <= :as_of
        ORDER BY e.seq LIMIT 1
    ) outcome ON true
    WHERE NOT o.replay AND o.created_at >= :start AND o.created_at < :end
      AND EXISTS (SELECT 1 FROM order_events c
                  WHERE c.order_id = o.id AND c.type = 'ORDER_CREATED' AND c.recorded_at <= :as_of)
      AND NOT EXISTS (SELECT 1 FROM order_events f
                      WHERE f.order_id = o.id AND f.type = 'FILLED' AND f.recorded_at <= :as_of)
    GROUP BY 1
    """
)

_CLOSED = text(
    """
    SELECT o.id AS order_id, o.origin, o.replay, o.price_source, g.ticker, g.direction, g.strategy,
           g.created_at AS signal_created_at, st.opened_at, st.closed_at, st.r_multiple, st.mfe_r, st.mae_r,
           st.needs_review
    FROM orders o
    JOIN signals g ON g.id = o.signal_id
    JOIN order_state st ON st.order_id = o.id
    WHERE st.status = 'CLOSED' AND st.closed_at >= :start AND st.closed_at < :end
      AND EXISTS (
          SELECT 1 FROM order_events e
          WHERE e.order_id = o.id AND e.bar_ts = st.closed_at AND e.recorded_at <= :as_of
            AND e.type = ANY(CAST(:closing AS text[]))
      )
    ORDER BY st.closed_at, o.origin, o.id
    """
)


@dataclass(frozen=True)
class PressureBefore:
    estimate: PressureEstimate | None
    unavailable_reason: str | None
    window_start: datetime | None  # first bar used; None without an estimate
    window_end: datetime | None  # last bar used (strictly before the signal minute)
    spans_sessions: bool | None  # the bars come from more than one ET date (session)


def pressure_before(
    conn: Connection, *, ticker: str, price_source: str, decided_at: datetime, as_of: datetime
) -> PressureBefore:
    """D66: the last 30 stored bars strictly before the signal's decision minute, across sessions, as of the report.

    Never truncated at the session open, so a 09:45 ET signal reads the previous session's last bars too.
    INSUFFICIENT_BARS only when fewer than 30 bars exist at all as of the report; NO_BARS when there is none."""
    rows = conn.execute(_LAST_BARS_BEFORE, {
        "ticker": ticker, "source": price_source, "cutoff": floor_minute(decided_at), "as_of": as_of,
        "bars": OBSERVATION_PRESSURE_WINDOW_BARS,
    }).all()
    if not rows:
        return PressureBefore(None, NO_BARS, None, None, None)
    if len(rows) < OBSERVATION_PRESSURE_WINDOW_BARS:
        return PressureBefore(None, INSUFFICIENT_BARS, None, None, None)
    bars = [Bar(ts=row.ts, open=row.open, high=row.high, low=row.low, close=row.close, volume=row.volume,
                batch_id=row.batch_id) for row in reversed(rows)]
    estimate = estimate_pressure(bars)
    if estimate is None:
        return PressureBefore(None, INSUFFICIENT_BARS, None, None, None)
    first, last = bars[0].ts, bars[-1].ts
    return PressureBefore(estimate, None, first, last, market_day_start(first) != market_day_start(last))


@dataclass(frozen=True)
class TradeRow:
    order_id: UUID
    ticker: str
    direction: str
    origin: str
    strategy: str
    opened_at: datetime | None
    closed_at: datetime
    r_multiple: Decimal
    mfe_r: Decimal | None
    mae_r: Decimal | None
    needs_review: bool
    pressure_alignment: Alignment
    pressure_strength: Strength | None
    pressure_cmf: Decimal | None
    pressure_unavailable_reason: str | None
    pressure_window_start: datetime | None
    pressure_window_end: datetime | None
    pressure_spans_sessions: bool | None


@dataclass(frozen=True)
class TradesSection:
    created: dict[str, int]
    filled: int
    closed: int
    excluded_needs_review: int
    stats: TradeStats
    stats_by_origin: dict[str, TradeStats]
    replay_closed: int
    rows: tuple[TradeRow, ...]


@dataclass(frozen=True)
class FillLatencyRow:
    order_id: UUID
    ticker: str
    origin: str
    signal_created_at: datetime
    fill_bar_ts: datetime
    recorded_at: datetime
    bar_latency_seconds: int
    recorded_latency_seconds: int


@dataclass(frozen=True)
class LatencySection:
    bar: LatencyStats
    recorded: LatencyStats
    bar_by_origin: dict[str, LatencyStats]
    unfilled: dict[str, int]
    rows: tuple[FillLatencyRow, ...]


@dataclass(frozen=True)
class PressureResultSection:
    estimate: bool
    method: str
    disclaimer: str
    association_note: str
    window_bars: int
    cmf_threshold: Decimal
    strong_cmf: Decimal
    min_trades_for_mean: int
    buckets: tuple[PressureBucket, ...]
    unavailable_reasons: dict[str, int]


@dataclass(frozen=True)
class TradeFacts:
    trades: TradesSection
    latency: LatencySection
    pressure: PressureResultSection


def pressure_section(rows: list[TradeRow]) -> PressureResultSection:
    """Buckets and unavailable reasons over closed trades without a review flag (spec 5.4, D66)."""
    included = [row for row in rows if not row.needs_review]
    reasons: list[str] = []  # a typed list first: no Optional member narrowing inside a generator
    for row in included:
        if row.pressure_unavailable_reason is not None:
            reasons.append(row.pressure_unavailable_reason)
    return PressureResultSection(
        estimate=True, method=METHOD, disclaimer=DISCLAIMER, association_note=ASSOCIATION_NOTE,
        window_bars=OBSERVATION_PRESSURE_WINDOW_BARS, cmf_threshold=OBSERVATION_CMF_THRESHOLD, strong_cmf=STRONG_CMF,
        min_trades_for_mean=MIN_TRADES_FOR_BUCKET_MEAN,
        buckets=tuple(pressure_buckets((row.pressure_alignment, row.pressure_strength, row.r_multiple)
                                       for row in included)),
        unavailable_reasons=dict(sorted(Counter(reasons).items())),
    )


def latency_section(fills: list[FillLatencyRow], unfilled: dict[str, int]) -> LatencySection:
    by_origin: dict[str, list[int]] = defaultdict(list)
    for fill in fills:
        by_origin[fill.origin].append(fill.bar_latency_seconds)
    return LatencySection(
        bar=latency_stats([fill.bar_latency_seconds for fill in fills]),
        recorded=latency_stats([fill.recorded_latency_seconds for fill in fills]),
        bar_by_origin={origin: latency_stats(values) for origin, values in sorted(by_origin.items())},
        unfilled=dict(sorted(unfilled.items())), rows=tuple(fills),
    )


def trade_sections(conn: Connection, window: ObservationWindow) -> TradeFacts:
    params = {"start": window.start, "end": window.end, "as_of": window.as_of}
    created = {row.origin: int(row.orders) for row in conn.execute(_CREATED, params)}
    fills = [
        FillLatencyRow(row.order_id, row.ticker, row.origin, row.signal_created_at, row.bar_ts, row.recorded_at,
                       whole_seconds(row.signal_created_at, row.bar_ts),
                       whole_seconds(row.signal_created_at, row.recorded_at))
        for row in conn.execute(_FILLS, params)
    ]
    unfilled = {row.bucket: int(row.orders) for row in conn.execute(_UNFILLED, params)}
    rows: list[TradeRow] = []
    replay_closed = 0
    for row in conn.execute(_CLOSED, {**params, "closing": list(CLOSING_EVENT_TYPES)}).all():
        if row.replay:
            replay_closed += 1  # D64: counted and labelled, never mixed into the trade metrics
            continue
        pressure = pressure_before(conn, ticker=row.ticker, price_source=row.price_source,
                                   decided_at=row.signal_created_at, as_of=window.as_of)
        alignment, strength = classify_pressure(pressure.estimate, row.direction)
        rows.append(TradeRow(
            order_id=row.order_id, ticker=row.ticker, direction=row.direction, origin=row.origin,
            strategy=row.strategy, opened_at=row.opened_at, closed_at=row.closed_at, r_multiple=row.r_multiple,
            mfe_r=row.mfe_r, mae_r=row.mae_r, needs_review=row.needs_review, pressure_alignment=alignment,
            pressure_strength=strength,
            pressure_cmf=None if pressure.estimate is None else pressure.estimate.chaikin_money_flow,
            pressure_unavailable_reason=pressure.unavailable_reason, pressure_window_start=pressure.window_start,
            pressure_window_end=pressure.window_end, pressure_spans_sessions=pressure.spans_sessions,
        ))
    included = [row for row in rows if not row.needs_review]
    trades = TradesSection(
        created=dict(sorted(created.items())), filled=len(fills), closed=len(rows),
        excluded_needs_review=len(rows) - len(included),
        stats=trade_stats([(row.r_multiple, row.mfe_r, row.mae_r) for row in included]),
        stats_by_origin={
            origin: trade_stats([(row.r_multiple, row.mfe_r, row.mae_r) for row in included if row.origin == origin])
            for origin in sorted({row.origin for row in included})
        },
        replay_closed=replay_closed, rows=tuple(rows),
    )
    return TradeFacts(trades, latency_section(fills, unfilled), pressure_section(rows))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest -p no:cacheprovider -o addopts="" -q tests/integration/test_observation_trades.py tests/test_import_boundaries.py && uv run ruff check src tests migrations && uv run mypy`
Expected: PASS; ruff e mypy limpos. Se `row.closed_at` do cenário não for `12:50` ET, pare: o cenário gravado (`scenario_bars`) mudou, não o relatório.

- [ ] **Step 5: Commit**

```bash
git add src/virtual_orders/readmodels/observation_trades.py tests/integration/test_observation_trades.py
git commit -m "feat(readmodels): observation trades, MFE/MAE, signal-to-fill latency and pressure by result"
```

---

### Task 6: Montagem do relatório, resumo de vários pregões e fronteira "avaliação nunca lê o relatório" (D60, D69)

**Files:**
- Create: `src/virtual_orders/readmodels/observation.py`
- Create: `tests/integration/test_observation_report.py`
- Modify: `tests/test_import_boundaries.py` (teste novo `test_evaluation_never_reads_the_observation_report`)

**Interfaces:**
- Consumes: `observation_operations` (Task 4: seções, `RUN_KIND_ORDER`, `window_runs`), `observation_trades` (Task 5: `trade_sections`, `pressure_section`, `TradeRow`, `FillLatencyRow`), `analytics.observation.latency_stats`, `trade_stats`, `ObservationWindow`.
- Produces:
  - `observation_snapshot(engine: Engine) -> AbstractContextManager[Connection]` (uma transação `REPEATABLE READ` somente leitura, D61); `require_snapshot(conn: Connection) -> None` (`ValueError` fora do snapshot);
  - `REPORT_VERSION = "OBSERVATION_REPORT_V1"`; `DEFINITIONS: dict[str, str]` (chaves `window`, `provider_failures`, `data_quality`, `actionability`, `rechecks`, `alerts`, `worker`, `health`, `trades`, `latency`, `pressure`);
  - `ObservationReport(report_version, session_day, window_start, window_end, session_open_utc, session_close_utc, as_of, complete, provider_failures, data_quality, actionability, rechecks, alerts, worker, health, trades, latency, pressure, definitions)`;
  - `DailyRow(session_day, complete, provider_failures, expected_bars, missing_bars, actionability_unverifiable, rechecks, alert_failures, alerts_expired, worker_restarts, unclean_worker_ends, health_transitions, fills, trades, sum_r)`;
  - `ObservationSummary(report_version, first_day, last_day, as_of, sessions, days, trades: TradeStats, excluded_needs_review, latency: LatencyStats, recorded_latency: LatencyStats, pressure: PressureResultSection, definitions)`;
  - `build_observation_report(conn: Connection, window: ObservationWindow) -> ObservationReport` (exige o snapshot; uma varredura de runs);
  - `daily_row(report: ObservationReport) -> DailyRow`;
  - `build_observation_summary(conn: Connection, windows: Sequence[ObservationWindow]) -> ObservationSummary` (todos os relatórios no mesmo snapshot).

- [ ] **Step 1: Write the failing tests**

`tests/integration/test_observation_report.py`:

```python
from dataclasses import asdict
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from core.domain.hashing import canonical_json
from core.domain.models import FillConfig
from tests.integration.observation_support import window_for
from tests.integration.support import (
    CODE_VERSION,
    DAY,
    PRICE_SOURCE,
    FakeBarSource,
    count,
    feeds,
    scenario_bars,
    signal_body,
    submit_default,
)
from tests.support import et
from virtual_orders.analytics.observation import Alignment, PressureBucket, Strength
from virtual_orders.evaluator.cycle import run_live_cycle
from virtual_orders.evaluator.signals import submit_signal
from virtual_orders.marketdata.asof import acquire_data_as_of
from virtual_orders.readmodels.observation import (
    DEFINITIONS,
    REPORT_VERSION,
    build_observation_report,
    build_observation_summary,
    observation_snapshot,
)
from virtual_orders.readmodels.observation_window import summary_sessions
from virtual_orders.storage import tables

NEXT = "2025-11-26"
HISTORY = ("evaluation_runs", "evaluation_run_status", "order_events", "bar_batches", "alert_outbox", "worker_sessions")


def two_sessions_with_one_trade_each(engine):
    source = FakeBarSource(scenario_bars() + scenario_bars(NEXT))
    submit_default(engine)
    for hm in ("10:30", "11:30", "13:00"):
        run_live_cycle(engine, feeds(source), code_version=CODE_VERSION, market_now=et(DAY, hm))
    submit_signal(engine, signal_body(client_signal_id="next-day"), config=FillConfig(), code_version=CODE_VERSION,
                  price_source=PRICE_SOURCE, now=et(NEXT, "09:00"))
    for hm in ("10:30", "11:30", "13:00"):
        run_live_cycle(engine, feeds(source), code_version=CODE_VERSION, market_now=et(NEXT, hm))


def test_the_report_assembles_every_section_for_one_session_without_writing(engine):
    two_sessions_with_one_trade_each(engine)
    window = window_for(engine, date(2025, 11, 25))
    before = {name: count(engine, name) for name in HISTORY}

    with observation_snapshot(engine) as conn:
        report = build_observation_report(conn, window)

    assert {name: count(engine, name) for name in HISTORY} == before  # D60: reads only
    assert (report.report_version, report.session_day, report.complete) == (REPORT_VERSION, date(2025, 11, 25), True)
    assert (report.window_start, report.window_end, report.as_of) == (window.start, window.end, window.as_of)
    assert (report.session_open_utc, report.session_close_utc) == (et(DAY, "09:30"), et(DAY, "16:00"))
    assert report.provider_failures.by_run_kind[0].run_kind == "LIVE" and report.provider_failures.total_failures == 0
    assert (report.trades.closed, report.trades.stats.sum_r, report.latency.bar.median_seconds) == (
        1, Decimal("1.7500"), 3900)
    assert report.pressure.estimate is True
    assert set(report.definitions) == set(DEFINITIONS) == {
        "window", "provider_failures", "data_quality", "actionability", "rechecks", "alerts", "worker", "health",
        "trades", "latency", "pressure"}
    assert "estimate" in report.definitions["pressure"] and "never" in report.definitions["pressure"]
    assert canonical_json(asdict(report))  # every value is JSON-native after canonical normalization


def test_the_report_reads_one_read_only_repeatable_read_snapshot(engine):
    two_sessions_with_one_trade_each(engine)
    window = window_for(engine, date(2025, 11, 25))

    with engine.connect() as plain, pytest.raises(ValueError, match="REPEATABLE READ"):
        build_observation_report(plain, window)  # a plain READ COMMITTED connection is refused (D61)

    with observation_snapshot(engine) as conn:
        isolation = conn.execute(text("SHOW transaction_isolation")).scalar_one()
        read_only = conn.execute(text("SHOW transaction_read_only")).scalar_one()
        first = build_observation_report(conn, window)
        with engine.begin() as other:  # committed by another connection while the snapshot is open, in the window
            other.execute(tables.health_state_log.insert().values(
                state="DEGRADED", cause_codes=["LIVE_CYCLE_STALE"], observed_at=et(DAY, "14:00")))
        again = build_observation_report(conn, window)

    assert (isolation, read_only) == ("repeatable read", "on")
    assert again == first  # every section of both reads comes from the same snapshot
    with observation_snapshot(engine) as conn:
        later = build_observation_report(conn, window)
    assert (later.health.log_rows, later.health.transitions) == (first.health.log_rows + 1,
                                                                 first.health.transitions + 1)


def test_the_summary_aggregates_the_sessions_of_the_range(engine):
    two_sessions_with_one_trade_each(engine)
    windows = summary_sessions(date(2025, 11, 22), date(2025, 11, 26), acquire_data_as_of(engine))

    with observation_snapshot(engine) as conn:
        summary = build_observation_summary(conn, windows)

    assert (summary.first_day, summary.last_day, summary.sessions) == (date(2025, 11, 24), date(2025, 11, 26), 3)
    assert [(row.session_day, row.trades, row.fills, row.sum_r) for row in summary.days] == [
        (date(2025, 11, 24), 0, 0, Decimal("0.0000")),
        (date(2025, 11, 25), 1, 1, Decimal("1.7500")),
        (date(2025, 11, 26), 1, 1, Decimal("1.7500")),
    ]
    assert (summary.trades.trades, summary.trades.sum_r, summary.trades.mean_r) == (2, Decimal("3.5000"),
                                                                                   Decimal("1.7500"))
    assert (summary.latency.count, summary.latency.median_seconds, summary.excluded_needs_review) == (2, 3900, 0)
    # 2025-11-25: no stored bar before the 09:00 signal. 2025-11-26: the previous session's 12:21-12:50 bars.
    assert summary.pressure.buckets == (  # one trade each: counts and sum only, no mean below 5 trades
        PressureBucket(Alignment.NEUTRAL, Strength.WEAK, 1, 1, Decimal("1.7500"), None),
        PressureBucket(Alignment.UNAVAILABLE, None, 1, 1, Decimal("1.7500"), None),
    )
    assert summary.pressure.unavailable_reasons == {"NO_BARS": 1}
    assert summary.definitions == DEFINITIONS
```

Em `tests/test_import_boundaries.py`, acrescente ao final:

```python
OBSERVATION_MODULES = (
    "virtual_orders.readmodels.observation", "virtual_orders.readmodels.observation_window",
    "virtual_orders.readmodels.observation_operations", "virtual_orders.readmodels.observation_trades",
    "virtual_orders.analytics.observation", "virtual_orders.alerts.observation", "virtual_orders.observation",
)
EVALUATION_PACKAGES = ("virtual_orders/evaluator", "virtual_orders/ledger", "virtual_orders/marketdata")


def _evaluation_files() -> list[Path]:
    files = list(_core_files())
    for package in EVALUATION_PACKAGES:
        files.extend(sorted((SRC / package).rglob("*.py")))
    return files


def test_evaluation_never_reads_the_observation_report() -> None:
    # Plan 4 (D66): the report and its recomputed pressure estimate never feed evaluation, fills or market data.
    offenders = {_rel(path): _offending(path, OBSERVATION_MODULES) for path in _evaluation_files()}
    assert {name: modules for name, modules in offenders.items() if modules} == {}
    assert "virtual_orders/evaluator/cycle.py" in offenders and "core/fills/v1/engine.py" in offenders
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest -p no:cacheprovider -o addopts="" -q tests/integration/test_observation_report.py tests/test_import_boundaries.py`
Expected: FAIL com `ModuleNotFoundError: No module named 'virtual_orders.readmodels.observation'` (o teste de fronteira novo já passa: nada importa o relatório).

- [ ] **Step 3: Implement the assembly**

`src/virtual_orders/readmodels/observation.py`:

```python
"""The daily paper-observation report (Plan 4, D60-D69): one NYSE session, or a range of sessions, from stored data
only and as of the request. Nothing here writes, calls a provider or feeds any evaluation."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Connection, Engine, text

from virtual_orders.analytics.observation import LatencyStats, TradeStats, latency_stats, trade_stats
from virtual_orders.readmodels.observation_operations import (
    RUN_KIND_ORDER,
    ActionabilitySection,
    AlertDeliverySection,
    DataQualitySection,
    HealthSection,
    ProviderFailures,
    RecheckSection,
    WorkerSection,
    actionability,
    alert_deliveries,
    data_quality,
    health_transitions,
    provider_failures,
    recheck_activity,
    window_runs,
    worker_activity,
)
from virtual_orders.readmodels.observation_trades import (
    LatencySection,
    PressureResultSection,
    TradesSection,
    pressure_section,
    trade_sections,
)
from virtual_orders.readmodels.observation_window import ObservationWindow

REPORT_VERSION = "OBSERVATION_REPORT_V1"
SNAPSHOT_REQUIRED = "the observation report reads one read-only REPEATABLE READ snapshot (D61)"
DEFINITIONS: dict[str, str] = {
    "window": (
        "From 00:00 ET of the day after the previous NYSE session to 00:00 ET of the day after the session. "
        "Evaluation facts belong to the window by their market instant (a still-running run without one falls back "
        "to its database start time); health log, alert and worker rows by the database clock. Every row is read "
        "as of the database clock at the request, and the whole report is read in one read-only REPEATABLE READ "
        "snapshot taken after that instant; complete = as_of is past the window end."
    ),
    "provider_failures": (
        "Provider failures recorded in run details (LIVE/WATCHLIST ingest_failures, OPENING source_failures, "
        "END_OF_DAY unavailable, QUALITY_RECHECK ingest_failures and unavailable, ACTIONABILITY ingest_error), "
        "counted per run and feed as fixed codes; messages never leave the database. Failed runs are counted by "
        "exception type name, or UNKNOWN. consecutive_live_max is the longest run of consecutive completed LIVE "
        "cycles inside this window with the same feed failing: it restarts at the window boundary, skips failed "
        "runs and is not the /health rule."
    ),
    "data_quality": (
        "Missing bars from the DATA_QUALITY:<session> events of non-replay orders (order-minutes), DATA_GAP events "
        "that start inside the session, and not_evaluated reasons of the latest completed END_OF_DAY run of the "
        "session."
    ),
    "actionability": (
        "Manual-order actionability checks clicked inside the window; unverifiable are the HTTP 503 "
        "ACTIONABILITY_UNVERIFIABLE answers, split into provider failure, missing minutes and policy contract "
        "violation."
    ),
    "rechecks": (
        "DATA_QUALITY_RECHECK runs whose market instant falls in the window and the rows they recorded, plus every "
        "recheck row about this session recorded up to the report (about_this_session). Those rows also count in "
        "the rows of the window whose run recorded them, so the two are never summed. not_evaluated excludes the "
        "ALREADY_RECHECKED and ALREADY_EVALUATED skips, which are counted in skipped."
    ),
    "alerts": (
        "Alerts created and delivery attempts made inside the window (database clock): outcomes, failure types (HTTP "
        "status or exception type) and alerts created in the window still undelivered at its end."
    ),
    "worker": (
        "worker_sessions rows: starts, restarts (a start after any earlier session), stop reasons and exit codes. A "
        "session without a stop row is open: running, or ended without one. open_session_at_end = the last session "
        "started by the window end has no stop row by then. An unclean end (no stop row before the next start) is "
        "only detectable at the next start and belongs to the window of that start; a still-open session with no "
        "later start is open, never a restart. A LOCK_LOST stop is always clean, even when recorded after the "
        "successor's start."
    ),
    "health": (
        "health_state_log rows inside the window: transitions are state changes (a row whose state differs from the "
        "previous row's, the first compared with the state in force at the window start); log_rows counts every row, "
        "including cause-code-only changes. Also states entered, cause codes and seconds spent in each state."
    ),
    "trades": (
        "Non-replay virtual orders: created, filled (FILLED bar in the window), closed (closing event recorded as of "
        "the report). Statistics exclude needs_review orders (spec 5.4); MFE/MAE come from the projection (spec D3). "
        "The MFE/MAE median is the middle value, or the mean of the two middle values for an even count (latency "
        "uses nearest-rank instead). Replay orders are only counted in replay_closed."
    ),
    "latency": (
        "Per FILLED event in the window: seconds from the signal created_at to the fill bar start and to the moment "
        "the fill was recorded; nearest-rank median and p90, never interpolated. Orders created in the window without "
        "a fill as of the report are counted by outcome (EXPIRED, INVALIDATED, CANCELED or NOT_FILLED_YET); this is a "
        "snapshot as of the report, so created is not filled plus unfilled in general."
    ),
    "pressure": (
        "An OHLCV pressure estimate recomputed at report time from the last 30 stored bars strictly before each "
        "closed trade's signal minute, across sessions and as of the report (each trade records window_start, "
        "window_end and spans_sessions), grouped by alignment with the trade direction and CMF strength against the "
        "trade R. CMF 0.05/0.15 and 30 bars are labelled conventions. A bucket mean needs at least 5 trades; n is "
        "shown beside it. It is an estimate, not order flow, descriptive only, never stored and never used by any "
        "evaluation."
    ),
}


@contextmanager
def observation_snapshot(engine: Engine) -> Iterator[Connection]:
    """D61: one read-only REPEATABLE READ transaction for a whole report or summary. Open it after acquire_data_as_of:
    every row committed up to as_of is visible, and nothing committed later changes any section of the read."""
    with engine.connect().execution_options(isolation_level="REPEATABLE READ", postgresql_readonly=True) as conn:
        with conn.begin():
            yield conn


def require_snapshot(conn: Connection) -> None:
    isolation = conn.execute(text("SHOW transaction_isolation")).scalar_one()
    read_only = conn.execute(text("SHOW transaction_read_only")).scalar_one()
    if (isolation, read_only) != ("repeatable read", "on"):
        raise ValueError(SNAPSHOT_REQUIRED)


@dataclass(frozen=True)
class ObservationReport:
    report_version: str
    session_day: date
    window_start: datetime
    window_end: datetime
    session_open_utc: datetime
    session_close_utc: datetime
    as_of: datetime
    complete: bool
    provider_failures: ProviderFailures
    data_quality: DataQualitySection
    actionability: ActionabilitySection
    rechecks: RecheckSection
    alerts: AlertDeliverySection
    worker: WorkerSection
    health: HealthSection
    trades: TradesSection
    latency: LatencySection
    pressure: PressureResultSection
    definitions: dict[str, str]


@dataclass(frozen=True)
class DailyRow:
    session_day: date
    complete: bool
    provider_failures: int
    expected_bars: int
    missing_bars: int
    actionability_unverifiable: int
    rechecks: int
    alert_failures: int
    alerts_expired: int
    worker_restarts: int
    unclean_worker_ends: int
    health_transitions: int
    fills: int
    trades: int
    sum_r: Decimal


@dataclass(frozen=True)
class ObservationSummary:
    report_version: str
    first_day: date
    last_day: date
    as_of: datetime
    sessions: int
    days: tuple[DailyRow, ...]
    trades: TradeStats
    excluded_needs_review: int
    latency: LatencyStats
    recorded_latency: LatencyStats
    pressure: PressureResultSection
    definitions: dict[str, str]


def build_observation_report(conn: Connection, window: ObservationWindow) -> ObservationReport:
    require_snapshot(conn)  # D61: every section below reads the same snapshot
    runs = window_runs(conn, window, RUN_KIND_ORDER)  # D69: one scan of evaluation_runs per report
    facts = trade_sections(conn, window)
    return ObservationReport(
        report_version=REPORT_VERSION, session_day=window.session_day, window_start=window.start,
        window_end=window.end, session_open_utc=window.session_open_utc, session_close_utc=window.session_close_utc,
        as_of=window.as_of, complete=window.complete, provider_failures=provider_failures(conn, window, runs=runs),
        data_quality=data_quality(conn, window, runs=runs), actionability=actionability(conn, window, runs=runs),
        rechecks=recheck_activity(conn, window, runs=runs), alerts=alert_deliveries(conn, window),
        worker=worker_activity(conn, window), health=health_transitions(conn, window), trades=facts.trades,
        latency=facts.latency, pressure=facts.pressure, definitions=dict(DEFINITIONS),
    )


def daily_row(report: ObservationReport) -> DailyRow:
    return DailyRow(
        session_day=report.session_day, complete=report.complete,
        provider_failures=report.provider_failures.total_failures, expected_bars=report.data_quality.expected_bars,
        missing_bars=report.data_quality.missing_bars, actionability_unverifiable=report.actionability.unverifiable,
        rechecks=report.rechecks.rows, alert_failures=report.alerts.attempts.get("FAILED", 0),
        alerts_expired=report.alerts.attempts.get("EXPIRED", 0), worker_restarts=report.worker.restarts,
        unclean_worker_ends=report.worker.unclean_ends, health_transitions=report.health.transitions,
        fills=report.trades.filled, trades=report.trades.stats.trades, sum_r=report.trades.stats.sum_r,
    )


def build_observation_summary(conn: Connection, windows: Sequence[ObservationWindow]) -> ObservationSummary:
    if not windows:
        raise ValueError("a summary needs at least one session window")
    reports = [build_observation_report(conn, window) for window in windows]
    rows = [row for report in reports for row in report.trades.rows]
    included = [row for row in rows if not row.needs_review]
    fills = [fill for report in reports for fill in report.latency.rows]
    return ObservationSummary(
        report_version=REPORT_VERSION, first_day=windows[0].session_day, last_day=windows[-1].session_day,
        as_of=windows[0].as_of, sessions=len(reports), days=tuple(daily_row(report) for report in reports),
        trades=trade_stats([(row.r_multiple, row.mfe_r, row.mae_r) for row in included]),
        excluded_needs_review=len(rows) - len(included),
        latency=latency_stats([fill.bar_latency_seconds for fill in fills]),
        recorded_latency=latency_stats([fill.recorded_latency_seconds for fill in fills]),
        pressure=pressure_section(rows), definitions=dict(DEFINITIONS),
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest -p no:cacheprovider -o addopts="" -q tests/integration/test_observation_report.py tests/integration/test_observation_operations.py tests/integration/test_observation_trades.py tests/test_import_boundaries.py && uv run ruff check src tests migrations && uv run mypy`
Expected: PASS; ruff e mypy limpos.

- [ ] **Step 5: Commit**

```bash
git add src/virtual_orders/readmodels/observation.py tests/integration/test_observation_report.py tests/test_import_boundaries.py
git commit -m "feat(readmodels): assemble the observation report and multi-session summary"
```

---

### Task 7: Rotas `GET /observation/report` e `GET /observation/summary` (D63, D70a)

**Files:**
- Create: `src/virtual_orders/api/routes/observation.py`
- Modify: `src/virtual_orders/api/app.py` (import e `app.include_router(observation.router)`)
- Create: `tests/integration/api/test_observation_api.py`

**Interfaces:**
- Consumes: `session_window`, `summary_sessions`, `ObservationRequestInvalid` (Task 4); `build_observation_report`, `build_observation_summary` (Task 6); `acquire_data_as_of`; `ApiError`, `json_response`, `ServicesDep`.
- Produces: `GET /observation/report?day=YYYY-MM-DD` → `ObservationReport` em JSON (Decimal como texto, datas ISO UTC); `GET /observation/summary?from=YYYY-MM-DD&to=YYYY-MM-DD` → `ObservationSummary`; erros `422 OBSERVATION_REQUEST_INVALID` com `detail.errors` e `422 REQUEST_INVALID`. Constante `OBSERVATION_ERROR = "OBSERVATION_REQUEST_INVALID"`.

- [ ] **Step 1: Write the failing API tests**

`tests/integration/api/test_observation_api.py`:

```python
from datetime import datetime
from uuid import UUID

from tests.integration.support import DAY, count, post_json, scenario_bars, signal_body
from tests.support import et
from virtual_orders.analytics.pressure import DISCLAIMER, METHOD
from virtual_orders.ledger.runs import RunKind, RunStatus, finish_run, start_run
from virtual_orders.marketdata.asof import acquire_data_as_of
from virtual_orders.worker.jobs import JobResult, WorkerJobs

NEXT = "2025-11-26"
ERROR = "OBSERVATION_REQUEST_INVALID"
HISTORY = ("evaluation_runs", "order_events", "bar_batches", "alert_outbox", "worker_sessions")


def recorded_session(api) -> UUID:
    """AUTO and MANUAL orders fill at 10:05 and close at 12:50 (r = 1.75); REPRODUCE replays both."""
    api.bars.load(scenario_bars())
    created = post_json(api.client, "/signals", signal_body())
    assert created.status_code == 201, created.text
    api.clock.set(et(DAY, "10:00", 30))
    assert api.client.post(f"/signals/{created.json()['signal_id']}/orders").status_code == 201
    jobs = WorkerJobs(api.services)
    for hm in ("10:30", "11:30", "13:00"):
        api.clock.set(et(DAY, hm))
        assert jobs.live_cycle() == JobResult("live_cycle", True, "COMPLETED")
    replay = post_json(api.client, "/replay", {"mode": "REPRODUCE", "from": et(DAY, "08:00").isoformat(),
                                               "to": et(DAY, "23:00").isoformat()})
    assert replay.status_code == 200, replay.text
    engine = api.services.engine
    with engine.begin() as conn:  # a failed cycle whose stored message carries a secret
        run = start_run(conn, RunKind.LIVE, acquire_data_as_of(engine), "test-sha")
        finish_run(conn, run.run_id, RunStatus.FAILED,
                   {"error": "OperationalError('password=hunter2 host=db.internal')", "market_now": et(DAY, "13:02")})
    return UUID(created.json()["auto_order_id"])


def test_the_report_is_read_from_stored_data_only_and_labels_the_estimate(api):
    auto_id = recorded_session(api)
    calls, before = len(api.bars.calls), {name: count(api.services.engine, name) for name in HISTORY}

    response = api.client.get("/observation/report", params={"day": DAY})

    assert response.status_code == 200, response.text
    assert len(api.bars.calls) == calls  # no provider call (D60)
    assert {name: count(api.services.engine, name) for name in HISTORY} == before  # no write (D60)
    body = response.json()
    assert (body["report_version"], body["session_day"], body["complete"]) == ("OBSERVATION_REPORT_V1", DAY, True)
    assert datetime.fromisoformat(body["as_of"]).tzinfo is not None
    trades = body["trades"]
    assert (trades["closed"], trades["replay_closed"], trades["filled"]) == (2, 2, 2)  # replay never mixed in
    assert (trades["stats"]["trades"], trades["stats"]["sum_r"], trades["stats"]["mean_r"]) == (2, "3.5", "1.75")
    assert {row["origin"] for row in trades["rows"]} == {"AUTO_STRATEGY", "MANUAL_USER"}
    assert str(auto_id) in {row["order_id"] for row in trades["rows"]}
    assert body["actionability"]["results"] == {"ACTIONABLE": 1} and body["actionability"]["unverifiable"] == 0
    assert body["latency"]["bar"] == {"count": 2, "median_seconds": 3900, "p90_seconds": 3900, "max_seconds": 3900}
    pressure = body["pressure"]
    assert (pressure["estimate"], pressure["method"], pressure["disclaimer"]) == (True, METHOD, DISCLAIMER)
    assert "not causal" in pressure["association_note"]
    live = next(item for item in body["provider_failures"]["by_run_kind"] if item["run_kind"] == "LIVE")
    assert live["failed_run_errors"] == {"OperationalError": 1}
    assert "hunter2" not in response.text and "db.internal" not in response.text


def test_invalid_report_days_use_fixed_codes(api):
    cases = [
        ({"day": "2025-11-29"}, 422, ERROR, ["NOT_A_SESSION:2025-11-29"]),
        ({"day": "2025-11-27"}, 422, ERROR, ["NOT_A_SESSION:2025-11-27"]),
        ({"day": "2099-01-05"}, 422, ERROR, ["DAY_IN_FUTURE:2099-01-05"]),
    ]
    for params, status, code, errors in cases:
        response = api.client.get("/observation/report", params=params)
        assert response.status_code == status, params
        assert response.json() == {"error": {"code": code, "reason": None, "detail": {"errors": errors}}}
    for params in ({"day": "25/11/2025"}, {}):
        response = api.client.get("/observation/report", params=params)
        assert response.status_code == 422 and response.json()["error"]["code"] == "REQUEST_INVALID"


def test_the_summary_covers_every_session_of_the_range(api):
    recorded_session(api)

    response = api.client.get("/observation/summary", params={"from": "2025-11-22", "to": NEXT})

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["first_day"], body["last_day"], body["sessions"]) == ("2025-11-24", NEXT, 3)
    assert [(day["session_day"], day["trades"]) for day in body["days"]] == [
        ("2025-11-24", 0), (DAY, 2), (NEXT, 0)]
    assert body["trades"]["sum_r"] == "3.5" and body["pressure"]["estimate"] is True
    assert "hunter2" not in response.text


def test_invalid_summary_ranges_use_fixed_codes(api):
    for params, errors in (
        ({"from": NEXT, "to": DAY}, ["EMPTY_RANGE"]),
        ({"from": "2025-09-01", "to": DAY}, ["RANGE_TOO_LARGE"]),
        ({"from": "2025-11-29", "to": "2025-11-30"}, ["NO_SESSIONS"]),
        ({"from": DAY, "to": "2099-01-05"}, ["DAY_IN_FUTURE:2099-01-05"]),
    ):
        response = api.client.get("/observation/summary", params=params)
        assert response.status_code == 422, params
        assert response.json() == {"error": {"code": ERROR, "reason": None, "detail": {"errors": errors}}}
    missing = api.client.get("/observation/summary", params={"from": DAY})
    assert missing.status_code == 422 and missing.json()["error"]["code"] == "REQUEST_INVALID"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest -p no:cacheprovider -o addopts="" -q tests/integration/api/test_observation_api.py`
Expected: FAIL — `404 NOT_FOUND` nas rotas novas.

- [ ] **Step 3: Implement the routes and register the router**

`src/virtual_orders/api/routes/observation.py`:

```python
"""Daily paper-observation report (Plan 4, D60-D70): stored data only, as of the request; never a provider call."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query, Response

from virtual_orders.api.deps import ServicesDep
from virtual_orders.api.encoding import json_response
from virtual_orders.api.errors import ApiError
from virtual_orders.marketdata.asof import acquire_data_as_of
from virtual_orders.readmodels.observation import (
    build_observation_report,
    build_observation_summary,
    observation_snapshot,
)
from virtual_orders.readmodels.observation_window import ObservationRequestInvalid, session_window, summary_sessions

router = APIRouter()
OBSERVATION_ERROR = "OBSERVATION_REQUEST_INVALID"


def _invalid(exc: ObservationRequestInvalid) -> ApiError:
    return ApiError(422, OBSERVATION_ERROR, detail={"errors": list(exc.codes)})


@router.get("/observation/report")
def get_observation_report(services: ServicesDep, day: date) -> Response:
    as_of = acquire_data_as_of(services.engine)  # D61: the database clock at the request
    try:
        window = session_window(day, as_of)
    except ObservationRequestInvalid as exc:
        raise _invalid(exc) from None
    with observation_snapshot(services.engine) as conn:  # D61: one read-only REPEATABLE READ snapshot
        return json_response(build_observation_report(conn, window))


@router.get("/observation/summary")
def get_observation_summary(
    services: ServicesDep,
    first: Annotated[date, Query(alias="from")],
    last: Annotated[date, Query(alias="to")],
) -> Response:
    as_of = acquire_data_as_of(services.engine)
    try:
        windows = summary_sessions(first, last, as_of)
    except ObservationRequestInvalid as exc:
        raise _invalid(exc) from None
    with observation_snapshot(services.engine) as conn:  # every session of the range in the same snapshot
        return json_response(build_observation_summary(conn, windows))
```

Em `src/virtual_orders/api/app.py`, acrescente `observation` à lista importada de `virtual_orders.api.routes` (em ordem alfabética, entre `metrics` e `observability`) e, depois de `app.include_router(observability.router)`:

```python
    app.include_router(observation.router)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest -p no:cacheprovider -o addopts="" -q tests/integration/api/test_observation_api.py tests/integration/api/test_signals_api.py tests/test_import_boundaries.py && uv run ruff check src tests migrations && uv run mypy`
Expected: PASS — inclusive `test_every_route_rejects_a_missing_or_wrong_key`, que agora percorre as duas rotas novas (a dependência de chave do app responde `401` antes da validação de query, como em `/market/bars`); ruff e mypy limpos.

- [ ] **Step 5: Commit**

```bash
git add src/virtual_orders/api/routes/observation.py src/virtual_orders/api/app.py tests/integration/api/test_observation_api.py
git commit -m "feat(api): observation report and summary routes, stored data only"
```

---

### Task 8: CLI `python -m virtual_orders.observation` e runbook (D70b, D73)

**Files:**
- Create: `src/virtual_orders/observation/__init__.py`
- Create: `src/virtual_orders/observation/render.py`
- Create: `src/virtual_orders/observation/__main__.py`
- Create: `tests/integration/test_observation_cli.py`
- Modify: `tests/test_import_boundaries.py` (fronteira da CLI)
- Modify: `docs/superpowers/runbooks/2026-09-14-compose-producao.md` (seção "Observação diária")

**Interfaces:**
- Consumes: `session_window`, `summary_sessions`, `ObservationRequestInvalid` (Task 4); `build_observation_report`, `build_observation_summary`, `observation_snapshot`, `ObservationReport`, `ObservationSummary` (Task 6); `PressureBucket` (Task 3); `acquire_data_as_of`; `make_engine`; `core.domain.hashing.canonical_json`.
- Produces:
  - `virtual_orders.observation.render.render_json(document: ObservationReport | ObservationSummary) -> str` e `render_markdown(document) -> str`;
  - `virtual_orders.observation.__main__.main(argv: Sequence[str] | None = None, *, environ: Mapping[str, str] | None = None, engine_factory: Callable[[str], Engine] = make_engine, out: TextIO | None = None, err: TextIO | None = None) -> int`;
  - códigos `EXIT_OK = 0`, `EXIT_INTERNAL = 1`, `EXIT_USAGE = 2`, `EXIT_INVALID_REQUEST = 3`, `EXIT_DATABASE_UNAVAILABLE = 4`; `DATABASE_UNAVAILABLE_ERRORS = (OperationalError, InterfaceError, sqlalchemy.exc.TimeoutError)`.

- [ ] **Step 1: Write the failing CLI tests**

`tests/integration/test_observation_cli.py`:

```python
import io
import json

import pytest
from sqlalchemy.exc import InterfaceError, ProgrammingError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from tests.integration.support import CODE_VERSION, DAY, FakeBarSource, count, feeds, scenario_bars, submit_default
from tests.support import et
from virtual_orders.analytics.observation import ASSOCIATION_NOTE
from virtual_orders.analytics.pressure import DISCLAIMER, METHOD
from virtual_orders.evaluator.cycle import run_live_cycle
from virtual_orders.observation import __main__ as cli

HISTORY = ("evaluation_runs", "order_events", "bar_batches", "alert_outbox", "worker_sessions")


def closed_trade(engine):
    source = FakeBarSource(scenario_bars())
    submit_default(engine)
    for hm in ("10:30", "11:30", "13:00"):
        run_live_cycle(engine, feeds(source), code_version=CODE_VERSION, market_now=et(DAY, hm))


def run(argv, environ):
    out, err = io.StringIO(), io.StringIO()
    code = cli.main(argv, environ=environ, out=out, err=err)
    return code, out.getvalue(), err.getvalue()


def test_report_prints_markdown_or_json_and_never_writes(engine, database_url):
    closed_trade(engine)
    environ = {"DATABASE_URL": database_url, "API_KEY": "never-read"}
    before = {name: count(engine, name) for name in HISTORY}

    code, markdown, err = run(["report", "--day", DAY], environ)
    assert (code, err) == (0, "")
    assert markdown.startswith("# Observação em paper — pregão 2025-11-25\n")
    for heading in ("## Falhas de provider", "## Candles ausentes", "## Actionability (503)", "## DATA_QUALITY_RECHECK",
                    "## Entrega de alertas", "## Reinícios do worker", "## Transições de saúde", "## Trades virtuais",
                    "## Latência sinal → fill", "## Pressão estimada × resultado", "## Definições"):
        assert heading in markdown, heading
    assert METHOD in markdown and DISCLAIMER in markdown and ASSOCIATION_NOTE in markdown
    assert "vo:vo@" not in markdown and "never-read" not in markdown

    code, text, err = run(["report", "--day", DAY, "--format", "json"], environ)
    document = json.loads(text)
    assert (code, err, document["session_day"]) == (0, "", DAY)
    assert (document["trades"]["stats"]["trades"], document["trades"]["stats"]["sum_r"]) == (1, "1.75")
    assert document["pressure"]["estimate"] is True
    assert {name: count(engine, name) for name in HISTORY} == before


def test_summary_prints_one_row_per_session(engine, database_url):
    closed_trade(engine)
    code, markdown, _ = run(["summary", "--from", DAY, "--to", "2025-11-26"], {"DATABASE_URL": database_url})
    assert code == 0
    assert markdown.startswith("# Observação em paper — resumo 2025-11-25 a 2025-11-26\n")
    assert "| 2025-11-25 |" in markdown and "| 2025-11-26 |" in markdown


def test_exit_codes_carry_codes_and_types_only(engine, database_url, monkeypatch):
    code, out, err = run(["report", "--day", "2025-11-29"], {"DATABASE_URL": database_url})
    assert (code, out) == (3, "")
    assert json.loads(err) == {"error": "OBSERVATION_REQUEST_INVALID", "errors": ["NOT_A_SESSION:2025-11-29"]}

    code, out, err = run(["report", "--day", DAY], {})
    assert (code, json.loads(err)) == (2, {"error": "CONFIG_INVALID", "errors": ["MISSING:DATABASE_URL"]})

    code, out, err = run(["report", "--day", DAY], {"DATABASE_URL": "postgresql+psycopg://vo:hunter2@127.0.0.1:1/x"})
    assert (code, out, json.loads(err)) == (4, "", {"error": "DATABASE_UNAVAILABLE", "type": "OperationalError"})
    assert "hunter2" not in err

    def exploding(*args, **kwargs):
        raise RuntimeError("password=hunter2")

    monkeypatch.setattr(cli, "build_observation_report", exploding)
    code, out, err = run(["report", "--day", DAY], {"DATABASE_URL": database_url})
    assert (code, json.loads(err)) == (1, {"error": "INTERNAL_ERROR", "type": "RuntimeError"})
    assert "hunter2" not in err

    with pytest.raises(SystemExit) as usage:
        cli.main(["report", "--day", "25/11/2025"], environ={"DATABASE_URL": database_url}, err=io.StringIO())
    assert usage.value.code == 2


def raising(error):
    def build(*args, **kwargs):
        raise error

    return build


@pytest.mark.parametrize("error, exit_code, payload", [
    (InterfaceError("SELECT 1", {}, Exception("password=hunter2")), 4,
     {"error": "DATABASE_UNAVAILABLE", "type": "InterfaceError"}),
    (PoolTimeoutError("QueuePool limit reached; password=hunter2"), 4,
     {"error": "DATABASE_UNAVAILABLE", "type": "TimeoutError"}),
    (ProgrammingError("SELECT 1", {}, Exception("password=hunter2")), 1,
     {"error": "DATABASE_ERROR", "type": "ProgrammingError"}),
], ids=["interface-error", "pool-timeout", "programming-error"])
def test_only_connection_errors_are_database_unavailable(database_url, monkeypatch, error, exit_code, payload):
    # D70b: a SQL defect (ProgrammingError, DataError, ...) is never reported as an unreachable database.
    monkeypatch.setattr(cli, "build_observation_report", raising(error))
    code, out, err = run(["report", "--day", DAY], {"DATABASE_URL": database_url})
    assert (code, out, json.loads(err)) == (exit_code, "", payload)
    assert "hunter2" not in err
```

Em `tests/test_import_boundaries.py`, acrescente ao final:

```python
OBSERVATION_CLI = "virtual_orders/observation"


def _observation_cli_files() -> list[Path]:
    return sorted((SRC / OBSERVATION_CLI).rglob("*.py"))


def test_the_observation_cli_never_imports_adapters_the_composition_root_or_the_http_layer() -> None:
    # Not parametrized: an empty parameter set before the package exists would be reported as a skip.
    files = _observation_cli_files()
    forbidden = PROVIDER_ADAPTERS + PROVIDER_LIBRARIES + (
        "virtual_orders.api", "virtual_orders.bootstrap", "virtual_orders.config", "virtual_orders.worker",
        "fastapi", "starlette", "uvicorn",
    )
    assert {_rel(path) for path in files} >= {
        "virtual_orders/observation/__init__.py", "virtual_orders/observation/__main__.py",
        "virtual_orders/observation/render.py",
    }
    assert {_rel(path): _offending(path, forbidden) for path in files} == {_rel(path): [] for path in files}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest -p no:cacheprovider -o addopts="" -q tests/integration/test_observation_cli.py tests/test_import_boundaries.py`
Expected: FAIL com `ModuleNotFoundError: No module named 'virtual_orders.observation'` e a fronteira nova sem os arquivos.

- [ ] **Step 3: Implement the renderers and the CLI**

`src/virtual_orders/observation/__init__.py`:

```python
"""Read-only daily paper-observation CLI (Plan 4, D70, D73)."""
```

`src/virtual_orders/observation/render.py`:

```python
"""Plain-text renderings of the observation report for the CLI (Plan 4, D70). Counts, codes, ids and labels only."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict
from datetime import datetime
from typing import Any

from core.domain.hashing import canonical_json
from virtual_orders.analytics.observation import LatencyStats, PressureBucket, TradeStats
from virtual_orders.readmodels.observation import ObservationReport, ObservationSummary

EMPTY = "—"
BUCKET_HEADERS = ("Alinhamento", "Força", "Trades", "Wins", "R somado", "R médio (n)")


def render_json(document: ObservationReport | ObservationSummary) -> str:
    return json.dumps(json.loads(canonical_json(asdict(document))), indent=2, sort_keys=True, ensure_ascii=False)


def _cell(value: Any) -> str:
    if value is None:
        return EMPTY
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return ", ".join(f"{key}: {item}" for key, item in sorted(value.items())) or EMPTY
    if isinstance(value, list | tuple):
        return ", ".join(str(item) for item in value) or EMPTY
    return str(value)


def _table(headers: Sequence[str], rows: Iterable[Sequence[Any]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines.extend("| " + " | ".join(_cell(value) for value in row) + " |" for row in rows)
    return [*lines, ""]


def _stats(stats: TradeStats) -> list[str]:
    return _table(
        ("Trades", "Wins", "Losses", "Win rate", "R somado", "R médio", "MFE médio", "MFE mediano", "MAE médio",
         "MAE mediano"),
        [(stats.trades, stats.wins, stats.losses, stats.win_rate, stats.sum_r, stats.mean_r, stats.mean_mfe_r,
          stats.median_mfe_r, stats.mean_mae_r, stats.median_mae_r)],
    )


def _latency(label: str, stats: LatencyStats) -> tuple[str, int, int | None, int | None, int | None]:
    return (label, stats.count, stats.median_seconds, stats.p90_seconds, stats.max_seconds)


def _buckets(buckets: Iterable[PressureBucket]) -> list[str]:
    """D66: n always beside the mean; the mean is empty below the minimum number of trades."""
    return _table(BUCKET_HEADERS, [
        (bucket.alignment, bucket.strength, bucket.trades, bucket.wins, bucket.sum_r,
         f"{_cell(bucket.mean_r)} (n={bucket.trades})")
        for bucket in buckets
    ])


def _report(report: ObservationReport) -> list[str]:
    failures, quality, action = report.provider_failures, report.data_quality, report.actionability
    rechecks, alerts, worker, health = report.rechecks, report.alerts, report.worker, report.health
    trades, latency, pressure = report.trades, report.latency, report.pressure
    lines = [
        f"# Observação em paper — pregão {report.session_day.isoformat()}", "",
        f"- Janela: {report.window_start.isoformat()} → {report.window_end.isoformat()}",
        f"- As-of: {report.as_of.isoformat()} ({'completo' if report.complete else 'parcial'})",
        f"- Versão: {report.report_version}", "",
        "## Falhas de provider", "",
        f"Total: {failures.total_failures} · maior sequência de ciclos LIVE com o mesmo feed falhando: "
        f"{failures.consecutive_live_max}", "",
        *_table(("Run", "Runs", "Runs FAILED", "Erros", "Runs com falha", "Falhas", "Códigos", "Feeds"),
                [(item.run_kind, item.runs, item.failed_runs, item.failed_run_errors, item.runs_with_failures,
                  item.failures, item.codes, item.feeds) for item in failures.by_run_kind]),
        "## Candles ausentes", "",
        *_table(("Ordens medidas", "Minutos esperados", "Ausentes", "Cobertura %", "Ordens com ausência", "DATA_GAP",
                 "Minutos em gap", "Não avaliadas"),
                [(quality.orders_measured, quality.expected_bars, quality.missing_bars, quality.coverage_pct,
                  quality.orders_with_missing, quality.gaps, quality.gap_minutes, quality.not_evaluated)]),
        "## Actionability (503)", "",
        *_table(("Pedidos", "Resultados", "503", "Causas do 503", "Taxa de 503", "Sem resposta"),
                [(action.requests, action.results, action.unverifiable, action.unverifiable_causes,
                  action.unverifiable_rate, action.unfinished)]),
        "## DATA_QUALITY_RECHECK", "",
        *_table(("Runs", "Linhas", "Status", "Motivos terminais", "Pregões", "Sobre este pregão", "Não avaliadas",
                 "Ignoradas (já tratadas)"),
                [(rechecks.runs, rechecks.rows, rechecks.statuses, rechecks.terminal_reasons,
                  [day.isoformat() for day in rechecks.sessions], rechecks.about_this_session,
                  rechecks.not_evaluated, rechecks.skipped)]),
        "## Entrega de alertas", "",
        *_table(("Criados", "Tentativas", "Alertas com falha", "Tipos de falha", "Pendentes no fim"),
                [(alerts.created, alerts.attempts, alerts.failed_alerts, alerts.failure_types, alerts.pending_at_end)]),
        "## Reinícios do worker", "",
        *_table(("Inícios", "Reinícios", "Fins sem parada", "Paradas", "Códigos de saída", "Versões",
                 "Sessão aberta no fim"),
                [(worker.starts, worker.restarts, worker.unclean_ends, worker.stops, worker.exit_codes,
                  worker.code_versions, "sim" if worker.open_session_at_end else "não")]),
        "Sessão aberta = sem linha de parada: rodando, ou terminou sem parada (só se sabe no próximo início).", "",
        "## Transições de saúde", "",
        *_table(("No início", "Transições", "Linhas do log", "Entradas por estado", "Causas", "Segundos por estado",
                 "No fim"),
                [(health.state_at_start, health.transitions, health.log_rows, health.entered, health.cause_codes,
                  health.seconds_by_state, health.state_at_end)]),
        "## Trades virtuais", "",
        f"Criadas: {_cell(trades.created)} · fills: {trades.filled} · fechadas: {trades.closed} · excluídas por "
        f"revisão: {trades.excluded_needs_review} · replay fechadas (fora das métricas): {trades.replay_closed}", "",
        *_stats(trades.stats),
        "### MFE / MAE por trade", "",
        *_table(("Ordem", "Ticker", "Direção", "Origem", "Fechada", "R", "MFE", "MAE", "Revisão"),
                [(row.order_id, row.ticker, row.direction, row.origin, row.closed_at, row.r_multiple, row.mfe_r,
                  row.mae_r, "sim" if row.needs_review else "não") for row in trades.rows]),
        "## Latência sinal → fill", "",
        *_table(("Medida", "Fills", "Mediana (s)", "p90 (s)", "Máx (s)"),
                [_latency("candle do fill", latency.bar), _latency("gravação do fill", latency.recorded),
                 *(_latency(f"candle · {origin}", stats) for origin, stats in latency.bar_by_origin.items())]),
        f"Sem fill (criadas na janela): {_cell(latency.unfilled)}", "",
        "## Pressão estimada × resultado", "",
        f"- Estimativa: {pressure.method} · últimos {pressure.window_bars} candles antes do sinal (atravessando "
        f"pregões) · limiar CMF {pressure.cmf_threshold} · forte a partir de {pressure.strong_cmf} · média só com "
        f"n ≥ {pressure.min_trades_for_mean}",
        f"- {pressure.disclaimer}",
        f"- {pressure.association_note}", "",
        *_buckets(pressure.buckets),
        f"Sem estimativa: {_cell(pressure.unavailable_reasons)}", "",
        "## Definições", "",
        *(f"- **{name}**: {text}" for name, text in report.definitions.items()),
    ]
    return lines


def _summary(summary: ObservationSummary) -> list[str]:
    pressure = summary.pressure
    return [
        f"# Observação em paper — resumo {summary.first_day.isoformat()} a {summary.last_day.isoformat()}", "",
        f"- As-of: {summary.as_of.isoformat()} · pregões: {summary.sessions} · versão: {summary.report_version}", "",
        "## Por pregão", "",
        *_table(("Pregão", "Completo", "Falhas de provider", "Minutos ausentes", "503", "Rechecks", "Falhas de alerta",
                 "Alertas expirados", "Reinícios", "Fins sem parada", "Transições", "Fills", "Trades", "R somado"),
                [(row.session_day.isoformat(), "sim" if row.complete else "não", row.provider_failures,
                  f"{row.missing_bars}/{row.expected_bars}", row.actionability_unverifiable, row.rechecks,
                  row.alert_failures, row.alerts_expired, row.worker_restarts, row.unclean_worker_ends,
                  row.health_transitions, row.fills, row.trades, row.sum_r) for row in summary.days]),
        "## Trades (sem revisão)", "", f"Excluídas por revisão: {summary.excluded_needs_review}", "",
        *_stats(summary.trades),
        "## Latência sinal → fill", "",
        *_table(("Medida", "Fills", "Mediana (s)", "p90 (s)", "Máx (s)"),
                [_latency("candle do fill", summary.latency), _latency("gravação do fill", summary.recorded_latency)]),
        "## Pressão estimada × resultado", "",
        f"- Estimativa: {pressure.method}", f"- {pressure.disclaimer}", f"- {pressure.association_note}", "",
        *_buckets(pressure.buckets),
        f"Sem estimativa: {_cell(pressure.unavailable_reasons)}",
    ]


def render_markdown(document: ObservationReport | ObservationSummary) -> str:
    lines = _summary(document) if isinstance(document, ObservationSummary) else _report(document)
    return "\n".join(lines) + "\n"
```

`src/virtual_orders/observation/__main__.py`:

```python
"""`python -m virtual_orders.observation report --day YYYY-MM-DD [--format markdown|json]`
and `python -m virtual_orders.observation summary --from YYYY-MM-DD --to YYYY-MM-DD [--format markdown|json]`.

Read-only (Plan 4, D70, D73): reads DATABASE_URL only, never provider credentials, never writes; the whole document is
read in one read-only REPEATABLE READ snapshot (D61). Exit codes: 0 ok; 1 internal error (INTERNAL_ERROR, or
DATABASE_ERROR for any SQLAlchemy error that is not a connection failure); 2 usage or CONFIG_INVALID;
3 OBSERVATION_REQUEST_INVALID; 4 DATABASE_UNAVAILABLE (OperationalError, InterfaceError or a pool TimeoutError, as
/health maps them). Errors go to stderr as one JSON line with fixed codes or an exception type, never a message.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import date
from typing import TextIO

from sqlalchemy import Engine
from sqlalchemy.exc import InterfaceError, OperationalError, SQLAlchemyError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from virtual_orders.marketdata.asof import acquire_data_as_of
from virtual_orders.observation.render import render_json, render_markdown
from virtual_orders.readmodels.observation import (
    ObservationReport,
    ObservationSummary,
    build_observation_report,
    build_observation_summary,
    observation_snapshot,
)
from virtual_orders.readmodels.observation_window import ObservationRequestInvalid, session_window, summary_sessions
from virtual_orders.storage.database import make_engine

EXIT_OK = 0
EXIT_INTERNAL = 1
EXIT_USAGE = 2
EXIT_INVALID_REQUEST = 3
EXIT_DATABASE_UNAVAILABLE = 4
# The /health mapping (readmodels/health.py): only connection failures mean the database is unavailable.
DATABASE_UNAVAILABLE_ERRORS = (OperationalError, InterfaceError, PoolTimeoutError)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m virtual_orders.observation")
    commands = parser.add_subparsers(dest="command", required=True)
    report = commands.add_parser("report", help="one NYSE session")
    report.add_argument("--day", type=date.fromisoformat, required=True)
    summary = commands.add_parser("summary", help="every NYSE session of a date range")
    summary.add_argument("--from", dest="first", type=date.fromisoformat, required=True)
    summary.add_argument("--to", dest="last", type=date.fromisoformat, required=True)
    for command in (report, summary):
        command.add_argument("--format", choices=("markdown", "json"), default="markdown")
    return parser


def _error(stream: TextIO, payload: Mapping[str, object]) -> None:
    print(json.dumps(dict(payload)), file=stream)


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    engine_factory: Callable[[str], Engine] = make_engine,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    stdout = sys.stdout if out is None else out
    stderr = sys.stderr if err is None else err
    args = _parser().parse_args(argv)  # argparse exits 2 with a usage line on invalid arguments
    url = (os.environ if environ is None else environ).get("DATABASE_URL", "").strip()
    if not url:
        _error(stderr, {"error": "CONFIG_INVALID", "errors": ["MISSING:DATABASE_URL"]})
        return EXIT_USAGE
    document: ObservationReport | ObservationSummary
    try:
        engine = engine_factory(url)
    except Exception as exc:  # noqa: BLE001 - a malformed URL is reported by type only
        _error(stderr, {"error": "CONFIG_INVALID", "errors": ["INVALID:DATABASE_URL"], "type": type(exc).__name__})
        return EXIT_USAGE
    try:
        as_of = acquire_data_as_of(engine)
        if args.command == "report":
            window = session_window(args.day, as_of)
            with observation_snapshot(engine) as conn:  # D61: one read-only REPEATABLE READ snapshot
                document = build_observation_report(conn, window)
        else:
            windows = summary_sessions(args.first, args.last, as_of)
            with observation_snapshot(engine) as conn:
                document = build_observation_summary(conn, windows)
    except ObservationRequestInvalid as exc:
        _error(stderr, {"error": "OBSERVATION_REQUEST_INVALID", "errors": list(exc.codes)})
        return EXIT_INVALID_REQUEST
    except DATABASE_UNAVAILABLE_ERRORS as exc:
        _error(stderr, {"error": "DATABASE_UNAVAILABLE", "type": type(exc).__name__})
        return EXIT_DATABASE_UNAVAILABLE
    except SQLAlchemyError as exc:  # a SQL defect (ProgrammingError, DataError, ...), never "database unavailable"
        _error(stderr, {"error": "DATABASE_ERROR", "type": type(exc).__name__})
        return EXIT_INTERNAL
    except Exception as exc:  # noqa: BLE001 - never a traceback: its text could carry data or connection details
        _error(stderr, {"error": "INTERNAL_ERROR", "type": type(exc).__name__})
        return EXIT_INTERNAL
    finally:
        engine.dispose()
    stdout.write(render_json(document) + "\n" if args.format == "json" else render_markdown(document))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
```

Acrescente ao final de `docs/superpowers/runbooks/2026-09-14-compose-producao.md`:

````markdown
## Observação diária (Plano 4)

Relatório só leitura de um pregão (as-of o relógio do banco, sem chamada a provider e sem escrita):

```bash
docker compose exec api python -m virtual_orders.observation report --day 2026-09-15            # Markdown
docker compose exec api python -m virtual_orders.observation report --day 2026-09-15 --format json
docker compose exec api python -m virtual_orders.observation summary --from 2026-09-15 --to 2026-09-26
```

- Saídas: `0` ok; `1` erro interno (`INTERNAL_ERROR`, ou `DATABASE_ERROR` para erro de SQL que não é de conexão; só o tipo); `2` uso inválido ou `DATABASE_URL` ausente; `3` dia sem pregão, no futuro ou intervalo inválido (`OBSERVATION_REQUEST_INVALID`); `4` banco inacessível (`OperationalError`, `InterfaceError` ou timeout do pool).
- Pela API/dashboard: `GET /observation/report?day=…` e `GET /observation/summary?from=…&to=…`; página "Observação". Cada pedido lê um único snapshot do banco.
- Com `N8N_WEBHOOK_URL`, o fim de dia enfileira um alerta `OBSERVATION_DAILY:<pregão>` só com contagens (fotografia do primeiro fim de dia que roda; `complete: false`). O primeiro enfileiramento vence: se ele falhar (log `observation alert failed: <Tipo>`) e o pregão se resolver no fim de dia seguinte, não há alerta para esse pregão — use a CLI. Para o dia inteiro, rode o relatório depois da meia-noite ET.
- Reinícios do worker vêm de `worker_sessions`. "Sessão aberta no fim" = sem linha de parada: o worker está rodando **ou** morreu sem passar pelo `finally`; só o próximo início distingue. "Fins sem parada" (SIGKILL, OOM, queda do host) aparecem no relatório do pregão em que o worker **voltou** a iniciar — confira `docker compose ps`/`docker inspect` do contêiner.
- A pressão × resultado é **estimativa descritiva** sobre poucos trades: não é evidência causal nem sinal de trading.
````

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest -p no:cacheprovider -o addopts="" -q tests/integration/test_observation_cli.py tests/test_import_boundaries.py && uv run ruff check src tests migrations && uv run mypy`
Expected: PASS; ruff e mypy limpos.

- [ ] **Step 5: Commit**

```bash
git add src/virtual_orders/observation tests/integration/test_observation_cli.py tests/test_import_boundaries.py \
  docs/superpowers/runbooks/2026-09-14-compose-producao.md
git commit -m "feat(observation): read-only CLI for the daily report and summary"
```

---

### Task 9: Alerta n8n `OBSERVATION_DAILY` no fim de dia (D70d)

**Files:**
- Modify: `src/virtual_orders/alerts/outbox.py` (`AlertKind.OBSERVATION_DAILY`)
- Create: `src/virtual_orders/alerts/observation.py`
- Modify: `src/virtual_orders/worker/jobs.py` (import e gancho em `end_of_day`)
- Modify: `tests/integration/worker/test_jobs.py` (testes acrescentados)

**Interfaces:**
- Consumes: `enqueue_alert`, `AlertKind` (outbox); `session_window` (Task 4); `build_observation_report`, `observation_snapshot`, `ObservationReport` (Task 6); `acquire_data_as_of`; `alert_outbox.kind` aceita o tipo (Task 1).
- Produces:
  - `AlertKind.OBSERVATION_DAILY = "OBSERVATION_DAILY"`;
  - `OBSERVATION_ALERT_FIELDS: tuple[str, ...]`; `observation_alert_document(report: ObservationReport) -> dict[str, Any]`;
  - `enqueue_observation_alert(engine: Engine, *, session_day: date) -> bool` (chave `OBSERVATION_DAILY:<ISO>`, `True` só quando nova).

- [ ] **Step 1: Write the failing job tests**

Em `tests/integration/worker/test_jobs.py`, acrescente aos imports:

```python
import logging
from datetime import date

from virtual_orders.alerts.observation import OBSERVATION_ALERT_FIELDS, enqueue_observation_alert
```

e ao final:

```python
def observation_documents(engine):
    with engine.connect() as conn:
        return list(conn.execute(select(tables.alert_outbox.c.document)
                                 .where(tables.alert_outbox.c.kind == "OBSERVATION_DAILY")).scalars())


def a_closed_trade_then_end_of_day(worker, jobs):
    submit_default(worker.services.engine)
    worker.bars.load(scenario_bars())
    for hm in ("10:30", "11:30", "13:00"):
        worker.clock.set(et(DAY, hm))
        assert jobs.live_cycle() == JobResult("live_cycle", True, "COMPLETED")
    worker.clock.set(et(DAY, "16:30"))
    return jobs.end_of_day()


def test_end_of_day_enqueues_one_observation_alert_per_session_with_counts_only(worker):
    engine = worker.services.engine
    assert a_closed_trade_then_end_of_day(worker, WorkerJobs(worker.services)) == JobResult("end_of_day", True,
                                                                                            "COMPLETED")

    (document,) = observation_documents(engine)
    assert set(document) == set(OBSERVATION_ALERT_FIELDS) | {"schema_version", "alert_key", "kind"}
    assert (document["kind"], document["alert_key"], document["session_day"]) == (
        "OBSERVATION_DAILY", "OBSERVATION_DAILY:2025-11-25", DAY)
    counts = (document["trades_closed"], document["fills"], document["sum_r"], document["median_bar_latency_seconds"])
    assert counts == (1, 1, "1.75", 3900)
    assert all(not isinstance(value, list) for value in document.values())  # no ticker, feed or order lists
    assert "AAPL" not in str(document) and "fake_feed" not in str(document)
    assert enqueue_observation_alert(engine, session_day=date(2025, 11, 25)) is False  # one per session
    assert len(observation_documents(engine)) == 1


def test_no_observation_alert_without_a_webhook(worker):
    disabled = WorkerJobs(replace(worker.services, alert_sink=None))
    assert a_closed_trade_then_end_of_day(worker, disabled) == JobResult("end_of_day", True, "COMPLETED")
    assert observation_documents(worker.services.engine) == []


def test_a_failing_observation_alert_never_fails_the_end_of_day_job(worker, monkeypatch, caplog):
    def exploding(*args, **kwargs):
        raise RuntimeError("observation-secret-text")

    monkeypatch.setattr(jobs_module, "enqueue_observation_alert", exploding)
    with caplog.at_level(logging.ERROR, logger="virtual_orders.worker"):
        result = a_closed_trade_then_end_of_day(worker, WorkerJobs(worker.services))
    assert result == JobResult("end_of_day", True, "COMPLETED")
    assert "observation alert failed: RuntimeError" in caplog.text
    assert "observation-secret-text" not in caplog.text  # D70: the type only, never the message or a traceback
    assert any(key.startswith("END_OF_DAY_SUMMARY:2025-11-25:") for key in outbox_keys(worker.services.engine))
    assert observation_documents(worker.services.engine) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest -p no:cacheprovider -o addopts="" -q tests/integration/worker/test_jobs.py`
Expected: FAIL com `ModuleNotFoundError: No module named 'virtual_orders.alerts.observation'`.

- [ ] **Step 3: Implement the alert and the job hook**

Em `src/virtual_orders/alerts/outbox.py`, acrescente ao `AlertKind`:

```python
    OBSERVATION_DAILY = "OBSERVATION_DAILY"
```

`src/virtual_orders/alerts/observation.py`:

```python
"""End-of-day observation alert (Plan 4, D70): counts and codes only, one per session. n8n is never in the critical
path (spec 1.2 item 7): the job enqueues it inside a contained block after the session's work is done."""

from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Any

from sqlalchemy import Engine

from virtual_orders.alerts.outbox import AlertKind, enqueue_alert
from virtual_orders.marketdata.asof import acquire_data_as_of
from virtual_orders.readmodels.observation import ObservationReport, build_observation_report, observation_snapshot
from virtual_orders.readmodels.observation_window import session_window

OBSERVATION_ALERT_FIELDS = (
    "session_day", "as_of", "complete", "provider_failures", "provider_failure_codes", "consecutive_live_failures",
    "expected_bars", "missing_bars", "data_gaps", "actionability_requests", "actionability_unverifiable", "rechecks",
    "recheck_statuses", "alert_delivery_failures", "alerts_expired", "worker_starts", "worker_restarts",
    "unclean_worker_ends", "health_transitions", "health_state_at_end", "orders_created", "fills", "trades_closed",
    "excluded_needs_review", "sum_r", "mean_r", "median_bar_latency_seconds", "p90_bar_latency_seconds",
)


def observation_alert_document(report: ObservationReport) -> dict[str, Any]:
    codes: Counter[str] = Counter()
    for item in report.provider_failures.by_run_kind:
        codes.update(item.codes)
    document: dict[str, Any] = {
        "session_day": report.session_day, "as_of": report.as_of, "complete": report.complete,
        "provider_failures": report.provider_failures.total_failures,
        "provider_failure_codes": dict(sorted(codes.items())),
        "consecutive_live_failures": report.provider_failures.consecutive_live_max,
        "expected_bars": report.data_quality.expected_bars, "missing_bars": report.data_quality.missing_bars,
        "data_gaps": report.data_quality.gaps, "actionability_requests": report.actionability.requests,
        "actionability_unverifiable": report.actionability.unverifiable, "rechecks": report.rechecks.rows,
        "recheck_statuses": report.rechecks.statuses,
        "alert_delivery_failures": report.alerts.attempts.get("FAILED", 0),
        "alerts_expired": report.alerts.attempts.get("EXPIRED", 0), "worker_starts": report.worker.starts,
        "worker_restarts": report.worker.restarts, "unclean_worker_ends": report.worker.unclean_ends,
        "health_transitions": report.health.transitions, "health_state_at_end": report.health.state_at_end,
        "orders_created": sum(report.trades.created.values()), "fills": report.trades.filled,
        "trades_closed": report.trades.stats.trades, "excluded_needs_review": report.trades.excluded_needs_review,
        "sum_r": report.trades.stats.sum_r, "mean_r": report.trades.stats.mean_r,
        "median_bar_latency_seconds": report.latency.bar.median_seconds,
        "p90_bar_latency_seconds": report.latency.bar.p90_seconds,
    }
    return document


def enqueue_observation_alert(engine: Engine, *, session_day: date) -> bool:
    """Idempotent by `OBSERVATION_DAILY:<session>`: the first end-of-day run of the session wins (D70).

    The report is read in one read-only REPEATABLE READ snapshot (D61); the outbox row is written afterwards in its
    own write transaction."""
    window = session_window(session_day, acquire_data_as_of(engine))
    with observation_snapshot(engine) as conn:
        document = observation_alert_document(build_observation_report(conn, window))
    with engine.begin() as conn:
        return enqueue_alert(
            conn, alert_key=f"OBSERVATION_DAILY:{session_day.isoformat()}", kind=AlertKind.OBSERVATION_DAILY,
            document=document, subject=session_day.isoformat(),
        )
```

Em `src/virtual_orders/worker/jobs.py`, acrescente o import:

```python
from virtual_orders.alerts.observation import enqueue_observation_alert
```

e, dentro de `end_of_day`, substitua o bloco `if s.alert_sink is not None:` por:

```python
        if s.alert_sink is not None:
            try:
                state = build_health_report(s.engine, now=now, eval_interval_minutes=s.eval_interval_minutes).state
                enqueue_end_of_day_summary(s.engine, report, session_day=session.day, health_state=state,
                                           recheck=recheck)
            except Exception as exc:  # noqa: BLE001 - n8n is never in the critical path
                logger.error("end-of-day summary failed: %s", type(exc).__name__, exc_info=True)
            try:
                enqueue_observation_alert(s.engine, session_day=session.day)  # D70: counts only, one per session
            except Exception as exc:  # noqa: BLE001 - n8n is never in the critical path
                logger.error("observation alert failed: %s", type(exc).__name__)  # D70: type only, no exc_info
```

O `exc_info=True` da linha pré-existente do resumo de fim de dia fica como está (fora do escopo); a linha nova nunca leva traceback nem mensagem, porque o texto pode trazer SQL, valores ligados ou o host do banco. O teste do Step 1 compara o conjunto de chaves do documento gravado com `OBSERVATION_ALERT_FIELDS`, então a lista publicada e o documento não divergem em silêncio.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest -p no:cacheprovider -o addopts="" -q tests/integration/worker tests/integration/test_alert_outbox.py tests/integration/test_schema_0005.py && uv run ruff check src tests migrations && uv run mypy`
Expected: PASS; ruff e mypy limpos.

- [ ] **Step 5: Commit**

```bash
git add src/virtual_orders/alerts/outbox.py src/virtual_orders/alerts/observation.py src/virtual_orders/worker/jobs.py \
  tests/integration/worker/test_jobs.py
git commit -m "feat(alerts): counts-only OBSERVATION_DAILY alert enqueued once per session at end of day"
```

---

### Task 10: Contrato gravado das rotas de observação e cliente do dashboard (D57, D71, D72)

**Files:**
- Modify: `tests/integration/api/test_dashboard_contract.py` (canonicalização da latência por relógio real; walk das rotas novas)
- Create (gravadas): `dashboard/tests/fixtures/api/observation_report.json`, `dashboard/tests/fixtures/api/observation_summary.json`
- Modify: `tests/test_import_boundaries.py` (`test_boundary_scan_covers_the_3c_modules`: 17 → 19 fixtures)
- Modify: `dashboard/dashboard/client.py` (`observation_report`, `observation_summary`)
- Modify: `dashboard/tests/test_client.py`, `dashboard/tests/test_api_contract.py`

**Interfaces:**
- Consumes: rotas da Task 7; `tables.worker_sessions`, `tables.health_state_log`.
- Produces:
  - `ApiClient.observation_report(day: date) -> JsonObject` (`GET /observation/report?day=`);
  - `ApiClient.observation_summary(start: date, end: date) -> JsonObject` (`GET /observation/summary?from=&to=`);
  - fixtures `observation_report.json` (pregão `2025-11-25`) e `observation_summary.json` (`2025-11-25` a `2025-11-26`) usadas nas Tasks 11 e 12.

- [ ] **Step 1: Write the failing client tests**

Em `dashboard/tests/test_client.py`, acrescente ao final:

```python
def test_observation_routes_send_iso_dates():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"session_day": "2025-11-25"})

    api = client_for(handler)
    assert api.observation_report(date(2025, 11, 25)) == {"session_day": "2025-11-25"}
    api.observation_summary(date(2025, 11, 12), date(2025, 11, 25))
    assert (seen[0].url.path, dict(seen[0].url.params)) == ("/observation/report", {"day": "2025-11-25"})
    assert (seen[1].url.path, dict(seen[1].url.params)) == (
        "/observation/summary", {"from": "2025-11-12", "to": "2025-11-25"})
    assert seen[0].headers["X-API-Key"] == "secret-key"
```

Em `dashboard/tests/test_api_contract.py`, acrescente `from datetime import date` aos imports e, ao final:

```python
def test_the_client_reads_the_recorded_observation_responses():
    report = serving("observation_report").observation_report(date(2025, 11, 25))
    assert (report["session_day"], report["trades"]["closed"], report["trades"]["replay_closed"]) == (
        "2025-11-25", 2, 2)
    assert report["pressure"]["estimate"] is True and report["pressure"]["method"] == "OHLCV_PRESSURE_ESTIMATE_V1"
    summary = serving("observation_summary").observation_summary(date(2025, 11, 25), date(2025, 11, 26))
    assert [day["session_day"] for day in summary["days"]] == ["2025-11-25", "2025-11-26"]
```

- [ ] **Step 2: Run the dashboard tests to verify they fail**

Run: `uv run --directory dashboard pytest -p no:cacheprovider -o addopts="" -q tests/test_client.py tests/test_api_contract.py`
Expected: FAIL — `AttributeError: 'ApiClient' object has no attribute 'observation_report'` e `FileNotFoundError` da fixture ausente.

- [ ] **Step 3: Add the client methods**

Em `dashboard/dashboard/client.py`, depois de `quality_overview`:

```python
    def observation_report(self, day: date) -> JsonObject:
        return self._request("GET", "/observation/report", params={"day": day})

    def observation_summary(self, start: date, end: date) -> JsonObject:
        return self._request("GET", "/observation/summary", params={"from": start, "to": end})
```

- [ ] **Step 4: Extend the recorded walk**

Em `tests/integration/api/test_dashboard_contract.py`, acrescente `from uuid import UUID, uuid4` (substituindo `from uuid import UUID`) e, antes de `def _dump_json`:

```python
_WALL_CLOCK_LATENCY_STATS = frozenset({"recorded", "recorded_latency"})


def _pin_wall_clock_latency(value: Any, key: str | None = None) -> Any:
    """D72: latency measured to recorded_at uses the real database clock, so it differs on every recording; it is
    pinned to 0 like the *_hash fields (only its shape is compared)."""
    if isinstance(value, dict):
        if key in _WALL_CLOCK_LATENCY_STATS:
            return {name: (item if name == "count" or item is None else 0) for name, item in value.items()}
        return {name: (0 if name == "recorded_latency_seconds" else _pin_wall_clock_latency(item, name))
                for name, item in value.items()}
    if isinstance(value, list):
        return [_pin_wall_clock_latency(item) for item in value]
    return value
```

No início de `_canonicalize`, antes do `if name == "portfolio_virtual":`:

```python
    if name in ("observation_report", "observation_summary"):
        return _pin_wall_clock_latency(body)
```

Em `test_dashboard_contract_matches_the_recorded_api_responses`, logo antes de `if not RECORD:`:

```python
    # Plan 4 (D72): recorded after every earlier fixture, so re-recording leaves those files byte-identical. Health log
    # and worker rows carry explicit instants inside the recorded session (their columns use the database clock).
    session_id = uuid4()
    with api.services.engine.begin() as conn:
        for state, codes, hm in (("DEGRADED", ["LIVE_CYCLE_STALE"], "11:00"), ("HEALTHY", [], "11:10")):
            conn.execute(tables.health_state_log.insert().values(state=state, cause_codes=codes,
                                                                 observed_at=et(DAY, hm)))
        conn.execute(tables.worker_sessions.insert(), [
            {"session_id": session_id, "event": "STARTED", "code_version": "test-sha", "host_fingerprint": None,
             "exit_code": None, "reason": None, "recorded_at": et(DAY, "09:00")},
            {"session_id": session_id, "event": "STOPPED", "code_version": None, "host_fingerprint": None,
             "exit_code": 0, "reason": "SIGNAL", "recorded_at": et(DAY, "17:00")},
        ])
    get("observation_report", "/observation/report", day=DAY)
    get("observation_summary", "/observation/summary", **{"from": DAY, "to": NEXT})
```

Em `tests/test_import_boundaries.py`, em `test_boundary_scan_covers_the_3c_modules`, troque `== 17  # D57` por `== 19  # D57 + Plan 4 D72`.

- [ ] **Step 5: Record the fixtures and prove they are deterministic**

Na mesma sessão de shell:

```bash
docker compose -f docker-compose.test.yml up -d --wait
DASHBOARD_CONTRACT_RECORD=1 uv run pytest -p no:cacheprovider -o addopts="" -q tests/integration/api/test_dashboard_contract.py
git status --porcelain dashboard/tests/fixtures/api
SNAPSHOT="$(mktemp -d)"
cp dashboard/tests/fixtures/api/*.json "$SNAPSHOT"/
DASHBOARD_CONTRACT_RECORD=1 uv run pytest -p no:cacheprovider -o addopts="" -q tests/integration/api/test_dashboard_contract.py
diff -r "$SNAPSHOT" dashboard/tests/fixtures/api && echo "fixtures are deterministic"
rm -rf "$SNAPSHOT"
grep -c '"estimate": true' dashboard/tests/fixtures/api/observation_report.json
grep -E 'hunter2|secret|X-API-Key|test-api-key' dashboard/tests/fixtures/api/observation_*.json || echo "no secrets"
```

Expected: `git status` mostra **só** `?? dashboard/tests/fixtures/api/observation_report.json` e `?? dashboard/tests/fixtures/api/observation_summary.json` (as 17 existentes não mudam); `fixtures are deterministic`; `1`; `no secrets`. Se alguma fixture existente mudar, pare: a ordem do walk foi alterada.

- [ ] **Step 6: Run both projects**

Run: `uv run pytest -p no:cacheprovider -o addopts="" -q tests/integration/api/test_dashboard_contract.py tests/test_import_boundaries.py && uv run ruff check src tests migrations && uv run mypy && uv run --directory dashboard pytest -p no:cacheprovider -o addopts="" -q && uv run --directory dashboard ruff check . && uv run --directory dashboard mypy`
Expected: PASS nos dois projetos (o contrato agora em modo de comparação); ruff e mypy limpos.

- [ ] **Step 7: Commit**

```bash
git add tests/integration/api/test_dashboard_contract.py tests/test_import_boundaries.py \
  dashboard/tests/fixtures/api/observation_report.json dashboard/tests/fixtures/api/observation_summary.json \
  dashboard/dashboard/client.py dashboard/tests/test_client.py dashboard/tests/test_api_contract.py
git commit -m "test(contract): record observation responses and add the dashboard client calls"
```

---

### Task 11: View-models e figuras da página "Observação" (D71)

**Files:**
- Modify: `dashboard/dashboard/viewmodels.py`
- Modify: `dashboard/dashboard/charts.py`
- Modify: `dashboard/tests/test_viewmodels.py`, `dashboard/tests/test_charts.py`, `dashboard/tests/test_api_contract.py`

**Interfaces:**
- Consumes: fixtures `observation_report.json`/`observation_summary.json` (Task 10); `MetricCard`, `fmt_r`, `fmt_ts`, `EMPTY`, `ERROR_MESSAGES` existentes.
- Produces (em `dashboard.viewmodels`):
  - `ALIGNMENT_LABELS`, `STRENGTH_LABELS`, `OBSERVATION_PRESSURE_TITLE`; `ERROR_MESSAGES["OBSERVATION_REQUEST_INVALID"]`;
  - `fmt_duration(seconds: Any) -> str`; `pressure_bucket_label(bucket: Mapping[str, Any]) -> str`;
  - `ObservationView(title, status, cards: list[MetricCard], operations, trades, trades_caption, latency, pressure, pressure_method, pressure_disclaimer, pressure_note)`; `observation_view(report: Mapping[str, Any]) -> ObservationView`;
  - `observation_day_rows(summary: Mapping[str, Any]) -> list[dict[str, str]]`.
- Produces (em `dashboard.charts`): `daily_r_figure(days: Sequence[Mapping[str, Any]]) -> go.Figure`; `pressure_buckets_figure(buckets: Sequence[Mapping[str, Any]], *, method: str) -> go.Figure`.

- [ ] **Step 1: Write the failing tests**

Em `dashboard/tests/test_viewmodels.py`, acrescente `observation_day_rows`, `observation_view` e `fmt_duration` à lista importada de `dashboard.viewmodels` (ordem alfabética) e, ao final:

```python
OBSERVATION_REPORT = {
    "session_day": "2025-11-25", "as_of": "2025-11-25T20:00:00+00:00", "complete": False,
    "provider_failures": {"total_failures": 4, "consecutive_live_max": 3, "by_run_kind": [
        {"run_kind": "LIVE", "codes": {"SOURCE_ERROR": 3}}, {"run_kind": "OPENING", "codes": {"SOURCE_ERROR": 1}}]},
    "data_quality": {"expected_bars": 390, "missing_bars": 35, "coverage_pct": "91.03", "gaps": 1, "gap_minutes": 35,
                     "not_evaluated": {"PROVIDER_FAILURE": 1}},
    "actionability": {"requests": 6, "unverifiable": 3,
                      "unverifiable_causes": {"MISSING_MINUTES": 2, "PROVIDER_FAILURE": 1}},
    "rechecks": {"rows": 2, "statuses": {"EVALUATED": 2}, "about_this_session": {}},
    "alerts": {"attempts": {"DELIVERED": 1, "FAILED": 3}, "failure_types": {"ConnectTimeout": 2, "HTTP_502": 1},
               "pending_at_end": 2},
    "worker": {"starts": 3, "restarts": 3, "unclean_ends": 1, "stops": {"LOCK_LOST": 1}, "open_session_at_end": True},
    "health": {"state_at_start": "HEALTHY", "state_at_end": "DEGRADED", "transitions": 2, "log_rows": 3,
               "seconds_by_state": {"DEGRADED": 900, "HEALTHY": 36000}},
    "trades": {"stats": {"trades": 1, "sum_r": "1.75"}, "excluded_needs_review": 1, "replay_closed": 2, "rows": [
        {"order_id": "0f3e2d1c-0000-4000-8000-000000000001", "ticker": "AAPL", "origin": "AUTO_STRATEGY",
         "closed_at": "2025-11-25T17:50:00+00:00", "r_multiple": "1.75", "mfe_r": "2.375", "mae_r": "-0.1",
         "needs_review": False, "pressure_alignment": "ALIGNED", "pressure_strength": "STRONG"}]},
    "latency": {
        "bar": {"count": 2, "median_seconds": 3900, "p90_seconds": 3900, "max_seconds": 3900},
        "recorded": {"count": 2, "median_seconds": 4000, "p90_seconds": 4100, "max_seconds": 4100},
        "bar_by_origin": {"AUTO_STRATEGY": {"count": 2, "median_seconds": 3900, "p90_seconds": 3900,
                                            "max_seconds": 3900}},
        "unfilled": {"EXPIRED": 1},
    },
    "pressure": {"method": "OHLCV_PRESSURE_ESTIMATE_V1", "disclaimer": "Estimate, not order flow.",
                 "association_note": "Descriptive counts, not causal.", "unavailable_reasons": {"NO_BARS": 1},
                 "buckets": [
                     {"alignment": "ALIGNED", "strength": "STRONG", "trades": 5, "wins": 4, "sum_r": "3.5",
                      "mean_r": "0.7"},
                     {"alignment": "UNAVAILABLE", "strength": None, "trades": 1, "wins": 0, "sum_r": "-1",
                      "mean_r": None}]},
}
OBSERVATION_SUMMARY = {"sessions": 2, "days": [
    {"session_day": "2025-11-25", "complete": True, "provider_failures": 4, "expected_bars": 390, "missing_bars": 35,
     "actionability_unverifiable": 3, "rechecks": 2, "alert_failures": 3, "alerts_expired": 0, "worker_restarts": 3,
     "unclean_worker_ends": 1, "health_transitions": 2, "fills": 2, "trades": 1, "sum_r": "1.75"},
    {"session_day": "2025-11-26", "complete": False, "provider_failures": 0, "expected_bars": 0, "missing_bars": 0,
     "actionability_unverifiable": 0, "rechecks": 0, "alert_failures": 0, "alerts_expired": 0, "worker_restarts": 0,
     "unclean_worker_ends": 0, "health_transitions": 0, "fills": 0, "trades": 0, "sum_r": "0"},
]}


def test_durations_are_shown_in_hours_minutes_and_seconds():
    assert (fmt_duration(None), fmt_duration(59), fmt_duration(3900), fmt_duration(36000)) == (
        EMPTY, "0m59s", "1h05m00s", "10h00m00s")


def test_the_observation_view_turns_every_section_into_display_strings():
    view = observation_view(OBSERVATION_REPORT)
    assert (view.title, view.status) == ("Pregão 2025-11-25", "Janela parcial (as-of 2025-11-25 15:00 ET)")
    cards = {card.label: card.value for card in view.cards}
    assert cards == {
        "Falhas de provider": "4", "Minutos ausentes": "35/390", "503 de actionability": "3",
        "Linhas de recheck": "2", "Falhas de entrega": "3", "Reinícios do worker": "3", "Transições de saúde": "2",
        "Trades fechados": "1", "R somado": "+1.75R", "Latência mediana": "1h05m00s",
    }
    details = {row["Métrica"]: (row["Valor"], row["Detalhe"]) for row in view.operations}
    assert details["Falhas de provider"] == ("4", "SOURCE_ERROR: 4 · maior sequência LIVE: 3")
    assert details["Candles ausentes"] == ("35/390", "cobertura 91.03% · DATA_GAP 1 (35 min) · não avaliadas: "
                                                     "PROVIDER_FAILURE: 1")
    assert details["503 de actionability"] == ("3/6", "MISSING_MINUTES: 2, PROVIDER_FAILURE: 1")
    assert details["Entrega de alertas"] == ("3 falha(s)", "tentativas: DELIVERED: 1, FAILED: 3 · tipos: "
                                                          "ConnectTimeout: 2, HTTP_502: 1 · pendentes no fim: 2")
    assert details["Worker"] == ("3 reinício(s)", "inícios 3 · fins sem parada 1 · paradas: LOCK_LOST: 1 · "
                                                  "sessão aberta no fim: sim")
    assert details["Saúde"] == ("2 transição(ões)", "HEALTHY → DEGRADED · linhas do log 3 · DEGRADED: 15m00s, "
                                                     "HEALTHY: 10h00m00s")
    assert details["DATA_QUALITY_RECHECK"] == ("2", "status: EVALUATED: 2 · sobre este pregão: —")
    assert view.trades == [{"Ordem": "0f3e2d1c", "Ticker": "AAPL", "Origem": "AUTO_STRATEGY",
                            "Fechada": "2025-11-25 12:50 ET", "R": "+1.75R", "MFE": "+2.38R", "MAE": "-0.10R",
                            "Revisão": "não", "Pressão (estimativa)": "a favor · forte"}]
    assert view.trades_caption == ("Excluídas por revisão: 1 · replay fechadas (fora das métricas): 2 · "
                                   "sem fill: EXPIRED: 1")
    assert [row["Medida"] for row in view.latency] == ["Candle do fill", "Gravação do fill", "Candle · AUTO_STRATEGY"]
    assert (view.latency[1]["Mediana"], view.latency[1]["p90"]) == ("1h06m40s", "1h08m20s")
    assert view.pressure == [  # n beside every mean; no mean below 5 trades (D66)
        {"Pressão": "a favor · forte", "Trades": "5", "Wins": "4", "R somado": "+3.50R", "R médio (n)": "+0.70R (n=5)"},
        {"Pressão": "indisponível", "Trades": "1", "Wins": "0", "R somado": "-1.00R", "R médio (n)": "— (n=1)"},
    ]
    assert (view.pressure_method, view.pressure_disclaimer, view.pressure_note) == (
        "OHLCV_PRESSURE_ESTIMATE_V1", "Estimate, not order flow.", "Descriptive counts, not causal.")


def test_summary_rows_and_the_observation_error_message():
    rows = observation_day_rows(OBSERVATION_SUMMARY)
    assert rows[0] == {"Pregão": "2025-11-25", "Completo": "sim", "Falhas de provider": "4", "Ausentes": "35/390",
                       "503": "3", "Rechecks": "2", "Falhas de alerta": "3", "Reinícios": "3", "Fins sem parada": "1",
                       "Transições": "2", "Fills": "2", "Trades": "1", "R": "+1.75R"}
    assert (rows[1]["Completo"], rows[1]["R"]) == ("não", "+0.00R")
    error = ApiRequestFailed(422, "OBSERVATION_REQUEST_INVALID", detail={"errors": ["NOT_A_SESSION:2025-11-29"]})
    assert describe_api_error(error) == "Pedido de observação inválido. Códigos: NOT_A_SESSION:2025-11-29."
```

Em `dashboard/tests/test_charts.py`, troque o import de `dashboard.charts` por `from dashboard.charts import candlestick_figure, cumulative_r_figure, daily_r_figure, pressure_buckets_figure` e acrescente:

```python
def test_daily_r_and_pressure_bucket_figures():
    daily = daily_r_figure([{"session_day": "2025-11-25", "sum_r": "1.75"},
                            {"session_day": "2025-11-26", "sum_r": "-1"}])
    (bars,) = daily.data
    assert (bars.type, list(bars.x), list(bars.y)) == ("bar", ["2025-11-25", "2025-11-26"], [1.75, -1.0])
    buckets = [{"alignment": "ALIGNED", "strength": "STRONG", "trades": 5, "mean_r": "1.75"},
               {"alignment": "UNAVAILABLE", "strength": None, "trades": 2, "mean_r": None}]
    figure = pressure_buckets_figure(buckets, method="OHLCV_PRESSURE_ESTIMATE_V1")
    (trace,) = figure.data
    assert (list(trace.x), list(trace.y), list(trace.text)) == (
        ["a favor · forte", "indisponível"], [1.75, None], ["n=5", "n<5"])  # no bar drawn at 0 for n < 5
    assert "OHLCV_PRESSURE_ESTIMATE_V1" in figure.layout.title.text
    assert "não é fluxo de ordens" in figure.layout.title.text
```

Em `dashboard/tests/test_api_contract.py`, acrescente `daily_r_figure` ao import de `dashboard.charts`, `observation_day_rows` e `observation_view` ao import de `dashboard.viewmodels`, e ao final:

```python
def test_observation_views_from_the_recorded_responses():
    view = observation_view(load("observation_report"))
    cards = {card.label: card.value for card in view.cards}
    assert (cards["Trades fechados"], cards["R somado"], cards["Latência mediana"]) == ("2", "+3.50R", "1h05m00s")
    assert view.status == "Janela completa"
    assert [row["Pressão"] for row in view.pressure] == ["indisponível"]  # the 09:00 signal had no bar before it
    assert view.pressure_method == "OHLCV_PRESSURE_ESTIMATE_V1"
    operations = {row["Métrica"]: row for row in view.operations}
    assert operations["Saúde"]["Valor"] == "2 transição(ões)" and "DEGRADED: 10m00s" in operations["Saúde"]["Detalhe"]
    assert operations["Worker"]["Valor"] == "0 reinício(s)"
    assert "replay fechadas (fora das métricas): 2" in view.trades_caption
    summary = load("observation_summary")
    assert [(row["Pregão"], row["Trades"]) for row in observation_day_rows(summary)] == [
        ("2025-11-25", "2"), ("2025-11-26", "0")]
    assert list(daily_r_figure(summary["days"]).data[0].y) == [3.5, 0.0]
```

- [ ] **Step 2: Run the dashboard tests to verify they fail**

Run: `uv run --directory dashboard pytest -p no:cacheprovider -o addopts="" -q tests/test_viewmodels.py tests/test_charts.py tests/test_api_contract.py`
Expected: FAIL com `ImportError: cannot import name 'observation_view'`.

- [ ] **Step 3: Implement the view-models and figures**

Em `dashboard/dashboard/viewmodels.py`, acrescente ao dicionário `ERROR_MESSAGES`:

```python
    "OBSERVATION_REQUEST_INVALID": "Pedido de observação inválido.",
```

e ao final do arquivo:

```python
OBSERVATION_PRESSURE_TITLE = "Pressão estimada (OHLCV) × resultado — associação descritiva, não fluxo de ordens"
ALIGNMENT_LABELS = {"ALIGNED": "a favor", "OPPOSED": "contra", "NEUTRAL": "neutra", "UNAVAILABLE": "indisponível"}
STRENGTH_LABELS = {"WEAK": "fraca", "MODERATE": "moderada", "STRONG": "forte"}


def fmt_duration(seconds: Any) -> str:
    if seconds is None:
        return EMPTY
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}h{minutes:02d}m{secs:02d}s" if hours else f"{minutes}m{secs:02d}s"


def _pairs(mapping: Mapping[str, Any] | None) -> str:
    return ", ".join(f"{key}: {value}" for key, value in sorted((mapping or {}).items())) or EMPTY


def pressure_bucket_label(bucket: Mapping[str, Any]) -> str:
    alignment = ALIGNMENT_LABELS.get(str(bucket.get("alignment")), str(bucket.get("alignment")))
    strength = bucket.get("strength")
    return alignment if strength is None else f"{alignment} · {STRENGTH_LABELS.get(str(strength), str(strength))}"


@dataclass(frozen=True)
class ObservationView:
    title: str
    status: str
    cards: list[MetricCard]
    operations: list[dict[str, str]]
    trades: list[dict[str, str]]
    trades_caption: str
    latency: list[dict[str, str]]
    pressure: list[dict[str, str]]
    pressure_method: str
    pressure_disclaimer: str
    pressure_note: str


def _latency_row(label: str, stats: Mapping[str, Any]) -> dict[str, str]:
    return {"Medida": label, "Fills": str(stats["count"]), "Mediana": fmt_duration(stats.get("median_seconds")),
            "p90": fmt_duration(stats.get("p90_seconds")), "Máx": fmt_duration(stats.get("max_seconds"))}


def observation_view(report: Mapping[str, Any]) -> ObservationView:
    failures, quality, action = report["provider_failures"], report["data_quality"], report["actionability"]
    rechecks, alerts, worker, health = report["rechecks"], report["alerts"], report["worker"], report["health"]
    trades, latency, pressure = report["trades"], report["latency"], report["pressure"]
    codes: dict[str, int] = {}
    for item in failures["by_run_kind"]:
        for code, occurrences in item["codes"].items():
            codes[code] = codes.get(code, 0) + int(occurrences)
    failed_attempts = int(alerts["attempts"].get("FAILED", 0))
    coverage = quality.get("coverage_pct")
    seconds = ", ".join(
        f"{state}: {fmt_duration(value)}" for state, value in sorted(health["seconds_by_state"].items())
    )
    return ObservationView(
        title=f"Pregão {report['session_day']}",
        status="Janela completa" if report["complete"] else f"Janela parcial (as-of {fmt_ts(report['as_of'])})",
        cards=[
            MetricCard("Falhas de provider", str(failures["total_failures"]), None),
            MetricCard("Minutos ausentes", f"{quality['missing_bars']}/{quality['expected_bars']}", None),
            MetricCard("503 de actionability", str(action["unverifiable"]), None),
            MetricCard("Linhas de recheck", str(rechecks["rows"]), None),
            MetricCard("Falhas de entrega", str(failed_attempts), None),
            MetricCard("Reinícios do worker", str(worker["restarts"]), None),
            MetricCard("Transições de saúde", str(health["transitions"]), None),
            MetricCard("Trades fechados", str(trades["stats"]["trades"]), None),
            MetricCard("R somado", fmt_r(trades["stats"]["sum_r"]), None),
            MetricCard("Latência mediana", fmt_duration(latency["bar"]["median_seconds"]), None),
        ],
        operations=[
            {"Métrica": "Falhas de provider", "Valor": str(failures["total_failures"]),
             "Detalhe": f"{_pairs(codes)} · maior sequência LIVE: {failures['consecutive_live_max']}"},
            {"Métrica": "Candles ausentes", "Valor": f"{quality['missing_bars']}/{quality['expected_bars']}",
             "Detalhe": f"cobertura {EMPTY if coverage is None else f'{coverage}%'} · DATA_GAP {quality['gaps']} "
                        f"({quality['gap_minutes']} min) · não avaliadas: {_pairs(quality['not_evaluated'])}"},
            {"Métrica": "503 de actionability", "Valor": f"{action['unverifiable']}/{action['requests']}",
             "Detalhe": _pairs(action["unverifiable_causes"])},
            {"Métrica": "DATA_QUALITY_RECHECK", "Valor": str(rechecks["rows"]),
             "Detalhe": f"status: {_pairs(rechecks['statuses'])} · sobre este pregão: "
                        f"{_pairs(rechecks['about_this_session'])}"},
            {"Métrica": "Entrega de alertas", "Valor": f"{failed_attempts} falha(s)",
             "Detalhe": f"tentativas: {_pairs(alerts['attempts'])} · tipos: {_pairs(alerts['failure_types'])} · "
                        f"pendentes no fim: {alerts['pending_at_end']}"},
            {"Métrica": "Worker", "Valor": f"{worker['restarts']} reinício(s)",
             "Detalhe": f"inícios {worker['starts']} · fins sem parada {worker['unclean_ends']} · paradas: "
                        f"{_pairs(worker['stops'])} · sessão aberta no fim: "
                        f"{'sim' if worker['open_session_at_end'] else 'não'}"},
            {"Métrica": "Saúde", "Valor": f"{health['transitions']} transição(ões)",
             "Detalhe": f"{health['state_at_start']} → {health['state_at_end']} · linhas do log {health['log_rows']} · "
                        f"{seconds or EMPTY}"},
        ],
        trades=[
            {"Ordem": str(row["order_id"])[:8], "Ticker": str(row["ticker"]), "Origem": str(row["origin"]),
             "Fechada": fmt_ts(row["closed_at"]), "R": fmt_r(row["r_multiple"]), "MFE": fmt_r(row.get("mfe_r")),
             "MAE": fmt_r(row.get("mae_r")), "Revisão": "sim" if row["needs_review"] else "não",
             "Pressão (estimativa)": pressure_bucket_label({"alignment": row["pressure_alignment"],
                                                            "strength": row.get("pressure_strength")})}
            for row in trades["rows"]
        ],
        trades_caption=(f"Excluídas por revisão: {trades['excluded_needs_review']} · replay fechadas (fora das "
                        f"métricas): {trades['replay_closed']} · sem fill: {_pairs(latency['unfilled'])}"),
        latency=[
            _latency_row("Candle do fill", latency["bar"]), _latency_row("Gravação do fill", latency["recorded"]),
            *(_latency_row(f"Candle · {origin}", stats) for origin, stats in sorted(latency["bar_by_origin"].items())),
        ],
        pressure=[
            {"Pressão": pressure_bucket_label(bucket), "Trades": str(bucket["trades"]), "Wins": str(bucket["wins"]),
             "R somado": fmt_r(bucket["sum_r"]),
             "R médio (n)": f"{fmt_r(bucket.get('mean_r'))} (n={bucket['trades']})"}  # D66: n beside the mean
            for bucket in pressure["buckets"]
        ],
        pressure_method=str(pressure["method"]), pressure_disclaimer=str(pressure["disclaimer"]),
        pressure_note=str(pressure["association_note"]),
    )


def observation_day_rows(summary: Mapping[str, Any]) -> list[dict[str, str]]:
    return [
        {"Pregão": str(day["session_day"]), "Completo": "sim" if day["complete"] else "não",
         "Falhas de provider": str(day["provider_failures"]),
         "Ausentes": f"{day['missing_bars']}/{day['expected_bars']}",
         "503": str(day["actionability_unverifiable"]), "Rechecks": str(day["rechecks"]),
         "Falhas de alerta": str(day["alert_failures"]), "Reinícios": str(day["worker_restarts"]),
         "Fins sem parada": str(day["unclean_worker_ends"]), "Transições": str(day["health_transitions"]),
         "Fills": str(day["fills"]), "Trades": str(day["trades"]), "R": fmt_r(day["sum_r"])}
        for day in summary["days"]
    ]
```

Em `dashboard/dashboard/charts.py`, troque `from dashboard.viewmodels import RPoint` por `from dashboard.viewmodels import RPoint, pressure_bucket_label` e acrescente ao final:

```python
def daily_r_figure(days: Sequence[Mapping[str, Any]]) -> go.Figure:
    figure = go.Figure(go.Bar(x=[str(day["session_day"]) for day in days], y=[_number(day["sum_r"]) for day in days],
                              name="R do pregão"))
    figure.update_layout(title={"text": "R somado por pregão (trades sem revisão)"}, yaxis_title="R", height=320)
    return figure


def pressure_buckets_figure(buckets: Sequence[Mapping[str, Any]], *, method: str) -> go.Figure:
    figure = go.Figure(go.Bar(
        x=[pressure_bucket_label(bucket) for bucket in buckets],
        # D66: a bucket without a mean (n < 5) gets no bar (a gap, never 0) and the label "n<5".
        y=[None if bucket.get("mean_r") is None else _number(bucket["mean_r"]) for bucket in buckets],
        text=["n<5" if bucket.get("mean_r") is None else f"n={bucket['trades']}" for bucket in buckets],
        name="R médio",
    ))
    figure.update_layout(title={"text": f"R médio por pressão estimada ({method}) — não é fluxo de ordens"},
                         yaxis_title="R", height=320)
    return figure
```

Se `fmt_r` com `Decimal("2.375")` não produzir `+2.38R` na versão instalada (o formato `+.2f` de `Decimal` arredonda com `ROUND_HALF_EVEN` do contexto: `2.375` → `2.38`), ajuste só o valor esperado do teste e registre `Ruling:`.

- [ ] **Step 4: Run the dashboard project**

Run: `uv run --directory dashboard pytest -p no:cacheprovider -o addopts="" -q && uv run --directory dashboard ruff check . && uv run --directory dashboard mypy`
Expected: PASS; ruff e mypy limpos.

- [ ] **Step 5: Commit**

```bash
git add dashboard/dashboard/viewmodels.py dashboard/dashboard/charts.py dashboard/tests/test_viewmodels.py \
  dashboard/tests/test_charts.py dashboard/tests/test_api_contract.py
git commit -m "feat(dashboard): observation view-models and figures"
```

---

### Task 12: Página "Observação" no dashboard com smoke `AppTest` (D71)

**Files:**
- Create: `dashboard/dashboard/views/observation.py`
- Modify: `dashboard/dashboard/app.py` (import e entrada `"Observação"` em `PAGES`)
- Modify: `dashboard/tests/test_app_smoke.py` (`FakeApi` e testes acrescentados)

**Interfaces:**
- Consumes: `ApiClient.observation_report/observation_summary` (Task 10); `observation_view`, `observation_day_rows`, `OBSERVATION_PRESSURE_TITLE`, `today_et` (Task 11 e existentes); `daily_r_figure`, `pressure_buckets_figure` (Task 11); `guarded`.
- Produces: `dashboard.views.observation.render(client: ApiClient) -> None`; página `"Observação"` no `st.sidebar.radio` (chave `page`).

- [ ] **Step 1: Write the failing smoke tests**

Em `dashboard/tests/test_app_smoke.py`, depois de `METRICS_GENERIC = _load("metrics")`:

```python
OBSERVATION_REPORT = _load("observation_report")
OBSERVATION_SUMMARY = _load("observation_summary")
```

no `__init__` de `FakeApi`, acrescente:

```python
        self.observation_error: Exception | None = None
```

como métodos de `FakeApi`:

```python
    def observation_report(self, day):
        if self.observation_error is not None:
            raise self.observation_error
        return OBSERVATION_REPORT

    def observation_summary(self, start, end):
        return OBSERVATION_SUMMARY
```

e ao final do arquivo:

```python
def test_observation_page_labels_the_pressure_estimate_and_shows_the_summary():
    at = open_page(FakeApi(), "Observação")
    assert at.title[0].value == "Observação"
    assert {metric.label for metric in at.metric} >= {
        "Falhas de provider", "Minutos ausentes", "503 de actionability", "Linhas de recheck", "Falhas de entrega",
        "Reinícios do worker", "Transições de saúde", "Trades fechados", "R somado", "Latência mediana"}
    captions = values(at.caption)
    assert "Método: OHLCV_PRESSURE_ESTIMATE_V1" in captions
    assert OBSERVATION_REPORT["pressure"]["disclaimer"] in captions
    assert OBSERVATION_REPORT["pressure"]["association_note"] in captions
    assert "Janela completa" in captions
    assert "Resumo: 2 pregão(ões)" in values(at.subheader)


def test_observation_page_shows_an_invalid_day_without_a_traceback():
    fake = FakeApi()
    fake.observation_error = ApiRequestFailed(422, "OBSERVATION_REQUEST_INVALID",
                                              detail={"errors": ["NOT_A_SESSION:2025-11-29"]})
    at = open_page(fake, "Observação")
    assert not at.exception
    assert values(at.error) == ["Pedido de observação inválido. Códigos: NOT_A_SESSION:2025-11-29."]
    assert "Resumo: 2 pregão(ões)" in values(at.subheader)  # the summary call still succeeds on its own
```

- [ ] **Step 2: Run the smoke tests to verify they fail**

Run: `uv run --directory dashboard pytest -p no:cacheprovider -o addopts="" -q tests/test_app_smoke.py`
Expected: FAIL — `"Observação"` não é uma opção do radio (`StreamlitAPIException`/`ValueError` no `set_value`).

- [ ] **Step 3: Implement the view and register the page**

`dashboard/dashboard/views/observation.py`:

```python
from __future__ import annotations

from datetime import date, timedelta

import streamlit as st

from dashboard.charts import daily_r_figure, pressure_buckets_figure
from dashboard.client import ApiClient
from dashboard.viewmodels import OBSERVATION_PRESSURE_TITLE, observation_day_rows, observation_view, today_et
from dashboard.views.common import guarded

SUMMARY_DAYS_DEFAULT = 14
MAX_SUMMARY_DAYS = 45  # the API's limit (Plan 4, D63)


def render(client: ApiClient) -> None:
    st.title("Observação")
    st.caption("Relatório só leitura dos dados gravados, as-of o instante do pedido; nenhuma chamada a provider.")
    picked = st.date_input("Pregão (ET)", value=today_et(), key="observation-day")
    if not isinstance(picked, date):
        return
    day: date = picked
    report = guarded(lambda: client.observation_report(day))
    if report is not None:
        view = observation_view(report)
        st.subheader(view.title)
        st.caption(view.status)
        columns = st.columns(5)
        for index, card in enumerate(view.cards):
            with columns[index % 5]:
                st.metric(card.label, card.value)
        st.subheader("Operação")
        st.dataframe(view.operations, hide_index=True)
        st.subheader("Trades virtuais (MFE / MAE)")
        st.caption(view.trades_caption)
        if view.trades:
            st.dataframe(view.trades, hide_index=True)
        else:
            st.info("Nenhum trade fechado nesta janela.")
        st.subheader("Latência sinal → fill")
        st.dataframe(view.latency, hide_index=True)
        st.subheader(OBSERVATION_PRESSURE_TITLE)
        if view.pressure:
            st.dataframe(view.pressure, hide_index=True)
            st.plotly_chart(pressure_buckets_figure(report["pressure"]["buckets"], method=view.pressure_method))
        st.caption(f"Método: {view.pressure_method}")
        st.caption(view.pressure_disclaimer)
        st.caption(view.pressure_note)
    span = st.number_input("Dias corridos no resumo", min_value=1, max_value=MAX_SUMMARY_DAYS,
                           value=SUMMARY_DAYS_DEFAULT, key="observation-span")
    summary = guarded(lambda: client.observation_summary(day - timedelta(days=int(span) - 1), day))
    if summary is not None:
        st.subheader(f"Resumo: {summary['sessions']} pregão(ões)")
        st.dataframe(observation_day_rows(summary), hide_index=True)
        st.plotly_chart(daily_r_figure(summary["days"]))
```

Em `dashboard/dashboard/app.py`, troque o import das views por:

```python
from dashboard.views import comparison, health, market, observation, orders, overview, portfolio, signals, watchlist
```

e acrescente ao final de `PAGES`:

```python
    "Observação": observation.render,
```

- [ ] **Step 4: Run the dashboard project**

Run: `uv run --directory dashboard pytest -p no:cacheprovider -o addopts="" -q && uv run --directory dashboard ruff check . && uv run --directory dashboard mypy`
Expected: PASS, inclusive `test_dashboard_talks_to_the_platform_only_over_http` quando rodado na raiz; ruff e mypy limpos. Se o `AppTest` instalado expuser subtítulos por outro acessor que não `at.subheader`, adapte **só o teste** (nunca a versão) e registre `Ruling:`.

- [ ] **Step 5: Commit**

```bash
git add dashboard/dashboard/views/observation.py dashboard/dashboard/app.py dashboard/tests/test_app_smoke.py
git commit -m "feat(dashboard): observation page over the report and summary routes"
```

---

### Task 13: Fechamento — fronteiras fixadas, verificação do zero, smoke sem rede, revisão do branch inteiro e nota de encerramento

**Files:**
- Modify: `tests/test_import_boundaries.py` (`test_boundary_scan_covers_the_plan_4_modules`)
- Create: `docs/superpowers/notes/2026-09-14-plan4-closeout.md`
- Modify: `docs/superpowers/plans/2026-09-14-paper-observation-report.md` (seção final "Encerramento do controlador")

**Interfaces:**
- Consumes: tudo o que as Tasks 1–12 produziram.
- Produces:
  - nota de encerramento no formato das notas dos Planos 3A–3C;
  - tag local `plan/paper-observation-report-complete`, criada **somente** se nenhuma revisão estiver aberta;
  - nenhum push: o responsável abre e mescla o PR de `plan-4-observation` pelo CI obrigatório.

- [ ] **Step 1: Pin the boundary coverage to the real files**

Acrescente ao final de `tests/test_import_boundaries.py`:

```python
def test_boundary_scan_covers_the_plan_4_modules() -> None:
    from virtual_orders.storage.tables import APPEND_ONLY_TABLES

    neutral = {_rel(p) for p in _neutral_files()}
    assert {
        "virtual_orders/readmodels/observation.py", "virtual_orders/readmodels/observation_window.py",
        "virtual_orders/readmodels/observation_operations.py", "virtual_orders/readmodels/observation_trades.py",
        "virtual_orders/ledger/worker_sessions.py", "virtual_orders/alerts/observation.py",
        "virtual_orders/analytics/observation.py",
    } <= neutral
    assert "virtual_orders/analytics/observation.py" in PLATFORM_PURE_MODULES
    assert "virtual_orders/api/routes/observation.py" in {_rel(p) for p in _api_files()}
    assert {"virtual_orders/observation/__main__.py", "virtual_orders/observation/render.py"} <= {
        _rel(p) for p in _observation_cli_files()}
    assert "virtual_orders.bootstrap" not in _imported_modules(SRC / "virtual_orders/observation/__main__.py")
    assert "dashboard/dashboard/views/observation.py" in {_dashboard_rel(p) for p in _dashboard_files()}
    assert {"observation_report.json", "observation_summary.json"} <= {
        path.name for path in (DASHBOARD_PROJECT / "tests" / "fixtures" / "api").glob("*.json")}
    assert "worker_sessions" in APPEND_ONLY_TABLES
    assert "virtual_orders.ledger.worker_sessions" in _imported_modules(SRC / "virtual_orders/worker/runner.py")
    assert "virtual_orders.alerts.observation" in _imported_modules(SRC / "virtual_orders/worker/jobs.py")
```

Run: `uv run pytest -p no:cacheprovider -o addopts="" -q tests/test_import_boundaries.py`
Expected: PASS.

Commit:

```bash
git add tests/test_import_boundaries.py
git commit -m "test(boundaries): pin observation report, worker sessions, CLI and dashboard page coverage"
```

- [ ] **Step 2: Final verification from zero**

Run, em sequência e na mesma sessão de shell, e registre as saídas no ledger:

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
uv run pytest -p no:cacheprovider -o addopts="" -q tests/test_import_boundaries.py tests/analytics/test_observation.py \
  tests/readmodels/test_observation_window.py tests/integration/test_schema_0005.py \
  tests/integration/test_observation_operations.py tests/integration/test_observation_trades.py \
  tests/integration/test_observation_report.py tests/integration/test_observation_cli.py \
  tests/integration/api/test_observation_api.py tests/integration/api/test_dashboard_contract.py \
  tests/integration/worker/test_runner.py tests/integration/worker/test_jobs.py tests/deploy
python3 - <<'PY'
import os, subprocess, tempfile
admin = "postgresql+psycopg://vo:vo@127.0.0.1:55432/postgres"
subprocess.run(["uv", "run", "python", "-c", (
    "import sqlalchemy as sa; e = sa.create_engine('" + admin + "', isolation_level='AUTOCOMMIT');"
    "c = e.connect(); c.execute(sa.text('DROP DATABASE IF EXISTS plan4_migrations'));"
    "c.execute(sa.text('CREATE DATABASE plan4_migrations'))")], check=True)
env = {**os.environ, "DATABASE_URL": admin.rsplit("/", 1)[0] + "/plan4_migrations"}
for step in (["upgrade", "head"], ["downgrade", "base"], ["upgrade", "head"]):
    subprocess.run(["uv", "run", "alembic", *step], check=True, env=env)
print("migrations from an empty database: OK")
PY
git diff --stat 09f2eea -- uv.lock dashboard/uv.lock
git diff --stat plan/virtual-order-engine-core-complete -- src/core
git status --porcelain -- src/core
git diff --stat 09f2eea -- .github/workflows/ci.yml
BASE=09f2eea
git log --format='%an <%ae>%n%B' "$BASE"..HEAD | grep -E '^(Co-Authored-By|Claude-Session):' || echo "no AI trailers"
git log --format='%an <%ae>' "$BASE"..HEAD | sort -u
```

Expected:
- Suíte da raiz verde, sem skips e sem warnings, com os `N_BASE` (1128) herdados e os novos; dashboard verde (47 herdados + novos), sem skips e sem warnings.
- Os dois `uv sync --locked` sem mudanças; `git diff --stat` dos dois locks **vazio** (nenhuma dependência nova).
- ruff e mypy limpos nos dois projetos.
- A chamada única que mistura raiz, `analytics`, `readmodels`, `integration`, `api`, `worker` e `deploy` passa (regressão da entrada 20 do 3B), com o contrato em modo de comparação.
- `migrations from an empty database: OK` (espelha o passo do CI), além de `test_migrations_run_from_zero_and_back`, `test_downgrade_to_0004_and_back`, `test_downgrade_to_0003_and_back` e `test_downgrade_to_0002_and_back` na suíte.
- Os dois comandos de `src/core` vazios; `.github/workflows/ci.yml` sem diff.
- `no AI trailers`; um único autor `Ricardo Carneiro <132141856+ricdomingues@users.noreply.github.com>`.

- [ ] **Step 3: Smoke the composition, the CLI and the dashboard without network**

`env -i` garante que nenhuma variável do host entre. Nada abre conexão fora de `127.0.0.1`: `build_services` não conecta, a porta `1` recusa na hora e o import do dashboard não chama a API.

```bash
env -i PATH="$PATH" HOME="$HOME" \
  API_KEY=x DATABASE_URL=postgresql+psycopg://vo:vo@127.0.0.1:55432/postgres ALPACA_API_KEY=a ALPACA_SECRET_KEY=s \
  FMP_API_KEY=f \
  uv run python -c "from fastapi.routing import iter_route_contexts; from virtual_orders.bootstrap import app_from_environment; app = app_from_environment(); print(sorted((sorted(c.route.methods)[0], c.route.path) for c in iter_route_contexts(app.routes))); app.state.services.close()"
env -i PATH="$PATH" HOME="$HOME" uv run python -m virtual_orders.observation report --day 2025-11-25; echo "exit=$?"
env -i PATH="$PATH" HOME="$HOME" DATABASE_URL=postgresql+psycopg://vo:hunter2@127.0.0.1:1/x \
  uv run python -m virtual_orders.observation report --day 2025-11-25; echo "exit=$?"
env -i PATH="$PATH" HOME="$HOME" uv run python -m virtual_orders.worker run; echo "exit=$?"
env -i PATH="$PATH" HOME="$HOME" uv run --directory dashboard python -c "import dashboard.views.observation as v; print(v.render.__name__)"
```

Expected:
1. As 24 rotas:

```
[('DELETE', '/alerts/{rule_id}'), ('DELETE', '/watchlist/{ticker}'), ('GET', '/alert-outbox'), ('GET', '/health'), ('GET', '/health/log'), ('GET', '/market/bars'), ('GET', '/market/pressure'), ('GET', '/metrics'), ('GET', '/observation/report'), ('GET', '/observation/summary'), ('GET', '/orders'), ('GET', '/orders/{order_id}'), ('GET', '/orders/{order_id}/chart'), ('GET', '/portfolio/real'), ('GET', '/portfolio/virtual'), ('GET', '/quality/overview'), ('GET', '/signals'), ('GET', '/watchlist'), ('POST', '/orders/{order_id}/cancel'), ('POST', '/replay'), ('POST', '/signals'), ('POST', '/signals/{signal_id}/orders'), ('POST', '/watchlist/{ticker}/alerts'), ('PUT', '/watchlist/{ticker}')]
```

2. `{"error": "CONFIG_INVALID", "errors": ["MISSING:DATABASE_URL"]}` e `exit=2`.
3. `{"error": "DATABASE_UNAVAILABLE", "type": "OperationalError"}` sem `hunter2`, e `exit=4`.
4. Uma linha `{"error": "CONFIG_INVALID", "errors": ["MISSING:API_KEY", …]}` sem valores e `exit=2` (worker inalterado).
5. `render` (a view importa sem servidor Streamlit e sem chamar a API).

- [ ] **Step 4: Whole-branch review**

- Despache a revisão final de branch inteiro (`requesting-code-review`), com o modelo mais capaz, sobre `09f2eea..HEAD`.
- Entregue ao revisor: a spec, este plano, as Global Constraints e a nota de encerramento do 3C.
- Peça verificação explícita de:
  - (a) D60–D73, os conflitos da spec e a tabela "Definição das métricas";
  - (b) somente leitura: nenhuma rota, CLI ou relatório grava ou chama provider (contagens antes/depois nos testes); as únicas escritas novas são `worker_sessions` e `OBSERVATION_DAILY`;
  - (c) as-of: toda leitura filtrada pela coluna de relógio do banco `<= as_of`; pertinência pelo instante de mercado onde existe; exceção documentada de `order_state` (D61); o relatório e o resumo lidos num único snapshot `REPEATABLE READ` somente leitura na rota, na CLI e no alerta (escrita do outbox em transação separada);
  - (d) ordens replay fora de trades, latência e pressão; só `replay_closed`;
  - (e) segredos e texto de exceção ausentes em respostas, saída da CLI, logs novos do runner, documento `OBSERVATION_DAILY`, fixtures e dashboard; `failure_code`/nomes de tipo apenas;
  - (f) `worker_sessions`: migration `0005` com triggers e grants iguais a 0003/0004, `CHECK` de campos, downgrade que falha alto; runner com códigos de saída inalterados e escrita em melhor esforço; `EXPECTED_SCHEMA_REVISION = "0005"`;
  - (g) pressão × resultado: estimativa recalculada as-of do relatório, janela antes do minuto do sinal, sempre com `estimate`/`method`/`disclaimer`/`association_note`, nunca gravada, nenhuma importação por avaliação;
  - (h) latência: definição exata, nearest-rank, ordens sem fill por desfecho;
  - (i) fronteiras: CLI sem bootstrap/config/adapters; dashboard só por HTTP; `analytics/observation.py` puro;
  - (j) SQL parametrizado (inclusive listas com `CAST`), `src/core` intocado, locks inalterados, CI sem mudança e verde;
  - (k) contrato D72: fixtures existentes byte-idênticas, as duas novas determinísticas e sem segredo.
- Achados viram **uma** rodada de correção seguida de uma re-revisão com escopo restrito. Resíduos são adjudicados no ledger (`Ruling: … — … — …`). Rode o Step 2 de novo depois da última correção.

- [ ] **Step 5: Write the close-out note**

`docs/superpowers/notes/2026-09-14-plan4-closeout.md`, com as seções:
- **Cabeçalho:** Plano e Spec (v1.2 + D5–D73); intervalo `09f2eea..<ponta>`; testes do Step 2 com os `N_BASE` herdados (raiz 1128, dashboard 47); núcleo congelado (comando e resultado); locks inalterados.
- **Critérios de aceite** (tabela # | Critério | Teste | Resultado), cobrindo cada linha de "Critérios de aceite do Plano 4" abaixo com os nomes reais dos testes (confirmados por `grep -n "def test_"`).
- **Decisões D60–D73:** uma linha cada (mantida/alterada — motivo — custo se estiver errada).
- **Rulings durante a execução.**
- **Resultado da revisão final** e **achados menores adiados**.
- **Entradas para o próximo plano**, no mínimo:
  1. Rodar a observação por 7–14 pregões e decidir, com os números do relatório: D33 (colapso de revisões), burst de cruzamentos (D26), T12, `NO_OBSERVATIONS_FINAL` sem revisão (D52), finalização na primeira tentativa (Minor 4 do 3C).
  2. Limiares da pressão × resultado (`0.05`/`0.15`, janela de 30 candles): rever só com amostra suficiente; nunca promover a sinal sem plano próprio.
  3. `window_runs` varre todos os runs dos tipos pedidos (sem limite inferior, porque o instante de mercado vive no `detail`): aceitável para semanas; com meses de histórico, gravar o instante de mercado em coluna própria ou limitar por `started_at` com folga.
  4. O alerta `OBSERVATION_DAILY` é a fotografia do fim de dia (`complete: false`); se o responsável quiser o dia fechado no n8n, um job às 00:05 ET.
  5. Entradas 1, 3, 4, 6–11 do encerramento do 3C continuam abertas (fora deste plano).

Commit:

```bash
git add docs/superpowers/notes/2026-09-14-plan4-closeout.md docs/superpowers/plans/2026-09-14-paper-observation-report.md
git commit -m "docs: Plan 4 close-out"
```

- [ ] **Step 6: Tag (only with no open review)**

Só se o ledger não tiver nenhuma revisão aberta, nenhum achado bloqueante sem ruling e o Step 2 estiver verde **depois** da última correção:

```bash
git tag -a plan/paper-observation-report-complete -m "Plan 4 — daily paper-observation report COMPLETE"
```

**Não crie a tag se houver qualquer revisão aberta.** Não faça push da tag nem do branch: o responsável abre o PR de `plan-4-observation` para `main` e o mescla pelo CI obrigatório (`engine` e `dashboard`).

---

## Critérios de aceite do Plano 4

| # | Critério (métrica do responsável ou invariante) | Teste(s) |
|---|---|---|
| 1 | **Falhas de provider** por tipo de run, com códigos fixos, maior sequência LIVE e sem mensagens | `test_provider_failures_are_counted_by_run_kind_with_codes_and_never_messages` (`tests/integration/test_observation_operations.py`); `test_failure_messages_become_fixed_codes` (`tests/analytics/test_observation.py`); `test_the_report_is_read_from_stored_data_only_and_labels_the_estimate` (`tests/integration/api/test_observation_api.py`) |
| 2 | **Candles ausentes**: minutos esperados/ausentes do pregão, `DATA_GAP`, não avaliadas | `test_missing_bars_gaps_and_unevaluated_orders_come_from_the_session_quality` |
| 3 | **503 de actionability** com causas e taxa | `test_actionability_answers_are_counted_with_the_503_causes` |
| 4 | **`DATA_QUALITY_RECHECK`**: runs, linhas, status, motivos terminais, linhas sobre o pregão | `test_recheck_rows_are_attributed_to_the_recheck_runs_of_the_window` |
| 5 | **Falhas de entrega de alertas** por desfecho e tipo, pendentes no fim | `test_alert_deliveries_are_counted_by_the_database_clock_inside_the_window` |
| 6 | **Reinícios do worker**: `worker_sessions` append-only; runner grava início/parada sem mudar códigos de saída; falha de escrita não para o worker; reinícios, fins sem parada atribuídos à janela do próximo início, sessão aberta no fim, `LOCK_LOST` limpo | `tests/integration/test_schema_0005.py` (todos); `test_a_session_records_its_start_and_its_signalled_stop`, `test_a_lost_lock_is_recorded_with_exit_code_3`, `test_a_failing_scheduler_is_recorded_and_still_raises`, `test_a_worker_that_never_gets_the_lock_is_not_a_session`, `test_a_failing_session_write_never_stops_the_worker_and_logs_only_the_type` (`tests/integration/worker/test_runner.py`); `test_worker_restarts_unclean_ends_and_open_sessions` |
| 7 | **Transições de saúde** (mudanças de estado, com `log_rows` à parte), tempo por estado e as-of | `test_health_transitions_and_time_in_each_state`; `test_seconds_by_state_splits_the_window_at_each_change` |
| 8 | **Trades virtuais**: criadas, fills, fechadas, política de revisão, replay fora | `test_trades_latency_and_unfilled_orders_of_a_session` (`tests/integration/test_observation_trades.py`); `test_the_report_is_read_from_stored_data_only_and_labels_the_estimate` (`replay_closed`) |
| 9 | **MFE / MAE** da projeção, médias e medianas | `test_trades_latency_and_unfilled_orders_of_a_session`; `test_trade_stats_follow_the_r_sign_and_median_rules` |
| 10 | **Latência sinal → fill** (candle e gravação), mediana/p90 nearest-rank, sem fill por desfecho | `test_trades_latency_and_unfilled_orders_of_a_session`; `test_nearest_rank_percentiles_never_interpolate`; `test_whole_seconds_floors_and_rejects_naive_datetimes` |
| 11 | **Pressão × resultado**: últimos 30 candles antes do sinal atravessando pregões (janela e `spans_sessions` gravados), buckets por alinhamento/força, média só com n ≥ 5, sempre rotulada, nunca lida pela avaliação | `test_pressure_is_recomputed_from_bars_before_the_signal_and_paired_with_the_result`; `test_an_early_session_signal_reads_the_last_bars_of_the_previous_session`; `test_pressure_is_classified_relative_to_the_trade_direction`; `test_buckets_group_results_in_a_fixed_order_and_need_five_trades_for_a_mean`; `test_evaluation_never_reads_the_observation_report` (`tests/test_import_boundaries.py`) |
| 12 | Janela do pregão, dias sem pregão e futuro rejeitados, resumo de intervalo | `tests/readmodels/test_observation_window.py` (todos); `test_the_summary_aggregates_the_sessions_of_the_range`; `test_invalid_report_days_use_fixed_codes`, `test_invalid_summary_ranges_use_fixed_codes`, `test_the_summary_covers_every_session_of_the_range` |
| 13 | Somente leitura: nenhuma escrita e nenhuma chamada a provider no relatório, na rota e na CLI; um único snapshot `REPEATABLE READ` somente leitura por pedido | `test_the_report_assembles_every_section_for_one_session_without_writing`; `test_the_report_reads_one_read_only_repeatable_read_snapshot`; `test_the_report_is_read_from_stored_data_only_and_labels_the_estimate`; `test_report_prints_markdown_or_json_and_never_writes` |
| 14 | CLI: Markdown/JSON, códigos de saída 0–4 (`4` só para erro de conexão), sem segredos | `test_report_prints_markdown_or_json_and_never_writes`, `test_summary_prints_one_row_per_session`, `test_exit_codes_carry_codes_and_types_only`, `test_only_connection_errors_are_database_unavailable`; `test_the_observation_cli_never_imports_adapters_the_composition_root_or_the_http_layer` |
| 15 | Alerta `OBSERVATION_DAILY`: só contagens, um por pregão, só com sink, contido, log só com o tipo | `test_end_of_day_enqueues_one_observation_alert_per_session_with_counts_only`, `test_no_observation_alert_without_a_webhook`, `test_a_failing_observation_alert_never_fails_the_end_of_day_job` (`tests/integration/worker/test_jobs.py`) |
| 16 | Dashboard "Observação" só por HTTP: cliente, view-models, figuras, página, erro 422 sem traceback, pressão rotulada | `test_observation_routes_send_iso_dates` (`dashboard/tests/test_client.py`); `test_the_observation_view_turns_every_section_into_display_strings`, `test_summary_rows_and_the_observation_error_message` (`dashboard/tests/test_viewmodels.py`); `test_daily_r_and_pressure_bucket_figures` (`dashboard/tests/test_charts.py`); `test_observation_page_labels_the_pressure_estimate_and_shows_the_summary`, `test_observation_page_shows_an_invalid_day_without_a_traceback` (`dashboard/tests/test_app_smoke.py`) |
| 17 | Contrato gravado das rotas novas (19 fixtures), determinístico e sem segredo | `test_dashboard_contract_matches_the_recorded_api_responses`; `test_the_client_reads_the_recorded_observation_responses`, `test_observation_views_from_the_recorded_responses` (`dashboard/tests/test_api_contract.py`); `test_boundary_scan_covers_the_3c_modules` (19) |
| 18 | `src/core` intocado; `N_BASE` herdados verdes; migrations do zero; ruff e mypy strict nos dois projetos; locks e CI inalterados | Task 13 Step 2 |
| 19 | Commits com identidade noreply e sem trailers de IA; nenhuma tag com revisão aberta; nenhum push | Task 13 Steps 2 e 6 |

## Encerramento do controlador

Seção preenchida pelo controlador no Step 5 da Task 13, no formato do Plano 3C: modelo de execução por task, rodadas de correção, saída da verificação do zero (contagens, `BASE=09f2eea`, autores, trailers), saída do smoke sem rede, estado da tag e caminho da nota de encerramento.
