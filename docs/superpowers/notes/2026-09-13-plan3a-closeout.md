# Plan 3A (Virtual Order Engine — Configuração, Composição, API e Endurecimento) — Encerramento

- **Plano:** `docs/superpowers/plans/2026-09-13-virtual-order-engine-api.md`
- **Spec:** v1.2 (`spec/virtual-order-engine-v1.2`) + D5–D12 (Plano 2) + D13–D19 (este plano)
- **Intervalo:** base `plan-2-persistence` (`4937615`) até a ponta do branch `plan-3a-api` / tag `plan/virtual-order-engine-api-complete` — 25 commits (24 de implementação, correções de revisão e testes + este encerramento). `BASE=plan-2-persistence` porque o branch ainda não foi rebaseado sobre um `main` que contenha o Plano 2 (regra do Step 2)
- **Testes:** 671 passando, 0 skips, 0 warnings, medidos do zero (`docker compose -f docker-compose.test.yml down -v && up -d --wait`, depois `uv run pytest -p no:cacheprovider -o addopts="" -q`) no commit `9cb2889`; 428 herdados dos Planos 1 e 2 (baseline da Task 0, medida em `4937615`)
- **Núcleo congelado:** `git diff plan/virtual-order-engine-core-complete -- src/core` vazio; `git status --porcelain -- src/core` vazio — checagem feita no Step 2, no commit final da verificação (`9cb2889`)

## Critérios de aceite

| Critério | Teste | Resultado |
|---|---|---|
| Autenticação em todas as rotas | `test_every_route_rejects_a_missing_or_wrong_key` (`tests/integration/api/test_signals_api.py`) | OK |
| Envelope de erro em 404/405/500 | `test_docs_unknown_routes_and_wrong_methods_use_the_error_envelope`, `test_unhandled_errors_are_500_without_the_message` (`tests/integration/api/test_signals_api.py`) | OK |
| JSON `Decimal` (100 == 100.0) | `test_body_numbers_are_read_as_decimal_never_float`, `test_integer_and_decimal_spellings_of_a_price_are_the_same_submission` (`tests/integration/api/test_signals_api.py`) | OK |
| Divergência de `REPRODUCE` em 409 com diff por ordem | `test_reproduce_divergence_is_409_with_the_diff_per_order` (`tests/integration/api/test_commands_api.py`) | OK |
| Ticker negociável (422 e 503) | `test_untradable_ticker_is_422_and_nothing_is_stored`, `test_ticker_check_unavailable_is_503_and_nothing_is_stored` (`tests/integration/api/test_signals_api.py`) | OK — nomes completos; o brief truncava com reticências, sem divergência de comportamento |
| Composição sem fallback de `PRICE_SOURCE` | `test_composition_pins_the_configured_price_source_without_fallback`, `test_unknown_price_source_fails_at_startup` (`tests/test_bootstrap.py`) | OK |
| Só a raiz de composição importa adapters | `test_only_the_composition_root_imports_provider_adapters` (`tests/test_import_boundaries.py`) | OK |
| Endurecimentos 9–14 (paginação, naive, replay somente leitura, validação de overrides, `_recalculate_order`, run `OPENING`) | Tasks 2–6: `tests/marketdata/test_http_sources.py` (paginação), `tests/integration/test_naive_clocks.py` (naive), `tests/integration/api/test_commands_api.py` + `tests/integration/test_commands.py` (replay somente leitura), `tests/integration/test_recalculate.py` + `tests/integration/test_reproduce.py` (validação de overrides / `_recalculate_order`), `tests/integration/test_opening.py` (run `OPENING`) | OK |
| `/health` estruturado (15, 17), 503 só para banco ou schema | `tests/readmodels/test_health_rules.py`, `tests/integration/api/test_health_api.py` | OK |
| Agregação (18, 19) | `tests/readmodels/test_incidents.py`, `tests/integration/test_incident_groups.py` | OK |
| Planos 1 e 2 verdes | suíte completa (671 passando, incluindo os 428 herdados) | OK |

Nenhum nome de teste do brief precisou de correção real: os dois casos com reticências (`test_untradable_ticker_is_422…`, `test_ticker_check_unavailable_is_503…`) correspondem exatamente a `test_untradable_ticker_is_422_and_nothing_is_stored` e `test_ticker_check_unavailable_is_503_and_nothing_is_stored`, confirmados por grep. `test_health_rules`, `test_health_api`, `test_incident_groups` e `test_incidents` são nomes de arquivo (não de função), também confirmados por grep.

## Decisões D13–D19

- **D13** (configuração pela spec 8, `load_settings` reporta todos os erros juntos, `PRICE_SOURCE`/taxas SEC-TAF fora da spec) — mantida, implementada tal qual nas Tasks 8 e 10.
- **D14** (ticker ativo e negociável via `AlpacaAssets`, 422 `TICKER_NOT_TRADABLE:<reason>` ou 503 `TICKER_UNVERIFIABLE` sem gravar nada) — mantida, com correção da revisão final: o `503` deixou de ecoar `str(exc)` (ver I1 abaixo).
- **D15** (replays somente leitura, sem encadeamento; `RECALCULATE` valida tudo antes de abrir o run; divergência de `REPRODUCE` vira 409 com diff por ordem) — mantida, implementada nas Tasks 3–5 e 12.
- **D16** (run próprio do job de abertura, migration 0002 acrescenta `OPENING`, falhas de fonte deixam de ser silenciosas) — mantida, implementada na Task 6.
- **D17** (`/health` estruturado, estado = pior severidade entre causas, 503 só para banco ou schema) — mantida, implementada na Task 15.
- **D18** (agregação de incidentes só na leitura, sem deduplicação na escrita; erros por ordem classificados em `INFRASTRUCTURE`/`PROGRAMMING`) — mantida, implementada na Task 7.
- **D19** (convenções de leitura da API: `replay=false` por padrão, `from`/`to` semi-abertos, `Decimal` na fronteira, `Services.clock` só para o instante de mercado, tabela de códigos de erro) — mantida, implementada nas Tasks 8–15.

## Rulings durante a execução

Inclui os dois já fixados na revisão do plano, citados pelo brief: sem deduplicação de incidentes na escrita (D18); `Services.clock` fornece só o instante de mercado, com `started_at`/`data_as_of` permanecendo no relógio do banco (D19).

Rulings do ledger de execução, na ordem em que apareceram:

1. **Task 0 não despachada** — `N_BASE = 428` passando, medido em `4937615` (suíte completa incluindo integração, saída 0); `9b81f01` difere só em docs — custo se estiver errada: a linha de base da Task 1 ficaria errada, mas a própria corrida completa da Task 1 pegaria.
2. **Resumo do pytest oculto por `pytest -q`** — o `pyproject.toml` já tem `-q` em `addopts`; o plano usa `uv run pytest` sem flag extra — custo se estiver errada: nenhum.
3. **Task 3 — ordem mandada pelo plano em `create_manual_order`** (leitura de `get_signal` antes de `require_aware`) — corrigida: a seção Interfaces do brief exige validação antes de qualquer I/O; ajuste de uma linha — custo se estiver errada: nenhum.
4. **Task 8 — implementador em haiku em vez de sonnet** — o brief trazia código completo para um único módulo (`config.py` + testes unitários, sem banco) — custo se estiver errada: uma rodada extra de correção, escalada pelo disjuntor do controlador.
5. **Task 15 — I1 (mandado pelo plano), texto de exceção bruto em causas/facts de `/health`** — corrigido na leitura: causas carregam só o tipo da exceção; `facts` mantém uma lista branca de chaves do detail do run (sem mensagens); os escritores de run do Plano 2 não foram tocados — D19 proíbe expor mensagens de erro — custo se estiver errada: menos detalhe diagnóstico em `/health` (o detail completo continua no banco).
6. **Task 15 — I2, DB pode travar** — acrescentado `connect_timeout` em `make_engine`/`connect_args` e mapeado `TimeoutError` do SQLAlchemy para `DATABASE_UNAVAILABLE` — o critério (c) exige 503 rápido, nunca travar ou 500 — custo se estiver errada: conexões lentas ao banco falham depois do timeout configurado.
7. **Task 16 — ordem de execução do fechamento**: Step 1 (haiku) → revisão final de branch inteiro (opus) → uma única rodada de correção cobrindo achados finais + achados menores marcados "fix-before-merge" (sonnet) → re-revisão de escopo restrito → Steps 2–3 de verificação do zero pelo controlador → nota de encerramento (sonnet) → controlador preenche o Encerramento e só cria a tag se nada estiver aberto — o plano exige o Step 2 verde depois da última correção; rodá-lo uma vez após as correções evita uma corrida completa duplicada — custo se estiver errada: nenhum (o revisor via uma árvore verde de 668 testes).
8. **Task 16, Step 1** (`c6c4dcc`, haiku, 668 passando) — o diff de só-teste (6 asserções) foi revisado dentro da revisão final de branch inteiro em vez de uma revisão de task separada — evita reservar uma revisão inteira para uma mudança de fixação de cobertura — custo se estiver errada: uma asserção ruim seria pega uma revisão mais tarde.
9. **Onda de correção final** = I1 + M1 + M2 + M3 + M8 + filtro dos 2 avisos de depreciação de terceiros com motivo documentado — o texto do plano ("nenhuma mensagem de exceção exposta", ruling da Task 15) prevalece sobre o texto do plano que mandava `str(exc)` em `intake.py` — correções de teste baratas fixam a D15/critério 1 — custo se estiver errada: menos texto diagnóstico nos corpos 503 (o texto completo permanece nos details dos runs).
10. **M4, M5, M6 e M7 adiados para as notas de encerramento do 3B** — nenhum é alcançável pelos caminhos do 3A segundo o revisor — custo se estiver errada: o 3B precisaria tratá-los antes do worker/healthcheck entrarem em produção.
11. **Step 3 do smoke test** — as rotas foram enumeradas via `fastapi.routing.iter_route_contexts` em vez de `app.routes`, porque o FastAPI 0.141 envolve routers incluídos (o mesmo achado da Task 11) — custo se estiver errada: nenhum, a saída bate literalmente com a lista do plano.

## Resultado da revisão final

Revisão de branch inteiro (opus, `4937615..c6c4dcc`): **Com correções** (0 Critical, 1 Important, 8 Minor). A correção obrigatória:

- **I1 — texto de exceção de provider vazando em corpos HTTP 503.** `intake.py` ecoava `str(exc)` em `TICKER_UNVERIFIABLE` (mandado pelo texto do plano) e `errors.py` repassava `ManualOrderError.detail["ingest_error"]` verbatim em `ACTIONABILITY_UNVERIFIABLE`. **Corrigido**: `intake.py` agora reporta `type(exc).__name__`; `errors.py` substitui o `ingest_error` livre por um código fixo (`UNKNOWN_DATA_SOURCE`/`SOURCE_UNAVAILABLE`), preservando as chaves estruturadas de cobertura. Isso corrige o texto do plano nas linhas que mandavam `str(exc)`: o texto correto é o tipo da exceção, nunca a mensagem, para não contradizer a regra "nenhuma mensagem de erro exposta" (D19, ruling da Task 15). Commit `793a508`.

Minors fixados na mesma onda:
- **M1** — três testes de API cobrindo REPRODUCE com `ORDER_NOT_FOUND` sem divergência, `REPRODUCE_DIVERGED` mantendo uma falha de não-divergência, e RECALCULATE rejeitado sem abrir run (`8aa64fc`).
- **M2** — fronteira de import de `services.py`/`config.py` passou a proibir também `virtual_orders.api`, `virtual_orders.bootstrap`, `fastapi`, `starlette`, `uvicorn` (`f57ff73`).
- **M3** — piso do `fastapi` elevado para `>=0.141`, alinhado ao comportamento de `iter_route_contexts` já usado pelos testes (`9cb2889`).
- **M8** — três itens de higiene de teste: asserção de status 404 em `test_commands_api.py`, asserção de status 422 em `test_orders_api.py`, e a checagem de cobertura de rotas passou a falhar (em vez de pular) em rotas que não são `APIRoute` (`336a9ee`).
- **Filtro de avisos** — os dois `DeprecationWarning` de terceiros (`starlette.testclient`/`httpx`, alias `anyio.abc.BlockingPortal`) passaram a ter `filterwarnings` com motivo, casados no texto e categoria exatos, sem ignorar `DeprecationWarning` de forma genérica (`9cb2889`).

Re-revisão de escopo restrito (sonnet): os 6 achados foram todos marcados ADDRESSED, sem quebra nova.

## Achados menores adiados

Só os itens que a triagem da revisão final marcou como adiados (os demais foram descartados — ver abaixo):

- **Task 6** — falta teste para o ramo `FAILED` de `run_opening` — adiado para o 3B, quando o job de abertura for agendado; o lado de `/health` já está coberto por testes de regra.
- **Task 10** — o cliente HTTP próprio do `bootstrap.py` não é fechado se `make_engine` levantar — adiado para o 3B: hoje só acontece no startup, onde o processo termina; o worker do 3B reutiliza `build_services`, e é lá que a correção deve entrar.
- **Task 13** — a seção `data_quality` do detalhe da ordem nunca foi testada com eventos `DATA_QUALITY`/`DATA_GAP` reais (`QUALITY_EVENT_TYPES` nunca exercitado por dados reais) — adiado para o 3B, quando `DATA_QUALITY_RECHECK` for implementado.
- **Task 15** — faltam testes de detecção de sessão de fim de semana/feriado/no fechamento; runs `FAILED` não carregam `market_now` (a obsolescência cai para o relógio do banco); `needs_review` com motivos vazios é subcontado; tamanho do payload `facts` — todos adiados para o 3B, quando o worker passar a agendar ciclos de verdade e exercitar esses caminhos.
- **M4** (`OperationalError` de query lenta reportado como `DATABASE_UNAVAILABLE`), **M5** (quarentena pode congelar uma ordem replay), **M6** (overrides do RECALCULATE validados contra `FillConfig()` padrão, não a configuração da ordem de origem), **M7** (`ALPACA_TRADING_URL` errada aparece como 422 `UNKNOWN_ASSET` em vez de 503) — adiados para o 3B/3C conforme a ruling da revisão final; nenhum é alcançável pelos caminhos do 3A.

Achados descartados pela triagem da revisão final (não são deferidos — foram fechados sem ação): Task 1 (colar `git log -1 --format=%B` nos relatórios — verificado direto no intervalo); Task 3 (contagem +3 da suíte — reconciliada pelo Step 2); Task 7 (`tuple(sorted(...))[:MAX_LISTED_ORDERS]` repetido — cosmético); Task 8 (imprecisões de narrativa do relatório — código correto, verificado pelo controlador); Task 9 (mensagem genérica "malformed Alpaca asset" — nunca chega ao cliente depois do I1); Task 11 (`1e400` viraria 500 — a premissa estava errada, o probe retornou 201); Task 12 (`replay_request.py:104-109`, fallback silencioso — o incidente é gravado antes do relatório retornar); Task 14 (`None` ordena por último, não primeiro — o relatório estava errado, não o código).

## Notas para a revisão do 3A

`Services.dividend_primary`, `dividend_secondary`, `split_source` e `reference` (Task 10) existem para o 3B e não são código morto da API: nenhum consumidor deste plano os usa, mas o worker de dividendos/splits do 3B (entrada 1 abaixo) depende deles.

## Entradas para o Plano 3B

1. Agendar `run_live_cycle` (a cada `EVAL_INTERVAL_MINUTES`, 09:30–16:05 ET), `run_opening` (09:25 ET) e `run_end_of_day` (16:30 ET) com `Services`, usando `Services.clock` só para `market_now`/`now`.
2. Guardar com `require_aware` as entradas `evaluate_order(market_now=)`, `finalize_validity(now=)` e `expire_due_orders(now=)` antes de agendá-las (ruling da Task 3).
3. Webhook `N8N_WEBHOOK_URL` com resumo de fim de dia e alerta quando `/health` ficar `DEGRADED` ou `UNHEALTHY`.
4. Alertas do responsável (2026-09-13), só pelo webhook n8n: eventos de ordem, preço cruzando níveis do usuário em tickers da watchlist, limiar de "pressão forte" rotulado como estimativa.
5. `DATA_QUALITY_RECHECK` (entrada 16 do Plano 2) consumindo `QUALITY_NOT_EVALUATED`.
6. Spec 6, "yfinance/FMP indisponíveis → `NEEDS_REVIEW` com motivo": quando **as duas** consultas de dividendo levantarem (não quando ambas voltarem vazias), marcar as ordens candidatas com revisão `DIVIDEND_UNVERIFIED` e ref da ex-date. Hoje a D16 só registra a falha em `source_failures` do run `OPENING`.
7. Comando `rebuild-projections`.
8. **M4** — `OperationalError` de query lenta em `/health` é hoje reportado como `DATABASE_UNAVAILABLE`; separar erros de conexão de timeouts de statement antes do healthcheck do 3B/3C.
9. **M5** — a quarentena (`ledger/quarantine.py`) pode congelar uma ordem replay ao contornar o guarda de `apply_command`; pular o congelamento para ordens replay em `rebuild-projections` (registrar só o incidente).
10. **M6** — os overrides do RECALCULATE são validados contra o `FillConfig()` padrão, não contra a configuração da ordem de origem; um override válido nos padrões pode falhar por ordem em combinação com outros campos da ordem de origem.
11. **M7** — um `ALPACA_TRADING_URL` errado (404 em todo path) aparece como 422 `UNKNOWN_ASSET` em vez de 503; tratar via smoke check de startup ou classificar um 404 sem corpo de erro Alpaca como `SourceDataError`.
12. O `bootstrap.py` não fecha o cliente HTTP próprio se `make_engine` levantar; corrigir onde o 3B reutiliza `build_services` para o worker.
13. Testes ainda faltando, a exercitar quando o worker do 3B rodar de verdade: ramo `FAILED` de `run_opening`; detecção de sessão em `/health` (fim de semana, feriado, no fechamento); `market_now` em `/health` para runs `FAILED` (`LIVE`); `needs_review` com motivos vazios; tamanho do payload `facts`.
14. Testar o `data_quality` do detalhe da ordem com eventos reais `DATA_QUALITY`/`DATA_GAP` (`QUALITY_EVENT_TYPES` hoje nunca exercitado por dados reais).

Escopo do responsável para 3B/3C, restabelecido em 2026-09-13: alertas via n8n; watchlist; candles com volume/VWAP/overlays de ordem; estimativas de pressão OHLCV; portfólio virtual + Robinhood somente leitura, este último condicionado à Fase 0 do roadmap.
