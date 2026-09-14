# Plan 1 (Virtual Order Engine — Núcleo Puro) — Encerramento

- **Plano:** `docs/superpowers/plans/2026-09-12-virtual-order-engine-core.md`
- **Spec:** v1.1 (`spec/virtual-order-engine-v1.1`)
- **Intervalo:** `2868e1b..b24cf69` (código) — 12 tasks, 5 rodadas de correção por task, 2 revisões de branch inteiro com rodadas de correção, 1 close-out
- **Testes:** 180 passando (unitários + Hypothesis com geradores ancorados, com viés pós-entrada e busca de alcançabilidade determinística)
- **Resultado:** revisão final de branch → pronto para tag; nenhum resultado de `fill_model v1` persistido até aqui

## Decisões tomadas durante a execução (rulings)

Cada item: decisão — motivo — custo se estiver errada.

1. **Task 4** — `OrderState` valida timezone em todos os campos datetime e `Event.bar_ts` exige minuto inteiro — restrições globais da spec prevalecem sobre o código do plano — overhead mínimo de validação por `replace()`.
2. **Task 5** — ordem criada com estado herdado infere `zone_ever_lost = zone_lost or entry_eligible_from is not None` (manual após reclaim anterior ao clique → `entry_path=RECLAIMED`), com testes de criação herdada — spec 3.7 define DIRECT como fill sem nenhum ZONE_LOST anterior e 3.4.1 fixa os campos herdados — ordem com `entry_eligible_from` sem perda de zona seria rotulada RECLAIMED (impossível nas regras v1).
3. **Task 5 → 6** — incluído teste de fill por ZONE_CROSS seguido de STOPPED no mesmo candle, fora do plano — consequência explícita da spec 4.3 só testada no caminho ZONE_OPEN — um teste a mais.
4. **Task 7** — `freeze()` idempotente para o mesmo motivo (refinado depois pelo item 12) — igualar a idempotência de `cancel()` — repetição com mesmo motivo e ref diferente não gera segundo NEEDS_REVIEW.
5. **Task 8 (1)** — geradores de propriedade ancorados na janela da ordem, preços centrados na zona e teste de alcançabilidade — ~75% dos exemplos eram vazios — suíte de propriedades mais lenta.
6. **Task 8 (2)(3)** — mantidas as propriedades "janela de avaliação" e "sem fill no candle de reclaim", apesar de garantidas por guardas — são guardas de regressão de invariantes da spec — dois testes de baixo rendimento.
7. **Revisão final #1** — uma rodada de correção: normalização UTC, bordas superiores do calendário, contexto Decimal explícito, `NEEDS_REVIEW:STALE_EXIT_BAR`, chaves não-str rejeitadas, `FillConfig` não negativo, testes de custo SHORT e comparação de payload no espelhamento — viram migração v2 depois de persistir — pequenas adições semânticas ao v1 (novo motivo de revisão, erros de calendário mais estritos).
8. **Revisão final #1** — minuto em andamento no clique e reconstrução da projeção levados ao responsável pela spec em vez de decididos pelo executor — são decisões de spec — (resolvidas como D1/D2).
9. **Métricas** — divisão em `core.metrics.summary` não recebe contexto Decimal — opera sobre `float` (R convertido) e int/int; métricas não são hasheadas — floats de exibição poderiam variar sob contexto exótico.
10. **Close-out** — rodada extra (auditoria D1 + busca de alcançabilidade determinística) além do limite padrão do processo — pedido explícito de "tag só com tudo verde" — um ciclo de revisão a mais.
11. **Revisão final #2** — rodada R1–R4 antes da tag: erro abaixo do primeiro pregão carregado, dinheiro sempre `Decimal` (int convertido; float/bool/NaN/Inf rejeitados), `OrderContext` exige minuto esperado e fechamento de pregão, `calendar_sessions_hash` no `ORDER_CREATED`, dividendos com contexto canônico, assert → `RuntimeError`, propriedade de P&L independente e cenários com viés pós-entrada — mesmos motivos do item 7 — hash de todo `ORDER_CREATED` muda (nada persistido).
12. **Revisão final #2 (R4b)** — `OrderState.frozen_reasons` separado de `review_reasons`; `freeze` só é no-op para motivo já congelado — `flag_review` prévio impedia registrar `FROZEN:{reason}` — um campo a mais na projeção.
13. **Revisão final #2 (R4c)** — em candle onde o stop dispara (inclusive o de entrada), a máxima não entra no MFE; a mínima continua no MAE — princípio "candle ambíguo → pior caso" (spec 4) — **altera valores de MFE**; é a decisão mais sujeita a preferência e deve ser confirmada pelo responsável.

## Decisões do responsável pela spec

- **D1** — candle parcialmente anterior ao clique não é usado; auditoria `partial_bar_skipped` / `skipped_bar_ts` no `ORDER_CREATED` manual (spec seção 10).
- **D2** — `order_state` reconstruível a partir de `order_events` + runs/segmentos + dados de mercado versionados; sem eventos por candle; `PROJECTION_INTEGRITY_ERROR` em divergência; teste A == B obrigatório no Plano 2 (spec seção 10).
- **D3** — MFE no candle de stop: o movimento favorável do candle que dispara o stop não entra no MFE (pior caso). Confirma o ruling 13; congelado no v1 (spec seção 10, v1.2).
- **D4** — `DATA_QUALITY` é imutável: fotografia com o conhecimento do `data_as_of` do job de fim de dia, emitida só para o pregão recém-fechado; reavaliações usam `DATA_QUALITY_RECHECK:{session_date}:{data_as_of}` ou `RECALCULATE` (spec seção 10, v1.2).

## Regra de disciplina para o Plano 2

`src/core/` (Plano 1) está congelado durante o desenvolvimento do banco/ledger. Só é alterado se um teste de integração do Plano 2 revelar um defeito real — e então com correção pequena, teste de regressão e registro explícito. Nada de melhorias oportunistas no motor.

## Entradas obrigatórias para o Plano 2

1. **Rebuild por replay de comandos:** percorrer eventos por `seq` e reinvocar `step` (candles as-of de cada segmento), `apply_validity_end`, `cancel`, `freeze`, `flag_review`, `apply_dividend`; comparar `(event_key, payload_hash)`. Só `DATA_QUALITY`/`DATA_GAP` são aplicados sem comando.
2. **Projeção persiste todos os campos de `OrderState`**, inclusive `zone_ever_lost`, `stop_previous`, `t1_done`, `best_price`, `worst_price`, `dividends`, `close_reason`, `review_reasons` (ordem), `frozen_reasons`. `FillConfig(**snapshot)` faz round-trip a partir de JSON.
3. **`DATA_QUALITY` e back-fill do fornecedor:** resolvido pela D4 — emitir apenas para o pregão recém-fechado, com `data_as_of` e `evaluation_run_id` no payload; nunca reemitir; reavaliações com `DATA_QUALITY_RECHECK:{session_date}:{data_as_of}`.
4. **Actionability exige checagem de cobertura as-of** antes de rodar (senão candles ausentes tornam o sinal acionável) → `503 ACTIONABILITY_UNVERIFIABLE`.
5. **Leitor as-of devolve exatamente um candle por minuto** (duplicatas tornariam o resultado dependente da ordem de entrada).
6. **Ledger revalida `bar_ts ≥ evaluation_start_ts`** independentemente de `step()`; persiste `canonical_json(hash_material)` junto do `payload_hash` e nunca recalcula hash a partir de jsonb relido.
7. **Calendários carregados com folga:** pelo menos um pregão antes do timestamp mais antigo consultado e um após `valid_until_ts`; `build_manual_order` pode lançar `CalendarRangeError` puro se o clique for anterior ao primeiro pregão carregado.
8. **Primeira task do Plano 2:** ruff, mypy e teste de fronteira de imports (núcleo puro não importa módulos de I/O; `dataquality` não importa `fills`).
9. **Execução/métricas:** `execution_counts` não aplica filtros de revisão/replay — a API aplica; dividendos precisam ser intercalados nas fronteiras de pregão durante replay.
10. **`nyse_calendar`:** adicionar assert de tz-aware antes de `astimezone` ao mexer no módulo.

## Achados menores adiados (não bloqueiam)

StrEnum/IntEnum no ramo str/int do hash; testes de ordem de chaves de baixo valor; checagens de faixa duplicadas no calendário; sem teste de faixa vazia no loader NYSE; `Event.payload` dict mutável (ledger calcula hash uma vez); divisões sem guarda em `position` (inalcançáveis por caminhos validados); estilos de construção de dict; imports no meio de `tests/support.py`; lacunas de teste unitário (trigger/reclaim SHORT, regra 4 com zona perdida, T2 em candle posterior, percentual de scale-out não padrão); asserts fracos em dois testes (regra/close_reason; "algum evento" às 10:31); relatórios de task com pseudo-código impreciso; reemissão idêntica de NO_EXIT_BAR/DIVIDEND_UNVERIFIED (ledger deduplica); ausência de teste de que `needs_review` sem `frozen` continua avaliando (testar no worker); `quality_window` usa `final_event_ts` em vez de `is_final`; memória O(resamples × n) no bootstrap; `Iterable` de `typing`; avisos de métricas sobre trades pós-filtro; janela de qualidade de ordens CANCELED com segundos e congeladas; conferência diária vs janela parcial; `skipped_bar_ts` quando o minuto precede o início do sinal; teste de alcançabilidade lento (~29 s, marcador `slow`); `coerce_decimal` aceita strings como `"1_000"`.
