# Virtual Order Engine — Plano 3B: Worker, Alertas n8n, Watchlist e Endurecimentos

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Colocar o Virtual Order Engine para rodar sozinho em paper: um processo worker APScheduler (ciclo live, abertura, fim de dia, `DATA_QUALITY_RECHECK`, vigia de `/health`, entrega de alertas), o comando `rebuild-projections`, alertas do responsável entregues **somente** pelo webhook n8n (eventos de ordem, cruzamento de níveis da watchlist, estimativa de "pressão forte"), a watchlist com rotas mínimas autenticadas e os endurecimentos 2, 5, 6, 8–14 do encerramento do Plano 3A.

**Architecture:** O núcleo (`src/core/`) continua congelado. Tudo novo fica em `src/virtual_orders/`:
- `worker/`: agenda (`schedule.py`), jobs testáveis sem tempo real (`jobs.py`), execução com lock de processo e desligamento gracioso (`runner.py`) e CLI (`__main__.py`, único módulo do worker que importa `bootstrap`);
- `alerts/` (neutro): contrato `AlertSink`, outbox append-only e entrega, varredura de eventos de ordem, watchlist e regras, avaliação das regras, vigia de transições de `/health` e resumo de fim de dia;
- `analytics/pressure.py` (puro): estimativas de pressão derivadas de OHLCV;
- `notify/n8n.py` (adapter httpx, importado só por `bootstrap.py`);
- `evaluator/recheck.py`: `DATA_QUALITY_RECHECK` com tabela própria;
- `readmodels/quality.py`: sessões pendentes de qualidade (D12) lidas por `/health` e pelo recheck;
- `api/routes/watchlist.py`: rotas mínimas da watchlist e das regras de alerta.

O worker é montado por `build_services` (raiz de composição única) e usa `Services.clock` só para o instante de mercado.

**Tech Stack:** Python 3.12, `uv`, APScheduler 3.x (`>=3.11,<4`), FastAPI, PostgreSQL 16, SQLAlchemy 2 Core + psycopg 3, Alembic, httpx (adapters, `MockTransport` nos testes), pytest, Hypothesis, ruff 0.8.6, mypy 1.13 strict.

**Spec:** `docs/superpowers/specs/2026-09-12-virtual-order-engine-design.md` (SPEC v1.2 FROZEN, tag `spec/virtual-order-engine-v1.2`), mais D5–D12 (`docs/superpowers/plans/2026-09-12-virtual-order-engine-persistence.md`), D13–D19 (`docs/superpowers/plans/2026-09-13-virtual-order-engine-api.md`) e D20–D39 abaixo.
- **Entradas:** `docs/superpowers/notes/2026-09-13-plan3a-closeout.md`, seção "Entradas para o Plano 3B", itens 1–14 (todos cobertos; tabela de rastreio abaixo).
- **Seções da spec cobertas:** 1.2 item 7 (n8n fora do caminho crítico), 2.1 (serviço `worker`), 3.3 (webhook de alerta de integridade, via D25), 4.6 (recheck), 4.7 (dividendo não verificável), 5.2 (lock do ciclo, reaproveitado), 5.3 (worker), 6 (linhas de yfinance/FMP, Alpaca 3 ciclos → webhook, `rebuild-projections`), 8 (`EVAL_INTERVAL_MINUTES`, `N8N_WEBHOOK_URL`), 10/D4 (`DATA_QUALITY_RECHECK`).
- **Contexto só de leitura:** `docs/superpowers/roadmap/2026-09-12-robinhood-mcp-read-only.md`. Nada da Robinhood entra no 3B.

## Sequência do Plano 3

| Plano | Conteúdo | Estado |
|---|---|---|
| 3A | Configuração, composição, API, `/health` estruturado, endurecimentos | concluído (`plan/virtual-order-engine-api-complete`) |
| **3B (este)** | Worker APScheduler, `rebuild-projections`, `DATA_QUALITY_RECHECK`, webhook n8n (resumo, `/health`, alertas do responsável), watchlist + regras + ingestão, estimativas de pressão, endurecimentos 2, 5, 6, 8–14 | — |
| 3C | Dashboard Streamlit, gráficos de candles com volume/VWAP/overlays, exibição de pressão, portfólio virtual (+ Robinhood somente leitura condicionada à Fase 0), Docker Compose de produção, ponta a ponta | depende do 3B |

## Fora deste plano

- **3C:** rotas de candles para a UI, exibição de pressão, gráficos, portfólio, dashboard, Docker Compose de produção, `/livez`.
- **Robinhood:** nada (roadmap em Fase 0, sem código).
- **Não muda:** `src/core/`, regras de fill, `fill_model v1`, `EventType`, semântica de `DATA_QUALITY` (D4) e D12.

Base: branch `plan-3b-worker`, criado na ponta de `plan-3a-api` (`8cfb743`).
- Intervalo de commits: `plan-3a-api..HEAD` enquanto o branch não for rebaseado sobre um `main` que contenha o 3A; depois, `$(git merge-base main HEAD)..HEAD`. A Task 17 registra no ledger qual base valeu.

## Rastreio das entradas do encerramento do 3A

| Entrada | Conteúdo | Onde |
|---|---|---|
| 1 | Agendar ciclo, abertura e fim de dia com `Services`; `Services.clock` só para `market_now`/`now` | Tasks 9, 16 (D20, D21, D36, D38, D39) |
| 2 | `require_aware` em `evaluate_order(market_now=)`, `finalize_validity(now=)`, `expire_due_orders(now=)` | Task 2 |
| 3 | Webhook `N8N_WEBHOOK_URL`: resumo de fim de dia e alerta de `/health` | Tasks 7, 12, 14 (D24, D25, D32) |
| 4 | Alertas do responsável (eventos de ordem, níveis da watchlist, pressão forte) | Tasks 11–16 (D24, D26, D27, D33, D35, D37) |
| 5 | `DATA_QUALITY_RECHECK` consumindo `QUALITY_NOT_EVALUATED` | Tasks 9, 10 (D22, D34) |
| 6 | `DIVIDEND_UNVERIFIED` quando as duas consultas levantam | Task 3 (D23) |
| 7 | Comando `rebuild-projections` | Tasks 4, 16 |
| 8 | M4: timeout de statement ≠ banco indisponível | Task 9 (D28) |
| 9 | M5: quarentena não congela ordem replay | Task 4 (D29) |
| 10 | M6: overrides do RECALCULATE contra a configuração da ordem de origem | Task 5 (D29) |
| 11 | M7: `ALPACA_TRADING_URL` errada vira 503, não 422 | Task 6 (D29) |
| 12 | `bootstrap.py` fecha o cliente HTTP se `make_engine` levantar | Task 7 |
| 13 | Testes faltantes: ramo `FAILED` de `run_opening`; sessão em `/health` (fim de semana, feriado, fechamento); `market_now` em runs `FAILED`; `needs_review` sem motivos; tamanho de `facts` | Tasks 2, 9 (D31) |
| 14 | `data_quality` do detalhe da ordem com eventos reais | Task 10 |

## Global Constraints

### Núcleo, evaluator e dados

- Python **3.12**. Dependências entram via `uv add`; testes rodam com `uv run pytest`.
- **`src/core/` está congelado.** Nenhum arquivo em `src/core/` é criado, editado ou removido. Se um teste exigir mudar a semântica do `fill_model v1` ou de qualquer módulo de `src/core/`, **pare e reporte ao responsável**. O encerramento exige `git diff plan/virtual-order-engine-core-complete -- src/core` vazio.
- Continuam valendo todas as Global Constraints dos Planos 2 e 3A: market data neutra; sem fallback de provider (`MarketDataGateway.bar_source(price_source)`); `Decimal` para preço, volume e dinheiro; `datetime` timezone-aware em UTC; `payload_hash` calculado uma vez; proveniência por `bar_batches` e leitura as-of; nenhum teste acessa a rede; Postgres real em `127.0.0.1:55432` (`docker compose -f docker-compose.test.yml up -d --wait`); fixtures rotuladas em `tests/fixtures/README.md`.
- **Relógio (D19, D21):** `Services.clock` fornece só o instante de mercado (`market_now`, `now` dos jobs, `created_at` de regras e itens da watchlist). `started_at`, `data_as_of`, `recorded_at` e `created_at` do outbox continuam no relógio do banco. Todo instante que entra num serviço passa por `require_aware`.
- **Testes sem tempo real:** nenhum teste dorme nem espera o scheduler. Jobs recebem o relógio pelo `Services`; triggers são testados com `get_next_fire_time`; o runner é testado com um scheduler falso.

### Fronteiras de import (verificadas por `tests/test_import_boundaries.py`)

- Só `virtual_orders/bootstrap.py` importa adapters: `virtual_orders.marketdata.{alpaca,fmp,yfinance_source,http}` e `virtual_orders.notify.n8n`.
- `virtual_orders/worker/**` nunca importa adapters, `httpx`, `yfinance`, `pandas`, `virtual_orders.api`, `fastapi`, `starlette` nem `uvicorn`. Só `virtual_orders/worker/__main__.py` pode importar `virtual_orders.bootstrap` e `virtual_orders.config`.
- Pacotes neutros (`storage`, `ledger`, `evaluator`, `readmodels`, `alerts`, `analytics` e os módulos neutros de `marketdata`) nunca importam adapters, provider libs, `virtual_orders.api`, `virtual_orders.bootstrap`, `virtual_orders.config`, `virtual_orders.worker`, `fastapi`, `starlette` nem `uvicorn`.
- Módulos puros da plataforma (`virtual_orders/analytics/pressure.py`, `virtual_orders/alerts/rules.py`) não importam bibliotecas de I/O (`sqlalchemy`, `psycopg`, `httpx`, `os`, …) nem pacotes de infraestrutura de `virtual_orders`.

### Segurança, HTTP e webhook

- Segredos (`API_KEY`, `ALPACA_*`, `FMP_API_KEY`, `DATABASE_URL`, `N8N_WEBHOOK_URL`, que pode conter token) nunca aparecem em `repr`, mensagens de erro, respostas HTTP, run details, documentos do outbox ou payloads de webhook.
- **Nenhum texto de exceção** em respostas HTTP ou payloads de webhook: só o tipo da exceção (`type(exc).__name__`) ou códigos fixos.
- **n8n nunca está no caminho crítico (spec 1.2 item 7):** falha de webhook é registrada (`alert_delivery_attempts`) e logada; nunca muda avaliação, eventos ou runs, e em `/health` só aparece como causa `INFO` (`UNDELIVERABLE_ALERTS`, D24). Timeout de 5 s **por fase da conexão** (httpx), no máximo uma tentativa por alerta por execução do job de entrega, orçamento de tempo por execução, backoff exponencial por alerta e nenhum retry bloqueante (D24).
- **Logs:** falhas de job são logadas com traceback (`exc_info=True`). Logs não são resposta HTTP nem payload de webhook; mesmo assim, o adapter n8n descarta a exceção original (`from None`), então a URL do webhook nunca entra em texto de exceção.
- Rotas novas usam o app, a autenticação `X-API-Key`, o envelope de erro e a codificação JSON do 3A (`json_response`, `read_json_object`).

### Qualidade e commits

- `uv run ruff check src tests migrations` e `uv run mypy` limpos. Sem novas entradas em `per-file-ignores`. O único override de mypy novo é `ignore_missing_imports` para a biblioteca de terceiros `apscheduler` (mesmo padrão de `yfinance` e `pandas_market_calendars`); nenhum override para código nosso.
- Migrations verificadas do zero (`test_migrations_run_from_zero_and_back` + banco novo).
- Testes com Postgres ficam em `tests/integration/` (marcados automaticamente como `integration`).
- Identificadores de código em inglês; prosa do plano em português; mensagens de commit em inglês.
- **Commits:** identidade repo-local `Ricardo Carneiro <132141856+ricdomingues@users.noreply.github.com>`. **Nenhum** trailer `Co-Authored-By:` ou `Claude-Session:` (também para subagentes). Nunca alterar configuração global do git. Nenhum push sem pedido explícito do responsável.

## Decisões deste plano (a spec v1.2 não é alterada)

- **D20 — Composição e agenda do worker.**
  - Processo único `python -m virtual_orders.worker` (subcomando `run`, padrão) montado por `load_settings` + `build_services`.
  - APScheduler 3.x (`BlockingScheduler`, timezone `America/New_York`). Jobs com `max_instances=1`, `coalesce=True`, `misfire_grace_time=60`:

    | Job | Trigger (ET) | Guarda no próprio job (calendário NYSE) |
    |---|---|---|
    | `live_cycle` | cron `mon-fri`, `hour=9-16`, `minute=*/EVAL_INTERVAL_MINUTES` | só com pregão em `[abertura, fechamento + 5 min]` (09:30–16:05; meio pregão 09:30–13:05); **só** avalia ordens |
    | `watchlist` | mesmo cron do `live_cycle` | mesma janela; ingestão da watchlist e regras de alerta (D26), em job próprio para nunca atrasar nem pular a avaliação |
    | `opening` | cron `mon-fri 09:25` | só antes da abertura de um pregão do dia; depois do run, enfileira alertas de eventos (D24) |
    | `end_of_day` | cron `mon-fri 16:30` e `18:30` | só depois do fechamento de um pregão do dia; às 18:30 só roda se o fim de dia do pregão ainda não estiver "assentado" (run `END_OF_DAY` `COMPLETED` sem `not_evaluated`), dando uso real à D12(b); depois do run, enfileira alertas de eventos (D24) |
    | `health_watch` | intervalo de `EVAL_INTERVAL_MINUTES` | sempre |
    | `deliver_alerts` | intervalo de 1 min | só com webhook configurado; primeiro enfileira alertas de eventos (D24), depois entrega |
    | `worker_lock` | intervalo de 1 min (registrado pelo runner) | sempre; confirma o lock do processo (D39) |

  - O executor padrão do APScheduler 3.x é um pool de 10 threads: jobs diferentes rodam em paralelo, e `max_instances=1` impede só a sobreposição do mesmo job. Por isso a watchlist, que faz uma chamada ao provider por ticker, fica fora do `live_cycle`.
  - `EVAL_INTERVAL_MINUTES` precisa estar em `1..59` para virar campo de minuto do cron (D36). O CLI valida antes de montar os serviços (`CONFIG_INVALID` com `OUT_OF_RANGE:EVAL_INTERVAL_MINUTES`, código 2), e `build_schedule` mantém o `ValueError` como segunda barreira.
  - Um único worker por banco: o runner segura o advisory lock de sessão `WORKER_LOCK_KEY = 0x564F0003` durante toda a vida do processo; um segundo worker sai com código 2 sem agendar nada. O job `worker_lock` detecta a perda do lock (D39).
  - Desligamento gracioso: `SIGTERM`/`SIGINT` chamam `scheduler.shutdown(wait=True)`; ao sair de `start()`, o lock é liberado e `services.close()` roda sempre (`finally`). Um fim de dia ou recheck em andamento pode passar do tempo de parada padrão do Docker (10 s); o `stop_grace_period` do Compose de produção é entrada do 3C. Um `SIGKILL` continua seguro, porque cada ordem grava na própria transação.
  - Cada job é isolado: exceção vira `JobResult(ran=False, reason="ERROR:<Type>")` e log com traceback, nunca derruba o scheduler.
  - Um job perdido (worker fora do ar às 09:25, 16:30 e 18:30) não é recuperado sozinho: `/health` o torna visível (D38).
- **D21 — Relógio do worker.** Cada job lê `Services.clock()` uma vez e o passa por `require_aware(…, "clock")`. `market_now` do ciclo, `now` da abertura e do fim de dia, `created_at` de regras e `added_at` da watchlist vêm desse instante. Runs `FAILED` do ciclo passam a gravar `market_now` (entrada 13), de modo que `/health` e worker usem o mesmo relógio de mercado também nas falhas.
- **D22 — `DATA_QUALITY_RECHECK` com identidade própria, fora de `order_events`.**
  - `EventType` é do núcleo congelado e a D10 manteve reavaliações fora do `fill_model v1`; por isso o recheck **não** é um evento de ordem. É uma linha append-only em `data_quality_rechecks` com `recheck_key = DATA_QUALITY_RECHECK:{session_date}:{data_as_of}` (a chave da D4), `run_id` (run `QUALITY_RECHECK`), `source_run_id` (o `END_OF_DAY` que registrou `not_evaluated`) e o payload de qualidade (campos da 4.6, lacunas, flags de revisão).
  - **Pendências** = pares (ordem, pregão) em `not_evaluated` dos últimos 20 runs `END_OF_DAY` `COMPLETED`, sem `DATA_QUALITY:{pregão}` na ordem e sem linha de recheck para o pregão. `/health` (`QUALITY_NOT_EVALUATED`) e o recheck leem a mesma consulta (`pending_quality_sessions`); uma pendência "consumida" sai do `/health`.
  - **D4 e D12 respeitadas:** o recheck nunca escreve `DATA_QUALITY` nem `DATA_GAP`; se `DATA_QUALITY:{pregão}` já existir, não faz nada. Só rechecka pregões que **não** são mais o recém-fechado (o recém-fechado continua sendo do fim de dia, D12(b)). Aplica as mesmas regras da D12: feed que falha ao reingerir → `PROVIDER_FAILURE`, sem linha e com a pendência mantida.
  - **Uma linha por (ordem, pregão):** o comando começa conferindo `data_quality_rechecks` e devolve `ALREADY_RECHECKED` se já houver linha para o par, mesmo com outro `data_as_of` (execuções sobrepostas, lock perdido).
  - **Pendências terminais (nunca somem em silêncio):**
    - janela da ordem sem interseção com o pregão (`OUTSIDE_WINDOW`) → linha terminal `status = "NOT_MEASURABLE"`, `terminal_reason = "OUTSIDE_WINDOW"`, sem flags;
    - `NO_OBSERVATIONS` com o pregão ainda dentro do lookback → sem linha e a pendência continua;
    - `NO_OBSERVATIONS` com `RECHECK_FINAL_AFTER_SESSIONS = 5` ou mais pregões fechados depois dele → linha terminal `status = "NO_OBSERVATIONS_FINAL"`, sem flags.

    A linha terminal consome a pendência e limpa `QUALITY_NOT_EVALUATED` explicitamente. O caso típico é um halt de dia inteiro, que a própria D12 prevê.
  - **Níveis vigentes no pregão rechecado (spec 4.6 "stop_vigente naquele minuto"):**
    - O toque de nível nunca usa os níveis atuais. O recheck deriva do ledger o stop em vigor até o fechamento daquele pregão: `signal.stop` desde a criação, trocado pelo `new_stop_level` de cada evento `TARGET1_HIT` gravado com `bar_ts <= fechamento`, a partir do seu `stop_active_from`.
    - Essa derivação é exata para o `fill_model v1`, cujo único evento que move o stop é o `TARGET1_HIT` (`core/fills/v1/engine.py`).
    - Para outra versão de modelo, ou para um ledger com mais de um movimento de stop, a derivação não é exata com os helpers existentes. Nesses casos o recheck **pula** a checagem de toque: não gera `MISSING_BAR_LEVEL_TOUCH`, grava `level_touch_check = "SKIPPED:LEVELS_NOT_DERIVABLE"` e lista em `level_touch_unchecked_minutes` os minutos faltantes que tinham referência. `MISSING_BAR_UNVERIFIABLE` independe dos níveis e continua valendo.
    - A janela de qualidade (`quality_window`) usa o estado atual. Isso é exato para o pregão: `evaluation_start_ts` é fixo e `final_event_ts`, uma vez gravado, não muda; a janela é recortada em `[abertura, fechamento]` do pregão.
  - Com dados, grava a linha e aplica as flags de revisão (`MISSING_BAR_LEVEL_TOUCH`, `MISSING_BAR_UNVERIFIABLE`, `DAILY_RANGE_MISMATCH`) pelo caminho de comando travado (`apply_command`), na mesma transação. Flags são marcações humanas reaplicáveis pelo rebuild; a política de métricas (5.4) já as exclui por padrão (D34).
  - O detalhe da ordem (`GET /orders/{id}`) passa a listar `data_quality.rechecks`.
  - **Mudança de forma em `/health`:** com `quality_pending` coletado (sempre, em produção), `QUALITY_NOT_EVALUATED.detail` passa de `{session_day, reasons}` para `{session_days, count, reasons}`. O ramo antigo só existe para snapshots montados à mão nos testes de regra do 3A, e o teste de API da Task 9 fixa a forma nova.
- **D23 — Dividendo não verificável (spec 6, entrada 6).** Quando **as duas** consultas (`primary` e `secondary`) levantam `SourceUnavailable`/`SourceDataError` para um ticker, nenhuma linha é gravada em `dividends` e cada ordem candidata recebe, pelo caminho travado: `NEEDS_REVIEW:DIVIDEND_POSITION_UNCONFIRMED:{ex_date}` se a posição não foi avaliada até o fechamento anterior (mesma regra do Plano 2); `NEEDS_REVIEW:DIVIDEND_UNVERIFIED:{ex_date}` se `qty_open > 0`; nada se `qty_open = 0`, se a ordem já é final/congelada ou se `DIVIDEND:{ex_date}` já existe. Duas respostas vazias continuam sendo "sem dividendo". A chave é a mesma que `apply_dividend(validated=False)` usa, então um rerun com as fontes de volta nunca conflita.
- **D24 — Outbox e entrega de alertas.**
  - `alert_outbox` (append-only, `alert_key UNIQUE`) e `alert_delivery_attempts` (append-only, `DELIVERED`/`FAILED`/`EXPIRED`, `status_code`, `error_type`). Enfileirar é `INSERT … ON CONFLICT DO NOTHING`: o mesmo fato nunca vira dois alertas, inclusive depois de reiniciar o worker.
  - Entrega **pelo menos uma vez**: o documento e o header `Idempotency-Key` carregam `alert_key`, para o n8n deduplicar o caso "entregou mas caiu antes de gravar `DELIVERED`".
  - **Execução de `deliver_alerts`:**
    - Lê o instante uma vez no relógio do banco (`clock_timestamp()`) e grava esse instante em `attempted_at`.
    - Candidatos: alertas sem `DELIVERED` nem `EXPIRED` criados nas últimas 24 h, em ordem de `id`.
    - Um alerta com `n` falhas só está **devido** quando `agora >= última falha + backoff(n)`, com `backoff(n) = min(2^(n-1), 30)` minutos: 1, 2, 4, 8, 16, 30, 30… O backoff é por alerta, então um alerta que falha não bloqueia os outros.
    - Tenta no máximo 50 alertas devidos, uma vez cada. **Não para no primeiro erro:** segue enquanto couber no orçamento de `DELIVERY_TIME_BUDGET_SECONDS = 20`, checado antes de cada tentativa. Uma execução dura no máximo o orçamento mais uma tentativa, e o que sobra fica para a execução seguinte.
    - O timeout de 5 s do httpx vale por fase da conexão (conectar, escrever, cada leitura), não para a requisição inteira; o orçamento é o limite por execução.
  - **Expiração por idade:** um alerta sem `DELIVERED` criado há mais de 24 h (`ALERT_EXPIRY`) recebe uma linha `EXPIRED` (nunca é apagado) e sai da fila. Alertas expirados nos últimos 7 dias viram a causa `INFO` `UNDELIVERABLE_ALERTS` em `/health` (INFO porque o n8n nunca está no caminho crítico) e o campo `undeliverable_alerts` do resumo de fim de dia.
  - Webhook desligado (`N8N_WEBHOOK_URL` vazio → `Services.alert_sink is None`): nada é enfileirado nem entregue; a ingestão da watchlist continua.
  - **Enfileiramento de eventos** (`enqueue_event_alerts`): é um passo à parte, não do ciclo live. Roda no início de cada `deliver_alerts` (a cada minuto, o dia todo) e logo depois dos jobs `end_of_day` e `opening`. Assim cobre também eventos gravados pela API (cancelamento, revisão manual, comandos) e pelo fim de dia/abertura, e é idempotente por `alert_key`.
  - **Eventos de ordem** alertáveis (ordens não replay): `FILLED`, `TARGET1_HIT`, `TARGET2_HIT`, `STOPPED`, `TIME_EXIT`, `EXPIRED`, `INVALIDATED`, `NEEDS_REVIEW`.
    - Chave `ORDER_EVENT:{order_id}:{event_key}`, exceto `NEEDS_REVIEW`, que usa `ORDER_REVIEW:{order_id}:{reason}:{session_day}` (D33).
    - Cada evento varrido recebe uma marca append-only em `alert_event_marks` (`order_event_id` PK), inclusive os colapsados, então a varredura nunca revisita um evento.
    - A varredura olha eventos gravados nos últimos 4 dias (`recorded_at`, relógio do banco) (D35).
  - **Incidentes de integridade** (spec 3.3, "incidente → webhook"): cada linha de `integrity_incidents` dos últimos 4 dias vira `INTEGRITY_INCIDENT:{incident_id}` com `kind`, `order_id`, `event_key` e `recorded_at`, nunca `detail`. Inclui incidentes sem ordem e de ordens replay (que a D29 nunca congela), então nenhum incidente fica sem alerta, mesmo com `INTEGRITY_INCIDENTS` já ativo em `/health`.
  - Documento: `schema_version=1`, `alert_key`, `kind` e dados estruturados (tickers, níveis, preços como string decimal, instantes ISO UTC). Nunca mensagens de exceção.
- **D25 — Alertas de `/health` por transição.**
  - Assinatura = (estado, códigos de causa não `INFO` ordenados). O worker grava uma linha em `health_state_log` (append-only) **só quando a assinatura muda**.
  - Alerta `HEALTH:{log_id}` quando o novo estado é `DEGRADED` ou `UNHEALTHY` **e** o estado mudou ou apareceu um código novo. Assim a spec 3.3 ("incidente de integridade → `/health` degradado e webhook") e a spec 6 ("3 ciclos com falha → degradado e webhook") são atendidas mesmo quando o sistema já estava `DEGRADED` por outra causa, sem alertar a cada ciclo.
  - Volta a `HEALTHY` depois de `DEGRADED`/`UNHEALTHY` grava a linha **e** envia um alerta de recuperação `HEALTH:{log_id}` (D32).
  - Se o banco estiver inacessível (ou o schema fora da head), a linha não pode ser gravada: o vigia usa a assinatura em memória e entrega direto pelo sink (`HEALTH_DIRECT:{state}:{now}`), sem outbox; após reiniciar, esse alerta pode repetir uma vez. Quando o banco volta, a assinatura em memória conta como a anterior, então a recuperação de uma queda do banco também gera log e alerta pelo outbox.
  - Documento: estado, estado anterior, `recovered`, `causes` só com `code` e `severity`, `observed_at`.
- **D26 — Watchlist e regras de alerta (ruling do controlador).**
  - `watchlist(ticker PK, added_at)` e `alert_rules` são tabelas **mutáveis** (curadoria do usuário); `DELETE` na watchlist remove as regras (`ON DELETE CASCADE`). `added_at`/`created_at` vêm de `Services.clock`.
  - Regras: `PRICE_CROSS` (`level > 0`, `direction` `ABOVE`|`BELOW`) e `PRESSURE` (`0 < cmf_threshold < 1`, `window_bars` 5..390). `cooldown_minutes` 0..1440 (padrão 30). CHECKs no banco e validação na API (`422 ALERT_RULE_INVALID`).
  - **Ingestão** no job próprio `watchlist` (D20), nunca no `live_cycle`, por `ingest_bars` com `gateway.bar_source(Services.price_source)`: sem fallback, mesma proveniência e deduplicação.
    - Um provider lento atrasa só a watchlist; a avaliação das ordens segue na própria cadência.
    - Janela por ticker: do último candle já gravado no pregão (+1 min) ou da abertura, até `min(floor(market_now), fechamento)`.
    - Falhas viram `ingest_failures` do run `WATCHLIST` (tipo da exceção), como no run `LIVE`.
    - Um minuto publicado pelo provider depois do sucessor nunca é buscado de novo. Isso é aceitável para alertas por fechamento e fica documentado aqui, não corrigido.
  - **Tickers da watchlist** são normalizados (`strip().upper()`) antes de qualquer consulta. Texto vazio ou com espaço interno → `422 TICKER_INVALID`, o mesmo código da validação de sinais (`core/domain/validation.py`). Índices e símbolos não negociáveis são rejeitados pela checagem da Alpaca (`422 TICKER_NOT_TRADABLE`).
  - **Ligar o webhook no meio do pregão** faz a primeira execução avaliar o pregão inteiro (as-of), e cruzamentos anteriores do dia podem sair de uma vez, limitados pelo cooldown de cada regra. Isso é intencional: o mesmo recuperar-o-atraso cobre execuções da watchlist puladas por lentidão do provider.
  - **Avaliação** num run `WATCHLIST` (`data_as_of` pós-ingestão, D5): candles lidos as-of o run, só do pregão corrente, nunca candles com `ts` anterior ao primeiro minuto inteiro após `created_at` da regra (espírito da D1).
    - Cruzamento só por fechamento, nunca intrabar: `ABOVE` quando `close_anterior < level <= close`; `BELOW` quando `close_anterior > level >= close`. O primeiro candle do pregão não tem anterior e nunca cruza.
    - Cooldown: um cruzamento só alerta se `bar.ts − último alerta da regra ≥ cooldown`.
    - Chaves `PRICE_CROSS:{rule_id}:{bar_ts}` e `PRESSURE:{rule_id}:{last_bar_ts}`.
  - Rotas (routers novos, D19; `{ticker}` normalizado, `422 TICKER_INVALID`): `GET /watchlist`, `PUT /watchlist/{ticker}` (checagem de ticker negociável como a D14 só para ticker novo: `422 TICKER_NOT_TRADABLE` com `reason`, `503 TICKER_UNVERIFIABLE`; `201 CREATED`/`200 EXISTING`), `DELETE /watchlist/{ticker}` (`404 WATCHLIST_TICKER_NOT_FOUND`), `POST /watchlist/{ticker}/alerts` (`201`, `422 ALERT_RULE_INVALID`, `404 WATCHLIST_TICKER_NOT_FOUND`), `DELETE /alerts/{rule_id}` (`404 ALERT_RULE_NOT_FOUND`).
- **D27 — Estimativas de pressão (OHLCV).** Módulo puro próprio `OHLCV_PRESSURE_ESTIMATE_V1`, determinístico, `Decimal` com `CANONICAL_CONTEXT`, quantizado em 4 casas (`ROUND_HALF_EVEN`): CLV do último candle, Chaikin Money Flow da janela, inclinação do OBV (variação por candle ÷ volume médio), VWAP da janela e distância ao VWAP em %. "Pressão forte" `BUY` quando `CMF ≥ limiar`, `obv_slope > 0` e `close ≥ VWAP`; `SELL` no espelho (semântica exata na D37). Todo documento de alerta traz `"estimate": true`, `method` e o aviso fixo `DISCLAIMER`. Não é o "Buy/Sell Pressure Engine" (roadmap 08), e nada disso entra em `src/core`.
- **D28 — `/health` distingue timeout de statement (M4).** As consultas de `/health` rodam com `statement_timeout` local de 5 s (`set_config`, só na transação). `OperationalError` cujo `orig.sqlstate == "57014"` (`query_canceled`) vira `HEALTH_QUERY_TIMEOUT` (`DEGRADED`, HTTP 200, `facts=null`); os demais `OperationalError`/`InterfaceError`/timeout do pool continuam `DATABASE_UNAVAILABLE` (503). O banco respondeu, só devagar; a D17 reserva o 503 para banco inacessível ou schema.
- **D29 — M5, M6, M7.**
  - **M5:** `quarantine` grava o incidente e **nunca** congela ordem replay (D15: nenhum comando escreve em replay); vale para `rebuild-projections` e para qualquer outro caminho. `rebuild_all_projections` passa a isolar qualquer exceção por ordem (`ORDER_NOT_FOUND`, `ERROR:<Type>`) e aceita uma lista de ordens.
  - **M6:** `recalculate_orders` valida, antes de abrir o run, os `config_overrides` sobre a configuração de **cada** ordem de origem; falha em qualquer uma → `ReplaySelectionError` (`422 REPLAY_REQUEST_INVALID`) com os ids.
  - **M7:** `get_json` guarda o corpo JSON de um 404 em `ResourceNotFound.body`. `AlpacaAssets` só responde `UNKNOWN_ASSET` quando o 404 traz um corpo de erro da Alpaca (objeto JSON com `message` string); qualquer outro 404 vira `SourceUnavailable` → `503 TICKER_UNVERIFIABLE`. Sem chamada de rede no startup.
- **D30 — Webhook composto na raiz.** `Settings.n8n_webhook_url` (já lido na D13) → `N8nWebhook(client, url)` em `build_services`, com o mesmo `httpx.Client` (timeout por requisição de 5 s, uma tentativa). `Services.alert_sink: AlertSink | None = None` (último campo, com padrão, para não quebrar quem monta `Services` nos testes). `repr` do adapter esconde a URL.
- **D31 — `facts` limitados e revisão sem motivo (entrada 13).**
  - Em `facts`, mapas por ordem dos run details (`integrity_errors`, `order_errors`, `not_evaluated`, `dividend_*_errors`, `split_*_errors`) e `quality_pending` guardam no máximo 100 entradas (chaves ordenadas), com `<chave>_total` quando truncados; listas de falhas de feed também param em 100. As causas continuam calculadas sobre os dados completos.
  - Ordens com `needs_review = true` e `review_reasons` vazio (só por corrupção) contam em `NEEDS_REVIEW_QUEUE` sob `UNSPECIFIED`, nunca somem. O SQL usa o literal `'UNSPECIFIED'` (sem parâmetro dentro da expressão agrupada), e um teste fixa que ele é igual a `UNSPECIFIED_REVIEW_REASON`.
- **D32 — Alerta de recuperação (pergunta aberta 1, ruling).** A volta a `HEALTHY` depois de um estado `DEGRADED`/`UNHEALTHY` gera um alerta `HEALTH:{log_id}` com `recovered: true`. Não há alerta quando o primeiro estado observado já é `HEALTHY`, nem entre dois `HEALTHY`. Sem isso o responsável nunca saberia que o incidente acabou.
- **D33 — Alertas `NEEDS_REVIEW` uma vez por (ordem, motivo, pregão) (pergunta aberta 2, ruling).**
  - Chave `ORDER_REVIEW:{order_id}:{reason}:{session_day}`, com `session_day` (`review_session_day`) calculado assim:
    - `ref` no formato de data ISO → essa data;
    - `ref` como instante ISO com fuso → a data ET desse instante;
    - qualquer outro `ref` → a data ET em que o evento foi gravado.
  - Resultado: os 35 `MISSING_BAR_UNVERIFIABLE` de um pregão viram um alerta, e um recheck gravado dias depois continua apontando para o pregão rechecado.
  - O documento traz o primeiro evento; as contagens continuam no resumo de fim de dia.
- **D34 — Flags do recheck em ordens vivas (pergunta aberta 3, ruling).** O recheck marca revisão também em ordens abertas ou fechadas, como o fim de dia. Pela política da spec 5.4, uma ordem com `needs_review` sai das métricas padrão; uma flag de recheck pode, portanto, retirar retroativamente uma ordem das métricas já exibidas. Isso é conservador e intencional, e a nota de encerramento registra.
- **D35 — Lookback de 4 dias e atraso do enfileiramento (pergunta aberta 5, ruling).**
  - O enfileiramento de eventos só considera eventos gravados nos últimos 4 dias (`ORDER_EVENT_LOOKBACK`). Um worker parado mais que isso perde esses alertas, e ligar o webhook mais tarde envia até 4 dias de backlog.
  - A causa `INFO` `ORDER_EVENT_ALERTS_BEHIND` torna a perda visível. Ela aparece quando existe evento alertável não replay, sem marca, gravado antes de `agora − 4 dias` e depois da primeira marca já gravada: o cursor efetivo (evento mais antigo sem marca) ficou para trás do lookback.
  - Com o webhook nunca ligado não há marcas, e a causa não aparece.
- **D36 — `EVAL_INTERVAL_MINUTES` em 1..59 (pergunta aberta 8, ruling).** A spec não tem limite, mas o campo de minuto do cron tem. `python -m virtual_orders.worker run` valida antes de montar os serviços: `{"error": "CONFIG_INVALID", "errors": ["OUT_OF_RANGE:EVAL_INTERVAL_MINUTES"]}` no stderr e código 2, nunca um traceback. `rebuild-projections` não depende do intervalo.
- **D37 — Semântica exata do alerta de pressão forte (pergunta aberta 7, ruling).**
  - **Janela:** os últimos `window_bars` candles de 1 min do pregão corrente, lidos as-of o run `WATCHLIST`, com `ts` a partir do primeiro minuto inteiro depois de `created_at` da regra. Com menos candles, nada é avaliado.
  - **Valores comparados:** já quantizados em 4 casas (`ROUND_HALF_EVEN`): `CMF = Σ(CLV·volume)/Σvolume` (0 com volume zero); `obv_slope = OBV/(n−1)/volume médio`; `VWAP = Σ(preço típico (H+L+C)/3 · volume)/Σvolume` (fechamento com volume zero); `distância = (fechamento − VWAP)/VWAP·100`.
  - **Regra:** `BUY` se e somente se `CMF ≥ cmf_threshold`, `obv_slope > 0` e `distância ≥ 0`. `SELL` se e somente se `CMF ≤ −cmf_threshold`, `obv_slope < 0` e `distância ≤ 0`. Caso contrário, nada. O limiar é obrigatório por regra (`0 < t < 1`), sem padrão.
  - **Um alerta por último candle** (`PRESSURE:{rule_id}:{last_bar_ts}`), respeitando o cooldown medido do `last_bar_ts` do último alerta da regra.
  - **O documento sempre** traz `"estimate": true`, `method = OHLCV_PRESSURE_ESTIMATE_V1`, `DISCLAIMER`, `side` e todos os valores.
- **D38 — Runs de abertura e de fim de dia ausentes (I5).**
  - `/health` coleta `missing_runs` (em `facts`, para ficar visível) e gera duas causas `DEGRADED`:
    - `OPENING_MISSING`: pregão cuja abertura + `OPENING_GRACE = 30 min` já passou sem run `OPENING` `COMPLETED` para o `session_day`;
    - `END_OF_DAY_MISSING`: pregão cujo fechamento + `END_OF_DAY_GRACE = 3 h` já passou (cobre o retry das 18:30) sem run `END_OF_DAY` `COMPLETED`.
  - **Âncora:** só pregões **posteriores** ao menor `session_day` de qualquer run `OPENING`/`END_OF_DAY` já gravado (o dia da primeira execução é de partida), dentro de `MISSING_RUN_LOOKBACK = 14 dias`. Sem nenhum run desses tipos não há causa, então um banco novo não nasce degradado.
  - A D25 alerta a transição. A recuperação continua manual: rodar o job pelo CLI é entrada do 3C.
- **D39 — Lock do worker vigiado (M5).**
  - O runner registra o job `worker_lock` (a cada 1 min). Ele confirma em `pg_locks`, pela conexão que segura o lock, que o advisory lock de sessão ainda pertence a este processo.
  - Se a conexão caiu (reinício ou failover do banco, `idle_session_timeout`) ou o lock sumiu, o job loga o erro, entrega direto pelo sink um alerta `WORKER_LOCK_LOST:{instante}` (sem outbox, porque o banco pode estar fora), chama `scheduler.shutdown(wait=False)`, e o processo sai com código 3.
  - O supervisor do contêiner reinicia o processo, que disputa o lock de novo. Um segundo worker nunca roda ao lado de um primeiro que perdeu o lock.

## Conflitos e lacunas da spec resolvidos acima

1. **D4 × spec 3.2 × núcleo congelado:** a D4 nomeia `DATA_QUALITY_RECHECK:{session_date}:{data_as_of}` como identidade, mas a tabela de eventos (3.2) e o `EventType` congelado não têm esse tipo. Resolvido pela D22: tabela append-only própria com essa chave, fora de `order_events`.
2. **Spec 5.3 "09:30–16:05 ET" × meio pregão e feriados:** resolvido pela D20 (janela `[abertura, fechamento + 5 min]` do calendário NYSE).
3. **Spec 3.3/6 "webhook de alerta" por incidente × pedido "alertar só na transição de estado":** resolvido pela D25 (transição de estado ou código novo de causa não `INFO`).
4. **Spec 6 "yfinance/FMP indisponíveis → NEEDS_REVIEW" × D16 (só registrava a falha):** resolvido pela D23.
5. **Spec 1.4 ("day trade e avaliação por streaming" fora do escopo) × alertas de preço:** os alertas usam candles de 1 min fechados na cadência do ciclo, lidos as-of; não há streaming (D26).
6. **Roadmap 1.3 item 08 (Buy/Sell Pressure Engine como sub-projeto) × pedido do responsável:** resolvido pela D27 (estimativa mínima versionada e rotulada, fora de `src/core`).
7. **Spec 8 não tem variáveis para watchlist, regras, timeout de webhook ou limiares de pressão:** regras vivem no banco e são geridas pela API (D26); timeouts e limites são constantes documentadas (D24, D28), não variáveis de ambiente.
8. **Spec 5.1 não lista rotas de watchlist:** routers novos, como o 3A previu (D26).
9. **Plano 3A ("watchlist com ingestão" no 3C) × ruling do controlador:** watchlist, ingestão e regras entram no 3B; gráficos, candles para UI e exibição de pressão continuam no 3C.
10. **Spec 5.2 (lock só do ciclo) × vários jobs e possíveis workers duplicados:** resolvido pela D20 (lock de processo + `max_instances=1`).
11. **Spec 3.6 ("leitura atual só para RECALCULATE e dashboard"):** alertas e recheck leem as-of o `data_as_of` de runs próprios (`WATCHLIST`, `QUALITY_RECHECK`), nunca "a versão mais recente sem limite".
12. **Spec 3.3 ("incidente de integridade → webhook") × D25 (só transição):** com `INTEGRITY_INCIDENTS` já ativo, um incidente novo não mudaria a assinatura; incidentes sem ordem e de replay (nunca congeladas, D29) não gerariam `NEEDS_REVIEW`. Resolvido pela D24: todo incidente vira `INTEGRITY_INCIDENT:{id}`, além da transição da D25.
13. **Spec 4.6 ("stop_vigente naquele minuto") × recheck dias depois:** resolvido pela D22, com níveis derivados do ledger até o fechamento do pregão rechecado, ou checagem pulada com motivo explícito, nunca os níveis atuais.
14. **Perguntas abertas do autor:** resolvidas pelos rulings do controlador nas D32 (recuperação), D33 (revisões por pregão), D34 (flags do recheck em ordens vivas), D35 (lookback e atraso), D36 (intervalo 1..59) e D37 (pressão). Também ficaram registradas: o primeiro candle do pregão nunca cruza (D26); APScheduler 3.x com override de mypy (D20, Task 1); a checagem de negociável da Alpaca rejeita índices (D26).

## Estrutura de arquivos

```
docs/superpowers/plans/2026-09-13-virtual-order-engine-worker.md     # Task 0  — este plano, versionado
pyproject.toml                                                        # Task 1  — apscheduler + override de mypy
migrations/versions/0003_worker_alerts_watchlist.py                   # Task 8
src/virtual_orders/
  services.py              # Task 7   — campo alert_sink
  bootstrap.py             # Task 7   — fecha o cliente se make_engine levantar; compõe N8nWebhook
  storage/database.py      # Task 16  — WORKER_LOCK_KEY
  storage/tables.py        # Task 8   — tabelas novas + APPEND_ONLY_TABLES
  ledger/runs.py           # Task 8   — RunKind.QUALITY_RECHECK, RunKind.WATCHLIST
  ledger/quarantine.py     # Task 4   — nunca congela replay (M5)
  evaluator/cycle.py       # Task 2   — require_aware em evaluate_order; market_now em runs FAILED
  evaluator/commands.py    # Task 2   — require_aware em finalize_validity/expire_due_orders
  evaluator/corporate.py   # Task 3   — DIVIDEND_UNVERIFIED quando as duas fontes levantam
  evaluator/rebuild.py     # Task 4   — isolamento por ordem, lista de ordens
  evaluator/replay.py      # Task 5   — overrides contra a configuração de origem (M6)
  evaluator/recheck.py     # Task 10  — run_quality_recheck (D22)
  marketdata/http.py       # Task 6   — ResourceNotFound.body
  marketdata/alpaca.py     # Task 6   — 404 sem corpo Alpaca → SourceUnavailable (M7)
  readmodels/health.py     # Tasks 8, 9, 12 — head 0003, M4, facts limitados, UNSPECIFIED, pendências D22, runs ausentes D38, causas INFO de alertas D24/D35
  readmodels/quality.py    # Task 9   — PendingQuality, pending_quality_sessions
  readmodels/orders.py     # Task 10  — data_quality.rechecks
  alerts/__init__.py       # Task 1
  alerts/sink.py           # Task 7   — AlertSink, AlertDeliveryFailed
  alerts/outbox.py         # Task 12  — AlertKind, enqueue_alert, enqueue_event_alerts, deliver_pending_alerts (backoff, expiração, orçamento), undeliverable_alerts, order_event_alerts_behind
  alerts/watchlist.py      # Task 13  — repositório da watchlist e das regras
  alerts/rules.py          # Task 13  — price_crossings, pressure_alert (puro)
  alerts/watch.py          # Task 13  — run_watchlist_cycle
  alerts/health_watch.py   # Task 14  — HealthWatcher (D25)
  alerts/summary.py        # Task 14  — enqueue_end_of_day_summary
  analytics/__init__.py    # Task 1
  analytics/pressure.py    # Task 11  — estimativas OHLCV (puro)
  notify/__init__.py       # Task 1
  notify/n8n.py            # Task 7   — adapter N8nWebhook
  api/errors.py            # Task 15  — mapeamento dos erros da watchlist
  api/app.py               # Task 15  — inclui o router da watchlist
  api/routes/watchlist.py  # Task 15
  worker/__init__.py       # Task 1
  worker/schedule.py       # Task 16  — build_schedule, janelas de pregão
  worker/jobs.py           # Task 16  — WorkerJobs, JobResult
  worker/runner.py         # Task 16  — run_worker, acquire_worker_lock
  worker/__main__.py       # Task 16  — CLI run / rebuild-projections
tests/test_import_boundaries.py                                        # Tasks 1, 17
tests/test_bootstrap_worker.py                                         # Task 7
tests/notify/__init__.py, tests/notify/test_n8n_webhook.py             # Task 7
tests/marketdata/test_alpaca_assets_404.py                             # Task 6
tests/analytics/__init__.py, tests/analytics/test_pressure.py          # Task 11
tests/alerts/__init__.py, tests/alerts/test_review_keys.py             # Task 12
tests/alerts/test_rules.py                                             # Task 13
tests/worker/__init__.py, tests/worker/test_schedule.py                # Task 16
tests/integration/alert_support.py                                     # Task 12
tests/integration/test_naive_clocks.py, test_cycle.py, test_opening.py # Task 2 (testes acrescentados)
tests/integration/test_dividend_unverifiable.py                        # Task 3
tests/integration/test_rebuild_hardening.py                            # Task 4
tests/integration/test_recalculate_overrides.py                        # Task 5
tests/integration/test_schema_0003.py                                  # Task 8
tests/integration/test_quality_recheck.py                              # Task 10
tests/integration/test_alert_outbox.py                                 # Task 12
tests/integration/test_watchlist_alerts.py                             # Task 13
tests/integration/test_health_watch.py                                 # Task 14
tests/integration/api/test_health_api.py                               # Tasks 2, 8 (ajustes)
tests/integration/api/test_health_hardening.py                         # Task 9
tests/integration/api/test_order_quality_api.py                        # Task 10
tests/integration/api/test_orders_api.py                               # Task 10 (ajuste)
tests/integration/api/test_signals_api.py                              # Task 15 (ajuste do teste de autenticação)
tests/integration/api/test_watchlist_api.py                            # Task 15
tests/integration/worker/__init__.py, conftest.py, test_jobs.py, test_runner.py, test_cli.py   # Task 16
docs/superpowers/notes/2026-09-13-plan3b-closeout.md                   # Task 17
```

Comando de verificação usado em toda task (`<paths>` = arquivos de teste da task):

```bash
uv run pytest <paths> -q && uv run ruff check src tests migrations && uv run mypy
```

---

### Task 0: Conferir o plano versionado e medir a linha de base

**Files:**
- Nenhum arquivo novo. O controlador versiona este plano em `docs/superpowers/plans/2026-09-13-virtual-order-engine-worker.md` no branch `plan-3b-worker` antes de despachar a Task 1.

**Interfaces:**
- Produces: `N_BASE` (esperado: 671 passando, 0 skips, 0 warnings, medido no 3A em `9cb2889`), registrado no ledger e usado nas Tasks 1 e 17.

- [ ] **Step 1: Confirm the plan is committed on the branch**

```bash
git switch plan-3b-worker
git log --oneline -1 -- docs/superpowers/plans/2026-09-13-virtual-order-engine-worker.md
git status --short
git merge-base --is-ancestor 8cfb743 HEAD && echo "based on plan-3a-api tip"
```

Expected: um commit listado, árvore limpa e `based on plan-3a-api tip`. Se o plano não estiver versionado, pare e reporte ao controlador; não copie de outro lugar.

- [ ] **Step 2: Measure the inherited test baseline**

```bash
docker compose -f docker-compose.test.yml down -v
docker compose -f docker-compose.test.yml up -d --wait
uv run pytest -p no:cacheprovider -o addopts="" -q 2>&1 | tail -3
uv run ruff check src tests migrations
uv run mypy
```

Expected: `671 passed` sem `skipped` nem `warnings`; ruff e mypy limpos. Registre a linha final como `N_BASE`. Se o número diferir, registre o valor medido (nenhuma task presume um número fixo) e siga. A Task 0 não gera commit.

---

### Task 1: Dependência APScheduler, pacotes novos e fronteiras de import

**Files:**
- Modify: `pyproject.toml` (via `uv add`; override de mypy)
- Create: `src/virtual_orders/alerts/__init__.py`, `src/virtual_orders/analytics/__init__.py`, `src/virtual_orders/notify/__init__.py`, `src/virtual_orders/worker/__init__.py`
- Modify: `tests/test_import_boundaries.py`

**Interfaces:**
- Consumes: helpers existentes de `tests/test_import_boundaries.py` (`SRC`, `_rel`, `_imported_modules`, `_offending`, `_neutral_files`, `IO_LIBRARIES`, `PROVIDER_ADAPTERS`, `PROVIDER_LIBRARIES`, `APPLICATION_LAYER`, `ADAPTER_FILES`, `COMPOSITION_ROOT`).
- Produces:
  - dependência `apscheduler>=3.11,<4`;
  - constantes de fronteira `WORKER_PACKAGE = "virtual_orders/worker"`, `WORKER_ENTRYPOINT = "virtual_orders/worker/__main__.py"`, `PLATFORM_PURE_MODULES`;
  - `virtual_orders.notify.n8n` registrado como adapter (arquivo criado na Task 7).

- [ ] **Step 1: Add the dependency and the third-party mypy override**

```bash
uv add "apscheduler>=3.11,<4"
uv run python -c "import apscheduler; from apscheduler.triggers.cron import CronTrigger; from apscheduler.schedulers.blocking import BlockingScheduler; print(apscheduler.__version__)"
```

Expected: uma versão `3.11.x` (ou `3.x` superior) impressa.

Em `pyproject.toml`, troque o último bloco de override:

```toml
[[tool.mypy.overrides]]
module = ["pandas_market_calendars", "pandas_market_calendars.*", "yfinance", "yfinance.*"]
ignore_missing_imports = true
```

por:

```toml
[[tool.mypy.overrides]]
module = [
    "pandas_market_calendars", "pandas_market_calendars.*", "yfinance", "yfinance.*",
    "apscheduler", "apscheduler.*",
]
ignore_missing_imports = true
```

- [ ] **Step 2: Create the empty packages**

Cada um dos quatro arquivos abaixo recebe só a docstring indicada:

`src/virtual_orders/alerts/__init__.py`:
```python
"""Owner alerts (D24–D26): outbox, rules, watchlist and health transitions. Provider-neutral."""
```

`src/virtual_orders/analytics/__init__.py`:
```python
"""Pure analytics over already-loaded market data (D27). No I/O."""
```

`src/virtual_orders/notify/__init__.py`:
```python
"""Notification adapters. Imported only by the composition root (D30)."""
```

`src/virtual_orders/worker/__init__.py`:
```python
"""APScheduler worker process (spec 5.3, D20)."""
```

- [ ] **Step 3: Write the failing boundary tests**

Em `tests/test_import_boundaries.py`:

1. Substitua `NEUTRAL_PACKAGES` por:

```python
NEUTRAL_PACKAGES = [
    "virtual_orders/storage", "virtual_orders/ledger", "virtual_orders/evaluator", "virtual_orders/readmodels",
    "virtual_orders/alerts", "virtual_orders/analytics",
]
```

2. Substitua `PROVIDER_ADAPTERS` e `ADAPTER_FILES` por:

```python
PROVIDER_ADAPTERS = (
    "virtual_orders.marketdata.alpaca", "virtual_orders.marketdata.fmp",
    "virtual_orders.marketdata.yfinance_source", "virtual_orders.marketdata.http",
    "virtual_orders.notify.n8n",
)
```

```python
ADAPTER_FILES = frozenset({
    "virtual_orders/marketdata/alpaca.py", "virtual_orders/marketdata/fmp.py",
    "virtual_orders/marketdata/yfinance_source.py", "virtual_orders/marketdata/http.py",
    "virtual_orders/notify/n8n.py",
})
```

3. Substitua `APPLICATION_LAYER` por:

```python
APPLICATION_LAYER = (
    "virtual_orders.api", "virtual_orders.bootstrap", "virtual_orders.config", "virtual_orders.worker",
    "fastapi", "starlette", "uvicorn",
)
```

4. Acrescente, depois de `APPLICATION_NEUTRAL`:

```python
WORKER_PACKAGE = "virtual_orders/worker"
WORKER_ENTRYPOINT = "virtual_orders/worker/__main__.py"
PLATFORM_PURE_MODULES = ["virtual_orders/analytics/pressure.py", "virtual_orders/alerts/rules.py"]
PLATFORM_INFRASTRUCTURE = (
    "virtual_orders.storage", "virtual_orders.ledger", "virtual_orders.evaluator", "virtual_orders.readmodels",
    "virtual_orders.marketdata", "virtual_orders.api", "virtual_orders.worker", "virtual_orders.bootstrap",
    "virtual_orders.config", "virtual_orders.services", "virtual_orders.notify",
)


def _worker_files() -> list[Path]:
    return sorted((SRC / WORKER_PACKAGE).rglob("*.py"))
```

5. Acrescente ao final do arquivo:

```python
@pytest.mark.parametrize("path", _worker_files(), ids=_rel)
def test_worker_never_imports_adapters_provider_libraries_or_the_http_layer(path: Path) -> None:
    assert _offending(
        path, PROVIDER_ADAPTERS + PROVIDER_LIBRARIES + ("virtual_orders.api", "fastapi", "starlette", "uvicorn"),
    ) == []


@pytest.mark.parametrize("path", [p for p in _worker_files() if _rel(p) != WORKER_ENTRYPOINT], ids=_rel)
def test_only_the_worker_entrypoint_imports_the_composition_root(path: Path) -> None:
    assert _offending(path, ("virtual_orders.bootstrap", "virtual_orders.config")) == []


def test_platform_pure_modules_have_no_io_or_infrastructure_imports() -> None:
    # Not parametrized on purpose: until Tasks 11 and 13 create the modules an empty parameter set would be
    # reported as a skip, and every full run must be skip-free. Task 17 pins that both files exist.
    existing = [SRC / relative for relative in PLATFORM_PURE_MODULES if (SRC / relative).exists()]
    assert {_rel(path): _offending(path, IO_LIBRARIES + PLATFORM_INFRASTRUCTURE) for path in existing} == {
        _rel(path): [] for path in existing
    }


def test_boundary_scan_covers_the_worker_and_alert_packages() -> None:
    assert "virtual_orders/worker/__init__.py" in {_rel(p) for p in _worker_files()}
    neutral = {_rel(p) for p in _neutral_files()}
    assert {"virtual_orders/alerts/__init__.py", "virtual_orders/analytics/__init__.py"} <= neutral
    assert "virtual_orders/notify/n8n.py" in ADAPTER_FILES
    assert "virtual_orders.worker" in APPLICATION_LAYER
    assert importlib.util.find_spec("apscheduler") is not None
```

- [ ] **Step 4: Run the boundary tests**

Run: `uv run pytest tests/test_import_boundaries.py -q && uv run ruff check src tests migrations && uv run mypy`
Expected: PASS. Os pacotes novos só têm docstrings; os dois testes parametrizados sobre o worker já têm `worker/__init__.py`; o teste dos módulos puros não é parametrizado e verifica só os arquivos que já existem. Se o Step 2 não tivesse sido feito, `test_boundary_scan_covers_the_worker_and_alert_packages` falharia, que é o RED desta task.

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest 2>&1 | tail -1`
Expected: `N_BASE` + os testes novos, todos verdes, **sem** `skipped`.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock src/virtual_orders/alerts/__init__.py src/virtual_orders/analytics/__init__.py src/virtual_orders/notify/__init__.py src/virtual_orders/worker/__init__.py tests/test_import_boundaries.py
git commit -m "build(worker): add APScheduler and import boundaries for worker, alerts and notify"
```

---

### Task 2: Guardas de relógio no evaluator, `market_now` em runs `FAILED` e ramo `FAILED` da abertura (entradas 2 e 13)

**Files:**
- Modify: `src/virtual_orders/evaluator/cycle.py` (`evaluate_order`, bloco `except` de `run_live_cycle`)
- Modify: `src/virtual_orders/evaluator/commands.py` (`finalize_validity`, `expire_due_orders`)
- Test: `tests/integration/test_naive_clocks.py`, `tests/integration/test_cycle.py`, `tests/integration/test_opening.py`, `tests/integration/api/test_health_api.py` (testes acrescentados)

**Interfaces:**
- Consumes: `require_aware(value, name)` (`evaluator/clock.py`); `run_opening`; `latest_run_status`; harness `api` do 3A.
- Produces:
  - `evaluate_order(..., market_now=…)`, `finalize_validity(..., now=…)` e `expire_due_orders(..., now=…)` levantam `ValueError("<name> must be timezone-aware")` antes de qualquer I/O;
  - detail `FAILED` do run `LIVE` = `{"error": repr(exc), "market_now": now}`.

- [ ] **Step 1: Write the failing tests**

Acrescente ao final de `tests/integration/test_naive_clocks.py` (e os imports indicados ao bloco de imports do arquivo):

```python
from virtual_orders.evaluator import commands as commands_module
from virtual_orders.evaluator.commands import expire_due_orders, finalize_validity
from virtual_orders.evaluator.cycle import evaluate_order
from virtual_orders.ledger.runs import RunKind, start_run
from virtual_orders.marketdata.asof import acquire_data_as_of


def test_evaluate_order_rejects_naive_market_now_before_locking(engine):
    order_id = submit_default(engine).auto_order_id
    as_of = acquire_data_as_of(engine)
    with engine.begin() as conn:
        run = start_run(conn, RunKind.LIVE, as_of, CODE_VERSION)
    with pytest.raises(ValueError, match="market_now must be timezone-aware"):
        evaluate_order(engine, order_id, run, market_now=NAIVE)
    assert count(engine, "order_eval_segments") == 0


def test_finalize_validity_rejects_naive_now_before_locking(engine, monkeypatch):
    # apply_validity_end already rejects a naive `now`, but only after the order row is locked and loaded.
    # Making lock_order explode proves the guard now runs before any I/O (this is the RED of the test).
    order_id = submit_default(engine).auto_order_id

    def must_not_lock(conn, locked_id):
        raise AssertionError("finalize_validity locked the order before validating now")

    monkeypatch.setattr(commands_module, "lock_order", must_not_lock)
    with pytest.raises(ValueError, match="now must be timezone-aware"):
        finalize_validity(engine, order_id, now=NAIVE)


def test_expire_due_orders_rejects_naive_now(engine):
    submit_default(engine, valid_sessions=1)
    with pytest.raises(ValueError, match="now must be timezone-aware"):
        expire_due_orders(engine, now=NAIVE)
```

Acrescente ao final de `tests/integration/test_cycle.py` (imports: `from datetime import datetime`; os demais já existem no arquivo):

```python
def test_failed_cycle_records_its_market_now_and_releases_the_lock(engine, monkeypatch):
    submit_default(engine)

    def exploding(outcomes):
        raise RuntimeError("boom")

    monkeypatch.setattr(cycle_module, "split_errors", exploding)
    with pytest.raises(RuntimeError):
        cycle(engine, FakeBarSource(scenario_bars()), "10:30")
    with engine.connect() as conn:
        run_id = conn.execute(select(tables.evaluation_runs.c.run_id)).scalar_one()
        status, detail = latest_run_status(conn, run_id)
        assert conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": CYCLE_LOCK_KEY}).scalar_one()
    assert status is RunStatus.FAILED
    assert datetime.fromisoformat(detail["market_now"]) == et(DAY, "10:30")
    assert detail["error"].startswith("RuntimeError")
```

Acrescente ao final de `tests/integration/test_opening.py` (import: `from sqlalchemy import select`):

```python
class ExplodingDividends:
    name = "exploding"

    def fetch_dividends(self, ticker, start, end):
        raise RuntimeError("programming error (fake)")


def test_unexpected_error_marks_the_opening_run_failed(engine):
    open_confirmed(engine)
    with pytest.raises(RuntimeError):
        opening(engine, ExplodingDividends(), FakeDividends("yfinance"), FakeSplits())
    with engine.connect() as conn:
        run_id = conn.execute(
            select(tables.evaluation_runs.c.run_id).where(tables.evaluation_runs.c.kind == "OPENING")
        ).scalar_one()
        status, detail = latest_run_status(conn, run_id)
    assert status is RunStatus.FAILED
    assert detail["session_day"] == EX_DAY and detail["error"].startswith("RuntimeError")
```

Acrescente ao final de `tests/integration/api/test_health_api.py` (import: `from datetime import datetime`):

```python
def test_failed_live_run_staleness_uses_its_recorded_market_now(api):
    with api.services.engine.begin() as conn:
        run = start_run(conn, RunKind.LIVE, et(DAY, "09:00"), CODE_VERSION)
        finish_run(conn, run.run_id, RunStatus.FAILED,
                   {"error": "RuntimeError('x')", "market_now": et(DAY, "10:00")})
    api.clock.set(et(DAY, "10:30"))
    status, body = health(api)
    assert status == 200 and body["state"] == "DEGRADED"
    stale = next(c for c in body["causes"] if c["code"] == "LIVE_CYCLE_STALE")
    assert datetime.fromisoformat(stale["detail"]["last_market_now"]) == et(DAY, "10:00")
```

Este último já passa hoje (a leitura de `market_now` existe desde o 3A); ele fixa o contrato que o Step 3 passa a cumprir na escrita.

Nos quatro arquivos, os imports indicados entram **no bloco de imports existente**, fundidos com as linhas do mesmo módulo (por exemplo `from virtual_orders.evaluator.commands import cancel_order, expire_due_orders, finalize_validity`). Rode `uv run ruff check --fix tests/integration/test_naive_clocks.py tests/integration/test_cycle.py tests/integration/test_opening.py tests/integration/api/test_health_api.py` para acertar a ordem (I001); se `pytest` ainda não estiver importado em `test_cycle.py`, acrescente `import pytest`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/integration/test_naive_clocks.py tests/integration/test_cycle.py tests/integration/test_opening.py -q`
Expected: FAIL em:
- `test_evaluate_order_rejects_naive_market_now_before_locking` e `test_expire_due_orders_rejects_naive_now` (sem `ValueError` ou com mensagem diferente);
- `test_finalize_validity_rejects_naive_now_before_locking` (`AssertionError: finalize_validity locked the order before validating now`: hoje o `ValueError` só sai de `apply_validity_end`, depois do lock);
- `test_failed_cycle_records_its_market_now_and_releases_the_lock` (`KeyError: 'market_now'`).

Dois testes já passam e são **pins** declarados, não RED:
- `test_unexpected_error_marks_the_opening_run_failed`: cobertura faltante do ramo `FAILED`, não defeito;
- `test_failed_live_run_staleness_uses_its_recorded_market_now` (no arquivo de `/health`): fixa a leitura que o Step 3 passa a alimentar na escrita.

- [ ] **Step 3: Implement the guards and the FAILED detail**

Em `src/virtual_orders/evaluator/cycle.py`, a primeira linha do corpo de `evaluate_order` (antes de `def operation()`) passa a ser:

```python
    market_now = require_aware(market_now, "market_now")
```

No mesmo arquivo, dentro de `run_live_cycle`, troque:

```python
                    finish_run(conn, run.run_id, RunStatus.FAILED, {"error": repr(exc)})
```

por:

```python
                    finish_run(conn, run.run_id, RunStatus.FAILED, {"error": repr(exc), "market_now": now})
```

Em `src/virtual_orders/evaluator/commands.py`, a primeira linha do corpo de `finalize_validity` passa a ser:

```python
    now = require_aware(now, "now")
```

e, em `expire_due_orders`, a primeira instrução depois da docstring (antes de `excluded = …`) passa a ser:

```python
    now = require_aware(now, "now")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/integration/test_naive_clocks.py tests/integration/test_cycle.py tests/integration/test_opening.py tests/integration/test_commands.py tests/integration/test_quality.py tests/integration/api/test_health_api.py -q && uv run ruff check src tests migrations && uv run mypy`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/virtual_orders/evaluator/cycle.py src/virtual_orders/evaluator/commands.py tests/integration/test_naive_clocks.py tests/integration/test_cycle.py tests/integration/test_opening.py tests/integration/api/test_health_api.py
git commit -m "fix(evaluator): reject naive clocks in scheduled entry points and keep market_now on failed cycles"
```

---

### Task 3: Dividendo não verificável quando as duas fontes levantam (entrada 6, D23)

**Files:**
- Modify: `src/virtual_orders/evaluator/corporate.py` (`_lookup`, novo `_unverifiable_command`, laço de `apply_dividends`)
- Test: `tests/integration/test_dividend_unverifiable.py`

**Interfaces:**
- Consumes: `apply_command`, `CommandInput`, `known_hashes`, `last_segment_end`, `isolated`, `fill_model.flag_review(state, reason, ref)`.
- Produces:
  - `_lookup(source, ticker, ex_date, failures) -> tuple[DividendRecord | None, bool]` (registro, levantou);
  - `apply_dividends` inalterado na assinatura; para um ticker com as duas consultas levantando, devolve um `OrderOutcome` por ordem candidata com `NEEDS_REVIEW:DIVIDEND_UNVERIFIED:{ex_date}`, `NEEDS_REVIEW:DIVIDEND_POSITION_UNCONFIRMED:{ex_date}` ou nenhum evento, e não grava `dividends`.

- [ ] **Step 1: Write the failing tests**

`tests/integration/test_dividend_unverifiable.py`:

```python
from datetime import date
from decimal import Decimal

from tests.integration.support import (
    CODE_VERSION,
    DAY,
    FakeBarSource,
    FakeDividends,
    count,
    feeds,
    flat_raw,
    raw,
    submit_default,
)
from tests.support import et
from virtual_orders.evaluator.corporate import apply_dividends
from virtual_orders.evaluator.cycle import run_live_cycle
from virtual_orders.evaluator.rebuild import rebuild_projection
from virtual_orders.ledger.events import stored_events
from virtual_orders.ledger.orders import delete_projection, read_projection_row

EX_DAY = "2025-11-26"
TOLERANCE = Decimal("0.001")
UNVERIFIED = f"NEEDS_REVIEW:DIVIDEND_UNVERIFIED:{EX_DAY}"


def held_open_bars():
    return flat_raw(DAY, "09:30", "10:05", 105) + [raw(DAY, "10:05", 101, 101.5, 100.5, 101.2)] + flat_raw(
        DAY, "10:06", "16:00", 103
    )


def evaluate_through_close(engine, bars):
    run_live_cycle(engine, feeds(FakeBarSource(bars)), code_version=CODE_VERSION, market_now=et(DAY, "16:30"),
                   close_trailing_gap=True)


def pay(engine, primary, secondary, failures=None):
    return apply_dividends(engine, ex_date=date.fromisoformat(EX_DAY), primary=primary, secondary=secondary,
                           tolerance=TOLERANCE, now=et(EX_DAY, "09:25"), source_failures=failures)


def both_failing():
    return FakeDividends("fmp", failing=True), FakeDividends("yfinance", failing=True)


def keys(engine, order_id):
    with engine.connect() as conn:
        return [e.prepared.event_key for e in stored_events(conn, order_id)]


def test_both_sources_raising_flags_an_open_position_without_a_dividend_record(engine):
    order_id = submit_default(engine).auto_order_id
    evaluate_through_close(engine, held_open_bars())
    failures: dict[str, str] = {}

    outcomes = pay(engine, *both_failing(), failures=failures)

    assert [(o.order_id, o.event_keys) for o in outcomes] == [(order_id, (UNVERIFIED,))]
    assert set(failures) == {"fmp:AAPL", "yfinance:AAPL"}
    assert count(engine, "dividends") == 0
    with engine.connect() as conn:
        projection = read_projection_row(conn, order_id)
    assert projection["needs_review"] and projection["r_multiple"] == 0
    with engine.begin() as conn:
        delete_projection(conn, order_id)
    assert rebuild_projection(engine, order_id) == projection


def test_rerun_with_sources_still_down_is_a_no_op(engine):
    order_id = submit_default(engine).auto_order_id
    evaluate_through_close(engine, held_open_bars())
    pay(engine, *both_failing())
    assert [o.event_keys for o in pay(engine, *both_failing())] == [()]
    assert keys(engine, order_id).count(UNVERIFIED) == 1


def test_position_without_shares_is_not_flagged(engine):
    order_id = submit_default(engine).auto_order_id
    evaluate_through_close(engine, flat_raw(DAY, "09:30", "16:00", 105))  # never enters the zone: no position
    assert [o.event_keys for o in pay(engine, *both_failing())] == [()]
    assert not any(key.startswith("NEEDS_REVIEW") for key in keys(engine, order_id))


def test_position_not_confirmed_through_the_prior_close_is_flagged_as_unconfirmed(engine):
    order_id = submit_default(engine).auto_order_id  # never evaluated
    assert [o.event_keys for o in pay(engine, *both_failing())] == [
        (f"NEEDS_REVIEW:DIVIDEND_POSITION_UNCONFIRMED:{EX_DAY}",)
    ]
    assert UNVERIFIED not in keys(engine, order_id)


def test_both_sources_answering_empty_is_still_no_dividend(engine):
    order_id = submit_default(engine).auto_order_id
    evaluate_through_close(engine, held_open_bars())
    assert pay(engine, FakeDividends("fmp"), FakeDividends("yfinance")) == []
    assert not any(key.startswith("NEEDS_REVIEW") for key in keys(engine, order_id))


def test_one_source_raising_keeps_the_plan_2_behaviour(engine):
    order_id = submit_default(engine).auto_order_id
    evaluate_through_close(engine, held_open_bars())
    assert pay(engine, FakeDividends("fmp", failing=True), FakeDividends("yfinance")) == []
    assert UNVERIFIED not in keys(engine, order_id)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/integration/test_dividend_unverifiable.py -q`
Expected: FAIL em `test_both_sources_raising_flags_an_open_position_without_a_dividend_record`, `test_rerun_with_sources_still_down_is_a_no_op` e `test_position_not_confirmed_through_the_prior_close_is_flagged_as_unconfirmed` (hoje `apply_dividends` devolve `[]`). Os outros três já passam e fixam o que não pode mudar.

- [ ] **Step 3: Implement**

Em `src/virtual_orders/evaluator/corporate.py`, substitua `_lookup` por:

```python
def _lookup(
    source: DividendSource, ticker: str, ex_date: date, failures: dict[str, str]
) -> tuple[DividendRecord | None, bool]:
    """(record for the ex-date or None, whether the source raised). Empty and unavailable are different (D23)."""
    try:
        records = source.fetch_dividends(ticker, ex_date, ex_date)
    except (SourceUnavailable, SourceDataError) as exc:
        failures[f"{source.name}:{ticker}"] = str(exc)
        return None, True
    return next((record for record in records if record.ex_date == ex_date), None), False
```

Acrescente, logo depois de `_dividend_command`:

```python
def _unverifiable_command(ex_date: date, position_confirmed_by: datetime) -> Callable[[CommandInput], StepResult]:
    """Spec 6 / D23: both dividend sources unavailable -> review, never a credit and never a dividends row."""
    event_key = f"DIVIDEND:{ex_date.isoformat()}"
    ref = ex_date.isoformat()

    def command(inp: CommandInput) -> StepResult:
        state = inp.projection.state
        if state.is_final or state.frozen or event_key in known_hashes(inp.conn, inp.order.id):
            return StepResult(state)
        last_to = last_segment_end(inp.conn, inp.order.id)
        if last_to is None or last_to < position_confirmed_by:
            unconfirmed: StepResult = inp.model.flag_review(state, "DIVIDEND_POSITION_UNCONFIRMED", ref)
            return unconfirmed
        if state.qty_open <= 0:
            return StepResult(state)
        flagged: StepResult = inp.model.flag_review(state, "DIVIDEND_UNVERIFIED", ref)
        return flagged

    return command
```

Em `apply_dividends`, substitua as linhas:

```python
        first, second = _lookup(primary, ticker, ex_date, failures), _lookup(secondary, ticker, ex_date, failures)
        if first is None and second is None:
            continue
```

por:

```python
        first, first_failed = _lookup(primary, ticker, ex_date, failures)
        second, second_failed = _lookup(secondary, ticker, ex_date, failures)
        if first_failed and second_failed:
            unverifiable = _unverifiable_command(ex_date, position_confirmed_by)
            outcomes.extend(
                isolated(order_id, partial(apply_command, engine, order_id, unverifiable)) for order_id in order_ids
            )
            continue
        if first is None and second is None:
            continue
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/integration/test_dividend_unverifiable.py tests/integration/test_corporate.py tests/integration/test_opening.py tests/integration/test_rebuild.py -q && uv run ruff check src tests migrations && uv run mypy`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/virtual_orders/evaluator/corporate.py tests/integration/test_dividend_unverifiable.py
git commit -m "fix(evaluator): flag DIVIDEND_UNVERIFIED when both dividend sources are unavailable"
```

---

### Task 4: Quarentena nunca congela ordem replay e `rebuild_all_projections` isolado (entradas 7 e 9, D29)

**Files:**
- Modify: `src/virtual_orders/ledger/quarantine.py` (`quarantine`)
- Modify: `src/virtual_orders/evaluator/rebuild.py` (`rebuild_all_projections`)
- Test: `tests/integration/test_rebuild_hardening.py`

**Interfaces:**
- Consumes: `rebuild_projection(engine, order_id)`, `reproduce_orders`, `ERROR_PREFIX` (`evaluator/outcomes.py`), `OrderNotFound`.
- Produces:
  - `quarantine(engine, error) -> bool`: devolve `False` sem congelar quando a ordem é replay (o incidente continua gravado);
  - `rebuild_all_projections(engine: Engine, order_ids: Sequence[UUID] | None = None) -> dict[UUID, str]`, com valores `"OK"`, o `kind` do `LedgerIntegrityError`, `"ORDER_NOT_FOUND"` ou `"ERROR:<Type>"`. A Task 16 usa esta função no comando `rebuild-projections`.

- [ ] **Step 1: Write the failing tests**

`tests/integration/test_rebuild_hardening.py`:

```python
from uuid import uuid4

from sqlalchemy import select, text

from tests.integration.support import (
    CODE_VERSION,
    DAY,
    FakeBarSource,
    count,
    feeds,
    scenario_bars,
    submit_default,
)
from tests.support import et
from virtual_orders.evaluator import rebuild as rebuild_module
from virtual_orders.evaluator.cycle import run_live_cycle
from virtual_orders.evaluator.rebuild import rebuild_all_projections
from virtual_orders.evaluator.replay import reproduce_orders
from virtual_orders.ledger import errors
from virtual_orders.ledger.events import stored_events
from virtual_orders.ledger.orders import load_projection
from virtual_orders.storage import tables


def closed_order(engine):
    order_id = submit_default(engine).auto_order_id
    for hm in ("10:30", "11:30", "13:00"):
        run_live_cycle(engine, feeds(FakeBarSource(scenario_bars())), code_version=CODE_VERSION,
                       market_now=et(DAY, hm))
    return order_id


def event_keys(engine, order_id):
    with engine.connect() as conn:
        return [e.prepared.event_key for e in stored_events(conn, order_id)]


def test_tampered_replay_projection_records_an_incident_without_freezing_the_replay(engine):
    source_id = closed_order(engine)
    replay_id = reproduce_orders(engine, code_version=CODE_VERSION, order_ids=[source_id]).created[source_id]
    before = event_keys(engine, replay_id)
    with engine.begin() as conn:
        conn.execute(text("UPDATE order_state SET r_multiple = 9 WHERE order_id = :id"), {"id": replay_id})

    assert rebuild_all_projections(engine, [replay_id]) == {replay_id: errors.PROJECTION_INTEGRITY_ERROR}

    with engine.connect() as conn:
        incident = conn.execute(select(tables.integrity_incidents)).one()
        assert not load_projection(conn, replay_id).state.frozen
    assert incident.order_id == replay_id and incident.kind == errors.PROJECTION_INTEGRITY_ERROR
    assert event_keys(engine, replay_id) == before  # no FROZEN / NEEDS_REVIEW appended to a replay (D15)


def test_tampered_live_projection_is_still_frozen(engine):
    order_id = submit_default(engine).auto_order_id
    run_live_cycle(engine, feeds(FakeBarSource(scenario_bars())), code_version=CODE_VERSION,
                   market_now=et(DAY, "10:30"))
    with engine.begin() as conn:
        conn.execute(text("UPDATE order_state SET qty_open = 9 WHERE order_id = :id"), {"id": order_id})
    assert rebuild_all_projections(engine, [order_id]) == {order_id: errors.PROJECTION_INTEGRITY_ERROR}
    assert "FROZEN:INTEGRITY" in event_keys(engine, order_id)


def test_rebuild_all_isolates_unexpected_errors_and_unknown_orders(engine, monkeypatch):
    broken = submit_default(engine).auto_order_id
    healthy = submit_default(engine, client_signal_id="other").auto_order_id
    original = rebuild_module.regenerate_history

    def exploding(conn, order):
        if order.id == broken:
            raise RuntimeError("boom")
        return original(conn, order)

    monkeypatch.setattr(rebuild_module, "regenerate_history", exploding)
    missing = uuid4()

    report = rebuild_all_projections(engine, [broken, healthy, missing])

    assert report == {broken: "ERROR:RuntimeError", healthy: "OK", missing: "ORDER_NOT_FOUND"}
    assert count(engine, "integrity_incidents") == 0


def test_rebuild_all_without_ids_still_scans_every_order(engine):
    first = submit_default(engine).auto_order_id
    second = submit_default(engine, client_signal_id="other").auto_order_id
    assert rebuild_all_projections(engine) == {first: "OK", second: "OK"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/integration/test_rebuild_hardening.py -q`
Expected: FAIL: `TypeError` (argumento `order_ids` desconhecido) nos três primeiros; depois do Step 3 parcial, `FROZEN:INTEGRITY` aparecendo na replay.

- [ ] **Step 3: Implement**

Em `src/virtual_orders/ledger/quarantine.py`, dentro de `quarantine`, troque:

```python
            order = lock_order(conn, error.order_id)
            projection = load_projection(conn, order.id)
```

por:

```python
            order = lock_order(conn, error.order_id)
            if order.replay:
                # D15/D29 (M5): replay orders are derived history and accept no writes; the committed
                # incident row alone records the failure.
                return False
            projection = load_projection(conn, order.id)
```

Atualize a docstring de `quarantine` acrescentando ao final: `Replay orders are never frozen (D29): the incident is recorded and False is returned.`

Em `src/virtual_orders/evaluator/rebuild.py`:
- troque o import `from typing import Any` por `from collections.abc import Sequence` e `from typing import Any`;
- acrescente `from virtual_orders.evaluator.outcomes import ERROR_PREFIX`;
- troque `from virtual_orders.ledger.errors import HistoryDivergence, LedgerIntegrityError, ProjectionIntegrityError` por `from virtual_orders.ledger.errors import HistoryDivergence, LedgerIntegrityError, OrderNotFound, ProjectionIntegrityError`;
- substitua `rebuild_all_projections` por:

```python
ORDER_NOT_FOUND = "ORDER_NOT_FOUND"


def rebuild_all_projections(engine: Engine, order_ids: Sequence[UUID] | None = None) -> dict[UUID, str]:
    """rebuild-projections (spec 6): one isolated result per order; one bad order never stops the scan."""
    if order_ids is None:
        with engine.connect() as conn:
            order_ids = list(conn.execute(select(orders.c.id).order_by(orders.c.created_at, orders.c.id)).scalars())
    report: dict[UUID, str] = {}
    for order_id in order_ids:
        try:
            rebuild_projection(engine, order_id)
            report[order_id] = "OK"
        except LedgerIntegrityError as error:
            report[order_id] = error.kind
        except OrderNotFound:
            report[order_id] = ORDER_NOT_FOUND
        except Exception as exc:  # noqa: BLE001 - reported per order, like the evaluator batches
            report[order_id] = f"{ERROR_PREFIX}{type(exc).__name__}"
    return report
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/integration/test_rebuild_hardening.py tests/integration/test_rebuild.py tests/integration/test_reproduce.py tests/integration/test_ledger.py -q && uv run ruff check src tests migrations && uv run mypy`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/virtual_orders/ledger/quarantine.py src/virtual_orders/evaluator/rebuild.py tests/integration/test_rebuild_hardening.py
git commit -m "fix(ledger): never freeze replay orders in quarantine and isolate rebuild-projections per order"
```

---

### Task 5: Overrides do RECALCULATE validados contra cada ordem de origem (entrada 10, M6, D29)

**Files:**
- Modify: `src/virtual_orders/evaluator/replay.py` (nova `validate_overrides_for_sources`; `recalculate_orders`)
- Test: `tests/integration/test_recalculate_overrides.py`

**Interfaces:**
- Consumes: `config_from_snapshot`, `to_document`, `orders` (tabela), `select_source_orders`, `ReplaySelectionError`.
- Produces: `validate_overrides_for_sources(conn: Connection, source_ids: Sequence[UUID], config_overrides: Mapping[str, Any] | None) -> None`, que levanta `ReplaySelectionError("config_overrides invalid for source orders: <ids>")` antes de qualquer run. A API do 3A já mapeia para `422 REPLAY_REQUEST_INVALID`.

- [ ] **Step 1: Write the failing test**

`FillConfig` v1 só tem validações por campo, então nenhuma combinação real falha hoje; o teste injeta uma regra cruzada no ponto de construção para provar que a validação usa a configuração de **cada** origem, e não `FillConfig()`.

`tests/integration/test_recalculate_overrides.py`:

```python
from decimal import Decimal

import pytest

from core.domain.models import FillConfig
from tests.integration.support import CODE_VERSION, PRICE_SOURCE, SIGNAL_CREATED_AT, count, signal_body, submit_default
from virtual_orders.evaluator import replay
from virtual_orders.evaluator.replay import ReplaySelectionError, recalculate_orders
from virtual_orders.evaluator.signals import submit_signal


def strict_order(engine):
    return submit_signal(
        engine, signal_body(client_signal_id="strict"), config=FillConfig(stop_slippage_bps=Decimal("7")),
        code_version=CODE_VERSION, price_source=PRICE_SOURCE, now=SIGNAL_CREATED_AT,
    ).auto_order_id


def install_cross_field_rule(monkeypatch):
    original = replay.config_from_snapshot

    def cross_field_rule(document):
        config = original(document)
        if config.stop_slippage_bps == Decimal("7") and config.risk_amount == Decimal("250"):
            raise ValueError("risk_amount 250 is not allowed with stop_slippage_bps 7 (test rule)")
        return config

    monkeypatch.setattr(replay, "config_from_snapshot", cross_field_rule)


def test_overrides_invalid_for_one_source_configuration_fail_before_the_run(engine, monkeypatch):
    lenient = submit_default(engine).auto_order_id
    strict = strict_order(engine)
    install_cross_field_rule(monkeypatch)
    runs_before = count(engine, "evaluation_runs")

    with pytest.raises(ReplaySelectionError, match=str(strict)) as caught:
        recalculate_orders(engine, code_version=CODE_VERSION, order_ids=[lenient, strict],
                           config_overrides={"risk_amount": "250"})

    assert str(lenient) not in str(caught.value)
    assert count(engine, "evaluation_runs") == runs_before
    assert count(engine, "orders") == 2


def test_overrides_valid_for_every_source_still_open_the_run(engine, monkeypatch):
    lenient = submit_default(engine).auto_order_id
    strict = strict_order(engine)
    install_cross_field_rule(monkeypatch)

    report = recalculate_orders(engine, code_version=CODE_VERSION, order_ids=[lenient, strict],
                                config_overrides={"risk_amount": "200"})

    assert report.failures == {} and set(report.created) == {lenient, strict}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/integration/test_recalculate_overrides.py -q`
Expected: FAIL em `test_overrides_invalid_for_one_source_configuration_fail_before_the_run` (`DID NOT RAISE`: hoje a falha aparece só como `failures[strict] = "ERROR:ValueError"` depois do run aberto).

- [ ] **Step 3: Implement**

Em `src/virtual_orders/evaluator/replay.py`, acrescente logo depois de `validate_recalculation_request`:

```python
def validate_overrides_for_sources(
    conn: Connection, source_ids: Sequence[UUID], config_overrides: Mapping[str, Any] | None
) -> None:
    """M6 (D29): overrides must build a valid FillConfig on top of each source order's own configuration."""
    if not config_overrides or not source_ids:
        return
    overrides = to_document(dict(config_overrides))
    rejected: list[str] = []
    rows = conn.execute(
        select(orders.c.id, orders.c.config_snapshot).where(orders.c.id.in_(list(source_ids))).order_by(orders.c.id)
    )
    for row in rows:
        try:
            config_from_snapshot({**row.config_snapshot, **overrides})
        except (TypeError, ValueError, ArithmeticError):
            rejected.append(str(row.id))
    if rejected:
        raise ReplaySelectionError(f"config_overrides invalid for source orders: {', '.join(rejected)}")
```

Em `recalculate_orders`, troque:

```python
    with engine.connect() as conn:
        source_ids = select_source_orders(conn, order_ids=order_ids, created_from=created_from, created_to=created_to)
```

por:

```python
    with engine.connect() as conn:
        source_ids = select_source_orders(conn, order_ids=order_ids, created_from=created_from, created_to=created_to)
        validate_overrides_for_sources(conn, source_ids, config_overrides)
```

(Ids inexistentes simplesmente não retornam linha e continuam aparecendo como `ORDER_NOT_FOUND` no relatório, como hoje.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/integration/test_recalculate_overrides.py tests/integration/test_recalculate.py tests/integration/api/test_commands_api.py -q && uv run ruff check src tests migrations && uv run mypy`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/virtual_orders/evaluator/replay.py tests/integration/test_recalculate_overrides.py
git commit -m "fix(replay): validate RECALCULATE overrides against each source order configuration"
```

---

### Task 6: 404 da Alpaca sem corpo de erro vira indisponibilidade (entrada 11, M7, D29)

**Files:**
- Modify: `src/virtual_orders/marketdata/http.py` (`ResourceNotFound`, ramo 404 de `get_json`)
- Modify: `src/virtual_orders/marketdata/alpaca.py` (`AlpacaAssets.check_ticker`, import)
- Test: `tests/marketdata/test_alpaca_assets_404.py`

**Interfaces:**
- Consumes: `get_json`, `AlpacaAssets`.
- Produces:
  - `ResourceNotFound(message: str, body: Any = None)` com atributo `body` (JSON do 404 ou `None`);
  - `AlpacaAssets.check_ticker`: `TickerStatus(ticker, False, "UNKNOWN_ASSET")` só com corpo `{"message": <str>, …}`; qualquer outro 404 levanta `SourceUnavailable("Alpaca asset endpoint answered 404 without an Alpaca error body for <ticker>")`.

- [ ] **Step 1: Write the failing tests**

`tests/marketdata/test_alpaca_assets_404.py`:

```python
import httpx
import pytest

from virtual_orders.marketdata.alpaca import AlpacaAssets
from virtual_orders.marketdata.http import ResourceNotFound, get_json
from virtual_orders.marketdata.sources import SourceUnavailable, TickerStatus


def client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_404_keeps_the_parsed_json_body():
    with pytest.raises(ResourceNotFound) as caught:
        get_json(client(lambda r: httpx.Response(404, text='{"code": 40410000, "message": "not found"}')),
                 "https://x.test/a", params={}, sleep=lambda s: None)
    assert caught.value.body == {"code": 40410000, "message": "not found"}


@pytest.mark.parametrize("content", [b"", b"<html>Not Found</html>"])
def test_404_without_json_has_no_body(content):
    with pytest.raises(ResourceNotFound) as caught:
        get_json(client(lambda r: httpx.Response(404, content=content)), "https://x.test/a", params={},
                 sleep=lambda s: None)
    assert caught.value.body is None


def test_404_with_an_alpaca_error_body_is_an_unknown_asset():
    check = AlpacaAssets(
        client(lambda r: httpx.Response(404, text='{"code": 40410000, "message": "asset not found for NOPE"}')),
        "k", "s",
    )
    assert check.check_ticker("NOPE") == TickerStatus("NOPE", False, "UNKNOWN_ASSET")


@pytest.mark.parametrize("content", [b"", b"<html>Not Found</html>", b'["unexpected"]', b'{"error": 1}'])
def test_404_without_an_alpaca_error_body_is_unavailable_not_unknown(content):
    calls = []
    check = AlpacaAssets(client(lambda r: calls.append(r) or httpx.Response(404, content=content)), "k", "s",
                         base_url="https://wrong-trading-url.example")
    with pytest.raises(SourceUnavailable, match="without an Alpaca error body") as caught:
        check.check_ticker("AAPL")
    assert not isinstance(caught.value, ResourceNotFound)
    assert len(calls) == 1  # a 404 is never retried
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/marketdata/test_alpaca_assets_404.py -q`
Expected: FAIL (`AttributeError: 'ResourceNotFound' object has no attribute 'body'`; o 404 sem corpo ainda vira `UNKNOWN_ASSET`).

- [ ] **Step 3: Implement**

Em `src/virtual_orders/marketdata/http.py`, substitua a classe `ResourceNotFound` por:

```python
class ResourceNotFound(SourceUnavailable):
    """HTTP 404: the provider answered that the requested resource does not exist.

    `body` is the parsed JSON of the 404 response (None when absent or not JSON), so an adapter can tell a
    provider's own "not found" answer from a wrong base URL (D29, M7).
    """

    def __init__(self, message: str, body: Any = None) -> None:
        super().__init__(message)
        self.body = body
```

No mesmo arquivo, troque:

```python
            if response.status_code == 404:
                raise ResourceNotFound(f"{url} returned HTTP 404")
```

por:

```python
            if response.status_code == 404:
                try:
                    body = json.loads(response.text) if response.text.strip() else None
                except ValueError:
                    body = None
                raise ResourceNotFound(f"{url} returned HTTP 404", body)
```

Em `src/virtual_orders/marketdata/alpaca.py`, troque o import de `sources` por:

```python
from virtual_orders.marketdata.sources import (
    DataTier,
    RawBar,
    SourceDataError,
    SourceUnavailable,
    SplitRecord,
    TickerStatus,
)
```

e, em `AlpacaAssets.check_ticker`, troque:

```python
        except ResourceNotFound:
            return TickerStatus(ticker, False, "UNKNOWN_ASSET")
```

por:

```python
        except ResourceNotFound as missing:
            if isinstance(missing.body, dict) and isinstance(missing.body.get("message"), str):
                return TickerStatus(ticker, False, "UNKNOWN_ASSET")
            raise SourceUnavailable(
                f"Alpaca asset endpoint answered 404 without an Alpaca error body for {ticker}"
            ) from None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/marketdata -q && uv run ruff check src tests migrations && uv run mypy`
Expected: PASS, inclusive `test_alpaca_assets_inactive_unknown_untradable_and_invalid_symbols` (corpo `{"message": "not found"}` continua `UNKNOWN_ASSET`) e `test_http_404_is_a_distinct_not_found_error`.

- [ ] **Step 5: Commit**

```bash
git add src/virtual_orders/marketdata/http.py src/virtual_orders/marketdata/alpaca.py tests/marketdata/test_alpaca_assets_404.py
git commit -m "fix(marketdata): treat Alpaca 404 without an Alpaca error body as unavailable"
```

---

### Task 7: Contrato `AlertSink`, adapter n8n e composição segura (entradas 3 e 12, D30)

**Files:**
- Create: `src/virtual_orders/alerts/sink.py`, `src/virtual_orders/notify/n8n.py`
- Modify: `src/virtual_orders/services.py` (campo `alert_sink`), `src/virtual_orders/bootstrap.py` (`build_services`)
- Test: `tests/notify/__init__.py` (vazio), `tests/notify/test_n8n_webhook.py`, `tests/test_bootstrap_worker.py`

**Interfaces:**
- Consumes: `Settings.n8n_webhook_url`, `build_services(settings, *, http_client=None, engine=None, clock=None)`, `make_engine`.
- Produces:
  - `AlertDeliveryFailed(*, status_code: int | None, error_type: str)` com atributos `status_code` e `error_type`;
  - `AlertSink` (Protocol): `name: str`; `deliver(self, alert_key: str, document: Mapping[str, Any]) -> None` (levanta `AlertDeliveryFailed`);
  - `N8nWebhook(client: httpx.Client, url: str, *, timeout_seconds: float = WEBHOOK_TIMEOUT_SECONDS)`, `name = "n8n"`, `WEBHOOK_TIMEOUT_SECONDS = 5.0`, `IDEMPOTENCY_HEADER = "Idempotency-Key"`;
  - `Services.alert_sink: AlertSink | None` (padrão `None`).

- [ ] **Step 1: Write the failing tests**

`tests/notify/test_n8n_webhook.py`:

```python
import json

import httpx
import pytest

from virtual_orders.alerts.sink import AlertDeliveryFailed
from virtual_orders.notify.n8n import IDEMPOTENCY_HEADER, N8nWebhook

URL = "https://n8n.test/webhook/secret-token"


def webhook(handler) -> N8nWebhook:
    return N8nWebhook(httpx.Client(transport=httpx.MockTransport(handler)), URL)


def test_posts_one_json_document_with_the_idempotency_key():
    seen: list[httpx.Request] = []
    sink = webhook(lambda request: seen.append(request) or httpx.Response(200))

    sink.deliver("ORDER_EVENT:o1:FILLED", {"kind": "ORDER_EVENT", "price": "101.5", "b": 1})

    (request,) = seen
    assert request.method == "POST" and str(request.url) == URL
    assert request.headers[IDEMPOTENCY_HEADER] == "ORDER_EVENT:o1:FILLED"
    assert request.headers["Content-Type"] == "application/json"
    assert json.loads(request.content) == {"kind": "ORDER_EVENT", "price": "101.5", "b": 1}
    assert sink.name == "n8n"


def test_non_2xx_raises_with_the_status_and_no_retry():
    calls: list[httpx.Request] = []
    sink = webhook(lambda request: calls.append(request) or httpx.Response(502, text="bad gateway secret-token"))
    with pytest.raises(AlertDeliveryFailed) as caught:
        sink.deliver("k", {})
    assert caught.value.status_code == 502 and caught.value.error_type == "HTTPStatus"
    assert len(calls) == 1
    assert "secret-token" not in str(caught.value)


def test_timeout_raises_the_exception_type_only():
    def handler(request):
        raise httpx.ReadTimeout("timed out talking to secret-token", request=request)

    with pytest.raises(AlertDeliveryFailed) as caught:
        webhook(handler).deliver("k", {})
    assert caught.value.status_code is None and caught.value.error_type == "ReadTimeout"
    assert "secret-token" not in str(caught.value) and caught.value.__cause__ is None


def test_repr_hides_the_url():
    assert "secret-token" not in repr(webhook(lambda request: httpx.Response(200)))
```

`tests/test_bootstrap_worker.py`:

```python
from dataclasses import replace

import httpx
import pytest
from sqlalchemy.exc import ArgumentError

from tests.config_support import BASE_ENV
from virtual_orders import bootstrap
from virtual_orders.bootstrap import build_services
from virtual_orders.config import load_settings
from virtual_orders.notify.n8n import N8nWebhook


def settings(**overrides):
    return replace(load_settings(BASE_ENV), **overrides)


def offline_client(calls):
    return httpx.Client(transport=httpx.MockTransport(lambda request: calls.append(request) or httpx.Response(500)))


def test_owned_client_is_closed_when_the_engine_cannot_be_built(monkeypatch):
    created: list[httpx.Client] = []
    real_client = httpx.Client

    def recording_client(*args, **kwargs):
        instance = real_client(transport=httpx.MockTransport(lambda request: httpx.Response(500)))
        created.append(instance)
        return instance

    def broken_engine(url):
        raise ArgumentError("could not parse the URL (test)")

    monkeypatch.setattr(bootstrap.httpx, "Client", recording_client)
    monkeypatch.setattr(bootstrap, "make_engine", broken_engine)

    with pytest.raises(ArgumentError):
        build_services(settings())

    assert len(created) == 1 and created[0].is_closed


def test_injected_client_is_left_open_when_the_engine_cannot_be_built(monkeypatch):
    client = offline_client([])

    def broken_engine(url):
        raise ArgumentError("could not parse the URL (test)")

    monkeypatch.setattr(bootstrap, "make_engine", broken_engine)
    with pytest.raises(ArgumentError):
        build_services(settings(), http_client=client)
    assert not client.is_closed
    client.close()


def test_webhook_is_disabled_without_a_url_and_composed_with_one():
    calls: list[httpx.Request] = []
    disabled = build_services(settings(), http_client=offline_client(calls))
    try:
        assert disabled.alert_sink is None
    finally:
        disabled.close()

    enabled = build_services(settings(n8n_webhook_url="https://n8n.test/hook/secret-token"),
                             http_client=offline_client(calls))
    try:
        assert isinstance(enabled.alert_sink, N8nWebhook)
        assert "secret-token" not in repr(enabled)
        assert "secret-token" not in repr(enabled.alert_sink)
    finally:
        enabled.close()
    assert calls == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/notify/test_n8n_webhook.py tests/test_bootstrap_worker.py -q`
Expected: FAIL com `ModuleNotFoundError: virtual_orders.alerts.sink` / `virtual_orders.notify.n8n`.

- [ ] **Step 3: Write `alerts/sink.py`**

```python
"""Provider-neutral alert delivery contract (D24, D30). n8n is never in the critical path (spec 1.2 item 7)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol


class AlertDeliveryFailed(Exception):
    """One delivery attempt failed. Carries only a status code and an exception type, never provider text."""

    def __init__(self, *, status_code: int | None, error_type: str) -> None:
        super().__init__(error_type if status_code is None else f"HTTP {status_code}")
        self.status_code = status_code
        self.error_type = error_type


class AlertSink(Protocol):
    name: str

    def deliver(self, alert_key: str, document: Mapping[str, Any]) -> None:
        """Sends one JSON-native document once; raises AlertDeliveryFailed. Never retries."""
        ...
```

- [ ] **Step 4: Write `notify/n8n.py`**

```python
"""n8n webhook adapter (spec 8 N8N_WEBHOOK_URL, D30). One bounded attempt per call; the URL never leaks."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import httpx

from virtual_orders.alerts.sink import AlertDeliveryFailed

WEBHOOK_TIMEOUT_SECONDS = 5.0
IDEMPOTENCY_HEADER = "Idempotency-Key"


class N8nWebhook:
    name = "n8n"

    def __init__(self, client: httpx.Client, url: str, *, timeout_seconds: float = WEBHOOK_TIMEOUT_SECONDS) -> None:
        self._client = client
        self._url = url
        self._timeout = httpx.Timeout(timeout_seconds)

    def __repr__(self) -> str:  # the webhook URL usually embeds a secret token
        return "N8nWebhook(url=<hidden>)"

    def deliver(self, alert_key: str, document: Mapping[str, Any]) -> None:
        body = json.dumps(dict(document), ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False)
        try:
            response = self._client.post(
                self._url, content=body.encode("utf-8"),
                headers={"Content-Type": "application/json", IDEMPOTENCY_HEADER: alert_key},
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise AlertDeliveryFailed(status_code=None, error_type=type(exc).__name__) from None
        if not 200 <= response.status_code < 300:
            raise AlertDeliveryFailed(status_code=response.status_code, error_type="HTTPStatus")
```

- [ ] **Step 5: Add the field to `Services` and compose in `bootstrap.py`**

Em `src/virtual_orders/services.py`, acrescente o import `from virtual_orders.alerts.sink import AlertSink` e, como **último** campo de `Services`:

```python
    alert_sink: AlertSink | None = field(default=None, repr=False)
```

Em `src/virtual_orders/bootstrap.py`, acrescente `from virtual_orders.notify.n8n import N8nWebhook` e substitua o corpo de `build_services` por:

```python
    owns_client = http_client is None
    client = http_client if http_client is not None else httpx.Client(timeout=HTTP_TIMEOUT_SECONDS)

    def release_client() -> None:
        if owns_client:
            client.close()

    gateway = MarketDataGateway([AlpacaBars(client, settings.alpaca_api_key, settings.alpaca_secret_key)])
    if settings.price_source not in gateway.source_ids:
        release_client()
        raise ConfigError([
            f"UNKNOWN_PRICE_SOURCE:PRICE_SOURCE (registered: {', '.join(gateway.source_ids)})",
        ])
    owns_engine = engine is None
    try:
        database = engine if engine is not None else make_engine(settings.database_url)
    except Exception:
        release_client()  # Plan 3A close-out entry 12: never leak the owned client on a failed startup
        raise

    def close() -> None:
        release_client()
        if owns_engine:
            database.dispose()

    yfinance = YFinanceSource()
    alert_sink = None if settings.n8n_webhook_url is None else N8nWebhook(client, settings.n8n_webhook_url)
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
        alert_sink=alert_sink,
    )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/notify tests/test_bootstrap_worker.py tests/test_bootstrap.py tests/test_import_boundaries.py tests/integration/api -q && uv run ruff check src tests migrations && uv run mypy`
Expected: PASS (`test_only_the_composition_root_imports_provider_adapters` agora cobre `notify/n8n.py`).

- [ ] **Step 7: Commit**

```bash
git add src/virtual_orders/alerts/sink.py src/virtual_orders/notify/n8n.py src/virtual_orders/services.py src/virtual_orders/bootstrap.py tests/notify/__init__.py tests/notify/test_n8n_webhook.py tests/test_bootstrap_worker.py
git commit -m "feat(alerts): n8n webhook sink composed at the root and close the owned client on startup failure"
```

---

### Task 8: Migration 0003 — recheck, outbox, watchlist, regras e log de saúde

**Files:**
- Create: `migrations/versions/0003_worker_alerts_watchlist.py`
- Modify: `src/virtual_orders/storage/tables.py`, `src/virtual_orders/ledger/runs.py` (`RunKind`), `src/virtual_orders/readmodels/health.py` (`EXPECTED_SCHEMA_REVISION`)
- Modify: `tests/integration/api/test_health_api.py` (`test_schema_behind_the_migration_head_is_503_without_facts`)
- Test: `tests/integration/test_schema_0003.py`

**Interfaces:**
- Produces:
  - `RunKind.QUALITY_RECHECK = "QUALITY_RECHECK"`, `RunKind.WATCHLIST = "WATCHLIST"`;
  - tabelas SQLAlchemy `data_quality_rechecks`, `watchlist`, `alert_rules`, `alert_outbox`, `alert_delivery_attempts` (outcomes `DELIVERED`/`FAILED`/`EXPIRED`), `alert_event_marks`, `health_state_log` em `storage/tables.py`;
  - `alert_outbox.kind` aceita `ORDER_EVENT`, `ORDER_REVIEW`, `INTEGRITY_INCIDENT`, `PRICE_CROSS`, `PRESSURE`, `HEALTH`, `END_OF_DAY_SUMMARY`;
  - `APPEND_ONLY_TABLES` acrescido de `data_quality_rechecks`, `alert_outbox`, `alert_delivery_attempts`, `alert_event_marks`, `health_state_log`;
  - `EXPECTED_SCHEMA_REVISION = "0003"`.

- [ ] **Step 1: Write the failing tests**

`tests/integration/test_schema_0003.py`:

```python
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from tests.integration.conftest import alembic_config
from tests.integration.support import CODE_VERSION, count, submit_default
from virtual_orders.ledger.runs import RunKind, start_run
from virtual_orders.marketdata.asof import acquire_data_as_of
from virtual_orders.storage import tables

NOW = datetime(2025, 11, 25, 15, 0, tzinfo=UTC)


def rule(**overrides):
    values = dict(id=uuid4(), ticker="AAPL", kind="PRICE_CROSS", level=Decimal("101"), direction="ABOVE",
                  cmf_threshold=None, window_bars=None, cooldown_minutes=30, created_at=NOW)
    values.update(overrides)
    return values


def test_new_run_kinds_are_accepted(engine):
    as_of = acquire_data_as_of(engine)
    with engine.begin() as conn:
        start_run(conn, RunKind.QUALITY_RECHECK, as_of, CODE_VERSION)
        start_run(conn, RunKind.WATCHLIST, as_of, CODE_VERSION)
    assert count(engine, "evaluation_runs") == 2


@pytest.mark.parametrize("bad", [
    {"level": None},
    {"direction": None},
    {"level": Decimal("0")},
    {"cmf_threshold": Decimal("0.5")},
    {"kind": "PRESSURE"},
    {"kind": "PRESSURE", "level": None, "direction": None, "cmf_threshold": Decimal("1"), "window_bars": 20},
    {"kind": "PRESSURE", "level": None, "direction": None, "cmf_threshold": Decimal("0.3"), "window_bars": 4},
    {"cooldown_minutes": 1441},
    {"kind": "VOLUME"},
])
def test_alert_rule_checks_reject_inconsistent_definitions(engine, bad):
    with engine.begin() as conn:
        conn.execute(tables.watchlist.insert().values(ticker="AAPL", added_at=NOW))
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(tables.alert_rules.insert().values(**rule(**bad)))


def test_removing_a_watchlist_ticker_removes_its_rules(engine):
    with engine.begin() as conn:
        conn.execute(tables.watchlist.insert().values(ticker="AAPL", added_at=NOW))
        conn.execute(tables.alert_rules.insert().values(**rule()))
        conn.execute(tables.alert_rules.insert().values(**rule(
            kind="PRESSURE", level=None, direction=None, cmf_threshold=Decimal("0.3"), window_bars=20)))
        conn.execute(tables.watchlist.delete().where(tables.watchlist.c.ticker == "AAPL"))
    assert count(engine, "alert_rules") == 0


def test_outbox_keys_are_unique(engine):
    values = dict(alert_key="ORDER_EVENT:x:FILLED", kind="ORDER_EVENT", subject="x", subject_ts=None, document={})
    with engine.begin() as conn:
        conn.execute(tables.alert_outbox.insert().values(**values))
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(tables.alert_outbox.insert().values(**values))


def test_recheck_keys_are_unique_per_order(engine):
    order_id = submit_default(engine).auto_order_id
    as_of = acquire_data_as_of(engine)
    with engine.begin() as conn:
        run = start_run(conn, RunKind.QUALITY_RECHECK, as_of, CODE_VERSION)
    values = dict(order_id=order_id, session_date=date(2025, 11, 25), recheck_key="DATA_QUALITY_RECHECK:k",
                  run_id=run.run_id, source_run_id=run.run_id, data_as_of=as_of, payload={})
    with engine.begin() as conn:
        conn.execute(tables.data_quality_rechecks.insert().values(**values))
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(tables.data_quality_rechecks.insert().values(**values))


def test_delivery_outcomes_include_expiry(engine):
    with engine.begin() as conn:
        alert_id = conn.execute(tables.alert_outbox.insert().values(
            alert_key="HEALTH:1", kind="HEALTH", subject=None, subject_ts=None, document={},
        ).returning(tables.alert_outbox.c.id)).scalar_one()
        conn.execute(tables.alert_delivery_attempts.insert().values(alert_id=alert_id, outcome="EXPIRED"))
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(tables.alert_delivery_attempts.insert().values(alert_id=alert_id, outcome="GIVEN_UP"))


def test_new_history_tables_are_append_only():
    assert {
        "data_quality_rechecks", "alert_outbox", "alert_delivery_attempts", "alert_event_marks", "health_state_log",
    } <= set(tables.APPEND_ONLY_TABLES)
    assert "watchlist" not in tables.APPEND_ONLY_TABLES and "alert_rules" not in tables.APPEND_ONLY_TABLES


def test_downgrade_to_0002_and_back(database_url, engine):
    config = alembic_config(database_url)
    command.downgrade(config, "0002")
    command.upgrade(config, "head")
    with engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(tables.health_state_log)).scalar_one() == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/integration/test_schema_0003.py -q`
Expected: FAIL (`AttributeError: … has no attribute 'QUALITY_RECHECK'` / tabelas inexistentes).

- [ ] **Step 3: Write the migration**

`migrations/versions/0003_worker_alerts_watchlist.py`:

```python
"""Worker, alerts and watchlist (Plan 3B: D22, D24, D25, D26).

Revision ID: 0003
Revises: 0002
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

_KINDS_BEFORE = "'LIVE', 'REPLAY', 'ACTIONABILITY', 'END_OF_DAY', 'OPENING'"
APPEND_ONLY = ("data_quality_rechecks", "alert_outbox", "alert_delivery_attempts", "alert_event_marks", "health_state_log")
MUTABLE = ("watchlist", "alert_rules")

SCHEMA = """
CREATE TABLE data_quality_rechecks (
  id bigserial PRIMARY KEY,
  order_id uuid NOT NULL REFERENCES orders(id),
  session_date date NOT NULL,
  recheck_key text NOT NULL,
  run_id uuid NOT NULL REFERENCES evaluation_runs(run_id),
  source_run_id uuid NOT NULL REFERENCES evaluation_runs(run_id),
  data_as_of timestamptz NOT NULL,
  payload jsonb NOT NULL,
  recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (order_id, recheck_key)
);
CREATE INDEX data_quality_rechecks_session_idx ON data_quality_rechecks (order_id, session_date);

CREATE TABLE watchlist (
  ticker text PRIMARY KEY,
  added_at timestamptz NOT NULL
);

CREATE TABLE alert_rules (
  id uuid PRIMARY KEY,
  ticker text NOT NULL REFERENCES watchlist(ticker) ON DELETE CASCADE,
  kind text NOT NULL CHECK (kind IN ('PRICE_CROSS', 'PRESSURE')),
  level numeric NULL,
  direction text NULL CHECK (direction IN ('ABOVE', 'BELOW')),
  cmf_threshold numeric NULL,
  window_bars int NULL,
  cooldown_minutes int NOT NULL CHECK (cooldown_minutes BETWEEN 0 AND 1440),
  created_at timestamptz NOT NULL,
  CHECK (
    (kind = 'PRICE_CROSS' AND level IS NOT NULL AND level > 0 AND direction IS NOT NULL
      AND cmf_threshold IS NULL AND window_bars IS NULL)
    OR (kind = 'PRESSURE' AND level IS NULL AND direction IS NULL AND cmf_threshold IS NOT NULL
      AND cmf_threshold > 0 AND cmf_threshold < 1 AND window_bars IS NOT NULL AND window_bars BETWEEN 5 AND 390)
  )
);
CREATE INDEX alert_rules_ticker_idx ON alert_rules (ticker);

CREATE TABLE alert_outbox (
  id bigserial PRIMARY KEY,
  alert_key text NOT NULL UNIQUE,
  kind text NOT NULL CHECK (kind IN ('ORDER_EVENT', 'ORDER_REVIEW', 'INTEGRITY_INCIDENT', 'PRICE_CROSS', 'PRESSURE',
                                     'HEALTH', 'END_OF_DAY_SUMMARY')),
  subject text NULL,
  subject_ts timestamptz NULL,
  document jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX alert_outbox_subject_idx ON alert_outbox (kind, subject, subject_ts DESC);
CREATE INDEX alert_outbox_created_idx ON alert_outbox (created_at, id);

CREATE TABLE alert_delivery_attempts (
  id bigserial PRIMARY KEY,
  alert_id bigint NOT NULL REFERENCES alert_outbox(id),
  outcome text NOT NULL CHECK (outcome IN ('DELIVERED', 'FAILED', 'EXPIRED')),
  status_code int NULL,
  error_type text NULL,
  attempted_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX alert_delivery_attempts_alert_idx ON alert_delivery_attempts (alert_id, outcome, attempted_at DESC);

CREATE TABLE alert_event_marks (
  order_event_id bigint PRIMARY KEY REFERENCES order_events(id),
  alert_key text NOT NULL,
  recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX alert_event_marks_recorded_idx ON alert_event_marks (recorded_at);

CREATE TABLE health_state_log (
  id bigserial PRIMARY KEY,
  state text NOT NULL CHECK (state IN ('HEALTHY', 'DEGRADED', 'UNHEALTHY')),
  cause_codes text[] NOT NULL,
  observed_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
"""


def upgrade() -> None:
    op.execute("ALTER TABLE evaluation_runs DROP CONSTRAINT evaluation_runs_kind_check")
    op.execute(
        "ALTER TABLE evaluation_runs ADD CONSTRAINT evaluation_runs_kind_check "
        f"CHECK (kind IN ({_KINDS_BEFORE}, 'QUALITY_RECHECK', 'WATCHLIST'))"
    )
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
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {', '.join(MUTABLE)} TO vo_app")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO vo_app")


def downgrade() -> None:
    # Fails loudly if QUALITY_RECHECK/WATCHLIST runs exist: append-only history is never rewritten for a downgrade.
    for table in ("alert_event_marks", "alert_delivery_attempts", "alert_outbox", "alert_rules", "watchlist",
                  "data_quality_rechecks", "health_state_log"):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute("ALTER TABLE evaluation_runs DROP CONSTRAINT evaluation_runs_kind_check")
    op.execute(
        f"ALTER TABLE evaluation_runs ADD CONSTRAINT evaluation_runs_kind_check CHECK (kind IN ({_KINDS_BEFORE}))"
    )
```

- [ ] **Step 4: Mirror the tables, run kinds and schema head**

Em `src/virtual_orders/ledger/runs.py`, acrescente ao final de `RunKind`:

```python
    QUALITY_RECHECK = "QUALITY_RECHECK"
    WATCHLIST = "WATCHLIST"
```

Em `src/virtual_orders/storage/tables.py`, troque a docstring do módulo por `"""SQLAlchemy Core mirror of migrations/versions/*.py (checked by test_schema)."""`, acrescente `from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID` (já existe) e, antes de `APPEND_ONLY_TABLES`:

```python
data_quality_rechecks = Table(
    "data_quality_rechecks", metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("order_id", UUID(as_uuid=True), nullable=False),
    Column("session_date", Date, nullable=False),
    Column("recheck_key", Text, nullable=False),
    Column("run_id", UUID(as_uuid=True), nullable=False),
    Column("source_run_id", UUID(as_uuid=True), nullable=False),
    _ts("data_as_of"),
    Column("payload", JSONB, nullable=False),
    _ts("recorded_at"),
)

watchlist = Table(
    "watchlist", metadata,
    Column("ticker", Text, primary_key=True),
    _ts("added_at"),
)

alert_rules = Table(
    "alert_rules", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("ticker", Text, nullable=False),
    Column("kind", Text, nullable=False),
    _num("level", True),
    Column("direction", Text),
    _num("cmf_threshold", True),
    Column("window_bars", Integer),
    Column("cooldown_minutes", Integer, nullable=False),
    _ts("created_at"),
)

alert_outbox = Table(
    "alert_outbox", metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("alert_key", Text, nullable=False, unique=True),
    Column("kind", Text, nullable=False),
    Column("subject", Text),
    _ts("subject_ts", True),
    Column("document", JSONB, nullable=False),
    _ts("created_at"),
)

alert_delivery_attempts = Table(
    "alert_delivery_attempts", metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("alert_id", BigInteger, nullable=False),
    Column("outcome", Text, nullable=False),
    Column("status_code", Integer),
    Column("error_type", Text),
    _ts("attempted_at"),
)

health_state_log = Table(
    "health_state_log", metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("state", Text, nullable=False),
    Column("cause_codes", ARRAY(Text), nullable=False),
    _ts("observed_at"),
)

alert_event_marks = Table(
    "alert_event_marks", metadata,
    Column("order_event_id", BigInteger, primary_key=True),
    Column("alert_key", Text, nullable=False),
    _ts("recorded_at"),
)
```

Colunas com `DEFAULT clock_timestamp()` no banco (`recorded_at`, `created_at` do outbox, `observed_at`) seguem o padrão de `order_events.recorded_at`: os `insert()` deste plano não as passam, o SQLAlchemy Core omite colunas sem valor e o default do banco preenche. A exceção é `alert_delivery_attempts.attempted_at`: a entrega grava explicitamente o instante do banco lido no início da execução (D24), para que o backoff use um único relógio.

Substitua `APPEND_ONLY_TABLES` por:

```python
APPEND_ONLY_TABLES = (
    "signals", "orders", "order_events", "evaluation_runs", "evaluation_run_status",
    "order_eval_segments", "bar_batches", "bars_1m", "market_data_snapshots", "integrity_incidents",
    "data_quality_rechecks", "alert_outbox", "alert_delivery_attempts", "alert_event_marks", "health_state_log",
)
```

Em `src/virtual_orders/readmodels/health.py`, troque `EXPECTED_SCHEMA_REVISION = "0002"` por `EXPECTED_SCHEMA_REVISION = "0003"`.

Em `tests/integration/api/test_health_api.py`, no teste `test_schema_behind_the_migration_head_is_503_without_facts`, troque `"detail": {"expected": "0002", "found": "0001"}` por `"detail": {"expected": "0003", "found": "0001"}`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/integration/test_schema_0003.py tests/integration/test_schema.py tests/integration/test_opening.py tests/readmodels/test_health_rules.py tests/integration/api/test_health_api.py -q && uv run ruff check src tests migrations && uv run mypy`
Expected: PASS (`test_every_append_only_table_has_triggers` e `test_metadata_matches_migrated_columns` cobrem as tabelas novas; `test_expected_schema_revision_is_the_migration_head` confirma `0003`).

- [ ] **Step 6: Verify migrations from zero**

Run: `docker compose -f docker-compose.test.yml down -v && docker compose -f docker-compose.test.yml up -d --wait && uv run pytest tests/integration/test_schema.py tests/integration/test_schema_0003.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add migrations/versions/0003_worker_alerts_watchlist.py src/virtual_orders/storage/tables.py src/virtual_orders/ledger/runs.py src/virtual_orders/readmodels/health.py tests/integration/test_schema_0003.py tests/integration/api/test_health_api.py
git commit -m "feat(storage): migration 0003 for rechecks, alert outbox, watchlist rules and health log"
```

---

### Task 9: `/health` endurecido — timeout de statement, `facts` limitados, revisão sem motivo, sessões e pendências de qualidade (entradas 5, 8 e 13; D22, D28, D31)

**Files:**
- Create: `src/virtual_orders/readmodels/quality.py`
- Modify: `src/virtual_orders/readmodels/health.py`
- Test: `tests/readmodels/test_health_facts.py`, `tests/integration/api/test_health_hardening.py`

**Interfaces:**
- Consumes: `data_quality_rechecks` (Task 8), `evaluate_health`, `collect_health_snapshot`, harness `api`.
- Produces:
  - `PendingQuality(order_id: UUID, session_day: date, source_run_id: UUID, reason: str, ticker: str, price_source: str)`;
  - `RECENT_END_OF_DAY_RUNS = 20`; `pending_quality_sessions(conn: Connection, *, runs: int = RECENT_END_OF_DAY_RUNS) -> list[PendingQuality]` (usada também pela Task 10);
  - em `health.py`:
    - constantes `HEALTH_STATEMENT_TIMEOUT_MS = 5000`, `QUERY_CANCELED_SQLSTATE = "57014"`, `UNSPECIFIED_REVIEW_REASON = "UNSPECIFIED"`, `OPENING_GRACE = timedelta(minutes=30)`, `END_OF_DAY_GRACE = timedelta(hours=3)`, `MISSING_RUN_LOOKBACK = timedelta(days=14)`;
    - `is_statement_timeout(error: BaseException) -> bool`, `health_query_timeout(error: Exception, timeout_ms: int) -> HealthReport`;
    - `HealthSnapshot.quality_pending: dict[str, str] | None = None` (chave `"<order_id>:<YYYY-MM-DD>"`);
    - `HealthSnapshot.missing_runs: dict[str, list[str]] | None = None` (chaves `"OPENING"`/`"END_OF_DAY"`, D38);
    - `missing_job_runs(conn: Connection, *, now: datetime) -> dict[str, list[str]]`;
    - causas `OPENING_MISSING` e `END_OF_DAY_MISSING` (`DEGRADED`, `detail = {"session_days": [...]}`);
    - `build_health_report(engine, *, now, eval_interval_minutes, statement_timeout_ms: int = HEALTH_STATEMENT_TIMEOUT_MS)`. A Task 14 usa `build_health_report`.
  - A Task 12 acrescenta a este mesmo arquivo as causas `INFO` `UNDELIVERABLE_ALERTS` e `ORDER_EVENT_ALERTS_BEHIND`.

- [ ] **Step 1: Write the failing unit tests**

`tests/readmodels/test_health_facts.py`:

```python
from dataclasses import replace
from datetime import UTC, datetime

from sqlalchemy.exc import OperationalError

from virtual_orders.readmodels import health as health_module
from virtual_orders.readmodels.health import (
    UNSPECIFIED_REVIEW_REASON,
    Cause,
    HealthSnapshot,
    HealthState,
    RunSummary,
    Severity,
    evaluate_health,
    health_query_timeout,
    is_statement_timeout,
)

NOW = datetime(2025, 11, 25, 13, 0, tzinfo=UTC)  # 08:00 ET, no session


def snapshot(**overrides) -> HealthSnapshot:
    base = HealthSnapshot(
        now=NOW, session_open_utc=None, live_runs=(), last_end_of_day=None, last_opening=None,
        latest_ingested_at=None, incident_groups=(), incidents_total=0, frozen_orders=0,
        orders_without_projection=0, orders_without_projection_ids=(), needs_review={}, actionability={},
    )
    return replace(base, **overrides)


def live(**detail) -> RunSummary:
    return RunSummary("LIVE-1", "LIVE", "COMPLETED", NOW, NOW, {"market_now": NOW.isoformat(), **detail})


class CanceledStatement(Exception):
    sqlstate = "57014"


class ConnectionLost(Exception):
    sqlstate = "08006"


def test_per_order_maps_and_feed_lists_in_facts_are_capped_but_causes_see_everything():
    errors = {f"order-{i:03d}": "ERROR:KeyError" for i in range(250)}
    feeds = {f"src:T{i:03d}": "down" for i in range(150)}
    report = evaluate_health(snapshot(live_runs=(live(order_errors=errors, ingest_failures=feeds),)),
                             eval_interval_minutes=2)
    assert report.snapshot is not None
    detail = report.snapshot.live_runs[0].detail
    assert list(detail["order_errors"]) == sorted(errors)[:100] and detail["order_errors_total"] == 250
    assert detail["ingest_failures"] == sorted(feeds)[:100] and detail["ingest_failures_total"] == 150
    group = next(c for c in report.causes if c.code == "ORDER_ERRORS").detail["groups"][0]
    assert group.occurrences == 250 and group.affected_count == 250


def test_small_maps_are_unchanged_and_have_no_total():
    report = evaluate_health(snapshot(live_runs=(live(order_errors={"o1": "ERROR:KeyError"}),)),
                             eval_interval_minutes=2)
    assert report.snapshot is not None
    detail = report.snapshot.live_runs[0].detail
    assert detail["order_errors"] == {"o1": "ERROR:KeyError"} and "order_errors_total" not in detail


def test_collected_quality_pending_replaces_the_last_end_of_day_view():
    eod = RunSummary("EOD-1", "END_OF_DAY", "COMPLETED", NOW, NOW,
                     {"session_day": "2025-11-24", "not_evaluated": {"o1": "PROVIDER_FAILURE"}})
    consumed = evaluate_health(snapshot(last_end_of_day=eod, quality_pending={}), eval_interval_minutes=2)
    assert "QUALITY_NOT_EVALUATED" not in {c.code for c in consumed.causes}

    pending = evaluate_health(snapshot(last_end_of_day=eod, quality_pending={
        "o1:2025-11-21": "NO_OBSERVATIONS", "o2:2025-11-24": "PROVIDER_FAILURE",
    }), eval_interval_minutes=2)
    cause = next(c for c in pending.causes if c.code == "QUALITY_NOT_EVALUATED")
    assert cause.severity is Severity.DEGRADED
    assert cause.detail == {"session_days": ["2025-11-21", "2025-11-24"], "count": 2,
                            "reasons": {"NO_OBSERVATIONS": 1, "PROVIDER_FAILURE": 1}}


def test_quality_pending_in_facts_is_capped():
    pending = {f"order-{i:03d}:2025-11-24": "PROVIDER_FAILURE" for i in range(120)}
    report = evaluate_health(snapshot(quality_pending=pending), eval_interval_minutes=2)
    assert report.snapshot is not None and report.snapshot.quality_pending is not None
    assert len(report.snapshot.quality_pending) == 100
    assert next(c for c in report.causes if c.code == "QUALITY_NOT_EVALUATED").detail["count"] == 120


def test_statement_timeout_is_recognized_from_the_driver_sqlstate():
    assert is_statement_timeout(OperationalError("SELECT pg_sleep(1)", {}, CanceledStatement()))
    assert not is_statement_timeout(OperationalError("SELECT 1", {}, ConnectionLost()))
    assert not is_statement_timeout(ValueError("no orig"))
    report = health_query_timeout(OperationalError("SELECT pg_sleep(1)", {}, CanceledStatement()), 50)
    assert report.state is HealthState.DEGRADED and report.snapshot is None
    assert report.causes == (Cause("HEALTH_QUERY_TIMEOUT", Severity.DEGRADED,
                                   {"error": "OperationalError", "timeout_ms": 50}),)


def test_missing_opening_and_end_of_day_runs_degrade_with_their_session_days():
    missing = {"OPENING": ["2025-11-26"], "END_OF_DAY": ["2025-11-24", "2025-11-26"]}
    report = evaluate_health(snapshot(missing_runs=missing), eval_interval_minutes=2)
    causes = {cause.code: cause for cause in report.causes}
    assert causes["OPENING_MISSING"] == Cause("OPENING_MISSING", Severity.DEGRADED, {"session_days": ["2025-11-26"]})
    assert causes["END_OF_DAY_MISSING"] == Cause("END_OF_DAY_MISSING", Severity.DEGRADED,
                                                 {"session_days": ["2025-11-24", "2025-11-26"]})
    assert report.state is HealthState.DEGRADED
    assert report.snapshot is not None and report.snapshot.missing_runs == missing
    assert evaluate_health(snapshot(missing_runs={}), eval_interval_minutes=2).state is HealthState.HEALTHY


def test_unspecified_literal_matches_the_constant():
    assert f"'{UNSPECIFIED_REVIEW_REASON}'" in str(health_module._REVIEW_REASONS)
```

- [ ] **Step 2: Write the failing integration tests**

`tests/integration/api/test_health_hardening.py`:

```python
from datetime import date
from uuid import UUID

import pytest
from sqlalchemy import text

from tests.integration.api.conftest import post_json
from tests.integration.support import CODE_VERSION, DAY, signal_body
from tests.support import et
from virtual_orders.ledger.runs import RunKind, RunStatus, finish_run, start_run
from virtual_orders.marketdata.asof import acquire_data_as_of
from virtual_orders.readmodels import health as health_module
from virtual_orders.readmodels.health import HealthState, build_health_report
from virtual_orders.readmodels.quality import pending_quality_sessions
from virtual_orders.storage import tables

SESSION = date(2025, 11, 25)


def body(api):
    response = api.client.get("/health")
    assert response.status_code == 200
    return response.json()


def test_slow_health_query_is_degraded_not_database_unavailable(engine, monkeypatch):
    def slow(conn, *, now):
        conn.execute(text("SELECT pg_sleep(2)"))
        raise AssertionError("statement_timeout did not fire")

    monkeypatch.setattr(health_module, "collect_health_snapshot", slow)
    report = build_health_report(engine, now=et(DAY, "08:00"), eval_interval_minutes=2, statement_timeout_ms=50)
    assert report.state is HealthState.DEGRADED and report.snapshot is None
    assert [c.code for c in report.causes] == ["HEALTH_QUERY_TIMEOUT"]


@pytest.mark.parametrize("day, hm", [
    ("2025-11-27", "11:00"),  # Thanksgiving holiday
    ("2025-11-29", "11:00"),  # Saturday
    ("2025-11-25", "09:29"),  # before the open
    ("2025-11-25", "16:00"),  # exactly at the close
    ("2025-11-28", "13:00"),  # exactly at the half-day early close
])
def test_outside_a_session_there_is_no_session_open_and_no_staleness(api, day, hm):
    api.clock.set(et(day, hm))
    payload = body(api)
    assert payload["facts"]["session_open_utc"] is None
    assert "LIVE_CYCLE_STALE" not in {c["code"] for c in payload["causes"]}


def test_half_day_session_is_detected_until_its_early_close(api):
    api.clock.set(et("2025-11-28", "12:59"))
    payload = body(api)
    assert payload["facts"]["session_open_utc"] == et("2025-11-28", "09:30").isoformat()
    assert [c["code"] for c in payload["causes"]] == ["LIVE_CYCLE_STALE"]


def test_review_flag_without_reasons_is_counted_as_unspecified(api):
    order_id = UUID(post_json(api.client, "/signals", signal_body()).json()["auto_order_id"])
    with api.services.engine.begin() as conn:
        conn.execute(tables.order_state.update().where(tables.order_state.c.order_id == order_id)
                     .values(needs_review=True))
    cause = next(c for c in body(api)["causes"] if c["code"] == "NEEDS_REVIEW_QUEUE")
    assert cause["detail"] == {"count": 1, "reasons": {"UNSPECIFIED": 1}}


def test_quality_not_evaluated_disappears_once_the_session_is_rechecked(api):
    order_id = UUID(post_json(api.client, "/signals", signal_body()).json()["auto_order_id"])
    engine = api.services.engine
    as_of = acquire_data_as_of(engine)
    with engine.begin() as conn:
        eod = start_run(conn, RunKind.END_OF_DAY, as_of, CODE_VERSION, detail={"session_day": SESSION})
        finish_run(conn, eod.run_id, RunStatus.COMPLETED,
                   {"session_day": SESSION, "not_evaluated": {str(order_id): "PROVIDER_FAILURE"}})

    cause = next(c for c in body(api)["causes"] if c["code"] == "QUALITY_NOT_EVALUATED")
    assert cause["detail"] == {"session_days": ["2025-11-25"], "count": 1, "reasons": {"PROVIDER_FAILURE": 1}}
    with engine.connect() as conn:
        pending = pending_quality_sessions(conn)
    assert [(p.order_id, p.session_day, p.source_run_id, p.reason, p.ticker, p.price_source) for p in pending] == [
        (order_id, SESSION, eod.run_id, "PROVIDER_FAILURE", "AAPL", "fake_feed")
    ]

    with engine.begin() as conn:
        recheck = start_run(conn, RunKind.QUALITY_RECHECK, as_of, CODE_VERSION)
        conn.execute(tables.data_quality_rechecks.insert().values(
            order_id=order_id, session_date=SESSION, recheck_key="DATA_QUALITY_RECHECK:test", run_id=recheck.run_id,
            source_run_id=eod.run_id, data_as_of=as_of, payload={},
        ))
    assert "QUALITY_NOT_EVALUATED" not in {c["code"] for c in body(api)["causes"]}
    with engine.connect() as conn:
        assert pending_quality_sessions(conn) == []


def job_run(engine, kind, day, status=RunStatus.COMPLETED):
    as_of = acquire_data_as_of(engine)
    with engine.begin() as conn:
        run = start_run(conn, kind, as_of, CODE_VERSION, detail={"session_day": day})
        finish_run(conn, run.run_id, status, {"session_day": day})


def cause_map(payload):
    return {c["code"]: c for c in payload["causes"]}


def test_missing_opening_and_end_of_day_runs_are_visible_after_their_grace(api):
    engine = api.services.engine
    job_run(engine, RunKind.OPENING, SESSION)  # D38 anchor: the first recorded session is never flagged
    job_run(engine, RunKind.END_OF_DAY, SESSION)
    next_day = date(2025, 11, 26)

    api.clock.set(et("2025-11-26", "09:59"))  # open + 29 min: still inside the opening grace
    assert "OPENING_MISSING" not in cause_map(body(api))

    api.clock.set(et("2025-11-26", "12:00"))
    causes = cause_map(body(api))
    assert causes["OPENING_MISSING"]["detail"] == {"session_days": ["2025-11-26"]}
    assert "END_OF_DAY_MISSING" not in causes

    api.clock.set(et("2025-11-26", "19:00"))  # close + 3 h: the 18:30 retry had its chance
    payload = body(api)
    assert cause_map(payload)["END_OF_DAY_MISSING"]["detail"] == {"session_days": ["2025-11-26"]}
    assert payload["facts"]["missing_runs"] == {"OPENING": ["2025-11-26"], "END_OF_DAY": ["2025-11-26"]}

    job_run(engine, RunKind.OPENING, next_day, status=RunStatus.FAILED)  # a failed run is still missing
    assert "OPENING_MISSING" in cause_map(body(api))

    job_run(engine, RunKind.OPENING, next_day)
    job_run(engine, RunKind.END_OF_DAY, next_day)
    payload = body(api)
    assert payload["state"] == "HEALTHY" and payload["causes"] == []
    assert payload["facts"]["missing_runs"] == {}


def test_without_any_opening_or_end_of_day_run_nothing_is_missing(api):
    api.clock.set(et("2025-12-03", "20:00"))
    payload = body(api)
    assert payload["facts"]["missing_runs"] == {}
    assert not {"OPENING_MISSING", "END_OF_DAY_MISSING"} & set(cause_map(payload))
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/readmodels/test_health_facts.py tests/integration/api/test_health_hardening.py -q`
Expected: FAIL (`ImportError: cannot import name 'health_query_timeout'`, `ModuleNotFoundError: virtual_orders.readmodels.quality`; os testes de runs ausentes falham com `TypeError: … unexpected keyword argument 'missing_runs'` e `KeyError: 'missing_runs'`). Os testes de detecção de sessão (`test_outside_a_session_…`, `test_half_day_session_…`) passam já hoje e são **pins** declarados da cobertura pedida pela entrada 13.

- [ ] **Step 4: Write `readmodels/quality.py`**

```python
"""Sessions D12 left unevaluated and not yet consumed by DATA_QUALITY or a recheck (D22)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from sqlalchemy import Connection, text

RECENT_END_OF_DAY_RUNS = 20

_PENDING = text(
    """
    WITH eod AS (
        SELECT r.run_id, r.started_at, latest.detail
        FROM evaluation_runs r
        JOIN LATERAL (
            SELECT status, detail FROM evaluation_run_status s WHERE s.run_id = r.run_id ORDER BY s.id DESC LIMIT 1
        ) latest ON true
        WHERE r.kind = 'END_OF_DAY' AND latest.status = 'COMPLETED'
        ORDER BY r.started_at DESC, r.run_id
        LIMIT :runs
    ), items AS (
        SELECT DISTINCT ON (ne.key, eod.detail->>'session_day')
               ne.key::uuid AS order_id, (eod.detail->>'session_day')::date AS session_day,
               eod.run_id AS source_run_id, ne.value AS reason
        FROM eod, jsonb_each_text(COALESCE(eod.detail->'not_evaluated', '{}'::jsonb)) AS ne(key, value)
        WHERE eod.detail->>'session_day' IS NOT NULL
        ORDER BY ne.key, eod.detail->>'session_day', eod.started_at DESC, eod.run_id
    )
    SELECT i.order_id, i.session_day, i.source_run_id, i.reason, g.ticker, o.price_source
    FROM items i
    JOIN orders o ON o.id = i.order_id
    JOIN signals g ON g.id = o.signal_id
    WHERE NOT EXISTS (
        SELECT 1 FROM order_events e
        WHERE e.order_id = i.order_id AND e.event_key = 'DATA_QUALITY:' || to_char(i.session_day, 'YYYY-MM-DD')
    )
      AND NOT EXISTS (
        SELECT 1 FROM data_quality_rechecks q WHERE q.order_id = i.order_id AND q.session_date = i.session_day
    )
    ORDER BY i.session_day, g.ticker, i.order_id
    """
)


@dataclass(frozen=True)
class PendingQuality:
    order_id: UUID
    session_day: date
    source_run_id: UUID
    reason: str
    ticker: str
    price_source: str


def pending_quality_sessions(conn: Connection, *, runs: int = RECENT_END_OF_DAY_RUNS) -> list[PendingQuality]:
    return [
        PendingQuality(row.order_id, row.session_day, row.source_run_id, row.reason, row.ticker, row.price_source)
        for row in conn.execute(_PENDING, {"runs": runs})
    ]
```

- [ ] **Step 5: Change `readmodels/health.py`**

1. Acrescente o import `from virtual_orders.readmodels.quality import pending_quality_sessions`, troque `from datetime import datetime, timedelta` por `from datetime import UTC, datetime, time, timedelta` e, depois de `UNKNOWN_DATA_SOURCE = "UNKNOWN_DATA_SOURCE"`:

```python
HEALTH_STATEMENT_TIMEOUT_MS = 5000  # D28: a slow query answers DEGRADED quickly instead of hanging /health
QUERY_CANCELED_SQLSTATE = "57014"
UNSPECIFIED_REVIEW_REASON = "UNSPECIFIED"  # also written as a literal in _REVIEW_REASONS (pinned by a test)
OPENING_GRACE = timedelta(minutes=30)  # D38: the 09:25 opening job had its chance by open + 30 min
END_OF_DAY_GRACE = timedelta(hours=3)  # D38: 16:30 job + 18:30 retry, then close + 3 h
MISSING_RUN_LOOKBACK = timedelta(days=14)
```

2. Depois de `_DETAIL_FAILURE_MAP_KEYS = …`, acrescente:

```python
_DETAIL_ORDER_MAP_KEYS = frozenset({
    "integrity_errors", "order_errors", "not_evaluated", "dividend_integrity_errors", "dividend_order_errors",
    "split_integrity_errors", "split_order_errors",
})
```

3. Acrescente como **últimos** campos de `HealthSnapshot`:

```python
    quality_pending: dict[str, str] | None = None  # "<order_id>:<session_day>" -> D12 reason (D22); None = not collected
    missing_runs: dict[str, list[str]] | None = None  # "OPENING"/"END_OF_DAY" -> session days without a run (D38)
```

4. Substitua `_sanitize_detail` por:

```python
def _capped(detail: dict[str, Any], key: str, value: Mapping[str, Any], *, keys_only: bool) -> None:
    ordered = sorted(value)[:MAX_LISTED_ORDERS]
    detail[key] = ordered if keys_only else {name: value[name] for name in ordered}
    if len(value) > MAX_LISTED_ORDERS:
        detail[f"{key}_total"] = len(value)


def _sanitize_detail(detail: Mapping[str, Any]) -> dict[str, Any]:
    """Allow-list a run's `detail` for `facts`/causes: structured keys and counts only, never free text (D19).

    Per-order maps and feed lists are capped at MAX_LISTED_ORDERS with a `<key>_total` count (D31)."""
    sanitized: dict[str, Any] = {}
    for key, value in detail.items():
        if key not in _DETAIL_ALLOWED_KEYS:
            continue
        if key == "error":
            sanitized[key] = _exception_type_from_repr(value) if isinstance(value, str) else value
        elif key in _DETAIL_FAILURE_MAP_KEYS and isinstance(value, Mapping):
            _capped(sanitized, key, value, keys_only=True)
        elif key in _DETAIL_ORDER_MAP_KEYS and isinstance(value, Mapping):
            _capped(sanitized, key, value, keys_only=False)
        else:
            sanitized[key] = value
    return sanitized
```

5. Em `_sanitize_snapshot`, acrescente ao `replace(...)` o argumento:

```python
        quality_pending=None if snapshot.quality_pending is None else {
            key: snapshot.quality_pending[key] for key in sorted(snapshot.quality_pending)[:MAX_LISTED_ORDERS]
        },
```

6. Em `evaluate_health`, substitua o bloco:

```python
    eod = snapshot.last_end_of_day
    if eod is not None and eod.status == "COMPLETED" and eod.detail.get("not_evaluated"):
        reasons = Counter(eod.detail["not_evaluated"].values())
        causes.append(Cause("QUALITY_NOT_EVALUATED", Severity.DEGRADED,
                            {"session_day": eod.detail.get("session_day"), "reasons": dict(sorted(reasons.items()))}))
```

por:

```python
    eod = snapshot.last_end_of_day
    if snapshot.quality_pending is not None:
        if snapshot.quality_pending:  # D22: pending sessions across recent END_OF_DAY runs, minus rechecked ones
            reasons = Counter(snapshot.quality_pending.values())
            causes.append(Cause("QUALITY_NOT_EVALUATED", Severity.DEGRADED, {
                "session_days": sorted({key.rsplit(":", 1)[1] for key in snapshot.quality_pending}),
                "count": len(snapshot.quality_pending),
                "reasons": dict(sorted(reasons.items())),
            }))
    elif eod is not None and eod.status == "COMPLETED" and eod.detail.get("not_evaluated"):
        reasons = Counter(eod.detail["not_evaluated"].values())
        causes.append(Cause("QUALITY_NOT_EVALUATED", Severity.DEGRADED,
                            {"session_day": eod.detail.get("session_day"), "reasons": dict(sorted(reasons.items()))}))
    for kind, code in (("OPENING", "OPENING_MISSING"), ("END_OF_DAY", "END_OF_DAY_MISSING")):
        days = (snapshot.missing_runs or {}).get(kind)
        if days:  # D38: a scheduled job that never completed for a past session
            causes.append(Cause(code, Severity.DEGRADED, {"session_days": list(days)}))
```

7. Depois de `database_unavailable`, acrescente:

```python
def is_statement_timeout(error: BaseException) -> bool:
    """psycopg reports a statement_timeout cancellation as SQLSTATE 57014 (query_canceled)."""
    return getattr(getattr(error, "orig", None), "sqlstate", None) == QUERY_CANCELED_SQLSTATE


def health_query_timeout(error: Exception, timeout_ms: int) -> HealthReport:
    """D28 (M4): the database answered but a health query exceeded its budget. Degraded, never 503."""
    return HealthReport(HealthState.DEGRADED, (Cause("HEALTH_QUERY_TIMEOUT", Severity.DEGRADED, {
        "error": type(error).__name__, "timeout_ms": timeout_ms,
    }),), None)
```

8. Substitua `_REVIEW_REASONS` por:

```python
_REVIEW_REASONS = text(
    """
    SELECT COALESCE(split_part(r.reason, ':', 1), 'UNSPECIFIED') AS reason, COUNT(DISTINCT st.order_id) AS orders
    FROM order_state st
    JOIN orders o ON o.id = st.order_id
    LEFT JOIN LATERAL jsonb_array_elements_text(
        COALESCE(st.state_document->'review_reasons', '[]'::jsonb)
    ) AS r(reason) ON true
    WHERE st.needs_review AND NOT o.replay
    GROUP BY 1 ORDER BY 1
    """
)
```

(o literal `'UNSPECIFIED'` fica no SQL, sem parâmetro dentro da expressão agrupada; `test_unspecified_literal_matches_the_constant` fixa a igualdade com `UNSPECIFIED_REVIEW_REASON`) e, em `collect_health_snapshot`, mantenha `needs_review={row.reason: int(row.orders) for row in conn.execute(_REVIEW_REASONS)},` e acrescente, logo depois dele:

```python
        quality_pending={
            f"{item.order_id}:{item.session_day.isoformat()}": item.reason for item in pending_quality_sessions(conn)
        },
        missing_runs=missing_job_runs(conn, now=now),
```

9. Depois de `_ORDERS_WITHOUT_PROJECTION`, acrescente o coletor da D38:

```python
_JOB_RUNS = text(
    """
    SELECT r.kind, (latest.detail->>'session_day')::date AS session_day, latest.status
    FROM evaluation_runs r
    JOIN LATERAL (
        SELECT status, detail FROM evaluation_run_status s WHERE s.run_id = r.run_id ORDER BY s.id DESC LIMIT 1
    ) latest ON true
    WHERE r.kind IN ('OPENING', 'END_OF_DAY') AND latest.detail->>'session_day' IS NOT NULL
    """
)


def missing_job_runs(conn: Connection, *, now: datetime) -> dict[str, list[str]]:
    """D38: past sessions after the first recorded OPENING/END_OF_DAY session without a COMPLETED run past grace."""
    rows = conn.execute(_JOB_RUNS).all()
    if not rows:
        return {}  # no scheduled job ever ran here: a fresh database is not degraded
    anchor = min(row.session_day for row in rows)
    completed = {(row.kind, row.session_day) for row in rows if row.status == "COMPLETED"}
    start = max(datetime.combine(anchor, time(12), tzinfo=UTC), now - MISSING_RUN_LOOKBACK)
    if start >= now:
        return {}
    missing: dict[str, list[str]] = {}
    for session in calendar_for_window(start, now).sessions:
        if session.day <= anchor:
            continue
        if session.open_utc + OPENING_GRACE <= now and ("OPENING", session.day) not in completed:
            missing.setdefault("OPENING", []).append(session.day.isoformat())
        if session.close_utc + END_OF_DAY_GRACE <= now and ("END_OF_DAY", session.day) not in completed:
            missing.setdefault("END_OF_DAY", []).append(session.day.isoformat())
    return missing
```

10. Substitua `build_health_report` por:

```python
def build_health_report(
    engine: Engine, *, now: datetime, eval_interval_minutes: int, statement_timeout_ms: int = HEALTH_STATEMENT_TIMEOUT_MS
) -> HealthReport:
    """Connection errors are DATABASE_UNAVAILABLE; a statement timeout is HEALTH_QUERY_TIMEOUT (D28); any other
    exception is a defect and propagates (500)."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT set_config('statement_timeout', :value, true)"),
                         {"value": str(statement_timeout_ms)})
            revision = schema_revision(conn)
            if revision != EXPECTED_SCHEMA_REVISION:
                return schema_not_at_head(revision)
            snapshot = collect_health_snapshot(conn, now=now)
    except OperationalError as exc:
        if is_statement_timeout(exc):
            return health_query_timeout(exc, statement_timeout_ms)
        return database_unavailable(exc)
    except (InterfaceError, PoolTimeoutError) as exc:
        return database_unavailable(exc)
    return evaluate_health(snapshot, eval_interval_minutes=eval_interval_minutes)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/readmodels tests/integration/api/test_health_hardening.py tests/integration/api/test_health_api.py -q && uv run ruff check src tests migrations && uv run mypy`
Expected: PASS (os testes de regra do 3A continuam verdes: `quality_pending=None` mantém o comportamento anterior).

- [ ] **Step 7: Commit**

```bash
git add src/virtual_orders/readmodels/quality.py src/virtual_orders/readmodels/health.py tests/readmodels/test_health_facts.py tests/integration/api/test_health_hardening.py
git commit -m "fix(health): separate statement timeouts, cap facts, count unspecified reviews and track pending quality"
```

---

### Task 10: `DATA_QUALITY_RECHECK` (entradas 5 e 14, D22, D34)

**Files:**
- Create: `src/virtual_orders/evaluator/recheck.py`
- Modify: `src/virtual_orders/readmodels/orders.py` (`order_detail`)
- Modify: `tests/integration/api/test_orders_api.py` (`test_order_detail_has_events_segments_quality_and_bars`)
- Test: `tests/integration/test_quality_recheck.py`, `tests/integration/api/test_order_quality_api.py`

**Interfaces:**
- Consumes: `pending_quality_sessions`, `PendingQuality` (Task 9); `session_for_day` (`evaluator/quality.py`); `apply_command`, `CommandInput`; `stored_events`, `known_hashes`; `DEFAULT_FILL_MODEL_VERSION` (`evaluator/context.py`); `parse_ts`, `PreparedEvent`, `to_document` (`storage/codec.py`); `ingest_bars`; `acquire_data_as_of`; `core.dataquality` (`quality_window`, `session_quality`, `find_gaps`, `missing_bar_reviews`, `daily_range_mismatch`, `ReviewFlag`); `RunKind.QUALITY_RECHECK` (Task 8).
- Produces:
  - `RECHECK_EVALUATED = "EVALUATED"`, `RECHECK_NOT_MEASURABLE = "NOT_MEASURABLE"`, `RECHECK_NO_OBSERVATIONS_FINAL = "NO_OBSERVATIONS_FINAL"`, `RECHECK_FINAL_AFTER_SESSIONS = 5`, `LEVEL_DERIVATION_MODELS = frozenset({"v1"})`, `LEVELS_FROM_LEDGER = "LEDGER_AT_SESSION_CLOSE"`, `LEVELS_NOT_DERIVABLE = "SKIPPED:LEVELS_NOT_DERIVABLE"`;
  - `RecheckReport(run_id: UUID | None, rechecked: dict[str, str], not_evaluated: dict[str, str], outcomes: tuple[OrderOutcome, ...] = (), terminal: dict[str, str] = {})` com chaves `"<order_id>:<YYYY-MM-DD>"`; `terminal` ⊆ `rechecked` guarda o motivo das linhas terminais (D22);
  - `recheck_key(session_day: date, data_as_of: datetime) -> str`;
  - `last_closed_session_day(now: datetime) -> date | None`;
  - `sessions_closed_after(day: date, now: datetime) -> int`;
  - `levels_state_at_close(state: OrderState, signal_stop: Decimal, events: Sequence[PreparedEvent], fill_model_version: str, close_utc: datetime) -> OrderState | None`;
  - `run_quality_recheck(engine: Engine, *, gateway: MarketDataGateway, reference: ReferenceSource, code_version: str, market_now: datetime) -> RecheckReport` (usada pelo job de fim de dia da Task 16);
  - `order_detail(...)["data_quality"]["rechecks"]`: lista de `{"session_date", "recheck_key", "run_id", "source_run_id", "data_as_of", "payload"}`.

- [ ] **Step 1: Write the failing integration tests**

`tests/integration/test_quality_recheck.py`:

```python
from collections import Counter
from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from tests.integration.support import (
    CODE_VERSION,
    DAY,
    TICKER,
    FakeBarSource,
    FakeReference,
    count,
    feeds,
    flat_raw,
    raw,
    submit_default,
)
from tests.support import bar, et
from virtual_orders.evaluator import recheck as recheck_module
from virtual_orders.evaluator.quality import run_end_of_day
from virtual_orders.evaluator.rebuild import rebuild_projection
from virtual_orders.evaluator.recheck import (
    RecheckReport,
    last_closed_session_day,
    levels_state_at_close,
    run_quality_recheck,
    sessions_closed_after,
)
from virtual_orders.ledger.events import stored_events
from virtual_orders.ledger.orders import delete_projection, load_projection, read_projection_row
from virtual_orders.ledger.runs import RunKind, RunStatus, finish_run, latest_run_status, start_run
from virtual_orders.marketdata.asof import acquire_data_as_of
from virtual_orders.readmodels.orders import order_detail
from virtual_orders.readmodels.quality import pending_quality_sessions
from virtual_orders.storage import tables

NEXT = "2025-11-26"
SESSION, NEXT_SESSION = date(2025, 11, 25), date(2025, 11, 26)


def session_bars(day, missing=None):
    bars = flat_raw(day, "09:30", "16:00", 105)  # above the zone: the order never fills
    if missing is not None:
        start, end = missing
        bars = [b for b in bars if not et(day, start) <= b.ts < et(day, end)]
    return bars


def end_of_day(engine, source, session_day, market_now):
    return run_end_of_day(engine, gateway=feeds(source), reference=FakeReference(), session_day=session_day,
                          code_version=CODE_VERSION, market_now=market_now)


def recheck(engine, source, market_now, reference=None):
    return run_quality_recheck(engine, gateway=feeds(source), reference=reference or FakeReference(),
                               code_version=CODE_VERSION, market_now=market_now)


def events(engine, order_id):
    with engine.connect() as conn:
        return [e.prepared for e in stored_events(conn, order_id)]


def skipped_first_session(engine, bars):
    """Session DAY is skipped by D12 (provider failure); session NEXT closes normally with data restored."""
    order_id = submit_default(engine).auto_order_id
    source = FakeBarSource(bars)
    source.failing.add(TICKER)
    first = end_of_day(engine, source, SESSION, et(DAY, "16:30"))
    with engine.connect() as conn:
        _, detail = latest_run_status(conn, first.quality.run_id)
    assert detail["not_evaluated"] == {str(order_id): "PROVIDER_FAILURE"}
    source.failing.clear()
    end_of_day(engine, source, NEXT_SESSION, et(NEXT, "16:30"))
    return order_id, source


def test_last_closed_session_day_follows_the_calendar():
    assert last_closed_session_day(et(DAY, "15:59")) == date(2025, 11, 24)
    assert last_closed_session_day(et(DAY, "16:00")) == SESSION
    assert last_closed_session_day(et("2025-11-27", "12:00")) == NEXT_SESSION  # holiday


def test_recheck_consumes_a_session_skipped_by_d12_exactly_once(engine):
    order_id, source = skipped_first_session(engine, session_bars(DAY) + session_bars(NEXT))
    key = f"{order_id}:{DAY}"

    report = recheck(engine, source, et(NEXT, "16:45"))

    assert report.not_evaluated == {} and set(report.rechecked) == {key}
    assert report.rechecked[key].startswith(f"DATA_QUALITY_RECHECK:{DAY}:")
    with engine.connect() as conn:
        row = conn.execute(select(tables.data_quality_rechecks)).one()
        status, detail = latest_run_status(conn, report.run_id)
        assert pending_quality_sessions(conn) == []
    assert status is RunStatus.COMPLETED and detail["rechecked"] == report.rechecked
    assert row.order_id == order_id and row.session_date == SESSION and row.run_id == report.run_id
    assert row.recheck_key == report.rechecked[key]
    assert row.payload["status"] == "EVALUATED" and row.payload["not_evaluated_reason"] == "PROVIDER_FAILURE"
    assert (row.payload["expected_bars"], row.payload["missing_bars"]) == (390, 0)
    # D4: the skipped session never receives a DATA_QUALITY event; only NEXT has one.
    assert [e.event_key for e in events(engine, order_id) if e.type == "DATA_QUALITY"] == [f"DATA_QUALITY:{NEXT}"]

    again = recheck(engine, source, et(NEXT, "17:00"))
    assert again == RecheckReport(None, {}, {}) and count(engine, "data_quality_rechecks") == 1
    with engine.connect() as conn:
        rechecks = order_detail(conn, order_id)["data_quality"]["rechecks"]
    assert [r["recheck_key"] for r in rechecks] == [report.rechecked[key]]


def test_recheck_flags_reviews_but_never_writes_data_quality_or_data_gap(engine):
    order_id, source = skipped_first_session(engine, session_bars(DAY, ("10:10", "10:45")) + session_bars(NEXT))

    report = recheck(engine, source, et(NEXT, "16:45"))

    (row,) = [r for r in _rows(engine)]
    assert row.payload["missing_bars"] == 35
    (gap,) = row.payload["gaps"]
    assert datetime.fromisoformat(gap["gap_start_ts"]) == et(DAY, "10:10") and gap["minutes"] == 35
    stored = events(engine, order_id)
    assert Counter(e.payload["reason"] for e in stored if e.type == "NEEDS_REVIEW") == {"MISSING_BAR_UNVERIFIABLE": 35}
    assert [e for e in stored if e.type == "DATA_GAP"] == []
    assert len(report.outcomes) == 1 and report.outcomes[0].error is None
    with engine.connect() as conn:
        projection = read_projection_row(conn, order_id)
    with engine.begin() as conn:
        delete_projection(conn, order_id)
    assert rebuild_projection(engine, order_id) == projection


def _rows(engine):
    with engine.connect() as conn:
        return conn.execute(select(tables.data_quality_rechecks)).all()


def test_the_just_closed_session_is_left_to_end_of_day(engine):
    submit_default(engine)
    source = FakeBarSource(session_bars(DAY))
    source.failing.add(TICKER)
    end_of_day(engine, source, SESSION, et(DAY, "16:30"))
    source.failing.clear()

    assert recheck(engine, source, et(DAY, "17:00")) == RecheckReport(None, {}, {})
    with engine.connect() as conn:
        runs = conn.execute(select(func.count()).select_from(tables.evaluation_runs)
                            .where(tables.evaluation_runs.c.kind == "QUALITY_RECHECK")).scalar_one()
    assert runs == 0


def never_observed_first_session(engine):
    order_id = submit_default(engine).auto_order_id
    source = FakeBarSource(session_bars(NEXT))  # nothing at all for DAY
    end_of_day(engine, source, SESSION, et(DAY, "16:30"))
    end_of_day(engine, source, NEXT_SESSION, et(NEXT, "16:30"))
    return order_id, source


def test_sessions_closed_after_counts_calendar_sessions():
    assert sessions_closed_after(SESSION, et(NEXT, "16:45")) == 1
    assert sessions_closed_after(SESSION, et("2025-12-03", "15:59")) == 4  # 11-26, 11-28 (half day), 12-01, 12-02
    assert sessions_closed_after(SESSION, et("2025-12-03", "16:45")) == 5


def test_no_observations_inside_the_lookback_keeps_the_session_pending(engine):
    order_id, source = never_observed_first_session(engine)

    report = recheck(engine, source, et(NEXT, "16:45"))

    assert report.rechecked == {} and report.terminal == {}
    assert report.not_evaluated == {f"{order_id}:{DAY}": "NO_OBSERVATIONS"}
    assert count(engine, "data_quality_rechecks") == 0
    with engine.connect() as conn:
        assert [p.order_id for p in pending_quality_sessions(conn)] == [order_id]


def test_no_observations_after_the_lookback_gets_a_final_row_and_clears_the_pending(engine):
    order_id, source = never_observed_first_session(engine)
    key = f"{order_id}:{DAY}"

    report = recheck(engine, source, et("2025-12-03", "16:45"))  # five sessions closed after DAY

    assert report.terminal == {key: "NO_OBSERVATIONS"} and set(report.rechecked) == {key}
    assert report.not_evaluated == {}
    (row,) = _rows(engine)
    assert row.payload["status"] == "NO_OBSERVATIONS_FINAL" and row.payload["terminal_reason"] == "NO_OBSERVATIONS"
    assert not [e for e in events(engine, order_id) if e.type == "NEEDS_REVIEW"]
    with engine.connect() as conn:
        assert pending_quality_sessions(conn) == []
        _, detail = latest_run_status(conn, report.run_id)
    assert detail["terminal"] == {key: "NO_OBSERVATIONS"}


def test_a_session_outside_the_order_window_gets_a_terminal_row(engine):
    order_id = submit_default(engine).auto_order_id  # evaluation starts on DAY
    before = date(2025, 11, 24)
    as_of = acquire_data_as_of(engine)
    with engine.begin() as conn:
        eod = start_run(conn, RunKind.END_OF_DAY, as_of, CODE_VERSION, detail={"session_day": before})
        finish_run(conn, eod.run_id, RunStatus.COMPLETED,
                   {"session_day": before, "not_evaluated": {str(order_id): "NO_OBSERVATIONS"}})
    key = f"{order_id}:2025-11-24"

    report = recheck(engine, FakeBarSource(), et(DAY, "16:45"))

    assert report.terminal == {key: "OUTSIDE_WINDOW"} and report.not_evaluated == {}
    (row,) = _rows(engine)
    assert row.payload["status"] == "NOT_MEASURABLE" and row.payload["terminal_reason"] == "OUTSIDE_WINDOW"
    assert not [e for e in events(engine, order_id) if e.type == "NEEDS_REVIEW"]
    with engine.connect() as conn:
        assert pending_quality_sessions(conn) == []


def test_a_second_recheck_row_for_the_same_session_is_never_written(engine, monkeypatch):
    order_id, source = skipped_first_session(engine, session_bars(DAY) + session_bars(NEXT))
    with engine.connect() as conn:
        stale = pending_quality_sessions(conn)
    recheck(engine, source, et(NEXT, "16:45"))
    monkeypatch.setattr(recheck_module, "pending_quality_sessions", lambda conn: stale)  # an overlapping run's view

    report = recheck(engine, source, et(NEXT, "17:00"))

    assert report.not_evaluated == {f"{order_id}:{DAY}": "ALREADY_RECHECKED"} and report.rechecked == {}
    assert count(engine, "data_quality_rechecks") == 1


def moved_stop_bars():
    """DAY: fill at 10:05 and a feed gap 10:10-10:45; NEXT: TARGET1 at 11:00 moves the stop to the entry."""
    first = flat_raw(DAY, "09:30", "10:05", 105) + [raw(DAY, "10:05", 101, 101.5, 100.5, 101.2)] + flat_raw(
        DAY, "10:06", "16:00", 103
    )
    following = flat_raw(NEXT, "09:30", "11:00", 103) + [raw(NEXT, "11:00", 105, 106, 104.8, 105.5)] + flat_raw(
        NEXT, "11:01", "16:00", 105
    )
    return [b for b in first if not et(DAY, "10:10") <= b.ts < et(DAY, "10:45")] + following


def touch_at_the_original_stop():
    minute = et(DAY, "10:20")
    return FakeReference(minute={(TICKER, SESSION): {minute: bar(minute, 98, 98, 96.5, 97.5)}})


def review_reasons(engine, order_id):
    return Counter(e.payload["reason"] for e in events(engine, order_id) if e.type == "NEEDS_REVIEW")


def test_level_touch_uses_the_stop_in_force_during_the_rechecked_session(engine):
    order_id, source = skipped_first_session(engine, moved_stop_bars())
    with engine.connect() as conn:
        state = load_projection(conn, order_id).state
        prepared = [e.prepared for e in stored_events(conn, order_id)]
    assert state.stop_current != Decimal("97") and state.stop_active_from is not None  # moved on NEXT

    at_day = levels_state_at_close(state, Decimal("97"), prepared, "v1", et(DAY, "16:00"))
    at_next = levels_state_at_close(state, Decimal("97"), prepared, "v1", et(NEXT, "16:00"))
    assert at_day is not None and (at_day.stop_current, at_day.stop_active_from) == (Decimal("97"), None)
    assert at_next is not None and (at_next.stop_previous, at_next.stop_current) == (Decimal("97"), state.stop_current)
    assert levels_state_at_close(state, Decimal("97"), prepared, "v2", et(DAY, "16:00")) is None

    report = recheck(engine, source, et(NEXT, "16:45"), reference=touch_at_the_original_stop())

    (row,) = _rows(engine)
    assert row.payload["level_touch_check"] == "LEDGER_AT_SESSION_CLOSE"
    assert row.payload["level_touch_unchecked_minutes"] == []
    assert review_reasons(engine, order_id) == {"MISSING_BAR_UNVERIFIABLE": 34, "MISSING_BAR_LEVEL_TOUCH": 1}
    assert report.outcomes[0].error is None


def test_level_touch_is_skipped_with_a_reason_when_levels_cannot_be_derived(engine, monkeypatch):
    order_id, source = skipped_first_session(engine, moved_stop_bars())
    monkeypatch.setattr(recheck_module, "LEVEL_DERIVATION_MODELS", frozenset())

    recheck(engine, source, et(NEXT, "16:45"), reference=touch_at_the_original_stop())

    (row,) = _rows(engine)
    assert row.payload["level_touch_check"] == "SKIPPED:LEVELS_NOT_DERIVABLE"
    assert [datetime.fromisoformat(m) for m in row.payload["level_touch_unchecked_minutes"]] == [et(DAY, "10:20")]
    assert review_reasons(engine, order_id) == {"MISSING_BAR_UNVERIFIABLE": 34}  # never the current levels


def test_provider_failure_during_the_recheck_follows_d12(engine):
    order_id, source = skipped_first_session(engine, session_bars(DAY) + session_bars(NEXT))
    source.failing.add(TICKER)

    report = recheck(engine, source, et(NEXT, "16:45"))

    assert report.not_evaluated == {f"{order_id}:{DAY}": "PROVIDER_FAILURE"} and report.rechecked == {}
    with engine.connect() as conn:
        _, detail = latest_run_status(conn, report.run_id)
    assert detail["ingest_failures"] == {f"fake_feed:{TICKER}:{DAY}": "SourceUnavailable"}


def test_naive_market_now_is_rejected(engine):
    with pytest.raises(ValueError, match="market_now must be timezone-aware"):
        run_quality_recheck(engine, gateway=feeds(FakeBarSource()), reference=FakeReference(),
                            code_version=CODE_VERSION, market_now=datetime(2025, 11, 26, 21, 0))  # noqa: DTZ001
```

`tests/integration/api/test_order_quality_api.py`:

```python
from datetime import date
from uuid import UUID

from tests.integration.api.conftest import post_json
from tests.integration.support import CODE_VERSION, DAY, FakeReference, scenario_bars, signal_body
from tests.support import et
from virtual_orders.evaluator.quality import run_end_of_day


def test_order_detail_shows_real_data_quality_and_data_gap_events(api):
    order_id = UUID(post_json(api.client, "/signals", signal_body()).json()["auto_order_id"])
    api.bars.load([b for b in scenario_bars() if not et(DAY, "10:10") <= b.ts < et(DAY, "10:45")])
    run_end_of_day(api.services.engine, gateway=api.services.gateway, reference=FakeReference(),
                   session_day=date(2025, 11, 25), code_version=CODE_VERSION, market_now=et(DAY, "16:30"))

    quality = api.client.get(f"/orders/{order_id}").json()["data_quality"]

    assert (quality["expected_bars"], quality["missing_bars"]) == (201, 35)
    assert [e["type"] for e in quality["events"]] == ["DATA_QUALITY", "DATA_GAP"]
    assert quality["events"][0]["event_key"] == "DATA_QUALITY:2025-11-25"
    assert quality["events"][0]["payload"]["coverage_pct"] == "82.59"
    assert quality["events"][1]["payload"]["minutes"] == 35
    assert quality["rechecks"] == []
```

Em `tests/integration/api/test_orders_api.py`, troque:

```python
    assert detail["data_quality"] == {"expected_bars": 0, "missing_bars": 0, "events": []}
```

por:

```python
    assert detail["data_quality"] == {"expected_bars": 0, "missing_bars": 0, "events": [], "rechecks": []}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/integration/test_quality_recheck.py tests/integration/api/test_order_quality_api.py tests/integration/api/test_orders_api.py -q`
Expected: FAIL (`ModuleNotFoundError: virtual_orders.evaluator.recheck`; `KeyError: 'rechecks'`).

- [ ] **Step 3: Write `evaluator/recheck.py`**

```python
"""DATA_QUALITY_RECHECK (spec 10 D4; D12; D22): "what do we know today" about a session D12 skipped.

Never writes DATA_QUALITY or DATA_GAP (D4 keeps the original snapshot immutable); records its own append-only
row keyed DATA_QUALITY_RECHECK:{session_date}:{data_as_of} and flags reviews through the locked command path.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from functools import partial
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Engine, select

from core.dataquality import (
    ReviewFlag,
    daily_range_mismatch,
    find_gaps,
    missing_bar_reviews,
    quality_window,
    session_quality,
)
from core.domain.calendar import Session
from core.domain.models import Bar, OrderState, StepResult
from virtual_orders.evaluator.clock import require_aware
from virtual_orders.evaluator.commands import CommandInput, apply_command
from virtual_orders.evaluator.context import DEFAULT_FILL_MODEL_VERSION
from virtual_orders.evaluator.outcomes import OrderOutcome, isolated, split_errors
from virtual_orders.evaluator.quality import session_for_day
from virtual_orders.ledger.events import known_hashes, stored_events
from virtual_orders.ledger.runs import RunInfo, RunKind, RunStatus, finish_run, start_run
from virtual_orders.marketdata.asof import acquire_data_as_of, bars_in_minutes, read_bars_as_of
from virtual_orders.marketdata.calendars import calendar_for_window
from virtual_orders.marketdata.gateway import MarketDataGateway, UnknownDataSource
from virtual_orders.marketdata.ingest import ingest_bars
from virtual_orders.marketdata.sources import ReferenceSource, SourceDataError, SourceUnavailable
from virtual_orders.readmodels.quality import PendingQuality, pending_quality_sessions
from virtual_orders.storage.codec import PreparedEvent, parse_ts, to_document
from virtual_orders.storage.tables import data_quality_rechecks

RECHECK_EVALUATED = "EVALUATED"
RECHECK_NOT_MEASURABLE = "NOT_MEASURABLE"
RECHECK_NO_OBSERVATIONS_FINAL = "NO_OBSERVATIONS_FINAL"
RECHECK_FINAL_AFTER_SESSIONS = 5  # D22: a session still unobserved this many closed sessions later is final
# Fill models whose only stop-moving event is TARGET1_HIT (payload new_stop_level + stop_active_from), so the stop
# in force during a past session is exactly derivable from the ledger. A new model must be added consciously.
LEVEL_DERIVATION_MODELS = frozenset({DEFAULT_FILL_MODEL_VERSION})
LEVELS_FROM_LEDGER = "LEDGER_AT_SESSION_CLOSE"
LEVELS_NOT_DERIVABLE = "SKIPPED:LEVELS_NOT_DERIVABLE"
MinuteReference = dict[datetime, Bar] | None
DailyReference = tuple[Decimal, Decimal] | None


@dataclass(frozen=True)
class RecheckReport:
    run_id: UUID | None
    rechecked: dict[str, str]
    not_evaluated: dict[str, str]
    outcomes: tuple[OrderOutcome, ...] = ()
    terminal: dict[str, str] = field(default_factory=dict)  # subset of `rechecked`: terminal reason (D22)


def recheck_key(session_day: date, data_as_of: datetime) -> str:
    return f"DATA_QUALITY_RECHECK:{session_day.isoformat()}:{data_as_of.astimezone(UTC).isoformat()}"


def last_closed_session_day(now: datetime) -> date | None:
    closed = [s.day for s in calendar_for_window(now, now).sessions if s.close_utc <= now]
    return closed[-1] if closed else None


def sessions_closed_after(day: date, now: datetime) -> int:
    session = session_for_day(day)
    if session.close_utc >= now:
        return 0
    return sum(1 for s in calendar_for_window(session.close_utc, now).sessions if s.day > day and s.close_utc <= now)


def levels_state_at_close(
    state: OrderState,
    signal_stop: Decimal,
    events: Sequence[PreparedEvent],
    fill_model_version: str,
    close_utc: datetime,
) -> OrderState | None:
    """Spec 4.6 / D22: `state` with the stop fields in force up to `close_utc`, rebuilt from the ledger.

    Only the stop fields are replaced (they are all `active_levels` reads besides the signal's static levels). Returns
    None when that cannot be derived exactly; the caller then skips the level-touch check and never falls back to the
    projection's current levels.
    """
    if fill_model_version not in LEVEL_DERIVATION_MODELS:
        return None
    moves = [
        (parse_ts(event.payload["stop_active_from"]), Decimal(event.payload["new_stop_level"]))
        for event in events
        if event.type == "TARGET1_HIT" and event.bar_ts is not None and event.bar_ts <= close_utc
        and "new_stop_level" in event.payload
    ]
    if len(moves) > 1:
        return None
    if not moves:
        return replace(state, stop_current=signal_stop, stop_previous=None, stop_active_from=None)
    active_from, level = moves[0]
    return replace(state, stop_current=level, stop_previous=signal_stop, stop_active_from=active_from)


def _item_key(item: PendingQuality) -> str:
    return f"{item.order_id}:{item.session_day.isoformat()}"


def _already_rechecked(conn: Connection, order_id: UUID, session_day: date) -> bool:
    return conn.execute(
        select(data_quality_rechecks.c.id)
        .where(data_quality_rechecks.c.order_id == order_id, data_quality_rechecks.c.session_date == session_day)
        .limit(1)
    ).first() is not None


def _recheck_command(
    run: RunInfo,
    session: Session,
    item: PendingQuality,
    minute_reference: MinuteReference,
    daily_reference: DailyReference,
    feed_failed: bool,
    final_no_observations: bool,
    rechecked: dict[str, str],
    not_evaluated: dict[str, str],
    terminal: dict[str, str],
) -> Callable[[CommandInput], StepResult]:
    day = session.day.isoformat()
    key = _item_key(item)
    identity = recheck_key(session.day, run.data_as_of)

    def skip(inp: CommandInput, reason: str) -> StepResult:
        not_evaluated[key] = reason
        return StepResult(inp.projection.state)

    def base(status: str) -> dict[str, Any]:
        return {
            "status": status, "session_date": session.day, "data_as_of": run.data_as_of,
            "evaluation_run_id": run.run_id, "source_run_id": item.source_run_id, "not_evaluated_reason": item.reason,
        }

    def record(inp: CommandInput, payload: dict[str, Any]) -> None:
        inp.conn.execute(data_quality_rechecks.insert().values(
            order_id=inp.order.id, session_date=session.day, recheck_key=identity, run_id=run.run_id,
            source_run_id=item.source_run_id, data_as_of=run.data_as_of, payload=to_document(payload),
        ))
        rechecked[key] = identity

    def final(inp: CommandInput, status: str, reason: str) -> StepResult:
        record(inp, {**base(status), "terminal_reason": reason})  # consumes the pending session explicitly
        terminal[key] = reason
        return StepResult(inp.projection.state)

    def command(inp: CommandInput) -> StepResult:
        state = inp.projection.state
        if _already_rechecked(inp.conn, inp.order.id, session.day):
            return skip(inp, "ALREADY_RECHECKED")  # one row per (order, session), even for overlapping runs
        if f"DATA_QUALITY:{day}" in known_hashes(inp.conn, inp.order.id):
            return skip(inp, "ALREADY_EVALUATED")  # D4: the original snapshot exists; nothing to recheck
        window_start, window_end = quality_window(state, inp.ctx, session.close_utc)
        start, end = max(window_start, session.open_utc), min(window_end, session.close_utc)
        if end <= start:
            return final(inp, RECHECK_NOT_MEASURABLE, "OUTSIDE_WINDOW")
        if feed_failed:
            return skip(inp, "PROVIDER_FAILURE")  # D12: retried by a later recheck
        ticker = inp.signal.spec.ticker
        minutes = inp.ctx.calendar.expected_minutes(start, end)
        bars = bars_in_minutes(
            read_bars_as_of(inp.conn, ticker, inp.order.price_source, start, end, run.data_as_of), minutes
        )
        if not bars:
            if final_no_observations:
                return final(inp, RECHECK_NO_OBSERVATIONS_FINAL, "NO_OBSERVATIONS")
            return skip(inp, "NO_OBSERVATIONS")  # D12
        quality = next((q for q in session_quality(inp.ctx.calendar, start, end, [b.ts for b in bars])
                        if q.day == session.day), None)
        if quality is None:
            return final(inp, RECHECK_NOT_MEASURABLE, "OUTSIDE_WINDOW")

        gaps = find_gaps(inp.ctx.calendar, quality.missing, inp.order.config.data_gap_minutes)
        flags: list[ReviewFlag] = []
        level_touch_check: str | None = None
        unchecked: list[datetime] = []
        if quality.missing:
            reference = minute_reference or {}
            levels = levels_state_at_close(
                state, inp.signal.spec.stop, [stored.prepared for stored in stored_events(inp.conn, inp.order.id)],
                inp.order.fill_model_version, session.close_utc,
            )
            if levels is not None:
                level_touch_check = LEVELS_FROM_LEDGER
                flags.extend(missing_bar_reviews(quality.missing, reference, inp.signal.spec, levels))
            else:
                level_touch_check = LEVELS_NOT_DERIVABLE
                missing = sorted(quality.missing)
                flags.extend(ReviewFlag("MISSING_BAR_UNVERIFIABLE", m.isoformat()) for m in missing if m not in reference)
                unchecked = [m for m in missing if m in reference]
        if daily_reference is not None and start == session.open_utc and end == session.close_utc:
            high, low = daily_reference
            if daily_range_mismatch(bars, high, low, inp.order.config.crosscheck_tolerance_pct):
                flags.append(ReviewFlag("DAILY_RANGE_MISMATCH", day))

        record(inp, {
            **quality.event().payload,
            **base(RECHECK_EVALUATED),
            "gaps": [{"gap_start_ts": gap_start, "minutes": length} for gap_start, length in gaps],
            "review_flags": [{"reason": flag.reason, "ref": flag.ref} for flag in flags],
            "level_touch_check": level_touch_check,
            "level_touch_unchecked_minutes": unchecked,
        })
        result = StepResult(state)
        for flag in flags:
            flagged = inp.model.flag_review(result.state, flag.reason, flag.ref)
            result = StepResult(flagged.state, result.events + flagged.events)
        return result

    return command


def run_quality_recheck(
    engine: Engine,
    *,
    gateway: MarketDataGateway,
    reference: ReferenceSource,
    code_version: str,
    market_now: datetime,
) -> RecheckReport:
    now = require_aware(market_now, "market_now")
    last_closed = last_closed_session_day(now)
    with engine.connect() as conn:
        # D12(b): the just-closed session still belongs to END_OF_DAY; only older sessions are rechecked.
        pending = [item for item in pending_quality_sessions(conn)
                   if last_closed is not None and item.session_day < last_closed]
    if not pending:
        return RecheckReport(None, {}, {})

    sessions: dict[date, Session] = {item.session_day: session_for_day(item.session_day) for item in pending}
    failed_feeds: dict[str, str] = {}
    for price_source, ticker, day in sorted({(i.price_source, i.ticker, i.session_day) for i in pending}):
        feed = f"{price_source}:{ticker}:{day.isoformat()}"
        try:
            ingest_bars(engine, gateway.bar_source(price_source), ticker, sessions[day].open_utc,
                        sessions[day].close_utc)
        except UnknownDataSource:
            failed_feeds[feed] = "UNKNOWN_DATA_SOURCE"
        except (SourceUnavailable, SourceDataError) as exc:
            failed_feeds[feed] = type(exc).__name__

    unavailable: dict[str, str] = {}
    minute_refs: dict[tuple[str, date], MinuteReference] = {}
    daily_refs: dict[tuple[str, date], DailyReference] = {}
    for ticker, day in sorted({(i.ticker, i.session_day) for i in pending}):
        try:
            minute_refs[(ticker, day)] = reference.fetch_minute_bars(ticker, day)
        except (SourceUnavailable, SourceDataError) as exc:
            minute_refs[(ticker, day)] = None
            unavailable[f"{ticker}:{day.isoformat()}:1m"] = type(exc).__name__
        try:
            daily_refs[(ticker, day)] = reference.fetch_daily_range(ticker, day)
        except (SourceUnavailable, SourceDataError) as exc:
            daily_refs[(ticker, day)] = None
            unavailable[f"{ticker}:{day.isoformat()}:1d"] = type(exc).__name__

    session_days = sorted(day.isoformat() for day in sessions)
    data_as_of = acquire_data_as_of(engine)
    run: RunInfo | None = None
    try:
        with engine.begin() as conn:
            run = start_run(conn, RunKind.QUALITY_RECHECK, data_as_of, code_version,
                            detail={"sessions": session_days, "market_now": now})
        rechecked: dict[str, str] = {}
        not_evaluated: dict[str, str] = {}
        terminal: dict[str, str] = {}
        outcomes: list[OrderOutcome] = []
        final_days = {day for day in sessions if sessions_closed_after(day, now) >= RECHECK_FINAL_AFTER_SESSIONS}
        for item in pending:
            command = _recheck_command(
                run, sessions[item.session_day], item, minute_refs[(item.ticker, item.session_day)],
                daily_refs[(item.ticker, item.session_day)],
                f"{item.price_source}:{item.ticker}:{item.session_day.isoformat()}" in failed_feeds,
                item.session_day in final_days, rechecked, not_evaluated, terminal,
            )
            outcome = isolated(item.order_id, partial(apply_command, engine, item.order_id, command))
            if outcome.error is not None:
                rechecked.pop(_item_key(item), None)  # the transaction rolled back together with the row
                terminal.pop(_item_key(item), None)
            outcomes.append(outcome)
        integrity_errors, order_errors = split_errors(outcomes)
        with engine.begin() as conn:
            finish_run(conn, run.run_id, RunStatus.COMPLETED, {
                "sessions": session_days, "market_now": now, "orders": len(outcomes), "rechecked": rechecked,
                "terminal": terminal, "not_evaluated": not_evaluated, "ingest_failures": failed_feeds,
                "unavailable": unavailable, "integrity_errors": integrity_errors, "order_errors": order_errors,
            })
        return RecheckReport(run.run_id, rechecked, not_evaluated, tuple(outcomes), terminal)
    except Exception as exc:
        if run is not None:
            with engine.begin() as conn:
                finish_run(conn, run.run_id, RunStatus.FAILED, {"error": repr(exc), "market_now": now})
        raise
```

- [ ] **Step 4: Expose rechecks in the order detail**

Em `src/virtual_orders/readmodels/orders.py`, troque o import das tabelas por `from virtual_orders.storage.tables import data_quality_rechecks, order_state, orders, signals` e, em `order_detail`, logo antes do `return`:

```python
    rechecks = [
        {"session_date": item.session_date, "recheck_key": item.recheck_key, "run_id": item.run_id,
         "source_run_id": item.source_run_id, "data_as_of": item.data_as_of, "payload": item.payload}
        for item in conn.execute(
            select(data_quality_rechecks)
            .where(data_quality_rechecks.c.order_id == order_id)
            .order_by(data_quality_rechecks.c.session_date, data_quality_rechecks.c.id)
        )
    ]
```

e, no dicionário `"data_quality"`, acrescente `"rechecks": rechecks,` depois de `"events": …`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/integration/test_quality_recheck.py tests/integration/api/test_order_quality_api.py tests/integration/api/test_orders_api.py tests/integration/test_quality.py tests/integration/api/test_health_hardening.py tests/test_import_boundaries.py -q && uv run ruff check src tests migrations && uv run mypy`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/virtual_orders/evaluator/recheck.py src/virtual_orders/readmodels/orders.py tests/integration/test_quality_recheck.py tests/integration/api/test_order_quality_api.py tests/integration/api/test_orders_api.py
git commit -m "feat(evaluator): DATA_QUALITY_RECHECK for sessions skipped by D12 without touching DATA_QUALITY"
```

---

### Task 11: Estimativas de pressão OHLCV (entrada 4, D27)

**Files:**
- Create: `src/virtual_orders/analytics/pressure.py`
- Test: `tests/analytics/__init__.py` (vazio), `tests/analytics/test_pressure.py`

**Interfaces:**
- Consumes: `core.domain.models.Bar`, `core.domain.hashing.CANONICAL_CONTEXT`.
- Produces (usado pela Task 13):
  - `METHOD = "OHLCV_PRESSURE_ESTIMATE_V1"`, `DISCLAIMER` (texto fixo), `QUANTUM = Decimal("0.0001")`;
  - `PressureSide(StrEnum)`: `BUY`, `SELL`;
  - `PressureEstimate(method: str, bars: int, first_bar_ts: datetime, last_bar_ts: datetime, close_location_value: Decimal, chaikin_money_flow: Decimal, obv_slope: Decimal, vwap: Decimal, vwap_distance_pct: Decimal)`;
  - `close_location_value(bar: Bar) -> Decimal`;
  - `estimate_pressure(bars: Sequence[Bar]) -> PressureEstimate | None` (menos de 2 candles → `None`; minuto duplicado → `ValueError`);
  - `strong_pressure(estimate: PressureEstimate, cmf_threshold: Decimal) -> PressureSide | None`.

- [ ] **Step 1: Write the failing tests**

`tests/analytics/test_pressure.py` (fixture sintética escrita à mão; os valores esperados foram calculados à mão no comentário de cada teste):

```python
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from core.domain.models import Bar
from virtual_orders.analytics.pressure import (
    DISCLAIMER,
    METHOD,
    PressureSide,
    close_location_value,
    estimate_pressure,
    strong_pressure,
)


def bar(minute: int, o, h, l, c, v) -> Bar:  # noqa: E741
    return Bar(ts=datetime(2025, 11, 25, 14, 30 + minute, tzinfo=UTC), open=Decimal(str(o)), high=Decimal(str(h)),
               low=Decimal(str(l)), close=Decimal(str(c)), volume=Decimal(str(v)))


BUYING = [bar(0, 10, 11, 9, 10.5, 100), bar(1, 10.5, 12, 10, 12, 200), bar(2, 12, 12.5, 11.5, 12, 100)]
SELLING = [bar(0, 10, 11, 9, 9.5, 100), bar(1, 9.5, 10, 8, 8, 200), bar(2, 8, 8.5, 7.5, 8, 100)]


def test_close_location_value():
    assert close_location_value(BUYING[0]) == Decimal("0.5")
    assert close_location_value(BUYING[1]) == Decimal("1")
    assert close_location_value(bar(0, 5, 5, 5, 5, 10)) == Decimal("0")  # zero range


def test_buying_fixture_matches_hand_computed_values():
    # CLV = 0.5, 1, 0 -> money flow 50 + 200 + 0 = 250 over volume 400 -> CMF 0.625
    # OBV = +200 (close up), 0 (unchanged) -> 200 / 2 bars / mean volume 133.33 -> 0.75
    # VWAP uses the typical price (H+L+C)/3 of each bar (D27, D37):
    #   bar 0: (11 + 9 + 10.5)/3 = 30.5/3 = 10.166666...  x 100 = 1016.666666...
    #   bar 1: (12 + 10 + 12)/3  = 34/3   = 11.333333...  x 200 = 2266.666666...
    #   bar 2: (12.5 + 11.5 + 12)/3 = 36/3 = 12           x 100 = 1200
    #   VWAP = 4483.333333... / 400 = 11.208333...  -> 11.2083 (ROUND_HALF_EVEN, 4 dp)
    # distance = (12 - 11.208333...) / 11.208333... * 100 = 0.791666.../11.208333... * 100 = 7.063197...  -> 7.0632
    estimate = estimate_pressure(BUYING)
    assert estimate is not None
    assert estimate.method == METHOD and estimate.bars == 3
    assert estimate.first_bar_ts == BUYING[0].ts and estimate.last_bar_ts == BUYING[2].ts
    assert estimate.close_location_value == Decimal("0.0000")
    assert estimate.chaikin_money_flow == Decimal("0.6250")
    assert estimate.obv_slope == Decimal("0.7500")
    assert estimate.vwap == Decimal("11.2083")
    assert estimate.vwap_distance_pct == Decimal("7.0632")
    assert strong_pressure(estimate, Decimal("0.625")) is PressureSide.BUY
    assert strong_pressure(estimate, Decimal("0.6251")) is None


def test_selling_fixture_is_the_mirror():
    estimate = estimate_pressure(SELLING)
    assert estimate is not None
    assert estimate.chaikin_money_flow == Decimal("-0.6250") and estimate.obv_slope == Decimal("-0.7500")
    assert estimate.vwap_distance_pct < 0
    assert strong_pressure(estimate, Decimal("0.5")) is PressureSide.SELL


def test_order_of_input_does_not_matter_and_results_are_deterministic():
    assert estimate_pressure(list(reversed(BUYING))) == estimate_pressure(BUYING) == estimate_pressure(BUYING)


def test_zero_volume_has_neutral_flow_and_vwap_at_the_last_close():
    estimate = estimate_pressure([bar(0, 10, 11, 9, 10.5, 0), bar(1, 10.5, 12, 10, 12, 0)])
    assert estimate is not None
    assert estimate.chaikin_money_flow == 0 and estimate.obv_slope == 0
    assert estimate.vwap == Decimal("12.0000") and estimate.vwap_distance_pct == 0
    assert strong_pressure(estimate, Decimal("0.1")) is None


def test_too_few_bars_or_duplicate_minutes():
    assert estimate_pressure([]) is None and estimate_pressure(BUYING[:1]) is None
    with pytest.raises(ValueError, match="duplicate bar minute"):
        estimate_pressure([BUYING[0], BUYING[0]])


def test_disclaimer_labels_the_output_as_an_estimate():
    assert "Estimate" in DISCLAIMER and "not order-flow" in DISCLAIMER
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/analytics/test_pressure.py -q`
Expected: FAIL com `ModuleNotFoundError: virtual_orders.analytics.pressure`.

- [ ] **Step 3: Write `analytics/pressure.py`**

```python
"""Buy/sell pressure ESTIMATES derived from 1-minute OHLCV (owner 2026-09-13, D27). Pure and deterministic.

This is not order-flow data and not the roadmap's Pressure Engine: every consumer labels it as an estimate.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from enum import StrEnum

from core.domain.hashing import CANONICAL_CONTEXT
from core.domain.models import Bar

METHOD = "OHLCV_PRESSURE_ESTIMATE_V1"
DISCLAIMER = "Estimate derived from 1-minute OHLCV bars; it is not order-flow or trade-side data."
QUANTUM = Decimal("0.0001")
ZERO = Decimal(0)
HUNDRED = Decimal(100)
THREE = Decimal(3)


class PressureSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True)
class PressureEstimate:
    method: str
    bars: int
    first_bar_ts: datetime
    last_bar_ts: datetime
    close_location_value: Decimal
    chaikin_money_flow: Decimal
    obv_slope: Decimal
    vwap: Decimal
    vwap_distance_pct: Decimal


def _quantized(value: Decimal) -> Decimal:
    with localcontext(CANONICAL_CONTEXT):
        result = value.quantize(QUANTUM, rounding=ROUND_HALF_EVEN)
    return result if result != 0 else ZERO.quantize(QUANTUM)  # never "-0.0000"


def close_location_value(bar: Bar) -> Decimal:
    """((close - low) - (high - close)) / (high - low), in [-1, 1]; 0 for a zero-range bar."""
    span = bar.high - bar.low
    if span == 0:
        return ZERO
    with localcontext(CANONICAL_CONTEXT):
        return ((bar.close - bar.low) - (bar.high - bar.close)) / span


def estimate_pressure(bars: Sequence[Bar]) -> PressureEstimate | None:
    ordered = sorted(bars, key=lambda item: item.ts)
    if len({item.ts for item in ordered}) != len(ordered):
        raise ValueError("duplicate bar minute")
    if len(ordered) < 2:
        return None
    with localcontext(CANONICAL_CONTEXT):
        volume = sum((item.volume for item in ordered), ZERO)
        flow = sum((close_location_value(item) * item.volume for item in ordered), ZERO)
        cmf = ZERO if volume == 0 else flow / volume
        obv = ZERO
        for previous, current in zip(ordered, ordered[1:], strict=False):
            if current.close > previous.close:
                obv += current.volume
            elif current.close < previous.close:
                obv -= current.volume
        mean_volume = volume / len(ordered)
        slope = ZERO if mean_volume == 0 else obv / (len(ordered) - 1) / mean_volume
        last_close = ordered[-1].close
        typical = sum(((item.high + item.low + item.close) / THREE * item.volume for item in ordered), ZERO)
        vwap = last_close if volume == 0 else typical / volume
        distance = ZERO if vwap == 0 else (last_close - vwap) / vwap * HUNDRED
    return PressureEstimate(
        method=METHOD, bars=len(ordered), first_bar_ts=ordered[0].ts, last_bar_ts=ordered[-1].ts,
        close_location_value=_quantized(close_location_value(ordered[-1])), chaikin_money_flow=_quantized(cmf),
        obv_slope=_quantized(slope), vwap=_quantized(vwap), vwap_distance_pct=_quantized(distance),
    )


def strong_pressure(estimate: PressureEstimate, cmf_threshold: Decimal) -> PressureSide | None:
    """BUY: CMF >= threshold, rising OBV and close at/above VWAP. SELL is the exact mirror."""
    if estimate.chaikin_money_flow >= cmf_threshold and estimate.obv_slope > 0 and estimate.vwap_distance_pct >= 0:
        return PressureSide.BUY
    if estimate.chaikin_money_flow <= -cmf_threshold and estimate.obv_slope < 0 and estimate.vwap_distance_pct <= 0:
        return PressureSide.SELL
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/analytics tests/test_import_boundaries.py -q && uv run ruff check src tests migrations && uv run mypy`
Expected: PASS, inclusive `test_platform_pure_modules_have_no_io_or_infrastructure_imports`, que agora verifica `virtual_orders/analytics/pressure.py`.

- [ ] **Step 5: Commit**

```bash
git add src/virtual_orders/analytics/pressure.py tests/analytics/__init__.py tests/analytics/test_pressure.py
git commit -m "feat(analytics): deterministic OHLCV pressure estimates labelled as estimates"
```

---

### Task 12: Outbox de alertas, eventos de ordem e incidentes, entrega com backoff e causas `INFO` (entradas 3 e 4, D24, D33, D35)

**Files:**
- Create: `src/virtual_orders/alerts/outbox.py`, `tests/integration/alert_support.py`
- Modify: `src/virtual_orders/readmodels/health.py` (causas `INFO` `UNDELIVERABLE_ALERTS` e `ORDER_EVENT_ALERTS_BEHIND`)
- Test: `tests/alerts/__init__.py` (vazio), `tests/alerts/test_review_keys.py`, `tests/integration/test_alert_outbox.py`

**Interfaces:**
- Consumes: `AlertSink`, `AlertDeliveryFailed` (Task 7); tabelas `alert_outbox`, `alert_delivery_attempts`, `alert_event_marks` (Task 8); `to_document`; `build_health_report`, `HealthSnapshot`, `Severity` (Task 9); `record_incident`, `flag_order_review`.
- Produces (usado pelas Tasks 13, 14 e 16):
  - `AlertKind(StrEnum)`: `ORDER_EVENT`, `ORDER_REVIEW`, `INTEGRITY_INCIDENT`, `PRICE_CROSS`, `PRESSURE`, `HEALTH`, `END_OF_DAY_SUMMARY`;
  - `ALERT_SCHEMA_VERSION = 1`, `DELIVERY_BATCH = 50`, `DELIVERY_TIME_BUDGET_SECONDS = 20.0`, `BACKOFF_CAP_MINUTES = 30`, `ALERT_EXPIRY = timedelta(hours=24)`, `UNDELIVERABLE_WINDOW = timedelta(days=7)`, `ORDER_EVENT_LOOKBACK = timedelta(days=4)`, `ORDER_EVENT_SCAN_LIMIT = 500`, `ORDER_EVENT_ALERT_TYPES`;
  - `enqueue_alert(conn: Connection, *, alert_key: str, kind: AlertKind, document: Mapping[str, Any], subject: str | None = None, subject_ts: datetime | None = None) -> bool`;
  - `last_alert_ts(conn: Connection, kind: AlertKind, subject: str) -> datetime | None`;
  - `backoff(failures: int) -> timedelta`; `review_session_day(ref: str, recorded_at: datetime) -> date`;
  - `enqueue_order_event_alerts(engine: Engine, *, limit: int = ORDER_EVENT_SCAN_LIMIT) -> int`; `enqueue_incident_alerts(engine: Engine, *, limit: int = ORDER_EVENT_SCAN_LIMIT) -> int`; `enqueue_event_alerts(engine: Engine) -> int` (soma dos dois; é o passo que a Task 16 chama);
  - `DeliveryReport(delivered: int, failed: int, expired: int = 0, budget_exhausted: bool = False)`;
  - `deliver_pending_alerts(engine: Engine, sink: AlertSink, *, limit: int = DELIVERY_BATCH, now: datetime | None = None, time_budget_seconds: float = DELIVERY_TIME_BUDGET_SECONDS, monotonic: Callable[[], float] = time.monotonic) -> DeliveryReport`;
  - `undeliverable_alerts(conn: Connection) -> int` (alertas expirados nos últimos 7 dias); `order_event_alerts_behind(conn: Connection) -> int`;
  - `HealthSnapshot.undeliverable_alerts: int | None = None`, `HealthSnapshot.order_event_alerts_behind: int | None = None`; causas `INFO` `UNDELIVERABLE_ALERTS` e `ORDER_EVENT_ALERTS_BEHIND` com `detail = {"count": n}`;
  - `tests/integration/alert_support.py`: `RecordingSink` (atributos `sent: list[tuple[str, dict]]`, `failure: Exception | None`, `failing_keys: set[str] | None`).

- [ ] **Step 1: Write the test support and the failing tests**

`tests/integration/alert_support.py`:

```python
"""In-memory AlertSink for integration tests. Never touches the network."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class RecordingSink:
    name = "recording"

    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []
        self.failure: Exception | None = None
        self.failing_keys: set[str] | None = None  # None: `failure` applies to every alert

    def deliver(self, alert_key: str, document: Mapping[str, Any]) -> None:
        self.sent.append((alert_key, dict(document)))
        if self.failure is not None and (self.failing_keys is None or alert_key in self.failing_keys):
            raise self.failure
```

`tests/alerts/test_review_keys.py`:

```python
from datetime import UTC, date, datetime, timedelta

import pytest

from virtual_orders.alerts.outbox import backoff, review_session_day

RECORDED = datetime(2025, 11, 26, 2, 0, tzinfo=UTC)  # 21:00 ET on 2025-11-25


@pytest.mark.parametrize("ref, expected", [
    ("2025-11-24", date(2025, 11, 24)),  # DAILY_RANGE_MISMATCH / DIVIDEND_* refs are session dates
    ("2025-11-25T15:10:00+00:00", date(2025, 11, 25)),  # MISSING_BAR_* refs are minute instants
    ("2025-11-26T01:30:00+00:00", date(2025, 11, 25)),  # 20:30 ET still belongs to the ET day
    ("2025-11-25T10:10:00", date(2025, 11, 25)),  # a naive instant falls back to the recorded ET day
    ("look", date(2025, 11, 25)),  # free-form manual refs fall back to the recorded ET day
])
def test_review_session_day(ref, expected):
    assert review_session_day(ref, RECORDED) == expected


def test_backoff_doubles_from_one_minute_and_caps_at_thirty():
    assert [backoff(n) for n in range(8)] == [timedelta(0)] + [
        timedelta(minutes=m) for m in (1, 2, 4, 8, 16, 30, 30)
    ]
```

`tests/integration/test_alert_outbox.py`:

```python
import itertools
import json
from datetime import datetime, timedelta

import httpx
from sqlalchemy import select, text

from tests.integration.alert_support import RecordingSink
from tests.integration.support import CODE_VERSION, DAY, FakeBarSource, count, feeds, scenario_bars, submit_default
from tests.support import et
from virtual_orders.alerts import outbox
from virtual_orders.alerts.outbox import (
    DeliveryReport,
    deliver_pending_alerts,
    enqueue_event_alerts,
    enqueue_incident_alerts,
    enqueue_order_event_alerts,
    order_event_alerts_behind,
    undeliverable_alerts,
)
from virtual_orders.alerts.sink import AlertDeliveryFailed
from virtual_orders.evaluator.commands import flag_order_review
from virtual_orders.evaluator.cycle import run_live_cycle
from virtual_orders.evaluator.replay import reproduce_orders
from virtual_orders.ledger import errors
from virtual_orders.ledger.errors import LedgerIntegrityError
from virtual_orders.ledger.quarantine import record_incident
from virtual_orders.notify.n8n import N8nWebhook
from virtual_orders.readmodels.health import HealthState, Severity, build_health_report
from virtual_orders.storage import tables


def closed_order(engine):
    order_id = submit_default(engine).auto_order_id
    source = FakeBarSource(scenario_bars())
    for hm in ("10:30", "11:30", "13:00"):
        run_live_cycle(engine, feeds(source), code_version=CODE_VERSION, market_now=et(DAY, hm))
    return order_id


def outbox_rows(engine):
    with engine.connect() as conn:
        return conn.execute(select(tables.alert_outbox).order_by(tables.alert_outbox.c.id)).all()


def attempts(engine):
    with engine.connect() as conn:
        return conn.execute(select(tables.alert_delivery_attempts)
                            .order_by(tables.alert_delivery_attempts.c.id)).all()


def db_now(engine) -> datetime:
    with engine.connect() as conn:
        return conn.execute(text("SELECT clock_timestamp()")).scalar_one()


def order_keys(order_id):
    return [f"ORDER_EVENT:{order_id}:{key}" for key in ("FILLED", "TARGET1_HIT", "TARGET2_HIT")]


def health_at_8(engine):
    return build_health_report(engine, now=et(DAY, "08:00"), eval_interval_minutes=2)  # outside the session


def test_order_events_are_enqueued_once_reviews_collapse_per_session_and_replays_are_ignored(engine):
    order_id = closed_order(engine)
    flag_order_review(engine, order_id, reason="MANUAL", ref="2025-11-25")
    for hm in ("10:10", "10:11"):
        flag_order_review(engine, order_id, reason="MISSING_BAR_UNVERIFIABLE", ref=et(DAY, hm).isoformat())
    reproduce_orders(engine, code_version=CODE_VERSION, order_ids=[order_id])

    assert enqueue_order_event_alerts(engine) == 5
    assert enqueue_order_event_alerts(engine) == 0

    rows = outbox_rows(engine)
    assert [row.alert_key for row in rows] == order_keys(order_id) + [
        f"ORDER_REVIEW:{order_id}:MANUAL:2025-11-25",
        f"ORDER_REVIEW:{order_id}:MISSING_BAR_UNVERIFIABLE:2025-11-25",  # two flags, one alert (D33)
    ]
    document = rows[0].document
    assert document["schema_version"] == 1 and document["kind"] == "ORDER_EVENT"
    assert document["alert_key"] == rows[0].alert_key and document["order_id"] == str(order_id)
    assert (document["ticker"], document["strategy"], document["direction"]) == ("AAPL", "REXSHARE", "LONG")
    assert document["event"]["type"] == "FILLED" and document["event"]["price"] == "101"
    assert rows[0].subject == str(order_id)
    review = rows[4].document
    assert review["kind"] == "ORDER_REVIEW" and review["review"] == {
        "reason": "MISSING_BAR_UNVERIFIABLE", "session_day": "2025-11-25", "first_ref": et(DAY, "10:10").isoformat(),
    }
    assert count(engine, "alert_event_marks") == 6  # every scanned live event is marked, collapsed ones included


def test_events_older_than_the_lookback_are_not_alerted_and_surface_as_behind(engine, monkeypatch):
    order_id = closed_order(engine)
    assert enqueue_order_event_alerts(engine) == 3  # alerts are enabled: marks exist from here on
    flag_order_review(engine, order_id, reason="MANUAL", ref="2025-11-25")
    monkeypatch.setattr(outbox, "ORDER_EVENT_LOOKBACK", timedelta(0))

    assert enqueue_order_event_alerts(engine) == 0
    with engine.connect() as conn:
        assert order_event_alerts_behind(conn) == 1
    report = health_at_8(engine)
    behind = next(c for c in report.causes if c.code == "ORDER_EVENT_ALERTS_BEHIND")
    assert behind.severity is Severity.INFO and behind.detail == {"count": 1}
    assert report.state is HealthState.HEALTHY


def test_without_any_mark_nothing_is_behind(engine, monkeypatch):
    closed_order(engine)  # webhook never enabled: no scan, no marks
    monkeypatch.setattr(outbox, "ORDER_EVENT_LOOKBACK", timedelta(0))
    with engine.connect() as conn:
        assert order_event_alerts_behind(conn) == 0
    assert "ORDER_EVENT_ALERTS_BEHIND" not in {c.code for c in health_at_8(engine).causes}


def test_integrity_incidents_are_alerted_once_each_without_their_detail(engine):
    order_id = submit_default(engine).auto_order_id
    with engine.begin() as conn:
        record_incident(conn, LedgerIntegrityError(errors.PROJECTION_MISSING, "gone secret-detail", order_id=order_id))
        record_incident(conn, LedgerIntegrityError(errors.PROJECTION_MISSING, "orderless secret-detail"))

    assert enqueue_incident_alerts(engine) == 2
    assert enqueue_incident_alerts(engine) == 0

    with engine.connect() as conn:
        ids = list(conn.execute(select(tables.integrity_incidents.c.id).order_by(tables.integrity_incidents.c.id))
                   .scalars())
    rows = outbox_rows(engine)
    assert [row.alert_key for row in rows] == [f"INTEGRITY_INCIDENT:{incident_id}" for incident_id in ids]
    first, orderless = rows[0].document, rows[1].document
    assert first["kind"] == "INTEGRITY_INCIDENT" and first["incident_kind"] == errors.PROJECTION_MISSING
    assert first["order_id"] == str(order_id) and orderless["order_id"] is None
    assert "secret-detail" not in json.dumps([row.document for row in rows])


def test_enqueue_event_alerts_covers_orders_and_incidents(engine):
    order_id = closed_order(engine)
    with engine.begin() as conn:
        record_incident(conn, LedgerIntegrityError(errors.PROJECTION_MISSING, "gone", order_id=order_id))
    assert enqueue_event_alerts(engine) == 4
    assert enqueue_event_alerts(engine) == 0


def test_pending_alerts_are_delivered_once_in_order_even_after_a_restart(engine):
    order_id = closed_order(engine)
    enqueue_order_event_alerts(engine)
    sink = RecordingSink()

    assert deliver_pending_alerts(engine, sink) == DeliveryReport(delivered=3, failed=0)
    assert [key for key, _ in sink.sent] == [
        f"ORDER_EVENT:{order_id}:FILLED", f"ORDER_EVENT:{order_id}:TARGET1_HIT",
        f"ORDER_EVENT:{order_id}:TARGET2_HIT",
    ]
    restarted = RecordingSink()
    assert deliver_pending_alerts(engine, restarted) == DeliveryReport(delivered=0, failed=0)
    assert restarted.sent == []
    assert [row.outcome for row in attempts(engine)] == ["DELIVERED"] * 3


def test_a_failing_alert_backs_off_without_blocking_the_others(engine):
    order_id = closed_order(engine)
    enqueue_order_event_alerts(engine)
    filled, *others = order_keys(order_id)
    sink = RecordingSink()
    sink.failure = AlertDeliveryFailed(status_code=502, error_type="HTTPStatus")
    sink.failing_keys = {filled}
    start = db_now(engine)

    assert deliver_pending_alerts(engine, sink, now=start) == DeliveryReport(delivered=2, failed=1)
    assert [key for key, _ in sink.sent] == [filled, *others]  # the failure did not stop the run

    # Failure n waits backoff(n) = 1, 2, 4 minutes after the previous failed attempt.
    for minutes, expected in ((0.5, (0, 0)), (1, (0, 1)), (2.5, (0, 0)), (3, (0, 1)), (6.5, (0, 0))):
        report = deliver_pending_alerts(engine, sink, now=start + timedelta(minutes=minutes))
        assert report == DeliveryReport(*expected), minutes
    sink.failure = None
    assert deliver_pending_alerts(engine, sink, now=start + timedelta(minutes=7)) == DeliveryReport(1, 0)

    first = [a for a in attempts(engine) if a.alert_id == outbox_rows(engine)[0].id]
    assert [(a.outcome, a.status_code, a.error_type) for a in first] == [("FAILED", 502, "HTTPStatus")] * 3 + [
        ("DELIVERED", None, None)
    ]
    with engine.connect() as conn:
        assert undeliverable_alerts(conn) == 0


def test_alerts_older_than_a_day_are_expired_not_deleted_and_surface_as_info(engine):
    closed_order(engine)
    enqueue_order_event_alerts(engine)
    sink = RecordingSink()
    later = db_now(engine) + timedelta(hours=25)

    assert deliver_pending_alerts(engine, sink, now=later) == DeliveryReport(delivered=0, failed=0, expired=3)
    assert sink.sent == [] and len(outbox_rows(engine)) == 3
    assert [a.outcome for a in attempts(engine)] == ["EXPIRED"] * 3
    assert deliver_pending_alerts(engine, sink, now=later) == DeliveryReport(0, 0)  # expired once, never retried
    with engine.connect() as conn:
        assert undeliverable_alerts(conn) == 3
    report = health_at_8(engine)
    cause = next(c for c in report.causes if c.code == "UNDELIVERABLE_ALERTS")
    assert cause.severity is Severity.INFO and cause.detail == {"count": 3}
    assert report.state is HealthState.HEALTHY  # n8n is never in the critical path


def test_the_time_budget_leaves_the_rest_for_the_next_run(engine):
    order_id = closed_order(engine)
    enqueue_order_event_alerts(engine)
    ticks = itertools.count(0, 15)  # seconds: 0 at the start, 15 (< 20) before the 1st attempt, 30 before the 2nd
    sink = RecordingSink()

    report = deliver_pending_alerts(engine, sink, monotonic=lambda: float(next(ticks)))

    assert report == DeliveryReport(delivered=1, failed=0, budget_exhausted=True)
    assert [key for key, _ in sink.sent] == order_keys(order_id)[:1]
    assert deliver_pending_alerts(engine, RecordingSink()) == DeliveryReport(delivered=2, failed=0)


def test_unexpected_sink_errors_are_recorded_by_type_only(engine):
    closed_order(engine)
    enqueue_order_event_alerts(engine)
    sink = RecordingSink()
    sink.failure = RuntimeError("secret detail")
    assert deliver_pending_alerts(engine, sink) == DeliveryReport(delivered=0, failed=3)
    assert {(a.outcome, a.status_code, a.error_type) for a in attempts(engine)} == {("FAILED", None, "RuntimeError")}


def test_real_webhook_adapter_end_to_end_without_network(engine):
    requests: list[httpx.Request] = []
    client = httpx.Client(transport=httpx.MockTransport(lambda request: requests.append(request) or httpx.Response(204)))
    closed_order(engine)
    enqueue_order_event_alerts(engine)

    assert deliver_pending_alerts(engine, N8nWebhook(client, "https://n8n.test/hook")) == DeliveryReport(3, 0)

    body = json.loads(requests[0].content)
    assert body["alert_key"] == requests[0].headers["Idempotency-Key"]
    assert body["event"]["type"] == "FILLED" and body["event"]["price"] == "101"
    client.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/alerts/test_review_keys.py tests/integration/test_alert_outbox.py -q`
Expected: FAIL com `ModuleNotFoundError: virtual_orders.alerts.outbox`.

- [ ] **Step 3: Write `alerts/outbox.py`**

```python
"""Alert outbox (D24, D33, D35): append-only and idempotent by alert_key. Delivery is bounded by a time budget,
backed off per alert and expired by age; n8n is never in the critical path (spec 1.2 item 7)."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Connection, Engine, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from virtual_orders.alerts.sink import AlertDeliveryFailed, AlertSink
from virtual_orders.storage.codec import to_document
from virtual_orders.storage.tables import alert_delivery_attempts, alert_event_marks, alert_outbox

logger = logging.getLogger("virtual_orders.alerts")

ALERT_SCHEMA_VERSION = 1
DELIVERY_BATCH = 50
DELIVERY_TIME_BUDGET_SECONDS = 20.0  # checked before each attempt: a run lasts at most this plus one attempt
BACKOFF_CAP_MINUTES = 30
ALERT_EXPIRY = timedelta(hours=24)
UNDELIVERABLE_WINDOW = timedelta(days=7)
ORDER_EVENT_LOOKBACK = timedelta(days=4)  # a long weekend without re-sending the whole history on first start
ORDER_EVENT_SCAN_LIMIT = 500
ORDER_EVENT_ALERT_TYPES = (
    "FILLED", "TARGET1_HIT", "TARGET2_HIT", "STOPPED", "TIME_EXIT", "EXPIRED", "INVALIDATED", "NEEDS_REVIEW",
)
_MARKET_TZ = ZoneInfo("America/New_York")


class AlertKind(StrEnum):
    ORDER_EVENT = "ORDER_EVENT"
    ORDER_REVIEW = "ORDER_REVIEW"
    INTEGRITY_INCIDENT = "INTEGRITY_INCIDENT"
    PRICE_CROSS = "PRICE_CROSS"
    PRESSURE = "PRESSURE"
    HEALTH = "HEALTH"
    END_OF_DAY_SUMMARY = "END_OF_DAY_SUMMARY"


@dataclass(frozen=True)
class DeliveryReport:
    delivered: int
    failed: int
    expired: int = 0
    budget_exhausted: bool = False


def enqueue_alert(
    conn: Connection,
    *,
    alert_key: str,
    kind: AlertKind,
    document: Mapping[str, Any],
    subject: str | None = None,
    subject_ts: datetime | None = None,
) -> bool:
    """True when the alert is new. The same alert_key never produces a second row (idempotent across restarts)."""
    body = to_document({"schema_version": ALERT_SCHEMA_VERSION, "alert_key": alert_key, "kind": kind.value,
                        **dict(document)})
    stmt = (
        pg_insert(alert_outbox)
        .values(alert_key=alert_key, kind=kind.value, subject=subject, subject_ts=subject_ts, document=body)
        .on_conflict_do_nothing(index_elements=["alert_key"])
        .returning(alert_outbox.c.id)
    )
    return conn.execute(stmt).first() is not None


def last_alert_ts(conn: Connection, kind: AlertKind, subject: str) -> datetime | None:
    value: datetime | None = conn.execute(
        select(func.max(alert_outbox.c.subject_ts))
        .where(alert_outbox.c.kind == kind.value, alert_outbox.c.subject == subject)
    ).scalar_one()
    return value


def backoff(failures: int) -> timedelta:
    """D24: wait after the latest failed attempt: 1, 2, 4, 8, 16, then 30 minutes."""
    if failures <= 0:
        return timedelta(0)
    return timedelta(minutes=min(2 ** min(failures - 1, 10), BACKOFF_CAP_MINUTES))


def review_session_day(ref: str, recorded_at: datetime) -> date:
    """D33: the session a NEEDS_REVIEW flag is about.

    An ISO date ref is that date; a timezone-aware ISO instant ref is its ET date; anything else falls back to the ET
    date the flag was recorded.
    """
    try:
        if len(ref) == 10:
            return date.fromisoformat(ref)
        moment = datetime.fromisoformat(ref)
    except ValueError:
        return recorded_at.astimezone(_MARKET_TZ).date()
    if moment.tzinfo is None:
        return recorded_at.astimezone(_MARKET_TZ).date()
    return moment.astimezone(_MARKET_TZ).date()


_NEW_ORDER_EVENTS = text(
    """
    SELECT e.id AS event_id, e.order_id, e.seq, e.event_key, e.type, e.bar_ts, e.price, e.qty, e.payload,
           e.recorded_at, o.origin, o.price_source, g.ticker, g.strategy, g.direction
    FROM order_events e
    JOIN orders o ON o.id = e.order_id
    JOIN signals g ON g.id = o.signal_id
    WHERE NOT o.replay AND e.type = ANY(:types) AND e.recorded_at >= :since
      AND NOT EXISTS (SELECT 1 FROM alert_event_marks m WHERE m.order_event_id = e.id)
    ORDER BY e.id
    LIMIT :limit
    """
)


def _order_event_alert(row: Any) -> tuple[str, AlertKind, dict[str, Any]]:
    if row.type == "NEEDS_REVIEW":  # D33: one alert per (order, reason, session)
        reason = str(row.payload.get("reason", "UNSPECIFIED"))
        ref = str(row.payload.get("ref", ""))
        session_day = review_session_day(ref, row.recorded_at)
        return (f"ORDER_REVIEW:{row.order_id}:{reason}:{session_day.isoformat()}", AlertKind.ORDER_REVIEW,
                {"review": {"reason": reason, "session_day": session_day, "first_ref": ref}})
    return f"ORDER_EVENT:{row.order_id}:{row.event_key}", AlertKind.ORDER_EVENT, {}


def enqueue_order_event_alerts(engine: Engine, *, limit: int = ORDER_EVENT_SCAN_LIMIT) -> int:
    """Marks every scanned alertable event exactly once (alert_event_marks), so collapsed reviews are never rescanned."""
    created = 0
    with engine.begin() as conn:
        now: datetime = conn.execute(text("SELECT clock_timestamp()")).scalar_one()
        rows = conn.execute(_NEW_ORDER_EVENTS, {
            "types": list(ORDER_EVENT_ALERT_TYPES), "since": now - ORDER_EVENT_LOOKBACK, "limit": limit,
        }).all()
        for row in rows:
            alert_key, kind, extra = _order_event_alert(row)
            document = {
                "order_id": row.order_id, "ticker": row.ticker, "strategy": row.strategy, "direction": row.direction,
                "origin": row.origin, "price_source": row.price_source, "recorded_at": row.recorded_at,
                "event": {"seq": row.seq, "type": row.type, "event_key": row.event_key, "bar_ts": row.bar_ts,
                          "price": row.price, "qty": row.qty, "payload": row.payload},
                **extra,
            }
            if enqueue_alert(conn, alert_key=alert_key, kind=kind, document=document, subject=str(row.order_id)):
                created += 1
            conn.execute(
                pg_insert(alert_event_marks).values(order_event_id=row.event_id, alert_key=alert_key)
                .on_conflict_do_nothing(index_elements=["order_event_id"])
            )
    return created


_NEW_INCIDENTS = text(
    """
    SELECT i.id, i.kind, i.order_id, i.event_key, i.recorded_at
    FROM integrity_incidents i
    WHERE i.recorded_at >= :since
      AND NOT EXISTS (SELECT 1 FROM alert_outbox a WHERE a.alert_key = 'INTEGRITY_INCIDENT:' || i.id::text)
    ORDER BY i.id
    LIMIT :limit
    """
)


def enqueue_incident_alerts(engine: Engine, *, limit: int = ORDER_EVENT_SCAN_LIMIT) -> int:
    """Spec 3.3: every integrity incident alerts once, orderless and replay ones included. Never its free-text detail."""
    created = 0
    with engine.begin() as conn:
        now: datetime = conn.execute(text("SELECT clock_timestamp()")).scalar_one()
        for row in conn.execute(_NEW_INCIDENTS, {"since": now - ORDER_EVENT_LOOKBACK, "limit": limit}).all():
            document = {"incident_id": row.id, "incident_kind": row.kind, "order_id": row.order_id,
                        "event_key": row.event_key, "recorded_at": row.recorded_at}
            if enqueue_alert(conn, alert_key=f"INTEGRITY_INCIDENT:{row.id}", kind=AlertKind.INTEGRITY_INCIDENT,
                             document=document, subject=None if row.order_id is None else str(row.order_id)):
                created += 1
    return created


def enqueue_event_alerts(engine: Engine) -> int:
    """The D24 enqueue step: runs in deliver_alerts and right after the opening and end-of-day jobs."""
    return enqueue_order_event_alerts(engine) + enqueue_incident_alerts(engine)


_EXPIRE = text(
    """
    INSERT INTO alert_delivery_attempts (alert_id, outcome, attempted_at)
    SELECT a.id, 'EXPIRED', :now
    FROM alert_outbox a
    WHERE a.created_at < :expiry_cutoff
      AND NOT EXISTS (
          SELECT 1 FROM alert_delivery_attempts t WHERE t.alert_id = a.id AND t.outcome IN ('DELIVERED', 'EXPIRED')
      )
    RETURNING alert_id
    """
)

_CANDIDATES = text(
    """
    SELECT a.id, a.alert_key, a.document,
           COUNT(t.id) FILTER (WHERE t.outcome = 'FAILED') AS failures,
           MAX(t.attempted_at) FILTER (WHERE t.outcome = 'FAILED') AS last_failed_at
    FROM alert_outbox a
    LEFT JOIN alert_delivery_attempts t ON t.alert_id = a.id
    WHERE a.created_at >= :expiry_cutoff
    GROUP BY a.id
    HAVING COUNT(t.id) FILTER (WHERE t.outcome IN ('DELIVERED', 'EXPIRED')) = 0
    ORDER BY a.id
    """
)

_UNDELIVERABLE = text(
    """
    SELECT COUNT(DISTINCT t.alert_id) FROM alert_delivery_attempts t
    WHERE t.outcome = 'EXPIRED' AND t.attempted_at >= clock_timestamp() - :window
    """
)

_BEHIND = text(
    """
    SELECT COUNT(*)
    FROM order_events e
    JOIN orders o ON o.id = e.order_id
    WHERE NOT o.replay AND e.type = ANY(:types)
      AND e.recorded_at < clock_timestamp() - :lookback
      AND e.recorded_at >= (SELECT min(m.recorded_at) FROM alert_event_marks m)
      AND NOT EXISTS (SELECT 1 FROM alert_event_marks m WHERE m.order_event_id = e.id)
    """
)


def deliver_pending_alerts(
    engine: Engine,
    sink: AlertSink,
    *,
    limit: int = DELIVERY_BATCH,
    now: datetime | None = None,
    time_budget_seconds: float = DELIVERY_TIME_BUDGET_SECONDS,
    monotonic: Callable[[], float] = time.monotonic,
) -> DeliveryReport:
    """D24: expire by age, then one attempt per due alert (oldest first, per-alert backoff) within a time budget.

    `now` defaults to the database clock read once per run; it is also the `attempted_at` of every row this run writes,
    so backoff compares instants from a single clock. A failure never stops the run: only the budget does.
    """
    started = monotonic()
    with engine.begin() as conn:
        instant: datetime = now if now is not None else conn.execute(text("SELECT clock_timestamp()")).scalar_one()
        cutoff = instant - ALERT_EXPIRY
        expired = len(conn.execute(_EXPIRE, {"now": instant, "expiry_cutoff": cutoff}).all())
        rows = conn.execute(_CANDIDATES, {"expiry_cutoff": cutoff}).all()
    due = [row for row in rows
           if row.last_failed_at is None or instant >= row.last_failed_at + backoff(int(row.failures))][:limit]
    delivered = failed = 0
    budget_exhausted = False
    for row in due:
        if monotonic() - started >= time_budget_seconds:
            budget_exhausted = True  # the rest stays due for the next run
            break
        status_code: int | None = None
        error_type: str | None = None
        try:
            sink.deliver(row.alert_key, row.document)
        except AlertDeliveryFailed as exc:
            status_code, error_type = exc.status_code, exc.error_type
        except Exception as exc:  # noqa: BLE001 - n8n is never in the critical path (spec 1.2 item 7)
            error_type = type(exc).__name__
        outcome = "DELIVERED" if error_type is None else "FAILED"
        with engine.begin() as conn:
            conn.execute(alert_delivery_attempts.insert().values(
                alert_id=row.id, outcome=outcome, status_code=status_code, error_type=error_type,
                attempted_at=instant,
            ))
        if outcome == "DELIVERED":
            delivered += 1
        else:
            failed += 1
            logger.warning("alert %s delivery failed via %s: %s", row.id, sink.name, error_type)
    return DeliveryReport(delivered, failed, expired, budget_exhausted)


def undeliverable_alerts(conn: Connection) -> int:
    """Alerts expired within UNDELIVERABLE_WINDOW (INFO in /health, count in the end-of-day summary)."""
    return int(conn.execute(_UNDELIVERABLE, {"window": UNDELIVERABLE_WINDOW}).scalar_one())


def order_event_alerts_behind(conn: Connection) -> int:
    """D35: unmarked alertable events that aged past the lookback after alerting started (no marks -> 0)."""
    return int(conn.execute(_BEHIND, {
        "types": list(ORDER_EVENT_ALERT_TYPES), "lookback": ORDER_EVENT_LOOKBACK,
    }).scalar_one())
```

- [ ] **Step 4: Surface alerting problems in `/health` as `INFO`**

Em `src/virtual_orders/readmodels/health.py`:

1. Acrescente o import `from virtual_orders.alerts.outbox import order_event_alerts_behind, undeliverable_alerts`.
2. Acrescente como **últimos** campos de `HealthSnapshot` (depois de `missing_runs`):

```python
    undeliverable_alerts: int | None = None  # alerts expired within 7 days (D24); INFO only
    order_event_alerts_behind: int | None = None  # unmarked events past the enqueue lookback (D35); INFO only
```

3. Em `evaluate_health`, logo depois do bloco `if snapshot.needs_review:`, acrescente:

```python
    if snapshot.undeliverable_alerts:  # n8n is never in the critical path: visible, never degrading
        causes.append(Cause("UNDELIVERABLE_ALERTS", Severity.INFO, {"count": snapshot.undeliverable_alerts}))
    if snapshot.order_event_alerts_behind:
        causes.append(Cause("ORDER_EVENT_ALERTS_BEHIND", Severity.INFO, {"count": snapshot.order_event_alerts_behind}))
```

4. Em `collect_health_snapshot`, logo depois de `missing_runs=missing_job_runs(conn, now=now),`, acrescente:

```python
        undeliverable_alerts=undeliverable_alerts(conn),
        order_event_alerts_behind=order_event_alerts_behind(conn),
```

Causas `INFO` ficam fora da assinatura da D25, então nunca geram alerta de `/health` nem tiram o estado de `HEALTHY`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/alerts/test_review_keys.py tests/integration/test_alert_outbox.py tests/readmodels tests/integration/api/test_health_api.py tests/integration/api/test_health_hardening.py tests/test_import_boundaries.py -q && uv run ruff check src tests migrations && uv run mypy`
Expected: PASS (`test_fresh_system_is_healthy_…` continua com `causes == []`: contagens zero não geram causa).

- [ ] **Step 6: Commit**

```bash
git add src/virtual_orders/alerts/outbox.py src/virtual_orders/readmodels/health.py tests/alerts/__init__.py tests/alerts/test_review_keys.py tests/integration/alert_support.py tests/integration/test_alert_outbox.py
git commit -m "feat(alerts): idempotent outbox with review collapsing, incident alerts, backoff, expiry and budgeted delivery"
```

---

### Task 13: Watchlist, regras de alerta e ciclo da watchlist (entrada 4, D26, D27)

**Files:**
- Create: `src/virtual_orders/alerts/watchlist.py`, `src/virtual_orders/alerts/rules.py`, `src/virtual_orders/alerts/watch.py`
- Test: `tests/alerts/test_rules.py`, `tests/integration/test_watchlist_alerts.py` (o pacote `tests/alerts/` é criado na Task 12)

**Interfaces:**
- Consumes: `estimate_pressure`, `strong_pressure`, `PressureEstimate`, `PressureSide`, `METHOD`, `DISCLAIMER` (Task 11); `AlertKind`, `enqueue_alert`, `last_alert_ts` (Task 12); tabelas `watchlist`, `alert_rules` (Task 8); `RunKind.WATCHLIST`; `ingest_bars`, `read_bars_as_of`, `acquire_data_as_of`, `floor_minute`, `calendar_for_window`.
- Produces (usado pelas Tasks 15 e 16):
  - `watchlist.py`: `RuleKind`, `CrossDirection`, `AlertRuleInvalid(errors: list[str])`, `WatchlistTickerNotFound`, `AlertRuleNotFound`, `RuleDraft(kind, level, direction, cmf_threshold, window_bars, cooldown_minutes)`, `AlertRule(id, ticker, kind, level, direction, cmf_threshold, window_bars, cooldown_minutes, created_at)`, `parse_rule_body(body: Mapping[str, Any]) -> RuleDraft`, `add_ticker(conn, ticker: str, *, added_at: datetime) -> bool`, `is_watched(conn, ticker: str) -> bool`, `remove_ticker(conn, ticker: str) -> None`, `watchlist_tickers(conn) -> list[str]`, `create_rule(conn, ticker: str, draft: RuleDraft, *, created_at: datetime) -> AlertRule`, `delete_rule(conn, rule_id: UUID) -> None`, `list_rules(conn) -> list[AlertRule]`, `list_watchlist(conn) -> list[dict[str, Any]]`;
  - `rules.py`: `price_crossings(bars: Sequence[Bar], *, level: Decimal, direction: str, cooldown: timedelta, last_alert_ts: datetime | None = None) -> list[Bar]`, `pressure_alert(bars: Sequence[Bar], *, cmf_threshold: Decimal, window_bars: int, cooldown: timedelta, last_alert_ts: datetime | None = None) -> tuple[PressureEstimate, PressureSide] | None`;
  - `watch.py`: `WATCH_GRACE = timedelta(minutes=5)`, `WatchlistReport(run_id: UUID | None, tickers: tuple[str, ...] = (), ingest_failures: dict[str, str] = {}, enqueued: tuple[str, ...] = (), rule_errors: dict[str, str] = {})`, `watch_session(now: datetime) -> Session | None`, `first_minute_at_or_after(ts: datetime) -> datetime`, `run_watchlist_cycle(engine: Engine, gateway: MarketDataGateway, *, price_source: str, code_version: str, market_now: datetime, alerts_enabled: bool) -> WatchlistReport`.

- [ ] **Step 1: Write the failing unit tests**

`tests/alerts/test_rules.py`:

```python
from datetime import timedelta
from decimal import Decimal

import pytest

from tests.support import bar, et
from virtual_orders.alerts.rules import pressure_alert, price_crossings
from virtual_orders.alerts.watchlist import AlertRuleInvalid, CrossDirection, RuleDraft, RuleKind, parse_rule_body
from virtual_orders.analytics.pressure import PressureSide

DAY = "2025-11-25"


def closes(*values):
    return [bar(et(DAY, f"10:{i:02d}"), v, v, v, v) for i, v in enumerate(values)]


def test_parse_price_cross_accepts_json_integers_and_defaults_the_cooldown():
    assert parse_rule_body({"kind": "PRICE_CROSS", "level": 101, "direction": "ABOVE"}) == RuleDraft(
        RuleKind.PRICE_CROSS, Decimal("101"), CrossDirection.ABOVE, None, None, 30)


def test_parse_pressure_rule():
    body = {"kind": "PRESSURE", "cmf_threshold": Decimal("0.3"), "window_bars": 20, "cooldown_minutes": 0}
    assert parse_rule_body(body) == RuleDraft(RuleKind.PRESSURE, None, None, Decimal("0.3"), 20, 0)


@pytest.mark.parametrize("body, errors", [
    ({"kind": "PRICE_CROSS", "level": -1, "direction": "UP", "window_bars": 10, "extra": 1},
     ["UNKNOWN_FIELD:extra", "INVALID_CHOICE:direction", "OUT_OF_RANGE:level", "NOT_ALLOWED:window_bars"]),
    ({"kind": "PRESSURE"}, ["MISSING_FIELD:cmf_threshold", "MISSING_FIELD:window_bars"]),
    ({"kind": "PRESSURE", "cmf_threshold": Decimal("1"), "window_bars": 4, "level": 5},
     ["OUT_OF_RANGE:cmf_threshold", "OUT_OF_RANGE:window_bars", "NOT_ALLOWED:level"]),
    ({"kind": "VOLUME"}, ["INVALID_CHOICE:kind"]),
    ({"kind": "PRICE_CROSS", "level": True, "direction": "ABOVE"}, ["INVALID_DECIMAL:level"]),
    ({"kind": "PRICE_CROSS", "level": 1.5, "direction": "ABOVE"}, ["INVALID_DECIMAL:level"]),
    ({"kind": "PRICE_CROSS", "direction": "BELOW", "cooldown_minutes": 2000},
     ["OUT_OF_RANGE:cooldown_minutes", "MISSING_FIELD:level"]),
])
def test_parse_rejects_invalid_rules_with_every_error(body, errors):
    with pytest.raises(AlertRuleInvalid) as caught:
        parse_rule_body(body)
    assert caught.value.errors == errors


def test_price_crossings_use_closes_only_and_the_first_bar_never_crosses():
    bars = closes(101.5, 100, 101.2, 100.5, 102)
    assert [b.ts for b in price_crossings(bars, level=Decimal("101"), direction="ABOVE",
                                          cooldown=timedelta(0))] == [et(DAY, "10:02"), et(DAY, "10:04")]
    assert [b.ts for b in price_crossings(bars, level=Decimal("101"), direction="BELOW",
                                          cooldown=timedelta(0))] == [et(DAY, "10:01"), et(DAY, "10:03")]


def test_intrabar_touch_is_not_a_crossing():
    touched = [bar(et(DAY, "10:00"), 100, 100, 100, 100), bar(et(DAY, "10:01"), 100, 102, 99, 100.5)]
    assert price_crossings(touched, level=Decimal("101"), direction="ABOVE", cooldown=timedelta(0)) == []


def test_cooldown_suppresses_repeated_crossings_including_across_calls():
    bars = closes(100, 101.5, 100, 101.5)
    assert [b.ts for b in price_crossings(bars, level=Decimal("101"), direction="ABOVE",
                                          cooldown=timedelta(minutes=5))] == [et(DAY, "10:01")]
    assert price_crossings(bars, level=Decimal("101"), direction="ABOVE", cooldown=timedelta(minutes=5),
                           last_alert_ts=et(DAY, "10:00")) == []


def rising(count):
    return [bar(et(DAY, f"09:{30 + i}"), 100 + i, 100.5 + i, 99.8 + i, 100.5 + i) for i in range(count)]


def test_pressure_alert_needs_a_full_window_and_respects_the_cooldown():
    assert pressure_alert(rising(4), cmf_threshold=Decimal("0.5"), window_bars=5, cooldown=timedelta(0)) is None
    found = pressure_alert(rising(7), cmf_threshold=Decimal("0.5"), window_bars=5, cooldown=timedelta(0))
    assert found is not None
    estimate, side = found
    assert side is PressureSide.BUY and estimate.bars == 5 and estimate.first_bar_ts == et(DAY, "09:32")
    assert pressure_alert(rising(7), cmf_threshold=Decimal("0.5"), window_bars=5, cooldown=timedelta(minutes=30),
                          last_alert_ts=et(DAY, "09:20")) is None
```

- [ ] **Step 2: Write the failing integration tests**

`tests/integration/test_watchlist_alerts.py`:

```python
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select

from tests.integration.support import CODE_VERSION, DAY, PRICE_SOURCE, FakeBarSource, count, feeds, flat_raw, raw
from tests.support import et
from virtual_orders.alerts import watch as watch_module
from virtual_orders.alerts.watch import first_minute_at_or_after, run_watchlist_cycle
from virtual_orders.alerts.watchlist import add_ticker, create_rule, parse_rule_body
from virtual_orders.ledger.runs import RunStatus, latest_run_status
from virtual_orders.storage import tables

MSFT = "MSFT"
CREATED = et(DAY, "09:00")


def crossing_bars():
    return (
        flat_raw(DAY, "09:30", "10:00", 100, MSFT)
        + [raw(DAY, "10:00", 100, 101.6, 99.9, 101.5, ticker=MSFT)]
        + flat_raw(DAY, "10:01", "10:10", 101.5, MSFT)
        + [raw(DAY, "10:10", 101.5, 101.5, 100.4, 100.5, ticker=MSFT),
           raw(DAY, "10:11", 100.5, 101.3, 100.5, 101.2, ticker=MSFT)]
        + flat_raw(DAY, "10:12", "10:40", 101.2, MSFT)
    )


def add_rule(engine, body, created_at=CREATED):
    with engine.begin() as conn:
        add_ticker(conn, MSFT, added_at=created_at)
        return create_rule(conn, MSFT, parse_rule_body(body), created_at=created_at)


def cross(direction="ABOVE", cooldown=0):
    return {"kind": "PRICE_CROSS", "level": Decimal("101"), "direction": direction, "cooldown_minutes": cooldown}


def cycle(engine, source, hm, alerts_enabled=True):
    return run_watchlist_cycle(engine, feeds(source), price_source=PRICE_SOURCE, code_version=CODE_VERSION,
                               market_now=et(DAY, hm), alerts_enabled=alerts_enabled)


def key(kind, rule, hm):
    return f"{kind}:{rule.id}:{et(DAY, hm).isoformat()}"


def documents(engine):
    with engine.connect() as conn:
        return {row.alert_key: row.document for row in conn.execute(select(tables.alert_outbox))}


def test_first_minute_at_or_after():
    assert first_minute_at_or_after(et(DAY, "10:10")) == et(DAY, "10:10")
    assert first_minute_at_or_after(et(DAY, "10:10", 30)) == et(DAY, "10:11")


def test_price_crossings_are_enqueued_once_with_cooldown(engine):
    fast, slow, down = add_rule(engine, cross()), add_rule(engine, cross(cooldown=30)), add_rule(engine, cross("BELOW"))
    source = FakeBarSource(crossing_bars())

    report = cycle(engine, source, "10:30")

    assert set(report.enqueued) == {
        key("PRICE_CROSS", fast, "10:00"), key("PRICE_CROSS", fast, "10:11"),
        key("PRICE_CROSS", slow, "10:00"), key("PRICE_CROSS", down, "10:10"),
    }
    document = documents(engine)[key("PRICE_CROSS", fast, "10:00")]
    assert document["kind"] == "PRICE_CROSS" and document["ticker"] == MSFT and document["direction"] == "ABOVE"
    assert Decimal(document["level"]) == Decimal("101") and Decimal(document["bar"]["close"]) == Decimal("101.5")
    assert document["price_source"] == PRICE_SOURCE and document["run_id"] == str(report.run_id)

    assert cycle(engine, source, "10:32").enqueued == ()
    assert count(engine, "alert_outbox") == 4


def test_a_rule_never_alerts_on_bars_before_its_creation_but_uses_the_previous_close(engine):
    late = add_rule(engine, cross(), created_at=et(DAY, "10:10", 30))
    assert cycle(engine, FakeBarSource(crossing_bars()), "10:30").enqueued == (key("PRICE_CROSS", late, "10:11"),)


def test_ingestion_resumes_from_the_last_stored_bar_and_failures_are_recorded(engine):
    add_rule(engine, cross())
    source = FakeBarSource(crossing_bars())
    cycle(engine, source, "10:00")
    cycle(engine, source, "10:05")
    assert source.calls == [(MSFT, et(DAY, "09:30"), et(DAY, "10:00")), (MSFT, et(DAY, "10:00"), et(DAY, "10:05"))]

    source.failing.add(MSFT)
    report = cycle(engine, source, "10:30")

    assert report.ingest_failures == {f"{PRICE_SOURCE}:{MSFT}": "SourceUnavailable"} and report.enqueued == ()
    with engine.connect() as conn:
        status, detail = latest_run_status(conn, report.run_id)
    assert status is RunStatus.COMPLETED and detail["ingest_failures"] == report.ingest_failures


def test_outside_a_session_or_without_tickers_nothing_runs(engine):
    source = FakeBarSource(crossing_bars())
    assert cycle(engine, source, "10:30").run_id is None  # empty watchlist
    add_rule(engine, cross())
    assert cycle(engine, source, "08:00").run_id is None and source.calls == []
    assert count(engine, "evaluation_runs") == 0


def test_alerts_disabled_still_ingests_but_enqueues_nothing(engine):
    add_rule(engine, cross())
    source = FakeBarSource(crossing_bars())
    report = cycle(engine, source, "10:30", alerts_enabled=False)
    assert report.run_id is not None and report.enqueued == () and count(engine, "alert_outbox") == 0
    assert source.calls and count(engine, "bars_1m") > 0


def test_pressure_rule_is_labelled_as_an_estimate(engine):
    rule = add_rule(engine, {"kind": "PRESSURE", "cmf_threshold": Decimal("0.5"), "window_bars": 5})
    bars = [raw(DAY, f"09:3{i}", 100 + i, 100.5 + i, 99.8 + i, 100.5 + i, ticker=MSFT) for i in range(5)]
    source = FakeBarSource(bars)

    assert cycle(engine, source, "09:34").enqueued == ()  # four closed bars: window not full
    report = cycle(engine, source, "09:40")

    assert report.enqueued == (key("PRESSURE", rule, "09:34"),)
    document = documents(engine)[report.enqueued[0]]
    assert document["estimate"] is True and document["side"] == "BUY"
    assert document["method"] == "OHLCV_PRESSURE_ESTIMATE_V1" and "not order-flow" in document["disclaimer"]
    assert Decimal(document["values"]["chaikin_money_flow"]) == Decimal("1")
    assert datetime.fromisoformat(document["values"]["last_bar_ts"]) == et(DAY, "09:34")


def test_one_failing_rule_never_stops_the_others(engine, monkeypatch):
    broken = add_rule(engine, cross())
    working = add_rule(engine, cross("BELOW"))
    original = watch_module.price_crossings

    def exploding(bars, *, level, direction, cooldown, last_alert_ts=None):
        if direction == "ABOVE":
            raise RuntimeError("boom")
        return original(bars, level=level, direction=direction, cooldown=cooldown, last_alert_ts=last_alert_ts)

    monkeypatch.setattr(watch_module, "price_crossings", exploding)
    report = cycle(engine, FakeBarSource(crossing_bars()), "10:30")

    assert report.rule_errors == {str(broken.id): "ERROR:RuntimeError"}
    assert report.enqueued == (key("PRICE_CROSS", working, "10:10"),)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/alerts/test_rules.py tests/integration/test_watchlist_alerts.py -q`
Expected: FAIL com `ModuleNotFoundError: virtual_orders.alerts.rules` / `virtual_orders.alerts.watchlist`.

- [ ] **Step 4: Write `alerts/watchlist.py`**

```python
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
    return raw


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
```

- [ ] **Step 5: Write `alerts/rules.py`**

```python
"""Pure alert rules over closed 1-minute bars (D26, D27). Close-to-close only: never infers an intrabar path."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal

from core.domain.models import Bar
from virtual_orders.analytics.pressure import PressureEstimate, PressureSide, estimate_pressure, strong_pressure

ABOVE = "ABOVE"
BELOW = "BELOW"


def _cooled(ts: datetime, last: datetime | None, cooldown: timedelta) -> bool:
    return last is None or ts - last >= cooldown


def price_crossings(
    bars: Sequence[Bar], *, level: Decimal, direction: str, cooldown: timedelta, last_alert_ts: datetime | None = None
) -> list[Bar]:
    """ABOVE: previous close < level <= close. BELOW: previous close > level >= close. The first bar has no previous."""
    if direction not in (ABOVE, BELOW):
        raise ValueError(f"unknown crossing direction: {direction}")
    crossings: list[Bar] = []
    previous: Decimal | None = None
    last = last_alert_ts
    for item in sorted(bars, key=lambda b: b.ts):
        if previous is not None:
            crossed = previous < level <= item.close if direction == ABOVE else previous > level >= item.close
            if crossed and _cooled(item.ts, last, cooldown):
                crossings.append(item)
                last = item.ts
        previous = item.close
    return crossings


def pressure_alert(
    bars: Sequence[Bar],
    *,
    cmf_threshold: Decimal,
    window_bars: int,
    cooldown: timedelta,
    last_alert_ts: datetime | None = None,
) -> tuple[PressureEstimate, PressureSide] | None:
    """Strong pressure over the latest `window_bars` bars; None without a full window or inside the cooldown."""
    window = sorted(bars, key=lambda b: b.ts)[-window_bars:]
    if len(window) < window_bars:
        return None
    estimate = estimate_pressure(window)
    if estimate is None:
        return None
    side = strong_pressure(estimate, cmf_threshold)
    if side is None or not _cooled(estimate.last_bar_ts, last_alert_ts, cooldown):
        return None
    return estimate, side
```

- [ ] **Step 6: Write `alerts/watch.py`**

```python
"""Watchlist ingestion and alert-rule evaluation for the worker's own `watchlist` job (D20, D26, D27). No fallback."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Engine, text

from core.domain.calendar import ONE_MINUTE, Session
from core.domain.models import Bar
from virtual_orders.alerts.outbox import AlertKind, enqueue_alert, last_alert_ts
from virtual_orders.alerts.rules import pressure_alert, price_crossings
from virtual_orders.alerts.watchlist import AlertRule, RuleKind, list_rules, watchlist_tickers
from virtual_orders.analytics.pressure import DISCLAIMER, METHOD
from virtual_orders.evaluator.clock import require_aware
from virtual_orders.evaluator.outcomes import ERROR_PREFIX
from virtual_orders.ledger.runs import RunInfo, RunKind, RunStatus, finish_run, start_run
from virtual_orders.marketdata.asof import acquire_data_as_of, floor_minute, read_bars_as_of
from virtual_orders.marketdata.calendars import calendar_for_window
from virtual_orders.marketdata.gateway import MarketDataGateway, UnknownDataSource
from virtual_orders.marketdata.ingest import ingest_bars
from virtual_orders.marketdata.sources import BarSource, SourceDataError, SourceUnavailable

WATCH_GRACE = timedelta(minutes=5)

_LATEST_BAR = text(
    "SELECT max(ts) FROM bars_1m WHERE ticker = :ticker AND source = :source AND ts >= :start AND ts < :end"
)


@dataclass(frozen=True)
class WatchlistReport:
    run_id: UUID | None
    tickers: tuple[str, ...] = ()
    ingest_failures: dict[str, str] = field(default_factory=dict)
    enqueued: tuple[str, ...] = ()
    rule_errors: dict[str, str] = field(default_factory=dict)


def watch_session(now: datetime) -> Session | None:
    return next((s for s in calendar_for_window(now, now).sessions
                 if s.open_utc <= now <= s.close_utc + WATCH_GRACE), None)


def first_minute_at_or_after(ts: datetime) -> datetime:
    floored = floor_minute(ts)
    return floored if floored == ts.astimezone(UTC) else floored + ONE_MINUTE


def _bar_document(item: Bar) -> dict[str, Any]:
    return {"ts": item.ts, "open": item.open, "high": item.high, "low": item.low, "close": item.close,
            "volume": item.volume, "batch_id": item.batch_id}


def _evaluate_rule(conn: Connection, rule: AlertRule, bars: list[Bar], run: RunInfo, price_source: str) -> list[str]:
    usable_from = first_minute_at_or_after(rule.created_at)  # D1 spirit: never a bar that straddles the creation
    cooldown = timedelta(minutes=rule.cooldown_minutes)
    base = {"rule_id": rule.id, "ticker": rule.ticker, "price_source": price_source, "data_as_of": run.data_as_of,
            "run_id": run.run_id}
    keys: list[str] = []
    if rule.kind is RuleKind.PRICE_CROSS:
        if rule.level is None or rule.direction is None:
            raise ValueError(f"price-cross rule {rule.id} without level or direction")
        before = [item for item in bars if item.ts < usable_from]
        window = before[-1:] + [item for item in bars if item.ts >= usable_from]  # the prior bar only feeds the close
        last = last_alert_ts(conn, AlertKind.PRICE_CROSS, str(rule.id))
        for item in price_crossings(window, level=rule.level, direction=rule.direction.value, cooldown=cooldown,
                                    last_alert_ts=last):
            alert_key = f"PRICE_CROSS:{rule.id}:{item.ts.isoformat()}"
            document = {**base, "level": rule.level, "direction": rule.direction, "bar": _bar_document(item)}
            if enqueue_alert(conn, alert_key=alert_key, kind=AlertKind.PRICE_CROSS, document=document,
                             subject=str(rule.id), subject_ts=item.ts):
                keys.append(alert_key)
        return keys
    if rule.cmf_threshold is None or rule.window_bars is None:
        raise ValueError(f"pressure rule {rule.id} without threshold or window")
    found = pressure_alert([item for item in bars if item.ts >= usable_from], cmf_threshold=rule.cmf_threshold,
                           window_bars=rule.window_bars, cooldown=cooldown,
                           last_alert_ts=last_alert_ts(conn, AlertKind.PRESSURE, str(rule.id)))
    if found is None:
        return keys
    estimate, side = found
    alert_key = f"PRESSURE:{rule.id}:{estimate.last_bar_ts.isoformat()}"
    document = {**base, "estimate": True, "method": METHOD, "disclaimer": DISCLAIMER, "side": side,
                "cmf_threshold": rule.cmf_threshold, "window_bars": rule.window_bars, "values": asdict(estimate)}
    if enqueue_alert(conn, alert_key=alert_key, kind=AlertKind.PRESSURE, document=document, subject=str(rule.id),
                     subject_ts=estimate.last_bar_ts):
        keys.append(alert_key)
    return keys


def _ingest(engine: Engine, gateway: MarketDataGateway, price_source: str, tickers: tuple[str, ...],
            session: Session, end: datetime) -> dict[str, str]:
    failures: dict[str, str] = {}
    try:
        source: BarSource | None = gateway.bar_source(price_source)
    except UnknownDataSource:
        source = None
    for ticker in tickers:
        feed = f"{price_source}:{ticker}"
        if source is None:
            failures[feed] = "UNKNOWN_DATA_SOURCE"
            continue
        with engine.connect() as conn:
            latest: datetime | None = conn.execute(_LATEST_BAR, {
                "ticker": ticker, "source": price_source, "start": session.open_utc, "end": end,
            }).scalar_one()
        begin = session.open_utc if latest is None else latest + ONE_MINUTE
        try:
            ingest_bars(engine, source, ticker, begin, end)
        except (SourceUnavailable, SourceDataError) as exc:
            failures[feed] = type(exc).__name__
    return failures


def run_watchlist_cycle(
    engine: Engine,
    gateway: MarketDataGateway,
    *,
    price_source: str,
    code_version: str,
    market_now: datetime,
    alerts_enabled: bool,
) -> WatchlistReport:
    now = require_aware(market_now, "market_now")
    session = watch_session(now)
    if session is None:
        return WatchlistReport(None)
    with engine.connect() as conn:
        tickers = tuple(watchlist_tickers(conn))
        rules = list_rules(conn) if alerts_enabled else []
    if not tickers:
        return WatchlistReport(None)
    end = min(floor_minute(now), session.close_utc)
    failures = _ingest(engine, gateway, price_source, tickers, session, end)
    data_as_of = acquire_data_as_of(engine)
    run: RunInfo | None = None
    try:
        with engine.begin() as conn:
            run = start_run(conn, RunKind.WATCHLIST, data_as_of, code_version,
                            detail={"tickers": list(tickers), "price_source": price_source, "market_now": now})
        enqueued: list[str] = []
        rule_errors: dict[str, str] = {}
        for rule in rules:
            if f"{price_source}:{rule.ticker}" in failures:
                continue
            try:
                with engine.begin() as conn:
                    bars = read_bars_as_of(conn, rule.ticker, price_source, session.open_utc, end, run.data_as_of)
                    keys = _evaluate_rule(conn, rule, bars, run, price_source)
                enqueued.extend(keys)
            except Exception as exc:  # noqa: BLE001 - one rule never stops the others (reported in the run detail)
                rule_errors[str(rule.id)] = f"{ERROR_PREFIX}{type(exc).__name__}"
        with engine.begin() as conn:
            finish_run(conn, run.run_id, RunStatus.COMPLETED, {
                "tickers": list(tickers), "price_source": price_source, "market_now": now,
                "ingest_failures": failures, "alerts_enabled": alerts_enabled, "enqueued": len(enqueued),
                "rule_errors": rule_errors,
            })
        return WatchlistReport(run.run_id, tickers, failures, tuple(enqueued), rule_errors)
    except Exception as exc:
        if run is not None:
            with engine.begin() as conn:
                finish_run(conn, run.run_id, RunStatus.FAILED, {"error": repr(exc), "market_now": now})
        raise
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/alerts tests/integration/test_watchlist_alerts.py tests/integration/test_alert_outbox.py tests/test_import_boundaries.py -q && uv run ruff check src tests migrations && uv run mypy`
Expected: PASS, inclusive `test_platform_pure_modules_have_no_io_or_infrastructure_imports`, que agora verifica também `virtual_orders/alerts/rules.py`.

- [ ] **Step 8: Commit**

```bash
git add src/virtual_orders/alerts/watchlist.py src/virtual_orders/alerts/rules.py src/virtual_orders/alerts/watch.py tests/alerts/test_rules.py tests/integration/test_watchlist_alerts.py
git commit -m "feat(alerts): watchlist rules, close-based level crossings and pressure alerts with watchlist ingestion"
```

---

### Task 14: Vigia de transições de `/health` e resumo de fim de dia (entrada 3, D25, D32)

**Files:**
- Create: `src/virtual_orders/alerts/health_watch.py`, `src/virtual_orders/alerts/summary.py`
- Test: `tests/integration/test_health_watch.py`

**Interfaces:**
- Consumes: `build_health_report`, `HealthReport`, `HealthState`, `Severity` (Task 9); `AlertKind`, `enqueue_alert`, `undeliverable_alerts` (Task 12); `AlertSink`; tabela `health_state_log` (Task 8); `EndOfDayReport` (`evaluator/quality.py`); `RecheckReport` (Task 10); `latest_run_status`; `to_document`.
- Produces (usado pela Task 16):
  - `HealthSignature(state: HealthState, cause_codes: tuple[str, ...])`; `signature(report: HealthReport) -> HealthSignature`; `should_alert(previous: HealthSignature | None, current: HealthSignature) -> bool` (D25 e D32: também `HEALTHY` logo depois de `DEGRADED`/`UNHEALTHY`); documento com `recovered: bool`;
  - `HealthObservation(state: HealthState, transitioned: bool, alerted: bool)`;
  - `HealthWatcher(engine: Engine, *, eval_interval_minutes: int)` com `observe(self, now: datetime, sink: AlertSink | None) -> HealthObservation`;
  - `enqueue_end_of_day_summary(engine: Engine, report: EndOfDayReport, *, session_day: date, health_state: HealthState | None, recheck: RecheckReport | None) -> bool` (chave `END_OF_DAY_SUMMARY:{session_day}:{cycle_run_id}`; `False` quando o ciclo foi pulado ou o resumo já existe).

- [ ] **Step 1: Write the failing tests**

`tests/integration/test_health_watch.py`:

```python
from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from tests.integration.alert_support import RecordingSink
from tests.integration.support import (
    CODE_VERSION,
    DAY,
    FakeBarSource,
    FakeReference,
    count,
    feeds,
    scenario_bars,
    submit_default,
)
from tests.support import et
from virtual_orders.alerts import health_watch as health_watch_module
from virtual_orders.alerts.health_watch import HealthSignature, HealthWatcher, should_alert
from virtual_orders.alerts.summary import enqueue_end_of_day_summary
from virtual_orders.evaluator.quality import run_end_of_day
from virtual_orders.evaluator.rebuild import rebuild_projection
from virtual_orders.ledger import errors
from virtual_orders.ledger.errors import LedgerIntegrityError
from virtual_orders.ledger.orders import delete_projection
from virtual_orders.ledger.quarantine import record_incident
from virtual_orders.readmodels.health import HealthState, database_unavailable
from virtual_orders.storage import tables
from virtual_orders.storage.database import make_engine

NOW = et(DAY, "08:00")  # outside the session: no staleness noise
SESSION = date(2025, 11, 25)


def outbox(engine):
    with engine.connect() as conn:
        return conn.execute(select(tables.alert_outbox).order_by(tables.alert_outbox.c.id)).all()


def log(engine):
    with engine.connect() as conn:
        return conn.execute(select(tables.health_state_log).order_by(tables.health_state_log.c.id)).all()


def incident(engine, order_id):
    with engine.begin() as conn:
        record_incident(conn, LedgerIntegrityError(errors.PROJECTION_MISSING, "gone", order_id=order_id))


def test_should_alert_rules():
    healthy = HealthSignature(HealthState.HEALTHY, ())
    degraded = HealthSignature(HealthState.DEGRADED, ("INTEGRITY_INCIDENTS",))
    wider = HealthSignature(HealthState.DEGRADED, ("FROZEN_ORDERS", "INTEGRITY_INCIDENTS"))
    narrower = HealthSignature(HealthState.DEGRADED, ("FROZEN_ORDERS",))
    unhealthy = HealthSignature(HealthState.UNHEALTHY, ("DATABASE_UNAVAILABLE",))
    assert should_alert(None, degraded) and should_alert(healthy, degraded) and should_alert(degraded, wider)
    assert should_alert(degraded, healthy) and should_alert(unhealthy, healthy)  # D32: recovery
    assert not should_alert(None, healthy) and not should_alert(healthy, healthy)
    assert not should_alert(degraded, degraded) and not should_alert(wider, narrower)


def test_transitions_are_logged_and_alerted_once_even_after_a_restart(engine):
    sink = RecordingSink()
    watcher = HealthWatcher(engine, eval_interval_minutes=2)

    first = watcher.observe(NOW, sink)
    assert (first.state, first.transitioned, first.alerted) == (HealthState.HEALTHY, True, False)
    assert watcher.observe(NOW, sink).transitioned is False

    order_id = submit_default(engine).auto_order_id
    incident(engine, order_id)
    degraded = watcher.observe(NOW, sink)
    assert (degraded.state, degraded.transitioned, degraded.alerted) == (HealthState.DEGRADED, True, True)
    assert watcher.observe(NOW, sink).alerted is False
    assert HealthWatcher(engine, eval_interval_minutes=2).observe(NOW, sink).alerted is False  # restart

    with engine.begin() as conn:
        delete_projection(conn, order_id)  # a new DEGRADED cause while already degraded
    assert watcher.observe(NOW, sink).alerted is True

    rows = log(engine)
    assert [row.state for row in rows] == ["HEALTHY", "DEGRADED", "DEGRADED"]
    alerts = outbox(engine)
    assert [row.alert_key for row in alerts] == [f"HEALTH:{rows[1].id}", f"HEALTH:{rows[2].id}"]
    document = alerts[1].document
    assert document["state"] == "DEGRADED" and document["previous_state"] == "DEGRADED"
    assert {cause["code"] for cause in document["causes"]} >= {"INTEGRITY_INCIDENTS", "PROJECTION_MISSING_ORDERS"}
    assert all(set(cause) == {"code", "severity"} for cause in document["causes"])
    assert sink.sent == []  # persisted alerts go through the outbox, never straight to the sink


def test_disabled_webhook_still_logs_transitions_without_enqueueing(engine):
    watcher = HealthWatcher(engine, eval_interval_minutes=2)
    incident(engine, submit_default(engine).auto_order_id)
    observation = watcher.observe(NOW, None)
    assert observation.transitioned is True and observation.alerted is False
    assert count(engine, "health_state_log") == 1 and count(engine, "alert_outbox") == 0


def test_return_to_healthy_sends_one_recovery_alert(engine):
    sink = RecordingSink()
    watcher = HealthWatcher(engine, eval_interval_minutes=2)
    order_id = submit_default(engine).auto_order_id
    with engine.begin() as conn:
        delete_projection(conn, order_id)
    assert watcher.observe(NOW, sink).alerted is True

    rebuild_projection(engine, order_id)  # the missing projection is restored: every cause clears
    recovered = watcher.observe(NOW, sink)

    assert (recovered.state, recovered.transitioned, recovered.alerted) == (HealthState.HEALTHY, True, True)
    assert watcher.observe(NOW, sink).alerted is False
    rows = log(engine)
    assert [row.state for row in rows] == ["DEGRADED", "HEALTHY"]
    alerts = outbox(engine)
    assert [row.alert_key for row in alerts] == [f"HEALTH:{rows[0].id}", f"HEALTH:{rows[1].id}"]
    assert alerts[0].document["recovered"] is False
    assert (alerts[1].document["state"], alerts[1].document["previous_state"], alerts[1].document["recovered"]) == (
        "HEALTHY", "DEGRADED", True,
    )


def test_recovery_after_a_database_outage_is_logged_and_alerted_through_the_outbox(engine, monkeypatch):
    sink = RecordingSink()
    watcher = HealthWatcher(engine, eval_interval_minutes=2)
    outage = OperationalError("SELECT 1", {}, Exception("down"))

    def unreachable(*args, **kwargs):
        return database_unavailable(outage)

    def log_unavailable(conn):
        raise outage

    with monkeypatch.context() as patch:
        patch.setattr(health_watch_module, "build_health_report", unreachable)
        patch.setattr(health_watch_module, "_latest", log_unavailable)
        assert watcher.observe(NOW, sink).alerted is True  # direct, in memory

    recovered = watcher.observe(NOW, sink)

    assert (recovered.state, recovered.transitioned, recovered.alerted) == (HealthState.HEALTHY, True, True)
    ((direct_key, _),) = sink.sent
    assert direct_key.startswith("HEALTH_DIRECT:UNHEALTHY:")
    (row,) = outbox(engine)
    assert (row.document["state"], row.document["previous_state"], row.document["recovered"]) == (
        "HEALTHY", "UNHEALTHY", True,
    )
    assert [entry.state for entry in log(engine)] == ["HEALTHY"]


def test_unreachable_database_alerts_directly_once_without_leaking_the_host():
    unreachable = make_engine("postgresql+psycopg://vo:vo@127.0.0.1:1/unreachable")
    sink = RecordingSink()
    watcher = HealthWatcher(unreachable, eval_interval_minutes=2)
    try:
        first = watcher.observe(NOW, sink)
        second = watcher.observe(NOW, sink)
    finally:
        unreachable.dispose()
    assert (first.state, first.alerted, second.alerted) == (HealthState.UNHEALTHY, True, False)
    ((key, document),) = sink.sent
    assert key.startswith("HEALTH_DIRECT:UNHEALTHY:")
    assert document["causes"] == [{"code": "DATABASE_UNAVAILABLE", "severity": "UNHEALTHY"}]
    assert "127.0.0.1" not in str(document)


def test_end_of_day_summary_is_enqueued_once_per_cycle_run(engine):
    order_id = submit_default(engine, valid_sessions=1).auto_order_id
    report = run_end_of_day(engine, gateway=feeds(FakeBarSource(scenario_bars())), reference=FakeReference(),
                            session_day=SESSION, code_version=CODE_VERSION, market_now=et(DAY, "16:30"))

    assert enqueue_end_of_day_summary(engine, report, session_day=SESSION, health_state=HealthState.HEALTHY,
                                      recheck=None) is True
    assert enqueue_end_of_day_summary(engine, report, session_day=SESSION, health_state=HealthState.HEALTHY,
                                      recheck=None) is False

    (row,) = outbox(engine)
    assert row.alert_key == f"END_OF_DAY_SUMMARY:{SESSION.isoformat()}:{report.cycle.run_id}"
    document = row.document
    assert document["session_day"] == "2025-11-25" and document["health_state"] == "HEALTHY"
    assert document["cycle_orders"] == 1 and document["quality_orders"] == 1
    assert document["events"]["TARGET2_HIT"] == 1 and document["events"]["DATA_QUALITY"] == 1
    assert document["not_evaluated"] == 0 and document["ingest_failures"] == []
    assert document["rechecked"] == 0 and document["recheck_terminal"] == 0 and document["undeliverable_alerts"] == 0
    assert UUID(document["quality_run_id"]) == report.quality.run_id
    assert order_id is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/integration/test_health_watch.py -q`
Expected: FAIL com `ModuleNotFoundError: virtual_orders.alerts.health_watch`.

- [ ] **Step 3: Write `alerts/health_watch.py`**

```python
"""/health transition alerts (D25): one log row and at most one alert per signature change, never per cycle."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, Engine, select
from sqlalchemy.exc import SQLAlchemyError

from virtual_orders.alerts.outbox import AlertKind, enqueue_alert
from virtual_orders.alerts.sink import AlertSink
from virtual_orders.readmodels.health import HealthReport, HealthState, Severity, build_health_report
from virtual_orders.storage.codec import to_document
from virtual_orders.storage.tables import health_state_log

logger = logging.getLogger("virtual_orders.alerts")
ALERT_STATES = frozenset({HealthState.DEGRADED, HealthState.UNHEALTHY})


@dataclass(frozen=True)
class HealthSignature:
    state: HealthState
    cause_codes: tuple[str, ...]


@dataclass(frozen=True)
class HealthObservation:
    state: HealthState
    transitioned: bool
    alerted: bool


def signature(report: HealthReport) -> HealthSignature:
    codes = sorted({cause.code for cause in report.causes if cause.severity is not Severity.INFO})
    return HealthSignature(report.state, tuple(codes))


def _recovering(previous: HealthSignature | None, current: HealthSignature) -> bool:
    return current.state is HealthState.HEALTHY and previous is not None and previous.state in ALERT_STATES


def should_alert(previous: HealthSignature | None, current: HealthSignature) -> bool:
    if current.state not in ALERT_STATES:
        return _recovering(previous, current)  # D32: the owner learns the incident ended
    if previous is None or previous.state is not current.state:
        return True
    return bool(set(current.cause_codes) - set(previous.cause_codes))


def _document(report: HealthReport, previous: HealthSignature | None, now: datetime) -> dict[str, Any]:
    return {
        "state": report.state, "previous_state": None if previous is None else previous.state,
        "recovered": _recovering(previous, signature(report)),
        "causes": [{"code": cause.code, "severity": cause.severity} for cause in report.causes],
        "observed_at": now,
    }


def _latest(conn: Connection) -> HealthSignature | None:
    row = conn.execute(
        select(health_state_log.c.state, health_state_log.c.cause_codes)
        .order_by(health_state_log.c.id.desc()).limit(1)
    ).first()
    return None if row is None else HealthSignature(HealthState(row.state), tuple(row.cause_codes))


class HealthWatcher:
    def __init__(self, engine: Engine, *, eval_interval_minutes: int) -> None:
        self._engine = engine
        self._eval_interval_minutes = eval_interval_minutes
        self._memory: HealthSignature | None = None

    def observe(self, now: datetime, sink: AlertSink | None) -> HealthObservation:
        report = build_health_report(self._engine, now=now, eval_interval_minutes=self._eval_interval_minutes)
        current = signature(report)
        try:
            with self._engine.begin() as conn:
                logged = _latest(conn)
                # A signature seen only in memory (database unreachable) is the real previous state, so the
                # recovery from an outage is logged and alerted like any other transition (D25, D32).
                previous = self._memory if self._memory is not None and self._memory != logged else logged
                if previous == current and logged == current:
                    self._memory = current
                    return HealthObservation(report.state, False, False)
                log_id = conn.execute(
                    health_state_log.insert().values(state=current.state.value, cause_codes=list(current.cause_codes))
                    .returning(health_state_log.c.id)
                ).scalar_one()
                transitioned = previous != current
                alerted = sink is not None and transitioned and should_alert(previous, current) and enqueue_alert(
                    conn, alert_key=f"HEALTH:{log_id}", kind=AlertKind.HEALTH,
                    document=_document(report, previous, now), subject_ts=now,
                )
            self._memory = current
            return HealthObservation(report.state, transitioned, alerted)
        except SQLAlchemyError as exc:
            # Database down or schema off head: no log row is possible, so dedupe in memory and alert directly.
            logger.warning("health log unavailable (%s); using in-memory transition tracking", type(exc).__name__)
            previous_memory, self._memory = self._memory, current
            if previous_memory == current:
                return HealthObservation(report.state, False, False)
            if sink is None or not should_alert(previous_memory, current):
                return HealthObservation(report.state, True, False)
            try:
                sink.deliver(f"HEALTH_DIRECT:{current.state.value}:{now.isoformat()}",
                             to_document(_document(report, previous_memory, now)))
            except Exception as delivery_error:  # noqa: BLE001 - n8n is never in the critical path
                logger.warning("direct health alert failed via %s: %s", sink.name, type(delivery_error).__name__)
                return HealthObservation(report.state, True, False)
            return HealthObservation(report.state, True, True)
```

- [ ] **Step 4: Write `alerts/summary.py`**

```python
"""End-of-day webhook summary (spec 5.3 "webhook opcional com resumo", D24). Counts and codes only."""

from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Any

from sqlalchemy import Engine

from virtual_orders.alerts.outbox import AlertKind, enqueue_alert, undeliverable_alerts
from virtual_orders.evaluator.outcomes import ERROR_PREFIX, OrderOutcome
from virtual_orders.evaluator.quality import EndOfDayReport
from virtual_orders.evaluator.recheck import RecheckReport
from virtual_orders.ledger.runs import latest_run_status
from virtual_orders.readmodels.health import HealthState


def _event_counts(outcomes: list[OrderOutcome]) -> dict[str, int]:
    return dict(sorted(Counter(key.split(":", 1)[0] for outcome in outcomes for key in outcome.event_keys).items()))


def _error_counts(outcomes: list[OrderOutcome]) -> dict[str, int]:
    errors = [outcome.error for outcome in outcomes if outcome.error is not None]
    return {
        "integrity_errors": sum(1 for error in errors if not error.startswith(ERROR_PREFIX)),
        "order_errors": sum(1 for error in errors if error.startswith(ERROR_PREFIX)),
    }


def enqueue_end_of_day_summary(
    engine: Engine,
    report: EndOfDayReport,
    *,
    session_day: date,
    health_state: HealthState | None,
    recheck: RecheckReport | None,
) -> bool:
    if report.cycle.skipped or report.cycle.run_id is None:
        return False
    quality_outcomes = [] if report.quality is None else list(report.quality.outcomes)
    outcomes = list(report.cycle.outcomes) + list(report.expired) + quality_outcomes
    with engine.begin() as conn:
        not_evaluated = 0
        if report.quality is not None:
            _, detail = latest_run_status(conn, report.quality.run_id)
            not_evaluated = len(detail.get("not_evaluated", {}))
        document: dict[str, Any] = {
            "session_day": session_day,
            "cycle_run_id": report.cycle.run_id,
            "quality_run_id": None if report.quality is None else report.quality.run_id,
            "cycle_orders": len(report.cycle.outcomes),
            "expired_orders": len(report.expired),
            "quality_orders": len(quality_outcomes),
            "events": _event_counts(outcomes),
            **_error_counts(outcomes),
            "ingest_failures": sorted(report.cycle.ingest_failures),
            "reference_unavailable": [] if report.quality is None else sorted(report.quality.unavailable),
            "not_evaluated": not_evaluated,
            "rechecked": 0 if recheck is None else len(recheck.rechecked),
            "recheck_not_evaluated": 0 if recheck is None else len(recheck.not_evaluated),
            "recheck_terminal": 0 if recheck is None else len(recheck.terminal),
            "health_state": health_state,
            "undeliverable_alerts": undeliverable_alerts(conn),
        }
        return enqueue_alert(
            conn, alert_key=f"END_OF_DAY_SUMMARY:{session_day.isoformat()}:{report.cycle.run_id}",
            kind=AlertKind.END_OF_DAY_SUMMARY, document=document,
        )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/integration/test_health_watch.py tests/test_import_boundaries.py -q && uv run ruff check src tests migrations && uv run mypy`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/virtual_orders/alerts/health_watch.py src/virtual_orders/alerts/summary.py tests/integration/test_health_watch.py
git commit -m "feat(alerts): health transition alerts and end-of-day summary through the outbox"
```

---

### Task 15: Rotas autenticadas da watchlist e das regras de alerta (entrada 4, D26)

**Files:**
- Create: `src/virtual_orders/api/routes/watchlist.py`
- Modify: `src/virtual_orders/api/app.py` (inclui o router), `src/virtual_orders/api/errors.py` (mapeamentos)
- Modify: `tests/integration/api/test_signals_api.py` (`test_every_route_rejects_a_missing_or_wrong_key`)
- Test: `tests/integration/api/test_watchlist_api.py`

**Interfaces:**
- Consumes: `parse_rule_body`, `add_ticker`, `is_watched`, `remove_ticker`, `create_rule`, `delete_rule`, `list_watchlist`, `AlertRuleInvalid`, `WatchlistTickerNotFound`, `AlertRuleNotFound` (Task 13); `ServicesDep`, `json_response`, `read_json_object`, `ApiError` (3A); `Services.ticker_check`, `Services.clock`.
- Produces: `GET /watchlist` → `{"watchlist": [{"ticker", "added_at", "rules": [...]}]}`; `PUT /watchlist/{ticker}` → `201 {"ticker", "status": "CREATED"}` / `200 {"status": "EXISTING"}` / `422 TICKER_NOT_TRADABLE` (`reason` = motivo do `TickerStatus`) / `503 TICKER_UNVERIFIABLE`; `DELETE /watchlist/{ticker}` → `200 {"ticker", "removed": true}` / `404 WATCHLIST_TICKER_NOT_FOUND`; `POST /watchlist/{ticker}/alerts` → `201 {"rule": {...}}` / `422 ALERT_RULE_INVALID` (`detail.errors`) / `404 WATCHLIST_TICKER_NOT_FOUND`; `DELETE /alerts/{rule_id}` → `200 {"rule_id", "removed": true}` / `404 ALERT_RULE_NOT_FOUND`. O corpo é validado antes de consultar o ticker (um corpo inválido é 422 mesmo para ticker desconhecido). Em toda rota com `{ticker}`, o ticker é normalizado (`strip().upper()`) antes de qualquer consulta; vazio ou com espaço interno → `422 TICKER_INVALID` (`detail.ticker` = texto recebido).

- [ ] **Step 1: Write the failing tests**

`tests/integration/api/test_watchlist_api.py`:

```python
from decimal import Decimal

from tests.integration.api.conftest import post_json
from tests.integration.support import count


def rule_body(**overrides):
    body = {"kind": "PRICE_CROSS", "level": Decimal("101.50"), "direction": "ABOVE"}
    body.update(overrides)
    return body


def test_adding_a_ticker_checks_it_once_and_is_idempotent(api):
    first = api.client.put("/watchlist/MSFT")
    assert first.status_code == 201 and first.json() == {"ticker": "MSFT", "status": "CREATED"}
    again = api.client.put("/watchlist/MSFT")
    assert again.status_code == 200 and again.json() == {"ticker": "MSFT", "status": "EXISTING"}
    assert api.tickers.calls == ["MSFT"]
    assert api.client.get("/watchlist").json() == {
        "watchlist": [{"ticker": "MSFT", "added_at": api.clock.now.isoformat(), "rules": []}]
    }


def test_untradable_is_422_and_unverifiable_is_503_without_storing(api):
    api.tickers.untradable["ZZZZ"] = "INACTIVE"
    untradable = api.client.put("/watchlist/ZZZZ")
    assert untradable.status_code == 422
    assert untradable.json()["error"] == {"code": "TICKER_NOT_TRADABLE", "reason": "INACTIVE",
                                          "detail": {"ticker": "ZZZZ"}}
    api.tickers.failing = True
    unavailable = api.client.put("/watchlist/MSFT")
    assert unavailable.status_code == 503 and unavailable.json()["error"]["code"] == "TICKER_UNVERIFIABLE"
    assert "(fake)" not in unavailable.text
    assert count(api.services.engine, "watchlist") == 0


def test_rules_are_created_listed_and_deleted_with_exact_decimals(api):
    api.client.put("/watchlist/MSFT")
    created = post_json(api.client, "/watchlist/MSFT/alerts", rule_body())
    assert created.status_code == 201
    rule = created.json()["rule"]
    assert (rule["ticker"], rule["kind"], rule["level"], rule["direction"]) == ("MSFT", "PRICE_CROSS", "101.5", "ABOVE")
    assert rule["cooldown_minutes"] == 30 and rule["created_at"] == api.clock.now.isoformat()
    pressure = post_json(api.client, "/watchlist/MSFT/alerts",
                         {"kind": "PRESSURE", "cmf_threshold": Decimal("0.35"), "window_bars": 20, "cooldown_minutes": 0})
    assert pressure.status_code == 201 and pressure.json()["rule"]["cmf_threshold"] == "0.35"

    listed = api.client.get("/watchlist").json()["watchlist"][0]["rules"]
    assert {item["id"] for item in listed} == {rule["id"], pressure.json()["rule"]["id"]}

    removed = api.client.delete(f"/alerts/{rule['id']}")
    assert removed.status_code == 200 and removed.json() == {"rule_id": rule["id"], "removed": True}
    again = api.client.delete(f"/alerts/{rule['id']}")
    assert again.status_code == 404 and again.json()["error"]["code"] == "ALERT_RULE_NOT_FOUND"


def test_invalid_rules_unknown_tickers_and_bad_json(api):
    invalid = post_json(api.client, "/watchlist/MSFT/alerts", {"kind": "PRICE_CROSS"})
    assert invalid.status_code == 422
    assert invalid.json()["error"] == {"code": "ALERT_RULE_INVALID", "reason": None,
                                       "detail": {"errors": ["MISSING_FIELD:level", "MISSING_FIELD:direction"]}}
    unknown = post_json(api.client, "/watchlist/MSFT/alerts", rule_body())
    assert unknown.status_code == 404 and unknown.json()["error"]["code"] == "WATCHLIST_TICKER_NOT_FOUND"
    missing = api.client.delete("/watchlist/MSFT")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "WATCHLIST_TICKER_NOT_FOUND"
    bad_json = api.client.post("/watchlist/MSFT/alerts", content="[1]", headers={"Content-Type": "application/json"})
    assert bad_json.status_code == 422 and bad_json.json()["error"]["code"] == "INVALID_JSON"
    bad_id = api.client.delete("/alerts/not-a-uuid")
    assert bad_id.status_code == 422 and bad_id.json()["error"]["code"] == "REQUEST_INVALID"


def test_removing_a_ticker_removes_its_rules(api):
    api.client.put("/watchlist/MSFT")
    post_json(api.client, "/watchlist/MSFT/alerts", rule_body())
    removed = api.client.delete("/watchlist/MSFT")
    assert removed.status_code == 200 and removed.json() == {"ticker": "MSFT", "removed": True}
    assert count(api.services.engine, "alert_rules") == 0 and api.client.get("/watchlist").json() == {"watchlist": []}


def test_tickers_are_normalized_and_blank_ones_rejected(api):
    created = api.client.put("/watchlist/%20msft%20")
    assert created.status_code == 201 and created.json() == {"ticker": "MSFT", "status": "CREATED"}
    assert api.tickers.calls == ["MSFT"]
    assert post_json(api.client, "/watchlist/msft/alerts", rule_body()).json()["rule"]["ticker"] == "MSFT"

    for raw_ticker in ("%20", "BRK%20B"):
        rejected = api.client.put(f"/watchlist/{raw_ticker}")
        assert rejected.status_code == 422 and rejected.json()["error"]["code"] == "TICKER_INVALID"
    assert api.tickers.calls == ["MSFT"]  # an invalid ticker never reaches the provider
    assert api.client.delete("/watchlist/msft").json() == {"ticker": "MSFT", "removed": True}
```

Em `tests/integration/api/test_signals_api.py`, no teste `test_every_route_rejects_a_missing_or_wrong_key`, troque:

```python
        path = route.path.replace("{signal_id}", str(uuid4())).replace("{order_id}", str(uuid4()))
```

por:

```python
        path = (route.path.replace("{signal_id}", str(uuid4())).replace("{order_id}", str(uuid4()))
                .replace("{rule_id}", str(uuid4())).replace("{ticker}", "AAPL"))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/integration/api/test_watchlist_api.py -q`
Expected: FAIL (as rotas não existem: `404 NOT_FOUND`/`405 METHOD_NOT_ALLOWED`).

- [ ] **Step 3: Write the router**

`src/virtual_orders/api/routes/watchlist.py`:

```python
"""Watchlist and alert-rule management (owner 2026-09-13, D26). Alerts themselves leave only through n8n."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request, Response
from starlette.concurrency import run_in_threadpool

from virtual_orders.alerts.watchlist import (
    AlertRule,
    add_ticker,
    create_rule,
    delete_rule,
    is_watched,
    list_watchlist,
    parse_rule_body,
    remove_ticker,
)
from virtual_orders.api.deps import ServicesDep
from virtual_orders.api.encoding import json_response, read_json_object
from virtual_orders.api.errors import ApiError
from virtual_orders.marketdata.sources import SourceDataError, SourceUnavailable
from virtual_orders.services import Services

router = APIRouter()


def _normalized(ticker: str) -> str:
    """D26: stripped and upper-cased, so the result satisfies the signal rule (core TICKER_INVALID)."""
    normalized = ticker.strip().upper()
    if not normalized or any(character.isspace() for character in normalized):
        raise ApiError(422, "TICKER_INVALID", detail={"ticker": ticker})
    return normalized


def _require_tradable(services: Services, ticker: str) -> None:
    """Same gate as POST /signals (D14): only for a ticker not yet watched; never echoes provider text."""
    try:
        status = services.ticker_check.check_ticker(ticker)
    except (SourceUnavailable, SourceDataError) as exc:
        raise ApiError(503, "TICKER_UNVERIFIABLE", detail={
            "ticker": ticker, "check": services.ticker_check.name, "error": type(exc).__name__,
        }) from exc
    if not status.tradable:
        raise ApiError(422, "TICKER_NOT_TRADABLE", status.reason, {"ticker": ticker})


def _add(services: Services, ticker: str) -> bool:
    with services.engine.connect() as conn:
        if is_watched(conn, ticker):
            return False
    _require_tradable(services, ticker)
    with services.engine.begin() as conn:
        return add_ticker(conn, ticker, added_at=services.clock())


def _create(services: Services, ticker: str, body: dict[str, Any]) -> AlertRule:
    draft = parse_rule_body(body)  # validated before any lookup
    with services.engine.begin() as conn:
        return create_rule(conn, ticker, draft, created_at=services.clock())


@router.get("/watchlist")
def get_watchlist(services: ServicesDep) -> Response:
    with services.engine.connect() as conn:
        return json_response({"watchlist": list_watchlist(conn)})


@router.put("/watchlist/{ticker}")
def put_watchlist_ticker(ticker: str, services: ServicesDep) -> Response:
    normalized = _normalized(ticker)
    created = _add(services, normalized)
    return json_response({"ticker": normalized, "status": "CREATED" if created else "EXISTING"},
                         201 if created else 200)


@router.delete("/watchlist/{ticker}")
def delete_watchlist_ticker(ticker: str, services: ServicesDep) -> Response:
    normalized = _normalized(ticker)
    with services.engine.begin() as conn:
        remove_ticker(conn, normalized)
    return json_response({"ticker": normalized, "removed": True})


@router.post("/watchlist/{ticker}/alerts")
async def post_alert_rule(ticker: str, request: Request, services: ServicesDep) -> Response:
    normalized = _normalized(ticker)
    body = await read_json_object(request)
    rule = await run_in_threadpool(_create, services, normalized, body)
    return json_response({"rule": rule}, 201)


@router.delete("/alerts/{rule_id}")
def delete_alert_rule(rule_id: UUID, services: ServicesDep) -> Response:
    with services.engine.begin() as conn:
        delete_rule(conn, rule_id)
    return json_response({"rule_id": rule_id, "removed": True})
```

- [ ] **Step 4: Map the errors and include the router**

Em `src/virtual_orders/api/errors.py`, acrescente o import:

```python
from virtual_orders.alerts.watchlist import AlertRuleInvalid, AlertRuleNotFound, WatchlistTickerNotFound
```

em `to_api_error`, logo depois do bloco de `ReplaySelectionError`:

```python
    if isinstance(exc, AlertRuleInvalid):
        return ApiError(422, "ALERT_RULE_INVALID", detail={"errors": exc.errors})
    if isinstance(exc, WatchlistTickerNotFound):
        return ApiError(404, "WATCHLIST_TICKER_NOT_FOUND")
    if isinstance(exc, AlertRuleNotFound):
        return ApiError(404, "ALERT_RULE_NOT_FOUND")
```

e substitua `HANDLED` por:

```python
HANDLED = (
    ApiError, SignalValidationError, IdempotencyConflict, ManualOrderError, SignalNotFound, OrderNotFound,
    OrderAlreadyFinal, ReplayOrderReadOnly, ReplaySelectionError, AlertRuleInvalid, WatchlistTickerNotFound,
    AlertRuleNotFound, LedgerIntegrityError, RequestValidationError, StarletteHTTPException, Exception,
)
```

Em `src/virtual_orders/api/app.py`, troque o import das rotas por `from virtual_orders.api.routes import health, metrics, orders, replay, signals, watchlist` e acrescente `app.include_router(watchlist.router)` depois de `app.include_router(health.router)`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/integration/api tests/test_api_errors.py tests/test_import_boundaries.py -q && uv run ruff check src tests migrations && uv run mypy`
Expected: PASS (inclusive `test_every_route_rejects_a_missing_or_wrong_key` sobre as cinco rotas novas e `test_api_never_imports_adapters_provider_libraries_or_the_composition_root[virtual_orders/api/routes/watchlist.py]`).

- [ ] **Step 6: Commit**

```bash
git add src/virtual_orders/api/routes/watchlist.py src/virtual_orders/api/app.py src/virtual_orders/api/errors.py tests/integration/api/test_watchlist_api.py tests/integration/api/test_signals_api.py
git commit -m "feat(api): authenticated watchlist and alert-rule routes"
```

---

### Task 16: Worker — agenda, jobs, processo e CLI (`run`, `rebuild-projections`) (entradas 1 e 7, D20, D21, D24, D26, D36, D39)

**Files:**
- Create: `src/virtual_orders/worker/schedule.py`, `src/virtual_orders/worker/jobs.py`, `src/virtual_orders/worker/runner.py`, `src/virtual_orders/worker/__main__.py`
- Modify: `src/virtual_orders/storage/database.py` (`WORKER_LOCK_KEY`)
- Test: `tests/worker/__init__.py` (vazio), `tests/worker/test_schedule.py`, `tests/integration/worker/__init__.py` (vazio), `tests/integration/worker/conftest.py`, `tests/integration/worker/test_jobs.py`, `tests/integration/worker/test_runner.py`, `tests/integration/worker/test_cli.py`

**Interfaces:**
- Consumes: `run_live_cycle`, `run_opening`, `run_end_of_day`, `run_quality_recheck` (Task 10), `run_watchlist_cycle` (Task 13), `enqueue_event_alerts`, `deliver_pending_alerts`, `DeliveryReport` (Task 12), `HealthWatcher`, `enqueue_end_of_day_summary` (Task 14), `build_health_report`, `rebuild_all_projections` (Task 4), `build_services`, `load_settings`, `ConfigError`, `Services`, `require_aware`, `calendar_for_window`, `flag_order_review`; `MutableClock`, `FakeTickerCheck` (`tests/integration/api/conftest.py`); `RecordingSink` (Task 12).
- Produces:
  - `schedule.py`: `MARKET_TZ`, `LIVE_WINDOW_GRACE = timedelta(minutes=5)`, `MISFIRE_GRACE_SECONDS = 60`, `MIN_EVAL_INTERVAL_MINUTES = 1`, `MAX_EVAL_INTERVAL_MINUTES = 59`, ids `LIVE_CYCLE = "live_cycle"`, `WATCHLIST = "watchlist"`, `OPENING = "opening"`, `END_OF_DAY = "end_of_day"`, `HEALTH_WATCH = "health_watch"`, `DELIVER_ALERTS = "deliver_alerts"`, `WORKER_LOCK = "worker_lock"`, `JobSpec(job_id: str, trigger: Any)`, `interval_config_errors(eval_interval_minutes: int) -> list[str]`, `build_schedule(eval_interval_minutes: int) -> tuple[JobSpec, ...]` (seis jobs, na ordem da tabela da D20), `worker_lock_schedule() -> JobSpec`, `live_session(now) -> Session | None`, `session_opening_today(now) -> Session | None`, `session_closed_today(now) -> Session | None`;
  - `jobs.py`: `JobResult(job: str, ran: bool, reason: str)`, `end_of_day_settled(engine, session_day: date) -> bool`, `WorkerJobs(services)` com `runner(job_id) -> Callable[[], JobResult]`, `live_cycle()`, `watchlist()`, `opening()`, `end_of_day()`, `health_watch()`, `deliver_alerts()`;
  - `runner.py`: `EXIT_OK = 0`, `EXIT_LOCKED = 2`, `EXIT_LOCK_LOST = 3`, `Scheduler` (Protocol), `blocking_scheduler() -> Scheduler`, `acquire_worker_lock(engine) -> Connection | None`, `release_worker_lock(conn) -> None`, `worker_lock_held(conn: Connection) -> bool`, `run_worker(services, *, scheduler_factory=blocking_scheduler, install_signal_handlers=True) -> int`;
  - `__main__.py`: `main(argv=None, *, environ=None, services_factory=build_services, worker=run_worker, out=None, err=None) -> int` (`EXIT_CONFIG = 2`, `EXIT_REBUILD_PROBLEMS = 1`; `run` valida `interval_config_errors` antes de montar os serviços);
  - `storage/database.py`: `WORKER_LOCK_KEY = 0x564F0003`.

- [ ] **Step 1: Write the failing schedule tests**

`tests/worker/test_schedule.py`:

```python
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from virtual_orders.worker.schedule import (
    build_schedule,
    interval_config_errors,
    live_session,
    session_closed_today,
    session_opening_today,
    worker_lock_schedule,
)

ET = ZoneInfo("America/New_York")


def at(day: str, hm: str) -> datetime:
    year, month, dom = (int(part) for part in day.split("-"))
    hour, minute = (int(part) for part in hm.split(":"))
    return datetime(year, month, dom, hour, minute, tzinfo=ET)


def triggers(interval: int = 2):
    return {spec.job_id: spec.trigger for spec in build_schedule(interval)}


def next_fire(job: str, now: datetime, interval: int = 2) -> datetime:
    return triggers(interval)[job].get_next_fire_time(None, now)


def test_job_ids_are_fixed():
    assert list(triggers()) == ["live_cycle", "watchlist", "opening", "end_of_day", "health_watch", "deliver_alerts"]


def test_watchlist_is_its_own_job_on_the_live_cadence():
    for now, expected in ((at("2025-11-24", "09:31"), at("2025-11-24", "09:32")),
                          (at("2025-11-24", "16:59"), at("2025-11-25", "09:00"))):
        assert next_fire("watchlist", now) == next_fire("live_cycle", now) == expected
    assert next_fire("watchlist", at("2025-11-24", "09:31"), interval=5) == at("2025-11-24", "09:35")


def test_worker_lock_is_checked_every_minute():
    spec = worker_lock_schedule()
    assert spec.job_id == "worker_lock" and spec.trigger.interval == timedelta(minutes=1)


def test_live_cycle_fires_every_interval_on_weekdays_between_9_and_16_et():
    assert next_fire("live_cycle", at("2025-11-24", "09:31")) == at("2025-11-24", "09:32")
    assert next_fire("live_cycle", at("2025-11-24", "09:31"), interval=5) == at("2025-11-24", "09:35")
    assert next_fire("live_cycle", at("2025-11-24", "16:59")) == at("2025-11-25", "09:00")
    assert next_fire("live_cycle", at("2025-11-28", "17:00")) == at("2025-12-01", "09:00")


def test_opening_fires_at_9_25_et_on_weekdays():
    assert next_fire("opening", at("2025-11-25", "09:26")) == at("2025-11-26", "09:25")
    assert next_fire("opening", at("2025-11-28", "10:00")) == at("2025-12-01", "09:25")


def test_end_of_day_fires_at_16_30_and_18_30_et():
    assert next_fire("end_of_day", at("2025-11-25", "16:31")) == at("2025-11-25", "18:30")
    assert next_fire("end_of_day", at("2025-11-25", "18:31")) == at("2025-11-26", "16:30")


def test_interval_jobs_use_the_configured_cadence():
    assert triggers(3)["health_watch"].interval == timedelta(minutes=3)
    assert triggers(3)["deliver_alerts"].interval == timedelta(minutes=1)


@pytest.mark.parametrize("interval", [0, 60])
def test_interval_outside_the_cron_minute_range_fails_at_startup(interval):
    assert interval_config_errors(interval) == ["OUT_OF_RANGE:EVAL_INTERVAL_MINUTES"]
    with pytest.raises(ValueError, match="between 1 and 59"):
        build_schedule(interval)


@pytest.mark.parametrize("interval", [1, 2, 59])
def test_interval_inside_the_cron_minute_range_is_valid(interval):
    assert interval_config_errors(interval) == [] and len(build_schedule(interval)) == 6


def test_live_session_window_follows_the_nyse_calendar():
    assert live_session(at("2025-11-25", "09:29")) is None
    assert live_session(at("2025-11-25", "09:30")).day == date(2025, 11, 25)
    assert live_session(at("2025-11-25", "16:05")) is not None and live_session(at("2025-11-25", "16:06")) is None
    assert live_session(at("2025-11-28", "13:05")) is not None and live_session(at("2025-11-28", "13:06")) is None
    assert live_session(at("2025-11-27", "11:00")) is None  # Thanksgiving


def test_opening_and_close_detection():
    assert session_opening_today(at("2025-11-26", "09:25")).day == date(2025, 11, 26)
    assert session_opening_today(at("2025-11-26", "09:30")) is None
    assert session_opening_today(at("2025-11-27", "09:25")) is None
    assert session_closed_today(at("2025-11-25", "16:30")).day == date(2025, 11, 25)
    assert session_closed_today(at("2025-11-25", "15:59")) is None
    assert session_closed_today(at("2025-11-28", "16:30")).day == date(2025, 11, 28)  # half day
```

- [ ] **Step 2: Write the failing integration tests**

`tests/integration/worker/conftest.py`:

```python
"""Worker harness: real Postgres, fake providers, a controllable clock and a recording sink. No network, no sleep."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import pytest

from core.domain.models import FillConfig
from tests.integration.alert_support import RecordingSink
from tests.integration.api.conftest import FakeTickerCheck, MutableClock
from tests.integration.support import (
    CODE_VERSION,
    DAY,
    PRICE_SOURCE,
    FakeBarSource,
    FakeDividends,
    FakeReference,
    FakeSplits,
    feeds,
)
from tests.support import et
from virtual_orders.services import Services


@dataclass
class WorkerHarness:
    services: Services
    bars: FakeBarSource
    clock: MutableClock
    sink: RecordingSink
    closed: list[int]


@pytest.fixture
def worker(engine) -> Iterator[WorkerHarness]:
    bars = FakeBarSource()
    clock = MutableClock(et(DAY, "09:00"))
    sink = RecordingSink()
    closed: list[int] = []
    services = Services(
        engine=engine, gateway=feeds(bars), price_source=PRICE_SOURCE, fill_config=FillConfig(),
        code_version=CODE_VERSION, ticker_check=FakeTickerCheck(), dividend_primary=FakeDividends("fmp"),
        dividend_secondary=FakeDividends("yfinance"), split_source=FakeSplits(), reference=FakeReference(),
        api_key="unused", eval_interval_minutes=2, bootstrap_resamples=200, bootstrap_seed=42,
        clock=clock, close=lambda: closed.append(1), alert_sink=sink,
    )
    yield WorkerHarness(services, bars, clock, sink, closed)
```

`tests/integration/worker/test_jobs.py`:

```python
from collections import Counter
from dataclasses import replace

from sqlalchemy import select

from tests.integration.support import DAY, count, flat_raw, scenario_bars, submit_default
from tests.support import et
from virtual_orders.alerts.watchlist import add_ticker
from virtual_orders.evaluator.commands import flag_order_review
from virtual_orders.ledger import errors
from virtual_orders.ledger.errors import LedgerIntegrityError
from virtual_orders.ledger.events import stored_events
from virtual_orders.ledger.quarantine import record_incident
from virtual_orders.storage import tables
from virtual_orders.worker import jobs as jobs_module
from virtual_orders.worker.jobs import JobResult, WorkerJobs


def run_kinds(engine):
    with engine.connect() as conn:
        return Counter(conn.execute(select(tables.evaluation_runs.c.kind)).scalars())


def outbox_keys(engine):
    with engine.connect() as conn:
        return list(conn.execute(select(tables.alert_outbox.c.alert_key).order_by(tables.alert_outbox.c.id)).scalars())


def watch_msft(worker):
    worker.bars.load(scenario_bars(ticker="MSFT"))
    with worker.services.engine.begin() as conn:
        add_ticker(conn, "MSFT", added_at=et(DAY, "09:00"))


def test_live_and_watchlist_jobs_outside_the_session_do_nothing(worker):
    jobs = WorkerJobs(worker.services)
    assert jobs.live_cycle() == JobResult("live_cycle", False, "OUTSIDE_SESSION")
    assert jobs.watchlist() == JobResult("watchlist", False, "OUTSIDE_SESSION")
    assert count(worker.services.engine, "evaluation_runs") == 0


def test_live_cycle_only_evaluates_orders(worker):
    engine = worker.services.engine
    submit_default(engine)
    worker.bars.load(scenario_bars())
    watch_msft(worker)
    worker.clock.set(et(DAY, "10:30"))

    assert WorkerJobs(worker.services).live_cycle() == JobResult("live_cycle", True, "COMPLETED")

    assert run_kinds(engine) == Counter({"LIVE": 1})
    assert outbox_keys(engine) == []  # enqueueing runs in deliver_alerts, opening and end_of_day (D24)
    assert all(call[0] != "MSFT" for call in worker.bars.calls)  # watchlist ingestion is its own job (D26)


def test_watchlist_job_ingests_and_records_feed_failures_on_its_own(worker):
    engine = worker.services.engine
    watch_msft(worker)
    worker.clock.set(et(DAY, "10:30"))
    jobs = WorkerJobs(worker.services)

    assert jobs.watchlist() == JobResult("watchlist", True, "COMPLETED")
    assert run_kinds(engine) == Counter({"WATCHLIST": 1})
    assert ("MSFT", et(DAY, "09:30"), et(DAY, "10:30")) in worker.bars.calls

    worker.bars.failing.add("MSFT")
    worker.clock.set(et(DAY, "10:32"))
    assert jobs.watchlist() == JobResult("watchlist", True, "COMPLETED_WITH_INGEST_FAILURES:1")


def test_watchlist_without_tickers_or_without_a_webhook(worker):
    engine = worker.services.engine
    worker.clock.set(et(DAY, "10:30"))
    assert WorkerJobs(worker.services).watchlist() == JobResult("watchlist", False, "NO_WATCHLIST")

    watch_msft(worker)
    disabled = WorkerJobs(replace(worker.services, alert_sink=None))
    assert disabled.watchlist() == JobResult("watchlist", True, "COMPLETED")
    assert run_kinds(engine) == Counter({"WATCHLIST": 1}) and outbox_keys(engine) == []


def test_a_failing_watchlist_never_touches_order_evaluation(worker, monkeypatch):
    def exploding(*args, **kwargs):
        raise RuntimeError("provider meltdown")

    monkeypatch.setattr(jobs_module, "run_watchlist_cycle", exploding)
    submit_default(worker.services.engine)
    worker.bars.load(scenario_bars())
    worker.clock.set(et(DAY, "10:30"))
    jobs = WorkerJobs(worker.services)

    assert jobs.runner("watchlist")() == JobResult("watchlist", False, "ERROR:RuntimeError")
    assert jobs.runner("live_cycle")() == JobResult("live_cycle", True, "COMPLETED")


def test_end_of_day_events_are_enqueued_without_any_live_cycle_job(worker):
    engine = worker.services.engine
    order_id = submit_default(engine, valid_sessions=1).auto_order_id
    worker.bars.load(flat_raw(DAY, "09:30", "16:00", 105))  # never fills: expires at the close
    worker.clock.set(et(DAY, "16:30"))

    assert WorkerJobs(worker.services).end_of_day() == JobResult("end_of_day", True, "COMPLETED")

    assert f"ORDER_EVENT:{order_id}:EXPIRED" in outbox_keys(engine)


def test_delivery_job_enqueues_events_written_outside_the_worker(worker):
    engine = worker.services.engine
    order_id = submit_default(engine).auto_order_id
    flag_order_review(engine, order_id, reason="MANUAL", ref="2025-11-25")  # e.g. through the API
    worker.clock.set(et(DAY, "08:00"))

    result = WorkerJobs(worker.services).deliver_alerts()

    assert result == JobResult("deliver_alerts", True, "DELIVERED:1 FAILED:0 EXPIRED:0")
    assert [key for key, _ in worker.sink.sent] == [f"ORDER_REVIEW:{order_id}:MANUAL:2025-11-25"]


def test_unexpected_errors_are_contained_by_the_runner(worker, monkeypatch):
    def exploding(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(jobs_module, "run_live_cycle", exploding)
    worker.clock.set(et(DAY, "10:30"))
    assert WorkerJobs(worker.services).runner("live_cycle")() == JobResult("live_cycle", False, "ERROR:RuntimeError")


def test_opening_runs_only_before_the_open(worker):
    jobs = WorkerJobs(worker.services)
    worker.clock.set(et("2025-11-26", "09:25"))
    assert jobs.opening() == JobResult("opening", True, "COMPLETED")
    worker.clock.set(et("2025-11-26", "10:00"))
    assert jobs.opening() == JobResult("opening", False, "NO_SESSION_OPENING")
    worker.clock.set(et("2025-11-27", "09:25"))
    assert jobs.opening() == JobResult("opening", False, "NO_SESSION_OPENING")
    assert run_kinds(worker.services.engine) == Counter({"OPENING": 1})


def test_end_of_day_retries_when_quality_was_not_evaluated_then_settles(worker):
    engine = worker.services.engine
    order_id = submit_default(engine).auto_order_id
    worker.bars.load(scenario_bars())
    worker.bars.failing.add("AAPL")
    jobs = WorkerJobs(worker.services)

    worker.clock.set(et(DAY, "15:00"))
    assert jobs.end_of_day() == JobResult("end_of_day", False, "NO_CLOSED_SESSION_TODAY")
    worker.clock.set(et(DAY, "16:30"))
    assert jobs.end_of_day() == JobResult("end_of_day", True, "COMPLETED")  # D12: PROVIDER_FAILURE
    worker.bars.failing.clear()
    worker.clock.set(et(DAY, "18:30"))
    assert jobs.end_of_day() == JobResult("end_of_day", True, "COMPLETED")  # not settled yet: runs again
    assert jobs.end_of_day() == JobResult("end_of_day", False, "ALREADY_SETTLED")

    with engine.connect() as conn:
        quality = [e.prepared.event_key for e in stored_events(conn, order_id) if e.prepared.type == "DATA_QUALITY"]
    assert quality == ["DATA_QUALITY:2025-11-25"]
    assert len([key for key in outbox_keys(engine) if key.startswith("END_OF_DAY_SUMMARY:2025-11-25:")]) == 2


def test_health_watch_and_alert_delivery(worker):
    engine = worker.services.engine
    jobs = WorkerJobs(worker.services)
    worker.clock.set(et(DAY, "08:00"))
    assert jobs.health_watch() == JobResult("health_watch", True, "HEALTHY")

    order_id = submit_default(engine).auto_order_id
    with engine.begin() as conn:
        record_incident(conn, LedgerIntegrityError(errors.PROJECTION_MISSING, "gone", order_id=order_id))
    assert jobs.health_watch() == JobResult("health_watch", True, "DEGRADED")

    assert jobs.deliver_alerts() == JobResult("deliver_alerts", True, "DELIVERED:2 FAILED:0 EXPIRED:0")
    assert worker.sink.sent[0][0].startswith("HEALTH:")
    assert worker.sink.sent[1][0].startswith("INTEGRITY_INCIDENT:")  # enqueued by the delivery job itself
    disabled = WorkerJobs(replace(worker.services, alert_sink=None))
    assert disabled.deliver_alerts() == JobResult("deliver_alerts", False, "WEBHOOK_DISABLED")
```

`tests/integration/worker/test_runner.py`:

```python
import signal

import pytest
from sqlalchemy import text

from tests.integration.support import DAY
from tests.support import et
from virtual_orders.storage.database import WORKER_LOCK_KEY
from virtual_orders.worker import runner as runner_module
from virtual_orders.worker.jobs import JobResult
from virtual_orders.worker.runner import acquire_worker_lock, release_worker_lock, run_worker


class FakeScheduler:
    def __init__(self) -> None:
        self.jobs: list[tuple] = []
        self.started = False
        self.stopped = False

    def add_job(self, func, trigger, *, id, name, max_instances, coalesce, misfire_grace_time):  # noqa: A002
        self.jobs.append((id, func, trigger, name, max_instances, coalesce, misfire_grace_time))

    def start(self) -> None:
        self.started = True

    def shutdown(self, wait: bool = True) -> None:
        self.stopped = True


def assert_lock_is_free(engine):
    lock = acquire_worker_lock(engine)
    assert lock is not None
    release_worker_lock(lock)


def test_run_worker_registers_every_job_then_releases_the_lock_and_closes(worker):
    scheduler = FakeScheduler()
    assert run_worker(worker.services, scheduler_factory=lambda: scheduler, install_signal_handlers=False) == 0
    assert [job[0] for job in scheduler.jobs] == ["live_cycle", "watchlist", "opening", "end_of_day", "health_watch",
                                                  "deliver_alerts", "worker_lock"]
    assert all(job[3] == job[0] and job[4:] == (1, True, 60) for job in scheduler.jobs)
    assert scheduler.started and worker.closed == [1]
    assert_lock_is_free(worker.services.engine)

    live = {job[0]: job[1] for job in scheduler.jobs}["live_cycle"]
    worker.clock.set(et(DAY, "08:00"))
    assert live() == JobResult("live_cycle", False, "OUTSIDE_SESSION")


def test_a_second_worker_exits_without_scheduling(worker):
    held = acquire_worker_lock(worker.services.engine)
    assert held is not None
    try:
        factory_calls: list[int] = []
        code = run_worker(worker.services, scheduler_factory=lambda: factory_calls.append(1) or FakeScheduler(),
                          install_signal_handlers=False)
        assert code == 2 and factory_calls == [] and worker.closed == [1]
    finally:
        release_worker_lock(held)


def test_services_are_closed_and_the_lock_released_when_the_scheduler_fails(worker):
    class Broken(FakeScheduler):
        def start(self) -> None:
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        run_worker(worker.services, scheduler_factory=Broken, install_signal_handlers=False)
    assert worker.closed == [1]
    assert_lock_is_free(worker.services.engine)


def test_termination_signals_shut_the_scheduler_down_gracefully(worker, monkeypatch):
    installed: dict = {}
    monkeypatch.setattr(runner_module.signal, "signal", lambda signum, handler: installed.__setitem__(signum, handler))

    class SignalledWhileRunning(FakeScheduler):
        def start(self) -> None:
            installed[signal.SIGTERM](signal.SIGTERM, None)

    scheduler = SignalledWhileRunning()
    assert run_worker(worker.services, scheduler_factory=lambda: scheduler) == 0
    assert scheduler.stopped and set(installed) == {signal.SIGTERM, signal.SIGINT}
    assert worker.closed == [1]


def test_a_lost_worker_lock_alerts_and_stops_the_scheduler(worker):
    engine = worker.services.engine

    class LosesTheLock(FakeScheduler):
        def start(self) -> None:
            watch = {job[0]: job[1] for job in self.jobs}["worker_lock"]
            assert watch() == JobResult("worker_lock", True, "HELD")
            with engine.begin() as conn:  # e.g. a database restart ended the session holding the lock
                conn.execute(text(
                    "SELECT pg_terminate_backend(pid) FROM pg_locks "
                    "WHERE locktype = 'advisory' AND objid = CAST(:key AS oid) AND pid <> pg_backend_pid()"
                ), {"key": WORKER_LOCK_KEY})
            assert watch() == JobResult("worker_lock", False, "LOCK_LOST")

    scheduler = LosesTheLock()
    assert run_worker(worker.services, scheduler_factory=lambda: scheduler, install_signal_handlers=False) == 3
    assert scheduler.stopped and worker.closed == [1]
    ((key, document),) = worker.sink.sent
    assert key.startswith("WORKER_LOCK_LOST:") and document["kind"] == "WORKER_LOCK_LOST"
    assert_lock_is_free(engine)
```

`tests/integration/worker/test_cli.py`:

```python
import io
import json

from sqlalchemy import text

from tests.config_support import BASE_ENV
from tests.integration.support import submit_default
from virtual_orders.worker.__main__ import main


def test_rebuild_projections_prints_a_summary_and_signals_problems_by_exit_code(worker):
    engine = worker.services.engine
    order_id = submit_default(engine).auto_order_id

    out = io.StringIO()
    assert main(["rebuild-projections"], environ=BASE_ENV, services_factory=lambda settings: worker.services,
                out=out) == 0
    assert json.loads(out.getvalue()) == {"orders": 1, "problems": {}, "results": {"OK": 1}}
    assert worker.closed == [1]

    with engine.begin() as conn:
        conn.execute(text("UPDATE order_state SET qty_open = 9 WHERE order_id = :id"), {"id": order_id})
    out = io.StringIO()
    assert main(["rebuild-projections", "--order-id", str(order_id)], environ=BASE_ENV,
                services_factory=lambda settings: worker.services, out=out) == 1
    assert json.loads(out.getvalue())["problems"] == {str(order_id): "PROJECTION_INTEGRITY_ERROR"}


def test_run_is_the_default_command(worker):
    calls = []
    code = main([], environ=BASE_ENV, services_factory=lambda settings: worker.services,
                worker=lambda services: calls.append(services) or 0)
    assert code == 0 and calls == [worker.services]


def test_invalid_configuration_exits_2_naming_variables_only():
    err = io.StringIO()
    assert main(["run"], environ={"API_KEY": "secret-value"}, err=err) == 2
    payload = json.loads(err.getvalue())
    assert payload["error"] == "CONFIG_INVALID" and "MISSING:DATABASE_URL" in payload["errors"]
    assert "secret-value" not in err.getvalue()


def test_run_rejects_an_interval_the_cron_cannot_express_before_building_services():
    err = io.StringIO()
    built = []
    code = main(["run"], environ={**BASE_ENV, "EVAL_INTERVAL_MINUTES": "60"},
                services_factory=lambda settings: built.append(settings), err=err)
    assert code == 2 and built == []
    assert json.loads(err.getvalue()) == {"error": "CONFIG_INVALID", "errors": ["OUT_OF_RANGE:EVAL_INTERVAL_MINUTES"]}
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/worker tests/integration/worker -q`
Expected: FAIL com `ModuleNotFoundError: virtual_orders.worker.schedule` / `virtual_orders.worker.jobs`.

- [ ] **Step 4: Add the worker lock key**

Em `src/virtual_orders/storage/database.py`, depois de `INGEST_LOCK_KEY`:

```python
WORKER_LOCK_KEY = 0x564F0003  # one worker process per database (D20), held for the process lifetime
```

- [ ] **Step 5: Write `worker/schedule.py`**

```python
"""Worker schedule (spec 5.3, D20): cron triggers in America/New_York; each job re-checks the NYSE calendar."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from core.domain.calendar import Session
from virtual_orders.marketdata.calendars import calendar_for_window

MARKET_TZ = ZoneInfo("America/New_York")
LIVE_WINDOW_GRACE = timedelta(minutes=5)  # spec 5.3: 09:30-16:05 ET
MISFIRE_GRACE_SECONDS = 60
MIN_EVAL_INTERVAL_MINUTES, MAX_EVAL_INTERVAL_MINUTES = 1, 59  # D36: the cron minute field
LIVE_CYCLE = "live_cycle"
WATCHLIST = "watchlist"
OPENING = "opening"
END_OF_DAY = "end_of_day"
HEALTH_WATCH = "health_watch"
DELIVER_ALERTS = "deliver_alerts"
WORKER_LOCK = "worker_lock"


@dataclass(frozen=True)
class JobSpec:
    job_id: str
    trigger: Any


def interval_config_errors(eval_interval_minutes: int) -> list[str]:
    """D36: CONFIG_INVALID codes for the worker schedule; names the variable, never its value."""
    if MIN_EVAL_INTERVAL_MINUTES <= eval_interval_minutes <= MAX_EVAL_INTERVAL_MINUTES:
        return []
    return ["OUT_OF_RANGE:EVAL_INTERVAL_MINUTES"]


def build_schedule(eval_interval_minutes: int) -> tuple[JobSpec, ...]:
    if interval_config_errors(eval_interval_minutes):  # the CLI reports this first; this is the second barrier
        raise ValueError("EVAL_INTERVAL_MINUTES must be between 1 and 59 for the worker schedule")
    every = f"*/{eval_interval_minutes}"
    return (
        JobSpec(LIVE_CYCLE, CronTrigger(day_of_week="mon-fri", hour="9-16", minute=every, timezone=MARKET_TZ)),
        # D26: same cadence, separate job, so watchlist provider latency never delays or skips order evaluation.
        JobSpec(WATCHLIST, CronTrigger(day_of_week="mon-fri", hour="9-16", minute=every, timezone=MARKET_TZ)),
        JobSpec(OPENING, CronTrigger(day_of_week="mon-fri", hour=9, minute=25, timezone=MARKET_TZ)),
        JobSpec(END_OF_DAY, CronTrigger(day_of_week="mon-fri", hour="16,18", minute=30, timezone=MARKET_TZ)),
        JobSpec(HEALTH_WATCH, IntervalTrigger(minutes=eval_interval_minutes, timezone=MARKET_TZ)),
        JobSpec(DELIVER_ALERTS, IntervalTrigger(minutes=1, timezone=MARKET_TZ)),
    )


def worker_lock_schedule() -> JobSpec:
    """D39: registered by the runner, which owns the connection holding the lock."""
    return JobSpec(WORKER_LOCK, IntervalTrigger(minutes=1, timezone=MARKET_TZ))


def _sessions(now: datetime) -> tuple[Session, ...]:
    return calendar_for_window(now, now).sessions


def live_session(now: datetime) -> Session | None:
    """The session whose [open, close + 5 min] contains `now` (half days close early; holidays have none)."""
    return next((s for s in _sessions(now) if s.open_utc <= now <= s.close_utc + LIVE_WINDOW_GRACE), None)


def session_opening_today(now: datetime) -> Session | None:
    today = now.astimezone(MARKET_TZ).date()
    return next((s for s in _sessions(now) if s.day == today and now < s.open_utc), None)


def session_closed_today(now: datetime) -> Session | None:
    today = now.astimezone(MARKET_TZ).date()
    return next((s for s in _sessions(now) if s.day == today and s.close_utc <= now), None)
```

- [ ] **Step 6: Write `worker/jobs.py`**

```python
"""Worker jobs (spec 5.3, D20, D21): plain callables over Services; the scheduler only decides when they run."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import Engine, text

from virtual_orders.alerts.health_watch import HealthWatcher
from virtual_orders.alerts.outbox import deliver_pending_alerts, enqueue_event_alerts
from virtual_orders.alerts.summary import enqueue_end_of_day_summary
from virtual_orders.alerts.watch import run_watchlist_cycle
from virtual_orders.evaluator.clock import require_aware
from virtual_orders.evaluator.cycle import run_live_cycle
from virtual_orders.evaluator.opening import run_opening
from virtual_orders.evaluator.quality import run_end_of_day
from virtual_orders.evaluator.recheck import RecheckReport, run_quality_recheck
from virtual_orders.readmodels.health import build_health_report
from virtual_orders.services import Services
from virtual_orders.worker.schedule import (
    DELIVER_ALERTS,
    END_OF_DAY,
    HEALTH_WATCH,
    LIVE_CYCLE,
    OPENING,
    WATCHLIST,
    live_session,
    session_closed_today,
    session_opening_today,
)

logger = logging.getLogger("virtual_orders.worker")

_SETTLED = text(
    """
    SELECT EXISTS (
        SELECT 1
        FROM evaluation_runs r
        JOIN LATERAL (
            SELECT status, detail FROM evaluation_run_status s WHERE s.run_id = r.run_id ORDER BY s.id DESC LIMIT 1
        ) latest ON true
        WHERE r.kind = 'END_OF_DAY' AND latest.status = 'COMPLETED' AND latest.detail->>'session_day' = :day
          AND COALESCE(latest.detail->'not_evaluated', '{}'::jsonb) = '{}'::jsonb
    )
    """
)


@dataclass(frozen=True)
class JobResult:
    job: str
    ran: bool
    reason: str


def end_of_day_settled(engine: Engine, session_day: date) -> bool:
    """A COMPLETED END_OF_DAY run for the session with nothing left in not_evaluated (D20, D12(b))."""
    with engine.connect() as conn:
        return bool(conn.execute(_SETTLED, {"day": session_day.isoformat()}).scalar_one())


class WorkerJobs:
    def __init__(self, services: Services) -> None:
        self._services = services
        self._health = HealthWatcher(services.engine, eval_interval_minutes=services.eval_interval_minutes)
        self._jobs: dict[str, Callable[[], JobResult]] = {
            LIVE_CYCLE: self.live_cycle, WATCHLIST: self.watchlist, OPENING: self.opening,
            END_OF_DAY: self.end_of_day, HEALTH_WATCH: self.health_watch, DELIVER_ALERTS: self.deliver_alerts,
        }

    def _now(self) -> datetime:
        return require_aware(self._services.clock(), "clock")  # D21: the market instant, read once per job

    def runner(self, job_id: str) -> Callable[[], JobResult]:
        job = self._jobs[job_id]

        def run() -> JobResult:
            try:
                result = job()
            except Exception as exc:  # noqa: BLE001 - a failing job never stops the scheduler (D20)
                logger.error("job %s failed: %s", job_id, type(exc).__name__, exc_info=True)
                return JobResult(job_id, False, f"ERROR:{type(exc).__name__}")
            logger.info("job %s: %s", job_id, result.reason)
            return result

        return run

    def _enqueue_event_alerts(self) -> None:
        """D24: sink-gated and contained; a failure here never undoes the job that just ran."""
        if self._services.alert_sink is None:
            return
        try:
            enqueue_event_alerts(self._services.engine)
        except Exception as exc:  # noqa: BLE001 - n8n is never in the critical path
            logger.error("event alert enqueue failed: %s", type(exc).__name__, exc_info=True)

    def live_cycle(self) -> JobResult:
        """Order evaluation only: no watchlist ingestion and no alert work in this job (D20, D26)."""
        now = self._now()
        if live_session(now) is None:
            return JobResult(LIVE_CYCLE, False, "OUTSIDE_SESSION")
        s = self._services
        report = run_live_cycle(s.engine, s.gateway, code_version=s.code_version, market_now=now)
        if report.skipped:
            return JobResult(LIVE_CYCLE, False, "LOCK_BUSY")
        return JobResult(LIVE_CYCLE, True, "COMPLETED")

    def watchlist(self) -> JobResult:
        """D26: its own job, so provider latency on watchlist tickers never delays or skips order evaluation."""
        now = self._now()
        if live_session(now) is None:
            return JobResult(WATCHLIST, False, "OUTSIDE_SESSION")
        s = self._services
        report = run_watchlist_cycle(s.engine, s.gateway, price_source=s.price_source, code_version=s.code_version,
                                     market_now=now, alerts_enabled=s.alert_sink is not None)
        if report.run_id is None:
            return JobResult(WATCHLIST, False, "NO_WATCHLIST")
        if report.ingest_failures:  # recorded in the WATCHLIST run detail, like LIVE ingest failures
            return JobResult(WATCHLIST, True, f"COMPLETED_WITH_INGEST_FAILURES:{len(report.ingest_failures)}")
        return JobResult(WATCHLIST, True, "COMPLETED")

    def opening(self) -> JobResult:
        now = self._now()
        session = session_opening_today(now)
        if session is None:
            return JobResult(OPENING, False, "NO_SESSION_OPENING")
        s = self._services
        run_opening(s.engine, session_day=session.day, primary=s.dividend_primary, secondary=s.dividend_secondary,
                    split_source=s.split_source, tolerance=s.fill_config.dividend_tolerance,
                    code_version=s.code_version, now=now)
        self._enqueue_event_alerts()  # D24: dividend/split reviews alert right after the opening
        return JobResult(OPENING, True, "COMPLETED")

    def end_of_day(self) -> JobResult:
        now = self._now()
        session = session_closed_today(now)
        if session is None:
            return JobResult(END_OF_DAY, False, "NO_CLOSED_SESSION_TODAY")
        s = self._services
        if end_of_day_settled(s.engine, session.day):
            return JobResult(END_OF_DAY, False, "ALREADY_SETTLED")
        report = run_end_of_day(s.engine, gateway=s.gateway, reference=s.reference, session_day=session.day,
                                code_version=s.code_version, market_now=now)
        if report.cycle.skipped:
            return JobResult(END_OF_DAY, False, "LOCK_BUSY")
        recheck: RecheckReport | None = None
        try:
            recheck = run_quality_recheck(s.engine, gateway=s.gateway, reference=s.reference,
                                          code_version=s.code_version, market_now=now)
        except Exception as exc:  # noqa: BLE001 - the recheck run records its own FAILED status
            logger.error("quality recheck failed: %s", type(exc).__name__, exc_info=True)
        self._enqueue_event_alerts()  # D24: EXPIRED/TIME_EXIT/close-cycle fills and quality reviews alert tonight
        if s.alert_sink is not None:
            try:
                state = build_health_report(s.engine, now=now, eval_interval_minutes=s.eval_interval_minutes).state
                enqueue_end_of_day_summary(s.engine, report, session_day=session.day, health_state=state,
                                           recheck=recheck)
            except Exception as exc:  # noqa: BLE001 - n8n is never in the critical path
                logger.error("end-of-day summary failed: %s", type(exc).__name__, exc_info=True)
        return JobResult(END_OF_DAY, True, "COMPLETED")

    def health_watch(self) -> JobResult:
        observation = self._health.observe(self._now(), self._services.alert_sink)
        return JobResult(HEALTH_WATCH, True, observation.state.value)

    def deliver_alerts(self) -> JobResult:
        sink = self._services.alert_sink
        if sink is None:
            return JobResult(DELIVER_ALERTS, False, "WEBHOOK_DISABLED")
        self._enqueue_event_alerts()  # D24: every minute, so API-originated events are caught too
        report = deliver_pending_alerts(self._services.engine, sink)
        reason = f"DELIVERED:{report.delivered} FAILED:{report.failed} EXPIRED:{report.expired}"
        return JobResult(DELIVER_ALERTS, True, f"{reason} BUDGET_EXHAUSTED" if report.budget_exhausted else reason)
```

- [ ] **Step 7: Write `worker/runner.py`**

```python
"""Worker process lifecycle (D20, D39): watched process lock, job registration, graceful shutdown, services closed."""

from __future__ import annotations

import logging
import signal
from collections.abc import Callable
from types import FrameType
from typing import Any, Protocol, cast

from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import SQLAlchemyError

from virtual_orders.services import Services
from virtual_orders.storage.database import WORKER_LOCK_KEY
from virtual_orders.worker.jobs import JobResult, WorkerJobs
from virtual_orders.worker.schedule import (
    MARKET_TZ,
    MISFIRE_GRACE_SECONDS,
    WORKER_LOCK,
    build_schedule,
    worker_lock_schedule,
)

logger = logging.getLogger("virtual_orders.worker")
EXIT_OK = 0
EXIT_LOCKED = 2
EXIT_LOCK_LOST = 3

# pg_try_advisory_lock(bigint) stores the high 32 bits in classid, the low 32 bits in objid and objsubid = 1.
_LOCK_HELD = text(
    """
    SELECT EXISTS (
        SELECT 1 FROM pg_locks
        WHERE locktype = 'advisory' AND pid = pg_backend_pid() AND granted
          AND classid = CAST(0 AS oid) AND objid = CAST(:key AS oid) AND objsubid = 1
    )
    """
)


class Scheduler(Protocol):
    def add_job(
        self, func: Callable[[], object], trigger: Any, *, id: str, name: str, max_instances: int, coalesce: bool,
        misfire_grace_time: int,
    ) -> object: ...

    def start(self) -> None: ...

    def shutdown(self, wait: bool = True) -> None: ...


def blocking_scheduler() -> Scheduler:
    return cast(Scheduler, BlockingScheduler(timezone=MARKET_TZ))


def acquire_worker_lock(engine: Engine) -> Connection | None:
    conn = engine.connect()
    try:
        acquired = bool(conn.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": WORKER_LOCK_KEY}).scalar_one())
        conn.commit()
    except Exception:
        conn.close()
        raise
    if not acquired:
        conn.close()
        return None
    return conn


def release_worker_lock(conn: Connection) -> None:
    try:
        conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": WORKER_LOCK_KEY})
        conn.commit()
    finally:
        conn.close()


def worker_lock_held(conn: Connection) -> bool:
    """D39: whether this session still holds WORKER_LOCK_KEY. A dropped or unusable connection counts as lost."""
    try:
        held = bool(conn.execute(_LOCK_HELD, {"key": WORKER_LOCK_KEY}).scalar_one())
        conn.commit()
    except SQLAlchemyError:
        return False
    return held


def _alert_lock_lost(services: Services) -> None:
    """Straight to the sink: the database that dropped the lock may be the thing that is down."""
    sink = services.alert_sink
    if sink is None:
        return
    observed = services.clock()
    alert_key = f"WORKER_LOCK_LOST:{observed.isoformat()}"
    try:
        sink.deliver(alert_key, {"schema_version": 1, "alert_key": alert_key, "kind": "WORKER_LOCK_LOST",
                                 "observed_at": observed.isoformat()})
    except Exception as exc:  # noqa: BLE001 - n8n is never in the critical path
        logger.warning("worker lock alert failed via %s: %s", sink.name, type(exc).__name__)


def run_worker(
    services: Services,
    *,
    scheduler_factory: Callable[[], Scheduler] = blocking_scheduler,
    install_signal_handlers: bool = True,
) -> int:
    lock: Connection | None = None
    lost = False
    try:
        lock = acquire_worker_lock(services.engine)
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
            scheduler.shutdown(wait=False)  # called from a job thread: never wait for itself
            return JobResult(WORKER_LOCK, False, "LOCK_LOST")

        for spec in (*schedule, worker_lock_schedule()):
            func = watch_lock if spec.job_id == WORKER_LOCK else jobs.runner(spec.job_id)
            scheduler.add_job(func, spec.trigger, id=spec.job_id, name=spec.job_id,
                              max_instances=1, coalesce=True, misfire_grace_time=MISFIRE_GRACE_SECONDS)
        if install_signal_handlers:
            def stop(signum: int, frame: FrameType | None) -> None:
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

- [ ] **Step 8: Write `worker/__main__.py`**

```python
"""`python -m virtual_orders.worker [run | rebuild-projections [--order-id UUID ...]]` (spec 5.3 and 6, D20).

The only worker module that touches the composition root.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from typing import TextIO
from uuid import UUID

from virtual_orders.bootstrap import build_services
from virtual_orders.config import ConfigError, Settings, load_settings
from virtual_orders.evaluator.rebuild import rebuild_all_projections
from virtual_orders.services import Services
from virtual_orders.worker.runner import run_worker
from virtual_orders.worker.schedule import interval_config_errors

EXIT_CONFIG = 2
EXIT_REBUILD_PROBLEMS = 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m virtual_orders.worker")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("run", help="start the scheduler (default)")
    rebuild = commands.add_parser("rebuild-projections", help="rebuild and verify order_state from history (D2)")
    rebuild.add_argument("--order-id", dest="order_ids", action="append", type=UUID, default=None)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    services_factory: Callable[[Settings], Services] = build_services,
    worker: Callable[[Services], int] = run_worker,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    stdout = sys.stdout if out is None else out
    stderr = sys.stderr if err is None else err
    args = _parser().parse_args(argv)
    try:
        settings = load_settings(os.environ if environ is None else environ)
        if args.command in (None, "run"):
            interval_errors = interval_config_errors(settings.eval_interval_minutes)
            if interval_errors:  # D36: reported like any configuration error, before anything is built
                raise ConfigError(interval_errors)
        services = services_factory(settings)
    except ConfigError as exc:  # errors name variables, never values (D13)
        print(json.dumps({"error": "CONFIG_INVALID", "errors": exc.errors}), file=stderr)
        return EXIT_CONFIG
    if args.command == "rebuild-projections":
        try:
            report = rebuild_all_projections(services.engine, args.order_ids)
        finally:
            services.close()
        problems = {str(order_id): result for order_id, result in report.items() if result != "OK"}
        print(json.dumps({
            "orders": len(report), "results": dict(sorted(Counter(report.values()).items())),
            "problems": dict(sorted(problems.items())),
        }, sort_keys=True), file=stdout)
        return EXIT_REBUILD_PROBLEMS if problems else 0
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    return worker(services)


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 9: Run tests to verify they pass**

Run: `uv run pytest tests/worker tests/integration/worker tests/test_import_boundaries.py -q && uv run ruff check src tests migrations && uv run mypy`
Expected: PASS (`test_worker_never_imports_adapters_provider_libraries_or_the_http_layer` e `test_only_the_worker_entrypoint_imports_the_composition_root` agora cobrem os quatro módulos). Se `test_a_lost_worker_lock_alerts_and_stops_the_scheduler` falhar com `permission denied to terminate process`, o usuário do banco de teste não pode encerrar backends do próprio papel; pare e reporte ao controlador em vez de afrouxar o teste.

- [ ] **Step 10: Run the whole suite**

Run: `uv run pytest 2>&1 | tail -1`
Expected: tudo verde, sem skips.

- [ ] **Step 11: Commit**

```bash
git add src/virtual_orders/storage/database.py src/virtual_orders/worker/schedule.py src/virtual_orders/worker/jobs.py src/virtual_orders/worker/runner.py src/virtual_orders/worker/__main__.py tests/worker/__init__.py tests/worker/test_schedule.py tests/integration/worker/__init__.py tests/integration/worker/conftest.py tests/integration/worker/test_jobs.py tests/integration/worker/test_runner.py tests/integration/worker/test_cli.py
git commit -m "feat(worker): APScheduler worker with calendar-aware jobs, process lock, graceful shutdown and CLI"
```

---

### Task 17: Fechamento — fronteiras fixadas, verificação do zero, smoke, revisão do branch inteiro e nota de encerramento

**Files:**
- Modify: `tests/test_import_boundaries.py` (`test_boundary_scan_covers_the_worker_and_alert_packages`)
- Create: `docs/superpowers/notes/2026-09-13-plan3b-closeout.md`
- Modify: `docs/superpowers/plans/2026-09-13-virtual-order-engine-worker.md` (seção final "Encerramento do controlador")

**Interfaces:**
- Consumes: tudo o que as Tasks 1–16 produziram.
- Produces:
  - nota de encerramento no formato das notas dos Planos 2 e 3A: decisões D20–D39 (mantida/alterada — motivo — custo se estiver errada), rulings, achados menores adiados e entradas para o 3C;
  - tag local `plan/virtual-order-engine-worker-complete`, criada **somente** se nenhuma revisão estiver aberta.

- [ ] **Step 1: Pin the boundary coverage to the real files**

Em `test_boundary_scan_covers_the_worker_and_alert_packages`, acrescente ao final:

```python
    worker = {_rel(p) for p in _worker_files()}
    assert {
        "virtual_orders/worker/schedule.py", "virtual_orders/worker/jobs.py", "virtual_orders/worker/runner.py",
        WORKER_ENTRYPOINT,
    } <= worker
    assert {
        "virtual_orders/alerts/outbox.py", "virtual_orders/alerts/watch.py", "virtual_orders/alerts/health_watch.py",
        "virtual_orders/analytics/pressure.py", "virtual_orders/evaluator/recheck.py",
        "virtual_orders/readmodels/quality.py",
    } <= neutral
    assert all((SRC / relative).exists() for relative in PLATFORM_PURE_MODULES)
    assert (SRC / "virtual_orders/notify/n8n.py").exists()
    assert "virtual_orders.notify.n8n" in _imported_modules(SRC / COMPOSITION_ROOT)
    assert "virtual_orders.bootstrap" in _imported_modules(SRC / WORKER_ENTRYPOINT)
    assert "virtual_orders/api/routes/watchlist.py" in {_rel(p) for p in _api_files()}
```

Run: `uv run pytest tests/test_import_boundaries.py -q`
Expected: PASS.

Commit:

```bash
git add tests/test_import_boundaries.py
git commit -m "test(boundaries): pin worker, alerts and notify coverage to the real modules"
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
if git merge-base --is-ancestor plan-3a-api HEAD && ! git merge-base --is-ancestor plan-3a-api main; then
  BASE=plan-3a-api
else
  BASE=$(git merge-base main HEAD)
fi
echo "BASE=$BASE"
git log --format='%an <%ae>%n%B' "$BASE"..HEAD | grep -E '^(Co-Authored-By|Claude-Session):' || echo "no AI trailers"
git log --format='%an <%ae>' "$BASE"..HEAD | sort -u
```

Expected:
- Suíte inteira verde, sem skips e sem warnings, com os `N_BASE` testes herdados (Task 0) e os novos.
- ruff e mypy limpos.
- Os dois comandos de `src/core` vazios.
- `no AI trailers`.
- Um único autor: `Ricardo Carneiro <132141856+ricdomingues@users.noreply.github.com>`.
- As migrations sobem do zero (o template de banco roda `alembic upgrade head` num container recém-criado) e `test_migrations_run_from_zero_and_back` e `test_downgrade_to_0002_and_back` passam.

- [ ] **Step 3: Smoke the composition, the schedule and the CLI without network**

O `env -i` garante que nenhuma variável do shell do host entre na composição. Nada aqui abre conexão de rede: `build_services` não conecta, e o `run` sem configuração falha antes de montar qualquer coisa.

```bash
env -i PATH="$PATH" HOME="$HOME" \
  API_KEY=x DATABASE_URL=postgresql+psycopg://vo:vo@127.0.0.1:55432/postgres ALPACA_API_KEY=a ALPACA_SECRET_KEY=s \
  FMP_API_KEY=f N8N_WEBHOOK_URL=https://n8n.invalid/hook/token \
  uv run python -c "from fastapi.routing import iter_route_contexts; from virtual_orders.bootstrap import app_from_environment; app = app_from_environment(); print(sorted((sorted(c.route.methods)[0], c.route.path) for c in iter_route_contexts(app.routes))); print(type(app.state.services.alert_sink).__name__, repr(app.state.services.alert_sink)); app.state.services.close()"
env -i PATH="$PATH" HOME="$HOME" uv run python -c "from virtual_orders.worker.schedule import build_schedule; print([spec.job_id for spec in build_schedule(2)])"
env -i PATH="$PATH" HOME="$HOME" uv run python -m virtual_orders.worker --help
env -i PATH="$PATH" HOME="$HOME" uv run python -m virtual_orders.worker run; echo "exit=$?"
```

Expected:
1. As rotas (o FastAPI 0.141 exige `iter_route_contexts`, como no smoke do 3A):

```
[('DELETE', '/alerts/{rule_id}'), ('DELETE', '/watchlist/{ticker}'), ('GET', '/health'), ('GET', '/metrics'), ('GET', '/orders'), ('GET', '/orders/{order_id}'), ('GET', '/signals'), ('GET', '/watchlist'), ('POST', '/orders/{order_id}/cancel'), ('POST', '/replay'), ('POST', '/signals'), ('POST', '/signals/{signal_id}/orders'), ('POST', '/watchlist/{ticker}/alerts'), ('PUT', '/watchlist/{ticker}')]
```

   seguida de `N8nWebhook N8nWebhook(url=<hidden>)` (sem `token`).
2. `['live_cycle', 'watchlist', 'opening', 'end_of_day', 'health_watch', 'deliver_alerts']` (o `worker_lock` é registrado pelo runner, não pelo `build_schedule`).
3. A ajuda do argparse com os subcomandos `run` e `rebuild-projections`.
4. Uma linha JSON `{"error": "CONFIG_INVALID", "errors": ["MISSING:API_KEY", …]}` sem nenhum valor e `exit=2`.

- [ ] **Step 4: Whole-branch review**

- Despache a revisão final de branch inteiro (`requesting-code-review`), com o modelo mais capaz, sobre `$BASE..HEAD` (o `BASE` do Step 2).
- Entregue ao revisor: a spec, este plano, as Global Constraints e a nota de encerramento do 3A.
- Peça verificação explícita de:
  - (a) D20–D39 e a tabela de rastreio das entradas 1–14;
  - (b) nenhum segredo nem texto de exceção em respostas HTTP, run details lidos por `/health`, documentos do outbox (incluindo `INTEGRITY_INCIDENT`, sem `detail`) e payloads de webhook;
  - (c) fronteiras (worker, alerts, analytics, notify, API);
  - (d) nenhuma mudança de semântica de fill e `src/core` intocado;
  - (e) D4 e D12 preservadas pelo recheck: nunca `DATA_QUALITY`/`DATA_GAP`; o pregão recém-fechado fica com o fim de dia; uma linha por (ordem, pregão); linhas terminais para `OUTSIDE_WINDOW` e `NO_OBSERVATIONS` após o lookback; toque de nível com os níveis vigentes no pregão, ou pulado com motivo;
  - (f) n8n fora do caminho crítico: timeout de 5 s por fase, orçamento por execução, backoff por alerta, expiração por idade registrada como `EXPIRED`, `UNDELIVERABLE_ALERTS` só `INFO`, falha nunca muda avaliação;
  - (g) idempotência dos alertas entre reinícios (`alert_key`, `alert_event_marks`, `health_state_log`), colapso de revisões por pregão (D33) e alerta de recuperação (D32);
  - (h) jobs testados sem tempo real, watchlist em job próprio, enfileiramento fora do ciclo live, lock vigiado (D39) e desligamento gracioso;
  - (i) códigos HTTP das rotas novas contra a D26 (inclusive `TICKER_INVALID`) e o envelope do 3A;
  - (j) `OPENING_MISSING`/`END_OF_DAY_MISSING` (D38) e `ORDER_EVENT_ALERTS_BEHIND` (D35) sem falso positivo num banco novo.
- Achados viram **uma** rodada de correção seguida de uma re-revisão com escopo restrito. Resíduos são adjudicados no ledger (`Ruling: … — … — …`). Rode o Step 2 de novo depois da última correção.

- [ ] **Step 5: Write the close-out note**

`docs/superpowers/notes/2026-09-13-plan3b-closeout.md`, com as seções:
- **Cabeçalho:** Plano, Spec (v1.2 + D5–D31), Intervalo (`BASE` do Step 2 até a ponta do branch, nomeando qual base valeu), Testes (contagem do Step 2, com `N_BASE` herdados), Núcleo congelado (comando e resultado).
- **Critérios de aceite** (tabela Critério | Teste | Resultado), cobrindo cada linha da tabela "Critérios de aceite do Plano 3B" abaixo com os nomes reais dos testes (confirmados por grep).
- **Rastreio das entradas 1–14 do 3A:** entrada → commit(s) → teste(s).
- **Decisões D20–D39:** uma linha cada (mantida/alterada — motivo — custo se estiver errada). A linha da D34 registra que flags do recheck podem retirar retroativamente uma ordem das métricas padrão.
- **Rulings durante a execução.**
- **Resultado da revisão final** e **achados menores adiados**.
- **Entradas para o Plano 3C**, no mínimo:
  1. Docker Compose de produção com os serviços `api`, `worker` (`python -m virtual_orders.worker`), `dashboard` e `postgres`:
     - um único `worker` (D20), com `restart` ativo (a D39 sai com código 3 ao perder o lock) e `stop_grace_period` de pelo menos 120 s, para um fim de dia ou recheck em andamento terminar no `SIGTERM`;
     - healthcheck do `api` (com a chave ou um `/livez` sem banco, decisão do 3C).
  2. Rotas de candles para a UI (as-of, com volume e VWAP) e sobreposição de ordens; exibição das estimativas de pressão sempre com `method` e `DISCLAIMER` (D27, D37).
  3. Telas da watchlist e das regras sobre as rotas da Task 15.
  4. Dashboard exibindo as causas `INFO` `UNDELIVERABLE_ALERTS` e `ORDER_EVENT_ALERTS_BEHIND`, os alertas `EXPIRED` e o último `health_state_log`.
  5. Recuperação de jobs perdidos (D38): comando do CLI para rodar a abertura ou o fim de dia de um pregão passado, com as travas da D4/D12, ou decisão explícita de manter a recuperação manual.
  6. Rever, depois da observação em paper, se o colapso de revisões por (ordem, motivo, pregão) da D33 é suficiente ou se precisa de um resumo por ordem.
  7. Portfólio virtual; portfólio real da Robinhood somente leitura, condicionado à Fase 0 do roadmap.
  8. Dashboard exibindo `data_quality.rechecks` do detalhe da ordem (incluindo `status`, `terminal_reason` e `level_touch_check`).
  9. Pendências `PROVIDER_FAILURE` que persistem além dos 20 últimos runs `END_OF_DAY` saem de `/health` sem linha terminal; decidir se ganham um status terminal próprio.
  10. Burst de cruzamentos ao ligar o webhook no meio do pregão (D26): rever com dados reais de paper se precisa de um corte de frescor.

Commit:

```bash
git add docs/superpowers/notes/2026-09-13-plan3b-closeout.md docs/superpowers/plans/2026-09-13-virtual-order-engine-worker.md
git commit -m "docs: Plan 3B close-out"
```

- [ ] **Step 6: Tag (only with no open review)**

Só se o ledger não tiver nenhuma revisão aberta, nenhum achado bloqueante sem ruling e o Step 2 estiver verde **depois** da última correção:

```bash
git tag -a plan/virtual-order-engine-worker-complete -m "Plan 3B — worker, n8n alerts, watchlist and hardening COMPLETE"
```

**Não crie a tag se houver qualquer revisão aberta.** Não faça push da tag nem do branch sem pedido explícito do responsável.

---

## Critérios de aceite do Plano 3B

| # | Critério | Onde |
|---|---|---|
| 1 | Worker agenda ciclo (a cada `EVAL_INTERVAL_MINUTES`, 09:30–16:05 ET, meio pregão e feriados respeitados), watchlist em job próprio na mesma cadência, abertura (09:25 ET) e fim de dia (16:30 ET, retry 18:30 só se não assentado); intervalo fora de 1..59 é `CONFIG_INVALID`; jobs testados sem tempo real; abertura/fim de dia ausentes aparecem em `/health` | Tasks 9, 16 (`test_schedule.py`, `test_jobs.py`, `test_cli.py`, `test_health_hardening.py`) |
| 2 | Um worker por banco; lock vigiado e saída com código 3 ao perdê-lo; desligamento gracioso; `services.close()` e liberação do lock sempre | Task 16 (`test_runner.py`) |
| 3 | `Services.clock` só para o instante de mercado; `require_aware` em `evaluate_order`, `finalize_validity`, `expire_due_orders`; `market_now` em runs `FAILED` | Tasks 2, 16 |
| 4 | `python -m virtual_orders.worker rebuild-projections` com resumo JSON e código de saída; isolamento por ordem; replay nunca congelada (M5) | Tasks 4, 16 |
| 5 | `DATA_QUALITY_RECHECK` consome `QUALITY_NOT_EVALUATED` uma vez (uma linha por ordem e pregão), nunca escreve `DATA_QUALITY`/`DATA_GAP`, respeita D4 e D12, fecha pendências impossíveis com linha terminal e checa toque de nível só com os níveis vigentes no pregão | Tasks 9, 10 |
| 6 | `DIVIDEND_UNVERIFIED` quando as duas fontes levantam; respostas vazias continuam sem revisão | Task 3 |
| 7 | Webhook n8n opcional: fora do caminho crítico, timeout de 5 s por fase, orçamento por execução, backoff por alerta, expiração em 24 h registrada e visível como `INFO`, sem URL/segredo/texto de exceção | Tasks 7, 12 |
| 8 | Resumo de fim de dia e alerta de `/health` por transição (estado, causa nova ou recuperação), sem repetir por ciclo nem após reinício | Task 14 |
| 9 | Alertas de eventos de ordem (enfileirados fora do ciclo live, revisões uma vez por ordem/motivo/pregão), de incidentes de integridade, de cruzamento de níveis e de pressão forte, uma vez por fato, idempotentes entre reinícios; pressão rotulada como estimativa; atraso além do lookback visível como `INFO` | Tasks 11–14, 16 |
| 10 | Watchlist + regras (migration 0003), ingestão de candles da watchlist sem fallback, rotas autenticadas no envelope do 3A | Tasks 8, 13, 15 |
| 11 | M4 (timeout ≠ indisponível), M6 (overrides contra a origem), M7 (404 sem corpo Alpaca → 503), `bootstrap` fecha o cliente | Tasks 5, 6, 7, 9 |
| 12 | Testes faltantes das entradas 13 e 14 (ramo `FAILED` da abertura, sessões em `/health`, `market_now` em `FAILED`, revisão sem motivo, `facts` limitados, `data_quality` com eventos reais) | Tasks 2, 9, 10 |
| 13 | Fronteiras: só `bootstrap.py` importa adapters (inclusive `notify/n8n.py`); worker sem adapters/HTTP; módulos puros sem I/O | Tasks 1, 17 |
| 14 | `src/core` intocado; `N_BASE` testes herdados verdes; migrations verificadas do zero; ruff e mypy limpos | Tasks 0, 8, 17 |
| 15 | Commits com identidade noreply e sem trailers de IA; nenhuma tag com revisão aberta; nenhum push | Task 17 |

## Encerramento do controlador

Escrita pelo controlador no Step 5 da Task 17, no formato do 3A: modelo de execução por task, rodadas de correção, verificação final do zero (contagens, ruff, mypy, `src/core`, `BASE`, trailers, autor, smoke), estado da tag e ponteiro para a nota de encerramento.
