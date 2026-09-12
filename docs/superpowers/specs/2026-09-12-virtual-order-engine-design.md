# Motor de Ordens Virtuais + Registro de Sinais (Ações EUA) — Design

- **Data:** 2026-09-12
- **Revisão:** 2 — incorpora revisão técnica (P0/P1) e semântica operacional da zona de entrada
- **Status:** aguardando revisão da spec escrita
- **Sub-projeto:** 01 da Research Platform

## 1. Contexto e objetivo

A Research Platform terá vários produtores de sinais (pré-market EUA "REXSHARE",
estratégia WIN/B3, swing, day trade, padrões de candle). Todos precisam responder à
mesma pergunta: **a estratégia funciona de verdade, sem olhar o futuro?**

Este sub-projeto entrega o componente que responde isso: recebe sinais de qualquer
origem, cria **ordens virtuais**, apura o resultado com candles reais de 1 minuto usando
regras de fill conservadoras e determinísticas, e mostra a eficácia em um dashboard.

Princípio: se o componente que diz se uma estratégia ganhou ou perdeu estiver errado,
toda a plataforma produz números sofisticados e falsos. **Determinismo, auditabilidade e
testes têm prioridade sobre qualquer funcionalidade.**

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
   exposição para executar todos os sinais. Isso é responsabilidade do Portfolio Simulator
   (sub-projeto 02). O P&L em dólares deste motor é **por 1R, não dinheiro de conta**.
3. **Dados de research ≠ dados de decisão em produção.** Dados gratuitos são aceitos apenas
   para research e paper trading. Todo lote de dados é marcado com `data_tier`
   (`RESEARCH` | `PRODUCTION`); qualquer executor real futuro deve recusar dados `RESEARCH`.
4. **`domain`, `fills` e `metrics` não fazem I/O.** O mesmo código serve para tempo real,
   replay e backtest.
5. **n8n nunca está no caminho crítico** (candle → fill → evento). Ele só consome resultados.

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
reconhecimento de padrões, simulação de borrow/locate para SHORT.

## 2. Arquitetura

### 2.1 Serviços (Docker Compose)

| Serviço | Responsabilidade |
|---|---|
| `api` (FastAPI) | Recebe sinais, cria/cancela ordens virtuais, expõe ordens, eventos, métricas, replay e health |
| `worker` | Avaliador durante o pregão; jobs de abertura e fim de dia |
| `dashboard` (Streamlit) | UI; comunica-se **exclusivamente com a API** |
| `postgres` | Eventos append-only, projeções, lotes de dados de mercado |

Roda localmente e em VPS sem alterações.

### 2.2 Módulos do pacote `core/`

| Módulo | Responsabilidade | Depende de | I/O |
|---|---|---|---|
| `domain/` | `Signal`, `Order`, eventos; máquina de estados pura `step(state, bar, ctx) -> list[Event]` | — | não |
| `fills/` | Regras de fill da seção 4 | `domain` | não |
| `metrics/` | Métricas e bootstrap (seção 5.4) | `domain` | não |
| `marketdata/` | `BarSource` (`AlpacaBars`, `YFinanceBars`), `DividendSource` (FMP, yfinance), lotes e proveniência | APIs externas, Postgres | sim |
| `ledger/` | Append idempotente, locks por ordem, reconstrução de projeções | Postgres | sim |
| `evaluator/` | Ciclo: ordens abertas → candles → `domain`+`fills` → `ledger` | todos acima | sim |

### 2.3 Extensão futura de execução

Interface `ExecutionAdapter`: `VirtualExecutor` (**esta spec**), `ImportedFills` (futuro),
`BrokerExecutor` (futuro; exigirá gate de risco, kill switch, ordens idempotentes e dados `PRODUCTION`).

### 2.4 Contrato com o Portfolio Simulator (futuro)

Cada ordem fechada expõe: `signal_id`, `ticker`, `direction`, `filled_at`, `closed_at`,
`avg_entry`, `initial_stop`, `stop_distance`, `qty_per_1r`, `notional_per_1r`, `r_multiple`,
fills parciais com timestamps e `config_snapshot`. O Portfolio Simulator reprocessa esses
resultados aplicando caixa, limites e arredondamento; ele não altera eventos deste motor.

## 3. Modelo de dados e ciclo de vida

### 3.1 Tabelas

```text
signals            -- imutável
  id uuid PK, client_signal_id text UNIQUE, created_at timestamptz,
  strategy text, strategy_version text, source text,
  ticker text, direction text CHECK (LONG|SHORT),
  entry_zone_low numeric, entry_zone_high numeric, trigger_price numeric NULL,
  confirmation_note text NULL,
  target1 numeric, target2 numeric NULL, stop numeric,
  valid_sessions int CHECK (1..20), score numeric NULL, thesis text NULL,
  raw_payload jsonb

orders
  id uuid PK, signal_id uuid FK, origin text CHECK (AUTO_STRATEGY|MANUAL_USER),
  fill_model_version text, config_snapshot jsonb, code_version text,
  replay boolean DEFAULT false, risk_amount numeric, created_at timestamptz

order_events       -- APPEND-ONLY
  id bigserial PK, order_id uuid FK, seq int, event_key text, type text,
  bar_ts timestamptz NULL, price numeric NULL, qty numeric NULL,
  bar_batch_id uuid NULL, payload jsonb, recorded_at timestamptz
  UNIQUE (order_id, seq), UNIQUE (order_id, event_key)

order_state        -- projeção, reconstruível
  order_id PK, status, zone_lost boolean, entry_eligible_from timestamptz NULL,
  trigger_hit_at timestamptz NULL, entry_path text NULL (DIRECT|RECLAIMED),
  avg_entry, initial_stop, stop_current, stop_active_from timestamptz NULL,
  qty_total, qty_open, realized_pnl, r_multiple, mfe_r, mae_r,
  opened_at, closed_at, last_bar_ts,
  expected_bars int, missing_bars int, needs_review boolean

bar_batches        -- proveniência de cada ingestão
  batch_id uuid PK, provider text, provider_version text, data_tier text CHECK (RESEARCH|PRODUCTION),
  request jsonb, content_hash text, ingested_at timestamptz

bars_1m            -- append-only; correções do fornecedor viram novas linhas
  ticker, ts, open, high, low, close, volume, source, batch_id FK
  PK (ticker, ts, source, batch_id)
  VIEW bars_1m_current: linha de maior ingested_at por (ticker, ts, source)

dividends
  ticker, ex_date, amount, pay_date, sources text[], validated boolean, checked_at
  PK (ticker, ex_date)
```

Timestamps em **UTC**; conversão para ET apenas na borda. `code_version` vem da variável
`GIT_SHA` gravada no build. `config_snapshot` contém todos os parâmetros efetivos da seção 8
que afetam fill, custo, qualidade de dados e tamanho.

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
| `DIVIDEND` | `DIVIDEND:{ex_date}` |
| `DATA_QUALITY` | `DATA_QUALITY:{session_date}` |
| `DATA_GAP` | `DATA_GAP:{gap_start_ts}` |
| `NEEDS_REVIEW` | `NEEDS_REVIEW:{reason}:{ref}` |

`seq` ordena; `event_key` garante idempotência. Reinserir um evento com `event_key`
existente é no-op.

### 3.3 Estados

```text
PENDING ─(entrada elegível na zona)─► OPEN ─(target1)─► PARTIAL ─(target2)─► CLOSED
   │  ▲                                 │                  │
   │  │ ZONE_RECLAIMED                  ├─(stop)─► CLOSED  ├─(stop em breakeven)─► CLOSED
   │  │ (flag zone_lost limpa)          └─(validade)─► CLOSED
   │  │
   ├──┴ ZONE_LOST (flag zone_lost; continua PENDING, entrada bloqueada)
   ├─(validade sem entrada)─► EXPIRED
   └─(preço atinge o stop sem posição)─► INVALIDATED
qualquer estado não final ─(humano)─► CANCELED
```

`ZONE_LOST`/`ZONE_RECLAIMED` alteram apenas flags da projeção. `DATA_QUALITY`, `DATA_GAP` e
`NEEDS_REVIEW` são marcações e não mudam o estado. Split durante posição congela a ordem e marca
`NEEDS_REVIEW`.

### 3.4 Regras de posição

- **Tamanho normalizado em R:** `qty_per_1r = risk_amount / |avg_entry − initial_stop|`
  (fracionário). Métricas em R e em %. Não há limite de notional nesta camada (ver 1.2 item 2).
- **Target 1:** realiza 50% e move o stop para `avg_entry`. O novo stop **só vale a partir do
  candle seguinte** ao fill do target 1 (`stop_active_from`). Sem `target2`, target 1 fecha 100%.
- **Confirmação:** apenas `trigger_price` é avaliado; `confirmation_note` é só exibição.
- **Validade:** o pregão 1 é o primeiro pregão regular cuja abertura (09:30 ET) ocorre em ou após
  `created_at`.
- **Ordem manual:** mesmos parâmetros do sinal; difere apenas em `origin`.
- **`entry_path`:** `DIRECT` se o fill ocorreu sem `ZONE_LOST` anterior; `RECLAIMED` caso contrário.

### 3.5 Validação de sinais

| Direção | Com `target2` | Sem `target2` |
|---|---|---|
| LONG | `stop < entry_zone_low ≤ entry_zone_high < target1 < target2` | `stop < entry_zone_low ≤ entry_zone_high < target1` |
| SHORT | `stop > entry_zone_high ≥ entry_zone_low > target1 > target2` | `stop > entry_zone_high ≥ entry_zone_low > target1` |

Também: `trigger_price`, se presente, positivo; ticker ativo e negociável na Alpaca;
`valid_sessions` entre 1 e 20. Violação → `422` com motivo.

## 4. Regras de fill — `fill_model v1`

Princípio: **nunca inferir trajetória intrabar.** Quando o candle é ambíguo, assumir o pior caso;
quando a decisão depende da ordem dos acontecimentos dentro do candle, adiar para o candle seguinte.

Regras escritas para LONG; SHORT é o espelho exato (inverter comparações, `high`↔`low`,
`entry_zone_low`↔`entry_zone_high`).

### 4.1 Pregão e dados

- Apenas pregão regular 09:30–16:00 ET (calendário NYSE, feriados e meios pregões).
- Candles brutos (`adjustment=raw`) da Alpaca IEX, via `bars_1m_current`. Apenas candles **fechados**.
- Uma ordem nunca mistura fontes de preço para fills; yfinance é conferência.
- Cada evento derivado de candle grava `bar_batch_id`.

### 4.2 Elegibilidade de entrada

Uma ordem `PENDING` é **elegível** num candle quando todas as condições valem:

1. `zone_lost = false`;
2. `bar_ts ≥ entry_eligible_from` (quando definido);
3. se `trigger_price` existe: `TRIGGER_HIT` ocorreu em candle **anterior**
   (`high ≥ trigger_price` registra o evento).

### 4.3 Entrada (LONG), avaliada em ordem para cada candle de ordem `PENDING`

Avaliação em ordem; a primeira regra terminal (fill ou `INVALIDATED`) encerra a fase de entrada
do candle. Regras 5 e 6 podem ocorrer no mesmo candle.

| # | Condição | Resultado |
|---|---|---|
| 1 | `open ≤ stop` | `INVALIDATED` |
| 2 | elegível e `entry_zone_low ≤ open ≤ entry_zone_high` | `FILLED` a `open`; segue para a checagem de stop de 4.4 no mesmo candle |
| 3 | elegível e `open > entry_zone_high` e `low ≤ entry_zone_high` | `FILLED` a `entry_zone_high`; segue para a checagem de stop de 4.4 no mesmo candle |
| 4 | sem fill neste candle e `low ≤ stop` | `INVALIDATED` |
| 5 | `zone_lost = false` e (`open < entry_zone_low` ou `close < entry_zone_low`) | `ZONE_LOST`, sem fill (com `ZONE_LOST_POLICY=CANCEL`: `INVALIDATED`) |
| 6 | `zone_lost = true` (inclusive se definido pela regra 5 neste candle) e `close ≥ entry_zone_low` | `ZONE_RECLAIMED`; `entry_eligible_from` = próximo candle |
| 7 | nenhum dos anteriores | permanece `PENDING` |

Por fim, se `trigger_price` existe e ainda não houve `TRIGGER_HIT`, `high ≥ trigger_price` registra
`TRIGGER_HIT` (efeito apenas a partir do candle seguinte, conforme 4.2).

**Consequências:** quando uma ordem elegível entra na zona e o candle também atinge o stop, o
resultado é fill seguido de `STOPPED` (pior caso). Quando o candle não produz fill e atinge o stop, a
ordem é `INVALIDATED`. O candle que gera `ZONE_RECLAIMED` nunca gera fill; a entrada volta a seguir as
regras 2–3 somente a partir do candle seguinte. `ZONE_LOST` e `ZONE_RECLAIMED` podem ocorrer múltiplas
vezes na vida de uma ordem.

**Política configurável `ZONE_LOST_POLICY`** (gravada em `config_snapshot`):
`RECLAIM` (padrão, regras acima) ou `CANCEL` (`ZONE_LOST` resulta imediatamente em `INVALIDATED`).
Permite comparar variantes por replay.

**Entrada por toque:** as regras usam `≤`/`≥` (tocar o nível permite fill). Fila de ordem limite não
é simulada; isso é uma limitação documentada, compensada pelo preço de fill conservador da regra 4.

### 4.4 Saídas

- **Stop:** quando `low ≤ stop_current` e `bar_ts ≥ stop_active_from`; fill a `min(open, stop_current)`
  com slippage adverso de `SLIPPAGE_BPS` (padrão 5). Antes de `stop_active_from`, vale o stop anterior.
- **Alvo:** quando `high ≥ target`; fill ao preço do alvo, sem ganho de gap e sem slippage.
- **Stop e alvo no mesmo candle:** stop primeiro.
- **Candle de entrada:** avalia apenas stop; alvos a partir do candle seguinte.
- **Candle do target 1:** o stop em breakeven ainda não vale nesse candle; vale o stop inicial
  para a quantidade remanescente (checagem de stop precede o alvo, conforme regra anterior).
- **Validade:** posição aberta sai no `close` do último candle do último pregão válido, com
  slippage adverso (`TIME_EXIT`). Ordem pendente vira `EXPIRED`.

### 4.5 Qualidade de dados

- **Minutos esperados:** todos os minutos do pregão regular pelo calendário (390, ou menos em meio pregão).
- **Contagem:** para cada ordem não final, `expected_bars` e `missing_bars` são atualizados a cada ciclo.
- **`DATA_QUALITY` (fim do dia):** um evento por ordem por pregão com `expected_bars`, `missing_bars`,
  cobertura % e a lista de minutos ausentes.
- **Verificação de minutos ausentes:** no job de fim de dia, para cada minuto ausente de uma ordem
  não final, buscar o candle de 1m do yfinance (consolidado). Se esse candle tocar qualquer nível
  ativo da ordem (`entry_zone_low`, `entry_zone_high`, `stop_current`, `target1`, `target2`,
  `trigger_price`) → `NEEDS_REVIEW:MISSING_BAR_LEVEL_TOUCH`. Se o yfinance não tiver o minuto
  (fora da janela disponível ou falha) → `NEEDS_REVIEW:MISSING_BAR_UNVERIFIABLE`.
- **`DATA_GAP`:** ausência contínua ≥ `DATA_GAP_MINUTES` (padrão 30) no pregão; alerta de incidente.
- **Conferência diária:** máxima/mínima agregadas do IEX vs diária do yfinance com divergência acima de
  `CROSSCHECK_TOLERANCE_PCT` (padrão 0,5) → `NEEDS_REVIEW:DAILY_RANGE_MISMATCH`.
- Uma ordem com `needs_review = true` continua sendo avaliada; as métricas permitem filtrá-la.

### 4.6 Custos e eventos corporativos

- Comissão padrão $0; taxas SEC/TAF configuráveis (padrão desligadas).
- **Dividendo:** posição com `qty_open > 0` no fechamento do pregão anterior à ex-date recebe
  `DIVIDEND` (`amount × qty_open`; LONG credita, SHORT debita). Válido somente se FMP e yfinance
  concordarem (tolerância $0,001); caso contrário `NEEDS_REVIEW:DIVIDEND_UNVERIFIED` e nenhum crédito.
- **Split** durante posição: congelar e `NEEDS_REVIEW:SPLIT`.
- **SHORT:** borrow, locate, hard-to-borrow e custo de empréstimo **não são simulados**. Métricas e
  telas que incluam ordens SHORT exibem esse aviso fixo.

### 4.7 Versionamento e replay

- Cada ordem grava `fill_model_version`, `config_snapshot` e `code_version`.
- Replay cria **novas ordens** (`replay=true`, mesma `origin`, nova versão/configuração) para os mesmos
  sinais; ordens, eventos e lotes existentes nunca são alterados.

## 5. Interfaces

### 5.1 API

Autenticação: header `X-API-Key` com chave única via variável de ambiente.

| Método e rota | Função |
|---|---|
| `POST /signals` | Cria sinal (idempotente por `client_signal_id`); cria ordem `AUTO_STRATEGY` salvo `auto_order=false` |
| `GET /signals?date&strategy` | Lista sinais |
| `POST /signals/{id}/orders` | Cria ordem `MANUAL_USER` |
| `POST /orders/{id}/cancel` | Cancela ordem não final |
| `GET /orders?status&origin&strategy&replay&needs_review` | Lista ordens com estado projetado |
| `GET /orders/{id}` | Detalhe, eventos, candles do período |
| `GET /metrics?group_by=strategy\|origin\|fill_model_version\|entry_path&from&to&include_needs_review` | Métricas agregadas |
| `POST /replay` | `{fill_model_version, config_overrides, from, to}` |
| `GET /health` | Último ciclo, frescor dos dados, falhas consecutivas, fila `NEEDS_REVIEW` |

### 5.2 Concorrência e idempotência

- Toda escrita de eventos de uma ordem ocorre em transação que começa com
  `SELECT … FROM orders WHERE id = :id FOR UPDATE`, recarrega a projeção e só então decide.
- Worker e `POST /orders/{id}/cancel` usam o mesmo caminho; o worker ignora ordens que, após o lock,
  estejam em estado final.
- O ciclo do worker adquire também um advisory lock global; se ocupado, o ciclo sai sem trabalho.

### 5.3 Worker

- **Ciclo do avaliador:** a cada `EVAL_INTERVAL_MINUTES` (padrão 2), 09:30–16:05 ET, em pregões.
  1. Adquire advisory lock.
  2. Carrega ordens não finais e agrupa por ticker.
  3. Busca candles desde o menor `last_bar_ts` do ticker; nova ingestão cria `bar_batches` + `bars_1m`.
  4. Por ordem: lock (5.2) → processa candles fechados em ordem cronológica → grava eventos,
     `last_bar_ts` e projeção na mesma transação.
- **Job de abertura (09:25 ET):** valida dividendos com ex-date no dia e aplica `DIVIDEND`.
- **Job de fim de dia (16:30 ET):** validade, `DATA_QUALITY`, verificação de minutos ausentes,
  conferência diária, webhook opcional (`N8N_WEBHOOK_URL`) com resumo.

### 5.4 Métricas

Sobre ordens `CLOSED`, filtráveis por `replay` e `needs_review`:

- Nº de trades, win rate (`r_multiple > 0`), R médio, expectância em R, profit factor,
  drawdown máximo em R (sequência por `closed_at`), duração média, MFE/MAE médios em R.
- **Taxa de execução** = ordens com `FILLED` ÷ ordens resolvidas (com `FILLED`, `EXPIRED` ou
  `INVALIDATED`); `PENDING` e `CANCELED` antes do fill ficam fora.
- **Incerteza:** intervalo de confiança de 95% por bootstrap (`BOOTSTRAP_RESAMPLES`, padrão 2000,
  semente `BOOTSTRAP_SEED` fixa) para win rate e expectância; drawdown máximo com distribuição por
  reamostragem da ordem dos trades (percentis 5/50/95).
- Aviso "amostra insuficiente" quando nº de trades < 30.
- Aviso SHORT (4.6) quando o conjunto contém ordens SHORT.

### 5.5 Dashboard

1. **Visão geral** — curva de R acumulado, cards de métricas com intervalos de confiança.
2. **Sinais do dia** — lista com status e botão "Comprar virtual".
3. **Ordens** — tabela filtrável; detalhe com gráfico de candles, zona de entrada, stop, alvos,
   marcadores de eventos (incluindo `ZONE_LOST`/`ZONE_RECLAIMED`) e log.
4. **Comparação** — AUTO × MANUAL, estratégia, versão/configuração de fill model, `entry_path`.
5. **Saúde** — último ciclo, cobertura de dados, `DATA_GAP`, fila `NEEDS_REVIEW` por motivo.

## 6. Tratamento de erros

| Falha | Comportamento |
|---|---|
| Alpaca indisponível / rate limit | 3 tentativas com backoff; falhando, pula o ticker sem avançar cursor. 3 ciclos consecutivos com falha → `/health` degradado e webhook de alerta |
| yfinance / FMP indisponíveis | Dividendos não validados e minutos não verificáveis → `NEEDS_REVIEW` com motivo; nenhum crédito |
| Sinal inválido | `422` com motivo (3.5) |
| Worker cai no meio do ciclo | Transação por ordem; retoma pelo cursor |
| Worker e API na mesma ordem | `SELECT … FOR UPDATE` (5.2) |
| Evento duplicado | `UNIQUE (order_id, event_key)` → no-op |
| Alteração de histórico | Role sem `UPDATE`/`DELETE` em `order_events`, `signals`, `bars_1m`, `bar_batches` + trigger que rejeita |
| Projeção inconsistente | Comando `rebuild-projections` a partir dos eventos |

## 7. Testes (TDD)

- **Unitários puros** — cada linha das tabelas 4.3 e cada regra de 3.4, 4.4, 4.5 e 4.6 como caso com
  candles sintéticos, LONG e SHORT. Casos obrigatórios:
  - candle ambíguo `open=99, high=103, low=96, close=101` com zona `[100,102]` e stop `97`
    (ordem sem posição) → `INVALIDATED`, nunca fill;
  - `ZONE_LOST` → candle com `close ≥ entry_zone_low` → `ZONE_RECLAIMED` sem fill → fill só no candle seguinte;
  - target 1 e mínima abaixo do breakeven no mesmo candle → breakeven não aplicado nesse candle;
  - `ZONE_LOST_POLICY=CANCEL`.
- **Propriedades (Hypothesis)** sobre sequências aleatórias de candles e sinais válidos:
  - `qty_open ≥ 0`; P&L realizado = soma dos fills, custos e dividendos;
  - determinismo: mesma entrada → mesmos eventos;
  - sem look-ahead: eventos com candles até *t* são prefixo dos eventos com a série completa;
  - idempotência: reprocessar a mesma série não cria eventos novos;
  - nenhum fill ocorre no mesmo candle de `ZONE_RECLAIMED`;
  - espelhamento: um sinal SHORT sobre candles espelhados produz a mesma sequência de tipos de evento
    e o mesmo `r_multiple` que o sinal LONG equivalente.
- **Métricas** — exemplos calculados à mão; bootstrap com semente fixa reproduz o mesmo intervalo.
- **Integração** — Postgres real em Docker: idempotência por `event_key`, trigger append-only, advisory
  lock, disputa cancel × worker com `FOR UPDATE`, rotas da API via `httpx`.
- **Dados de mercado** — fixtures gravadas de Alpaca, yfinance e FMP; nenhum teste acessa rede.
- **Ponta a ponta** — pregão histórico gravado: sinal → ordem → eventos → projeção → métricas
  comparados a resultado esperado.

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
| `SLIPPAGE_BPS` | 5 | sim |
| `COMMISSION_PER_ORDER` | 0 | sim |
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
