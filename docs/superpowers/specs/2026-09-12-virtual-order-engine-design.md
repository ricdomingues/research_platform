# Motor de Ordens Virtuais + Registro de Sinais (Ações EUA) — Design

- **Data:** 2026-09-12
- **Revisão:** 3 — relógio de avaliação, snapshot de dados, idempotência estrita, custos e janela de qualidade
- **Status:** aguardando aprovação para congelamento como **SPEC v1.0 FROZEN**
- **Sub-projeto:** 01 da Research Platform

## 0. Regra de congelamento

Após aprovação, esta spec é congelada como v1.0. A partir do primeiro resultado persistido, **qualquer
mudança na semântica de fill, custo, elegibilidade ou qualidade de dados gera um novo
`fill_model_version`** (ex.: `v2`) implementado em módulo próprio. O `fill_model v1` nunca é
alterado; correções de bug que mudem resultados também geram nova versão. O significado de
"ganhou +1,3R" não pode mudar retroativamente.

## 1. Contexto e objetivo

A Research Platform terá vários produtores de sinais (pré-market EUA "REXSHARE",
estratégia WIN/B3, swing, day trade, padrões de candle). Todos precisam responder à
mesma pergunta: **a estratégia funciona de verdade, sem olhar o futuro?**

Este sub-projeto entrega o componente que responde isso: recebe sinais de qualquer
origem, cria **ordens virtuais**, apura o resultado com candles reais de 1 minuto usando
regras de fill conservadoras e determinísticas, e mostra a eficácia em um dashboard.

Princípio: se o componente que diz se uma estratégia ganhou ou perdeu estiver errado,
toda a plataforma produz números sofisticados e falsos. **Determinismo, reprodutibilidade,
auditabilidade e testes têm prioridade sobre qualquer funcionalidade.**

### 1.1 Decisões de negócio

- **A ordem real sempre parte de um humano**, em corretora externa. A plataforma não envia
  ordens nesta fase.
- Todo sinal gera ordem virtual automática (`AUTO_STRATEGY`); o usuário pode criar ordens
  virtuais manuais (`MANUAL_USER`) sobre o mesmo sinal para comparar estratégia pura com
  seleção humana.
- Execução automática futura via API de corretora: o modelo de ordem é independente do executor.
- Mercado da v1: **ações dos EUA**, horizonte de swing (1–20 pregões).
- Dados de **fontes gratuitas**: Alpaca (IEX), yfinance, FMP (free tier).

### 1.2 Princípios arquiteturais

1. **Semântica da estratégia ≠ tipo de ordem da corretora.** O motor testa a tese do sinal
   (ex.: zona de entrada operacional). Um executor real futuro implementa essa semântica com
   ordens condicionais gerenciadas por software, não com uma única ordem limite.
2. **Order Validator ≠ Portfolio Simulator.** Este motor mede o *edge por sinal*, com resultado
   normalizado em R. Ele **não** mede se uma conta real teria capital, caixa ou limite de
   exposição para executar todos os sinais — responsabilidade do Portfolio Simulator
   (sub-projeto 02). O P&L em dólares deste motor é **por 1R, não dinheiro de conta**.
3. **Dados de research ≠ dados de decisão em produção.** Dados gratuitos são aceitos apenas
   para research e paper trading. Todo lote de dados é marcado com `data_tier`
   (`RESEARCH` | `PRODUCTION`); qualquer executor real futuro deve recusar dados `RESEARCH`.
4. **`domain`, `fills` e `metrics` não fazem I/O.** O mesmo código serve para tempo real,
   replay e backtest.
5. **Nenhuma ordem enxerga mercado anterior à sua existência** (`evaluation_start_ts`, 3.4).
6. **Todo resultado é reproduzível** a partir de sinal, configuração, versão de código e
   versão exata dos dados (3.6).
7. **n8n nunca está no caminho crítico** (candle → fill → evento). Ele só consome resultados.

### 1.3 Roadmap da plataforma (referência)

```text
01 Virtual Order Engine        ← esta spec
02 Portfolio Simulator         (caixa, posições simultâneas, notional máx., exposição, liquidez)
03 Historical Backtester
04 Market Data Platform
05 Technical Indicator Engine
06 Candlestick Engine
07 Chart Pattern Engine
08 Buy/Sell Pressure Engine
09 Strategy Engine
10 Strategy Validation         (out-of-sample, walk-forward, Monte Carlo, sensibilidade)
11 Paper Trading
12 Risk Engine
13 Client Portfolio Engine
14 Broker Execution
15 Audit / Compliance
```

REXSHARE, WIN/B3 e demais estratégias são **produtores de `Signal`** sobre a mesma infraestrutura.

### 1.4 Fora do escopo

Pipeline REXSHARE, estratégias WIN/B3, Portfolio Simulator, importação de fills reais,
`BrokerExecutor`, day trade e avaliação por streaming, multi-cliente, Next.js,
reconhecimento de padrões, simulação de borrow/locate para SHORT, normalização automática de splits.

## 2. Arquitetura

### 2.1 Serviços (Docker Compose)

| Serviço | Responsabilidade |
|---|---|
| `api` (FastAPI) | Recebe sinais, cria/cancela ordens virtuais, expõe ordens, eventos, métricas, replay e health |
| `worker` | Avaliador durante o pregão; jobs de abertura e fim de dia |
| `dashboard` (Streamlit) | UI; comunica-se **exclusivamente com a API** |
| `postgres` | Eventos append-only, projeções, lotes e snapshots de dados de mercado |

Roda localmente e em VPS sem alterações.

### 2.2 Módulos do pacote `core/`

| Módulo | Responsabilidade | Depende de | I/O |
|---|---|---|---|
| `domain/` | `Signal`, `Order`, eventos, calendário de minutos esperados (dados já carregados), invariantes; máquina de estados pura `step(state, bar, ctx) -> list[Event]` | — | não |
| `fills/v1/` | Regras de fill da seção 4; registro `get_fill_model(version)` | `domain` | não |
| `metrics/` | Métricas, bootstrap e permutação (5.4) | `domain` | não |
| `marketdata/` | `BarSource` (`AlpacaBars`, `YFinanceBars`), `DividendSource` (FMP, yfinance), lotes, snapshots, leitura "as of" | APIs externas, Postgres | sim |
| `ledger/` | Append idempotente com verificação de hash, locks por ordem, segmentos de avaliação, reconstrução de projeções | Postgres | sim |
| `evaluator/` | Ciclo: ordens abertas → candles as-of → `domain`+`fills` → `ledger` | todos acima | sim |

### 2.3 Extensão futura de execução

Interface `ExecutionAdapter`: `VirtualExecutor` (**esta spec**), `ImportedFills` (futuro),
`BrokerExecutor` (futuro; exigirá gate de risco, kill switch, ordens idempotentes e dados `PRODUCTION`).

### 2.4 Contrato com o Portfolio Simulator (futuro)

Cada ordem fechada expõe: `signal_id`, `ticker`, `direction`, `evaluation_start_ts`, `filled_at`,
`closed_at`, `avg_entry`, `initial_stop`, `stop_distance`, `qty_per_1r`, `notional_per_1r`,
`r_multiple`, fills parciais com timestamps, `config_snapshot` e referência de dados (3.6).
O Portfolio Simulator reprocessa esses resultados aplicando caixa, limites e arredondamento;
ele não altera eventos deste motor.

## 3. Modelo de dados e ciclo de vida

### 3.1 Tabelas

```text
signals            -- imutável
  id uuid PK, client_signal_id text UNIQUE, payload_hash text, created_at timestamptz,
  strategy text, strategy_version text, source text,
  ticker text, direction text CHECK (LONG|SHORT),
  entry_zone_low numeric, entry_zone_high numeric, trigger_price numeric NULL,
  confirmation_note text NULL,
  target1 numeric, target2 numeric NULL, stop numeric,
  valid_sessions int CHECK (1..20), valid_until_ts timestamptz,
  score numeric NULL, thesis text NULL, raw_payload jsonb

orders
  id uuid PK, signal_id uuid FK, origin text CHECK (AUTO_STRATEGY|MANUAL_USER),
  created_at timestamptz, evaluation_start_ts timestamptz, valid_until_ts timestamptz,
  fill_model_version text, config_snapshot jsonb, code_version text,
  replay boolean DEFAULT false, replay_mode text NULL (REPRODUCE|RECALCULATE),
  replay_of_order_id uuid NULL, market_data_snapshot_id uuid NULL FK,
  risk_amount numeric

order_events       -- APPEND-ONLY
  id bigserial PK, order_id uuid FK, seq int, event_key text, payload_hash text, type text,
  bar_ts timestamptz NULL, price numeric NULL, qty numeric NULL,
  bar_batch_id uuid NULL, payload jsonb, recorded_at timestamptz
  UNIQUE (order_id, seq), UNIQUE (order_id, event_key)

order_state        -- projeção, reconstruível
  order_id PK, status, zone_lost boolean, entry_eligible_from timestamptz NULL,
  trigger_hit_at timestamptz NULL, entry_path text NULL (DIRECT|RECLAIMED),
  avg_entry, initial_stop, stop_current, stop_active_from timestamptz NULL,
  qty_total, qty_open, realized_pnl, costs, r_multiple, mfe_r, mae_r,
  opened_at, closed_at, final_event_ts timestamptz NULL, last_bar_ts,
  expected_bars int, missing_bars int, needs_review boolean, frozen boolean

evaluation_runs    -- APPEND-ONLY; um por ciclo do worker ou execução de replay
  run_id uuid PK, kind text (LIVE|REPLAY), data_as_of timestamptz, started_at, finished_at

order_eval_segments -- APPEND-ONLY; quais candles cada run processou para cada ordem
  order_id uuid FK, run_id uuid FK, bar_from timestamptz, bar_to timestamptz
  PK (order_id, run_id)

bar_batches        -- APPEND-ONLY; proveniência de cada ingestão
  batch_id uuid PK, provider text, provider_version text, data_tier text CHECK (RESEARCH|PRODUCTION),
  request jsonb, content_hash text, ingested_at timestamptz

bars_1m            -- APPEND-ONLY; correções do fornecedor viram novas linhas
  ticker, ts, open, high, low, close, volume, source, batch_id FK
  PK (ticker, ts, source, batch_id)

market_data_snapshots -- APPEND-ONLY
  id uuid PK, created_at, source text, data_as_of timestamptz,
  tickers text[], range_from timestamptz, range_to timestamptz, content_manifest_hash text

dividends
  ticker, ex_date, amount, pay_date, sources text[], validated boolean, checked_at
  PK (ticker, ex_date)

integrity_incidents -- APPEND-ONLY
  id bigserial PK, kind text, order_id uuid NULL, event_key text NULL,
  existing_hash text, attempted_hash text, detail jsonb, recorded_at
```

Timestamps em **UTC**; conversão para ET apenas na borda. `bar_ts` é o **início** do minuto do
candle. `code_version` vem da variável `GIT_SHA` gravada no build. `config_snapshot` contém todos os
parâmetros da seção 8 marcados como "sim".

### 3.2 Eventos e `event_key`

| Tipo | `event_key` |
|---|---|
| `ORDER_CREATED` | `ORDER_CREATED` |
| `TRIGGER_HIT` | `TRIGGER_HIT` |
| `ZONE_LOST` | `ZONE_LOST:{bar_ts}` |
| `ZONE_RECLAIMED` | `ZONE_RECLAIMED:{bar_ts}` |
| `FILLED` | `FILLED` |
| `TARGET1_HIT` | `TARGET1_HIT` |
| `TARGET2_HIT` | `TARGET2_HIT` |
| `STOPPED` | `STOPPED` |
| `TIME_EXIT` | `TIME_EXIT` |
| `EXPIRED` | `EXPIRED` |
| `INVALIDATED` | `INVALIDATED` |
| `CANCELED` | `CANCELED` |
| `FROZEN` | `FROZEN:{reason}` |
| `DIVIDEND` | `DIVIDEND:{ex_date}` |
| `DATA_QUALITY` | `DATA_QUALITY:{session_date}` |
| `DATA_GAP` | `DATA_GAP:{gap_start_ts}` |
| `NEEDS_REVIEW` | `NEEDS_REVIEW:{reason}:{ref}` |

**Eventos dependentes de mercado:** `TRIGGER_HIT`, `ZONE_LOST`, `ZONE_RECLAIMED`, `FILLED`,
`TARGET1_HIT`, `TARGET2_HIT`, `STOPPED`, `TIME_EXIT`, `INVALIDATED`. Todos têm `bar_ts` obrigatório.

### 3.3 Idempotência estrita

- `payload_hash` = SHA-256 da serialização canônica (JSON com chaves ordenadas, decimais normalizados,
  timestamps ISO-8601 UTC) de `{type, bar_ts, price, qty, bar_batch_id, payload}`.
- Inserção com `event_key` inexistente → grava.
- `event_key` existente **e** `payload_hash` igual → no-op.
- `event_key` existente **e** `payload_hash` diferente → **`IntegrityError`**: transação abortada,
  registro em `integrity_incidents`, ordem marcada `frozen` e `NEEDS_REVIEW:INTEGRITY`, `/health`
  degradado e webhook de alerta. Nunca é silenciado.
- `POST /signals`: `payload_hash` do corpo canônico. Mesmo `client_signal_id` + mesmo hash → `200`
  com o recurso existente; hash diferente → **`409 IDEMPOTENCY_CONFLICT`**.

### 3.4 Relógio de avaliação

- **Minutos esperados:** conjunto de inícios de minuto do pregão regular dado pelo calendário NYSE
  (09:30 a 15:59 ET, ou até o fechamento antecipado em meio pregão).
- **Próximo candle** = **próximo minuto esperado** do calendário após `bar_ts`, atravessando
  fechamento, fins de semana e feriados. Nunca `bar_ts + 1 min` nem "próximo candle recebido".
  Se o minuto esperado não tiver candle, a condição passa a valer a partir do primeiro candle com
  `bar_ts ≥` esse minuto.
- **`evaluation_start_ts`** = primeiro minuto esperado `m` com `m ≥ created_at`, em que
  `created_at` é o do **sinal** para `AUTO_STRATEGY` e o da **própria ordem** para `MANUAL_USER`.
  Exemplos (ET): criado 08:00 → 09:30 do mesmo pregão; 10:30:00 → 10:30; 10:30:20 → 10:31;
  15:59:30 → 09:30 do próximo pregão.
- **Invariante de domínio:** nenhum evento dependente de mercado pode ter
  `bar_ts < evaluation_start_ts`. `step()` descarta candles anteriores, e o `ledger` rejeita
  qualquer evento que viole a invariante com `IntegrityError` (3.3).
- **Validade:** o pregão 1 é o pregão que contém o `evaluation_start_ts` do sinal. Sinal criado
  durante o pregão conta o pregão atual. `signals.valid_until_ts` = fechamento do pregão
  `valid_sessions`.
- **Ordem manual:** `orders.valid_until_ts = signals.valid_until_ts` (mesmo horizonte da tese).
  Criar ordem manual com `evaluation_start_ts > valid_until_ts` → `422`.

### 3.5 Estados

```text
PENDING ─(entrada elegível na zona)─► OPEN ─(target1)─► PARTIAL ─(target2)─► CLOSED
   │  ▲                                 │                  │
   │  │ ZONE_RECLAIMED                  ├─(stop)─► CLOSED  ├─(stop em breakeven)─► CLOSED
   │  │ (flag zone_lost limpa)          └─(validade)─► CLOSED
   │  │
   ├──┴ ZONE_LOST (flag zone_lost; continua PENDING, entrada bloqueada)
   ├─(validade sem entrada)─► EXPIRED
   └─(stop atingido sem posição)─► INVALIDATED
qualquer estado não final ─(humano)─► CANCELED
qualquer estado não final ─(split ou incidente de integridade)─► flag frozen (sem novos eventos de mercado)
```

`ZONE_LOST`/`ZONE_RECLAIMED` alteram apenas flags da projeção. `DATA_QUALITY`, `DATA_GAP` e
`NEEDS_REVIEW` são marcações. Uma ordem `frozen` não recebe eventos de mercado; é resolvida por
cancelamento humano ou por replay em nova ordem.

### 3.6 Reprodutibilidade de dados

- **Leitura as-of:** para um instante `T`, a versão de um candle `(ticker, ts, source)` é a linha
  com maior `ingested_at ≤ T` (desempate por `batch_id`). Como `bars_1m` e `bar_batches` são
  append-only, a leitura as-of é determinística para sempre.
- **Ordens live:** cada ciclo do worker cria um `evaluation_runs` com `data_as_of` = instante do
  início do ciclo e grava em `order_eval_segments` o intervalo `[bar_from, bar_to]` processado por
  ordem. A versão exata de cada candle usado é recuperável: candles do segmento lidos as-of o
  `data_as_of` do run.
- **Snapshots:** `market_data_snapshots` fixa `data_as_of`, fonte, tickers e intervalo, com
  `content_manifest_hash` = SHA-256 da lista ordenada `(ticker, ts, batch_id)` selecionada.
- **Replay em dois modos** (`POST /replay`):
  - `REPRODUCE` — para cada ordem de origem, reprocessa usando exatamente os segmentos e
    `data_as_of` originais, com a mesma `fill_model_version` e `config_snapshot`. O resultado
    **deve** ser idêntico evento a evento; qualquer divergência é `IntegrityError`.
  - `RECALCULATE` — cria (ou reutiliza) um `market_data_snapshot` com o `data_as_of` informado
    (padrão: agora) e reprocessa os sinais com a versão/configuração informadas. A nova ordem grava
    `market_data_snapshot_id`.
- Replays criam **novas ordens** (`replay=true`, `replay_of_order_id`) com o mesmo
  `evaluation_start_ts` e `valid_until_ts` da ordem de origem; nada existente é alterado.

### 3.7 Regras de posição

- **Tamanho normalizado em R:** `qty_per_1r = risk_amount / |avg_entry − initial_stop|`
  (fracionário). Métricas em R e em %. Sem limite de notional nesta camada (1.2 item 2).
- **Target 1:** realiza `TARGET1_SCALE_OUT_PCT` (padrão 50%) e move o stop para `avg_entry`. O novo
  stop vale a partir do **próximo minuto esperado** após o candle do target 1 (`stop_active_from`).
  Sem `target2`, target 1 fecha 100%.
- **`r_multiple`** = (P&L realizado − custos + dividendos) ÷ `risk_amount`.
- **Confirmação:** apenas `trigger_price` é avaliado; `confirmation_note` é só exibição.
- **`entry_path`:** `DIRECT` se o fill ocorreu sem `ZONE_LOST` anterior; `RECLAIMED` caso contrário.

### 3.8 Validação de sinais

| Direção | Com `target2` | Sem `target2` |
|---|---|---|
| LONG | `stop < entry_zone_low ≤ entry_zone_high < target1 < target2` | `stop < entry_zone_low ≤ entry_zone_high < target1` |
| SHORT | `stop > entry_zone_high ≥ entry_zone_low > target1 > target2` | `stop > entry_zone_high ≥ entry_zone_low > target1` |

Também: `trigger_price`, se presente, positivo; ticker ativo e negociável na Alpaca;
`valid_sessions` entre 1 e 20. Violação → `422` com motivo.

## 4. Regras de fill — `fill_model v1`

Princípios: **nunca inferir trajetória intrabar**; quando o candle é ambíguo, assumir o pior caso;
quando a decisão depende da ordem dos acontecimentos dentro do candle, adiar para o próximo minuto
esperado. **Entrada exige atravessar; alvo basta tocar; stop basta tocar.**

Regras escritas para LONG; SHORT é o espelho exato (inverter comparações, `high`↔`low`,
`entry_zone_low`↔`entry_zone_high`, `min`↔`max`).

### 4.1 Pregão e dados

- Apenas minutos esperados (3.4). Candles brutos (`adjustment=raw`) da Alpaca IEX, lidos as-of (3.6).
  Apenas candles **fechados**.
- Candles com `bar_ts < evaluation_start_ts` ou `bar_ts ≥ valid_until_ts` nunca são processados
  (exceto a regra de saída por validade em 4.4).
- Uma ordem nunca mistura fontes de preço para fills; yfinance é conferência.
- Cada evento dependente de mercado grava `bar_batch_id`.

### 4.2 Elegibilidade de entrada

Uma ordem `PENDING` e não `frozen` é **elegível** num candle quando:

1. `zone_lost = false`;
2. `bar_ts ≥ entry_eligible_from` (quando definido);
3. se `trigger_price` existe: `trigger_hit_at` definido e `bar_ts ≥` próximo minuto esperado após
   `trigger_hit_at`.

### 4.3 Entrada (LONG)

Avaliação em ordem para cada candle de ordem `PENDING`; a primeira regra terminal (fill ou
`INVALIDATED`) encerra a fase de entrada do candle. Regras 5 e 6 podem ocorrer no mesmo candle.

| # | Condição | Resultado |
|---|---|---|
| 1 | `open ≤ stop` | `INVALIDATED` |
| 2 | elegível e `entry_zone_low ≤ open ≤ entry_zone_high` | `FILLED` a `open` (+ slippage de entrada); segue para a checagem de stop de 4.4 no mesmo candle |
| 3 | elegível e `open > entry_zone_high` e `low < entry_zone_high` | `FILLED` a `entry_zone_high` (+ slippage de entrada); segue para a checagem de stop de 4.4 no mesmo candle |
| 4 | sem fill neste candle e `low ≤ stop` | `INVALIDATED` |
| 5 | `zone_lost = false` e (`open < entry_zone_low` ou `close < entry_zone_low`) | `ZONE_LOST`, sem fill (com `ZONE_LOST_POLICY=CANCEL`: `INVALIDATED`) |
| 6 | `zone_lost = true` (inclusive se definido pela regra 5 neste candle) e `close ≥ entry_zone_low` | `ZONE_RECLAIMED`; `entry_eligible_from` = próximo minuto esperado |
| 7 | nenhum dos anteriores | permanece `PENDING` |

Por fim, se `trigger_price` existe e ainda não houve `TRIGGER_HIT`, `high ≥ trigger_price` registra
`TRIGGER_HIT` (efeito a partir do próximo minuto esperado, 4.2).

**Definição de perda de zona:** o suporte é considerado perdido **por abertura ou fechamento** abaixo
de `entry_zone_low`. Violação intrabar (apenas `low < entry_zone_low` com `open` e `close` na zona ou
acima) **não** é perda de zona. Isso é intencional; trocar para `low < entry_zone_low` é mudança de
semântica e exige `fill_model v2`.

**Consequências:** ordem elegível que entra na zona e atinge o stop no mesmo candle → fill seguido de
`STOPPED` (pior caso). Candle sem fill que atinge o stop → `INVALIDATED`. O candle que gera
`ZONE_RECLAIMED` nunca gera fill. `ZONE_LOST` e `ZONE_RECLAIMED` podem ocorrer múltiplas vezes.

**Política `ZONE_LOST_POLICY`** (em `config_snapshot`): `RECLAIM` (padrão) ou `CANCEL`.

**Limitação documentada:** fila de ordem limite não é simulada. A exigência de atravessar
`entry_zone_high` (regra 3) é a penalização compensatória.

### 4.4 Saídas

- **Stop:** quando `low ≤ stop_vigente`; fill a `min(open, stop_vigente)` com slippage adverso de
  `STOP_SLIPPAGE_BPS`. `stop_vigente` é `stop_current` se `bar_ts ≥ stop_active_from`, senão o stop
  anterior.
- **Alvo:** quando `high ≥ target`; fill ao preço do alvo, sem ganho de gap e sem slippage.
- **Stop e alvo no mesmo candle:** stop primeiro.
- **Candle de entrada:** avalia apenas stop; alvos a partir do próximo minuto esperado.
- **Candle do target 1:** o breakeven não vale nesse candle (ver `stop_active_from`).
- **Validade:** posição aberta sai no `close` do último minuto esperado antes de `valid_until_ts`,
  com slippage adverso de `STOP_SLIPPAGE_BPS` (`TIME_EXIT`). Ordem pendente vira `EXPIRED`.

### 4.5 Custos

- `COMMISSION_PER_EXECUTION` (padrão $0): cobrada **em cada execução** — entrada, target 1,
  target 2, stop e `TIME_EXIT` — independentemente da quantidade.
- `ENTRY_SLIPPAGE_BPS` (padrão 0): adverso sobre o preço de fill das regras 4.3 #2 e #3.
- `STOP_SLIPPAGE_BPS` (padrão 5): adverso em stop e `TIME_EXIT`.
- `SEC_TAF_FEES_ENABLED` (padrão false): taxas regulatórias sobre vendas (LONG na saída, SHORT na entrada).
- Custos acumulados em `order_state.costs` e descontados em `r_multiple`.

### 4.6 Qualidade de dados

- **Janela de qualidade** de uma ordem = `[evaluation_start_ts, final_event_ts]`
  (ou até o momento atual, se não final). Ordens finalizadas preservam as métricas da janela.
- **Contagem:** `expected_bars` = minutos esperados na janela; `missing_bars` = minutos esperados sem
  candle na janela.
- **`DATA_QUALITY`:** no job de fim de dia, um evento por ordem **para cada pregão que intersecta sua
  janela** (inclusive o pregão em que a ordem finalizou), com minutos esperados, ausentes, cobertura %
  e lista de minutos ausentes.
- **Verificação de minutos ausentes:** para cada minuto ausente na janela, buscar o candle de 1m do
  yfinance. Se ele tocar qualquer nível ativo naquele minuto (`entry_zone_low`, `entry_zone_high`,
  `stop_vigente`, `target1`, `target2`, `trigger_price`) → `NEEDS_REVIEW:MISSING_BAR_LEVEL_TOUCH`.
  Se o yfinance não tiver o minuto → `NEEDS_REVIEW:MISSING_BAR_UNVERIFIABLE`.
- **`DATA_GAP`:** ausência contínua ≥ `DATA_GAP_MINUTES` minutos esperados; alerta de incidente.
- **Conferência diária:** máxima/mínima agregadas do IEX na janela vs diária do yfinance com divergência
  acima de `CROSSCHECK_TOLERANCE_PCT` → `NEEDS_REVIEW:DAILY_RANGE_MISMATCH`.
- `needs_review` não interrompe a avaliação (exceto `frozen`); a política nas métricas está em 5.4.

### 4.7 Eventos corporativos

- **Dividendo:** posição com `qty_open > 0` no fechamento do pregão anterior à ex-date recebe
  `DIVIDEND` (`amount × qty_open`; LONG credita, SHORT debita). Válido somente se FMP e yfinance
  concordarem (tolerância `DIVIDEND_TOLERANCE`); caso contrário `NEEDS_REVIEW:DIVIDEND_UNVERIFIED` e
  nenhum crédito.
- **Split** detectado para o ticker de **qualquer ordem não final** (pendente ou com posição): `FROZEN:SPLIT`
  e `NEEDS_REVIEW:SPLIT`. Normalização de níveis é feita apenas via novo sinal ou replay futuro; não
  há ajuste automático na v1.
- **SHORT:** borrow, locate, hard-to-borrow e custo de empréstimo **não são simulados**. Métricas e
  telas com ordens SHORT exibem esse aviso fixo.

## 5. Interfaces

### 5.1 API

Autenticação: header `X-API-Key` com chave única via variável de ambiente.

| Método e rota | Função |
|---|---|
| `POST /signals` | Cria sinal (3.3); cria ordem `AUTO_STRATEGY` salvo `auto_order=false`. `200` se já existente com mesmo hash, `201` se criado, `409` se conflito, `422` se inválido |
| `GET /signals?date&strategy` | Lista sinais |
| `POST /signals/{id}/orders` | Cria ordem `MANUAL_USER` (3.4) |
| `POST /orders/{id}/cancel` | Cancela ordem não final |
| `GET /orders?status&origin&strategy&replay&needs_review` | Lista ordens com estado projetado |
| `GET /orders/{id}` | Detalhe, eventos, segmentos, qualidade de dados, candles do período |
| `GET /metrics?group_by=strategy\|origin\|fill_model_version\|entry_path&from&to&include_needs_review` | Métricas agregadas (5.4) |
| `POST /replay` | `{mode: REPRODUCE\|RECALCULATE, order_ids? , from?, to?, fill_model_version?, config_overrides?, data_as_of?}`; `REPRODUCE` aceita apenas `order_ids` ou intervalo |
| `GET /health` | Último ciclo, frescor dos dados, falhas consecutivas, fila `NEEDS_REVIEW`, incidentes de integridade |

### 5.2 Concorrência

- Toda escrita de eventos de uma ordem ocorre em transação que começa com
  `SELECT … FROM orders WHERE id = :id FOR UPDATE`, recarrega a projeção e só então decide.
- Worker e `POST /orders/{id}/cancel` usam o mesmo caminho; o worker ignora ordens que, após o lock,
  estejam finais ou `frozen`.
- O ciclo do worker adquire também um advisory lock global; se ocupado, o ciclo sai sem trabalho.

### 5.3 Worker

- **Ciclo do avaliador:** a cada `EVAL_INTERVAL_MINUTES`, 09:30–16:05 ET, em pregões.
  1. Adquire advisory lock; cria `evaluation_runs` (`LIVE`, `data_as_of` = agora).
  2. Carrega ordens não finais e não `frozen`; agrupa por ticker.
  3. Ingere candles novos (novo `bar_batches` + `bars_1m`) desde o menor `last_bar_ts` do ticker
     ou `evaluation_start_ts`, o que for maior.
  4. Por ordem: lock (5.2) → lê candles fechados as-of `data_as_of` → processa em ordem cronológica →
     grava eventos, `order_eval_segments`, `last_bar_ts` e projeção na mesma transação.
- **Job de abertura (09:25 ET):** valida dividendos com ex-date no dia e aplica `DIVIDEND`;
  verifica splits (4.7).
- **Job de fim de dia (16:30 ET):** validade, `DATA_QUALITY`, verificação de minutos ausentes,
  conferência diária, webhook opcional (`N8N_WEBHOOK_URL`) com resumo.

### 5.4 Métricas

Sobre ordens `CLOSED`, filtráveis por `replay`:

- **Política de revisão:** por padrão (`include_needs_review=false`) as métricas principais
  **excluem** ordens com `needs_review = true` e a resposta sempre informa `excluded_needs_review`
  (quantidade e lista de motivos). Com `include_needs_review=true`, as incluídas são contadas
  separadamente em `included_needs_review`. Nunca misturar silenciosamente.
- Nº de trades, win rate (`r_multiple > 0`), R médio, expectância em R, profit factor,
  drawdown máximo histórico em R (sequência por `closed_at`), duração média, MFE/MAE médios em R.
- **Taxa de execução** = ordens com `FILLED` ÷ ordens resolvidas (com `FILLED`, `EXPIRED` ou
  `INVALIDATED`); `PENDING`, `frozen` e `CANCELED` antes do fill ficam fora.
- **Intervalos de confiança (bootstrap):** 95% para win rate e expectância, com
  `BOOTSTRAP_RESAMPLES` reamostragens com reposição e semente `BOOTSTRAP_SEED`.
- **Monte Carlo / permutação da ordem dos trades:** percentis 5/50/95 do drawdown máximo sob
  `BOOTSTRAP_RESAMPLES` permutações (semente fixa). Rotulado como **risco de sequência**, não como
  intervalo de confiança do drawdown histórico.
- Aviso "amostra insuficiente" quando nº de trades < 30. Aviso SHORT (4.7) quando aplicável.

### 5.5 Dashboard

1. **Visão geral** — curva de R acumulado, cards de métricas com intervalos, contagem de excluídas por revisão.
2. **Sinais do dia** — lista com status e botão "Comprar virtual".
3. **Ordens** — tabela filtrável; detalhe com gráfico de candles, zona de entrada, stop, alvos,
   `evaluation_start_ts`, marcadores de eventos (incluindo `ZONE_LOST`/`ZONE_RECLAIMED`), qualidade de dados e log.
4. **Comparação** — AUTO × MANUAL, estratégia, versão/configuração de fill model, `entry_path`,
   original × replay.
5. **Saúde** — último ciclo, cobertura de dados, `DATA_GAP`, fila `NEEDS_REVIEW` por motivo,
   incidentes de integridade, ordens `frozen`.

## 6. Tratamento de erros

| Falha | Comportamento |
|---|---|
| Alpaca indisponível / rate limit | 3 tentativas com backoff; falhando, pula o ticker sem avançar cursor. 3 ciclos consecutivos com falha → `/health` degradado e webhook |
| yfinance / FMP indisponíveis | Dividendos não validados e minutos não verificáveis → `NEEDS_REVIEW` com motivo; nenhum crédito |
| Sinal inválido | `422` (3.8) |
| `client_signal_id` repetido com payload diferente | `409 IDEMPOTENCY_CONFLICT` |
| Mesmo `event_key` com hash diferente | `IntegrityError`, `integrity_incidents`, ordem `frozen` (3.3) |
| Evento com `bar_ts < evaluation_start_ts` | `IntegrityError` (3.4) |
| Replay `REPRODUCE` divergente | `IntegrityError` com diff de eventos |
| Worker cai no meio do ciclo | Transação por ordem; retoma pelo cursor |
| Worker e API na mesma ordem | `SELECT … FOR UPDATE` (5.2) |
| Alteração de histórico | Role sem `UPDATE`/`DELETE` nas tabelas append-only + trigger que rejeita |
| Projeção inconsistente | Comando `rebuild-projections` a partir dos eventos |

## 7. Testes (TDD)

- **Unitários puros** — cada linha da tabela 4.3 e cada regra de 3.4, 3.7, 4.4, 4.5, 4.6 e 4.7,
  LONG e SHORT. Casos obrigatórios:
  - candle `open=99, high=103, low=96, close=101`, zona `[100,102]`, stop `97`, sem posição → `INVALIDATED`;
  - `open > entry_zone_high` e `low = entry_zone_high` → **sem fill** (entrada exige atravessar);
  - `high = target1` → `TARGET1_HIT` (alvo por toque);
  - `ZONE_LOST` → candle com `close ≥ entry_zone_low` → `ZONE_RECLAIMED` sem fill → fill só no próximo minuto esperado;
  - violação intrabar `open=103, low=99, close=101`, `entry_zone_low=100` → **sem** `ZONE_LOST`;
  - `ZONE_RECLAIMED` no último minuto do pregão → elegível só às 09:30 do próximo pregão;
  - target 1 e mínima abaixo do breakeven no mesmo candle → breakeven não aplicado nesse candle;
  - `ZONE_LOST_POLICY=CANCEL`;
  - sinal criado às 11:30 com o preço dentro da zona às 10:00 → nenhum evento antes de 11:30;
  - ordem manual criada às 14:00 sobre sinal das 08:00 → nenhum evento antes de 14:00; validade igual à do sinal;
  - `evaluation_start_ts` para 08:00, 10:30:00, 10:30:20, 15:59:30 e véspera de feriado;
  - comissão cobrada em entrada, target 1 e stop (3 execuções);
  - split com ordem `PENDING` → `FROZEN:SPLIT`.
- **Propriedades (Hypothesis)** sobre sequências aleatórias de candles e sinais válidos:
  - `qty_open ≥ 0`; P&L realizado = soma dos fills − custos + dividendos;
  - determinismo: mesma entrada → mesmos eventos e mesmos `payload_hash`;
  - sem look-ahead: eventos com candles até *t* são prefixo dos eventos com a série completa;
  - **nenhum evento dependente de mercado com `bar_ts < evaluation_start_ts`**, para qualquer `created_at`;
  - idempotência: reprocessar a mesma série não cria eventos e não gera `IntegrityError`;
  - nenhum fill no mesmo candle de `ZONE_RECLAIMED`;
  - espelhamento: sinal SHORT sobre candles espelhados produz os mesmos tipos de evento e o mesmo `r_multiple` do LONG equivalente.
- **Métricas** — exemplos à mão; bootstrap e permutação com semente fixa reproduzem os mesmos valores;
  política de exclusão de `needs_review`.
- **Integração** — Postgres real em Docker: idempotência estrita (mesmo hash → no-op; hash diferente →
  `IntegrityError` e incidente), `409` em `POST /signals`, trigger append-only, advisory lock, disputa
  cancel × worker com `FOR UPDATE`, leitura as-of com lote corrigido posterior, `REPRODUCE` idêntico após
  correção do fornecedor, `RECALCULATE` usando a correção, rotas via `httpx`.
- **Dados de mercado** — fixtures gravadas de Alpaca, yfinance e FMP; nenhum teste acessa rede.
- **Ponta a ponta** — pregão histórico gravado: sinal → ordem → eventos → projeção → métricas
  comparados a resultado esperado; em seguida `REPRODUCE` idêntico.

## 8. Configuração

| Variável | Padrão | Em `config_snapshot` |
|---|---|---|
| `API_KEY` | — (obrigatória) | não |
| `DATABASE_URL` | — (obrigatória) | não |
| `ALPACA_API_KEY`, `ALPACA_SECRET_KEY` | — (obrigatórias) | não |
| `FMP_API_KEY` | — (obrigatória) | não |
| `GIT_SHA` | `unknown` | como `code_version` |
| `EVAL_INTERVAL_MINUTES` | 2 | não |
| `DEFAULT_RISK_AMOUNT` | 100 | sim |
| `ENTRY_SLIPPAGE_BPS` | 0 | sim |
| `STOP_SLIPPAGE_BPS` | 5 | sim |
| `COMMISSION_PER_EXECUTION` | 0 | sim |
| `SEC_TAF_FEES_ENABLED` | false | sim |
| `ZONE_LOST_POLICY` | RECLAIM | sim |
| `TARGET1_SCALE_OUT_PCT` | 50 | sim |
| `DATA_GAP_MINUTES` | 30 | sim |
| `CROSSCHECK_TOLERANCE_PCT` | 0.5 | sim |
| `DIVIDEND_TOLERANCE` | 0.001 | sim |
| `BOOTSTRAP_RESAMPLES` | 2000 | não (parâmetro de métrica) |
| `BOOTSTRAP_SEED` | 42 | não (parâmetro de métrica) |
| `N8N_WEBHOOK_URL` | vazio (desligado) | não |

## 9. Stack

Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 + Alembic, PostgreSQL 16, APScheduler,
`pandas_market_calendars`, `alpaca-py`, `yfinance`, `httpx`, NumPy, Streamlit, Plotly,
pytest + Hypothesis, Docker Compose, `uv`.
