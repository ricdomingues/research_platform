# Motor de Ordens Virtuais + Registro de Sinais (Ações EUA) — Design

- **Data:** 2026-09-12
- **Status:** aprovado em brainstorming, aguardando revisão da spec escrita
- **Sub-projeto:** 1 de N da Research Platform

## 1. Contexto e objetivo

A Research Platform terá várias linhas de operação (pré-market EUA "REXSHARE",
estratégia algorítmica WIN/B3, estratégias futuras). Todas precisam responder à
mesma pergunta: **a estratégia funciona de verdade, sem olhar o futuro?**

Este sub-projeto entrega a base comum para responder isso: um motor que recebe
sinais de qualquer origem, cria **ordens virtuais**, apura o resultado com candles
reais de 1 minuto usando regras de fill conservadoras, e mostra a eficácia em um
dashboard.

Decisões de negócio que moldam o design:

- **A ordem real sempre parte de um humano**, em uma corretora externa. A plataforma
  não envia ordens nesta fase.
- Todo sinal gera uma ordem virtual automática (`AUTO_STRATEGY`); o usuário pode
  também criar ordens virtuais manuais (`MANUAL_USER`) sobre o mesmo sinal, para
  comparar a estratégia pura com a seleção humana.
- No futuro haverá execução automática via API da corretora. O modelo de ordem é
  independente do executor para permitir isso sem reescrita.
- Mercado da v1: **ações dos EUA**, horizonte de swing (1–20 pregões).
- Dados apenas de **fontes gratuitas**: Alpaca (IEX), yfinance, FMP (free tier).

### Fora do escopo

Pipeline REXSHARE (coleta macro, scanners, camada LLM), estratégias WIN/B3,
importação de fills reais da corretora, `BrokerExecutor` / execução automática,
day trade e avaliação por streaming, multi-cliente, frontend Next.js,
reconhecimento de padrões de candle. Cada item terá spec própria.

## 2. Arquitetura

### 2.1 Serviços (Docker Compose)

| Serviço | Responsabilidade |
|---|---|
| `api` (FastAPI) | Recebe sinais, cria ordens virtuais, expõe ordens, eventos, métricas, replay e health |
| `worker` | Agenda e executa o avaliador durante o pregão e jobs de abertura/fim de dia |
| `dashboard` (Streamlit) | UI; comunica-se **exclusivamente com a API**, nunca com o banco |
| `postgres` | Eventos append-only, projeções, cache de candles |

Roda localmente e em VPS sem alterações.

### 2.2 Módulos do pacote `core/`

| Módulo | Responsabilidade | Depende de | I/O |
|---|---|---|---|
| `domain/` | Modelos `Signal`, `Order`, eventos; máquina de estados pura `step(state, bar) -> events` | — | não |
| `fills/` | Regras de fill (seção 4) | `domain` | não |
| `metrics/` | Métricas sobre ordens fechadas (seção 5.4) | `domain` | não |
| `marketdata/` | Interface `BarSource`; `AlpacaBars`, `YFinanceBars`; `DividendSource` (FMP, yfinance); cache | APIs externas, Postgres | sim |
| `ledger/` | Append idempotente de eventos, reconstrução de projeções | Postgres | sim |
| `evaluator/` | Orquestra ciclo: ordens abertas → candles → `domain`+`fills` → `ledger` | todos acima | sim |

Regra de arquitetura: **`domain`, `fills` e `metrics` não fazem I/O.** O mesmo código
serve para tempo real, replay e (futuramente) backtest.

### 2.3 Extensão futura de execução

Interface `ExecutionAdapter` com implementações planejadas:

- `VirtualExecutor` — **esta spec**; fills simulados por `fills/`.
- `ImportedFills` — futuro; importa execuções reais da corretora.
- `BrokerExecutor` — futuro; exigirá gate de risco, kill switch e ordens idempotentes.

## 3. Modelo de dados e ciclo de vida

### 3.1 Tabelas

```text
signals            -- imutável
  id uuid PK, client_signal_id text UNIQUE, created_at timestamptz,
  strategy text, strategy_version text, source text,
  ticker text, direction text CHECK (LONG|SHORT),
  entry_low numeric, entry_high numeric, trigger_price numeric NULL,
  confirmation_note text NULL,
  target1 numeric, target2 numeric NULL, stop numeric,
  valid_sessions int CHECK (1..20), score numeric NULL, thesis text NULL,
  raw_payload jsonb

orders
  id uuid PK, signal_id uuid FK, origin text CHECK (AUTO_STRATEGY|MANUAL_USER),
  fill_model_version text, replay boolean DEFAULT false,
  risk_amount numeric DEFAULT 100, created_at timestamptz

order_events       -- APPEND-ONLY
  id bigserial PK, order_id uuid FK, seq int, type text,
  bar_ts timestamptz NULL, price numeric NULL, qty numeric NULL,
  payload jsonb, recorded_at timestamptz
  UNIQUE (order_id, seq)

order_state        -- projeção, reconstruível
  order_id PK, status, avg_entry, qty_total, qty_open, stop_current,
  realized_pnl, r_multiple, mfe_r, mae_r, opened_at, closed_at,
  last_bar_ts, needs_review boolean

bars_1m            -- cache
  ticker, ts, open, high, low, close, volume, source
  PK (ticker, ts, source)

dividends          -- cache validado
  ticker, ex_date, amount, pay_date, sources text[], validated boolean
  PK (ticker, ex_date)
```

Todos os timestamps em **UTC**; conversão para ET apenas na borda.

### 3.2 Tipos de evento

`ORDER_CREATED`, `TRIGGER_HIT`, `FILLED`, `TARGET1_HIT`, `TARGET2_HIT`,
`STOPPED`, `TIME_EXIT`, `EXPIRED`, `INVALIDATED`, `CANCELED`, `DIVIDEND`,
`DATA_GAP`, `NEEDS_REVIEW`.

### 3.3 Estados

```text
PENDING ─(entrada na faixa [+ trigger prévio])─► OPEN ─(target1)─► PARTIAL ─(target2)─► CLOSED
   │                                              │                   │
   │                                              ├─(stop)─► CLOSED   ├─(stop em breakeven)─► CLOSED
   │                                              └─(validade)─► CLOSED
   ├─(validade sem entrada)─► EXPIRED
   └─(gap além do stop antes da entrada)─► INVALIDATED
qualquer estado não final ─(humano)─► CANCELED
```

`DATA_GAP` e `NEEDS_REVIEW` são marcações; não mudam o estado.
Split durante posição congela a ordem (sem novos eventos de fill) e marca `NEEDS_REVIEW`.

### 3.4 Regras de posição

- **Tamanho em R:** `qty = risk_amount / |entry_fill − stop|` (fracionário permitido).
  Métricas reportadas em R e em %.
- **Target 1:** realiza 50% da posição e move o stop para o preço médio de entrada.
  Sem `target2`, target 1 fecha 100%.
- **Confirmação:** apenas `trigger_price` é avaliado. `confirmation_note` é só exibição.
- **Validade:** contada em pregões; o pregão 1 é o primeiro pregão regular cuja abertura
  (09:30 ET) ocorre em ou após `created_at`. Sinal criado às 08:00 ET de um pregão conta aquele dia.
- **Ordem manual:** mesmos parâmetros do sinal; difere apenas em `origin`.

## 4. Regras de fill — `fill_model v1`

Princípio: quando o candle de 1 minuto é ambíguo, **assumir o pior caso**.
Regras descritas para LONG; SHORT é o espelho.

### 4.1 Pregão e dados

- Somente pregão regular 09:30–16:00 ET (calendário NYSE, com feriados e meios pregões).
- Candles brutos (`adjustment=raw`) da Alpaca IEX. Apenas candles **fechados**.
- Uma ordem nunca mistura fontes de preço; yfinance é só conferência.
- Minuto sem candle = sem informação, nenhum evento.
- Ausência de candles por mais de `DATA_GAP_MINUTES` (padrão 30) no pregão → `DATA_GAP`.
- Fim do dia: se máxima/mínima agregadas do IEX divergirem da diária do yfinance em mais de
  `CROSSCHECK_TOLERANCE_PCT` (padrão 0,5%) → `NEEDS_REVIEW`.

### 4.2 Entrada

| Condição no candle | Resultado |
|---|---|
| `open ≤ stop` enquanto `PENDING` | `INVALIDATED` |
| `low < entry_high` (atravessa, não só toca) | `FILLED` a `min(open, entry_high)`, sem slippage |
| `open > entry_high` e `low ≥ entry_high` | permanece `PENDING` |
| `trigger_price` definido | `high ≥ trigger_price` registra `TRIGGER_HIT`; entrada elegível só a partir do **candle seguinte** |

Candle ambíguo: `open > stop`, `low < entry_high` e `low ≤ stop` no mesmo candle →
registra `FILLED` e, no mesmo candle, `STOPPED` conforme 4.3 (pior caso: entrou e foi estopado).

### 4.3 Saídas

- **Stop:** quando `low ≤ stop_current`; fill a `min(open, stop_current)` com slippage adverso
  de `SLIPPAGE_BPS` (padrão 5).
- **Alvo:** quando `high > target`; fill ao preço do alvo, sem ganho de gap e sem slippage.
- **Stop e alvo no mesmo candle:** stop primeiro.
- **Candle de entrada:** avalia só stop; alvos a partir do candle seguinte.
- **Validade:** posição aberta sai no close do último candle do último pregão válido, com slippage adverso.

### 4.4 Custos e eventos corporativos

- Comissão padrão $0; taxas SEC/TAF configuráveis (padrão desligadas).
- **Dividendo:** posição com `qty_open > 0` no fechamento do pregão anterior à ex-date recebe
  `DIVIDEND` (`amount × qty_open`; LONG credita, SHORT debita). Válido somente se FMP e
  yfinance concordarem (tolerância $0,001); caso contrário, `NEEDS_REVIEW` e nenhum crédito.
- **Split** detectado durante posição: congelar e `NEEDS_REVIEW`.

### 4.5 Versionamento e replay

- `fill_model_version` gravado em cada ordem.
- Replay cria **novas ordens** (`replay=true`, mesma `origin`, nova versão) para os mesmos
  sinais; ordens e eventos existentes nunca são alterados.

## 5. Interfaces

### 5.1 API

Autenticação: header `X-API-Key` com chave única via variável de ambiente.

| Método e rota | Função |
|---|---|
| `POST /signals` | Cria sinal (idempotente por `client_signal_id`); cria ordem `AUTO_STRATEGY` salvo `auto_order=false` |
| `GET /signals?date&strategy` | Lista sinais |
| `POST /signals/{id}/orders` | Cria ordem `MANUAL_USER` |
| `POST /orders/{id}/cancel` | Cancela ordem não final |
| `GET /orders?status&origin&strategy&replay` | Lista ordens com estado projetado |
| `GET /orders/{id}` | Detalhe, eventos e candles do período |
| `GET /metrics?group_by=strategy\|origin\|fill_model_version&from&to` | Métricas agregadas |
| `POST /replay` | `{fill_model_version, from, to}` |
| `GET /health` | Último ciclo, frescor de dados, falhas consecutivas, fila `NEEDS_REVIEW` |

Validação de sinal (`422` com motivo), LONG: `stop < entry_low ≤ entry_high < target1 < target2`
(SHORT espelhado); ticker ativo e negociável na Alpaca; `valid_sessions` entre 1 e 20.

### 5.2 Worker

- **Ciclo do avaliador:** a cada 2 min, 09:30–16:05 ET, em pregões.
  1. Adquire advisory lock do Postgres (se ocupado, sai).
  2. Carrega ordens não finais, agrupa por ticker.
  3. Busca candles desde o menor `last_bar_ts` do ticker, via cache `bars_1m`.
  4. Para cada ordem, processa candles fechados em ordem cronológica por `domain` + `fills`.
  5. Grava eventos + `last_bar_ts` na mesma transação por ordem; atualiza projeção.
- **Job de abertura (09:25 ET):** valida dividendos com ex-date no dia e aplica `DIVIDEND`.
- **Job de fim de dia (16:30 ET):** expira validades, conferência com yfinance,
  webhook opcional (`N8N_WEBHOOK_URL`) com resumo do dia.

### 5.3 Dashboard

1. **Visão geral** — curva de R acumulado e cards de métricas por estratégia.
2. **Sinais do dia** — lista com status e botão "Comprar virtual".
3. **Ordens** — tabela filtrável; detalhe com gráfico de candles, linhas de entrada/stop/alvos,
   marcadores de eventos e log.
4. **Comparação** — AUTO × MANUAL, estratégia × estratégia, versão de fill model.
5. **Saúde** — último ciclo, `DATA_GAP`, fila `NEEDS_REVIEW`.

### 5.4 Métricas

Sobre ordens `CLOSED` (exceto `replay` quando não solicitado):

- Nº de trades, win rate (`r_multiple > 0`), R médio, expectância em R, profit factor,
  drawdown máximo em R (sequência por `closed_at`), duração média.
- MFE e MAE médios em R.
- **Taxa de execução** = ordens que chegaram a `FILLED` ÷ ordens resolvidas, onde resolvidas =
  ordens que já tiveram `FILLED`, `EXPIRED` ou `INVALIDATED`. Ordens ainda `PENDING` e
  ordens `CANCELED` antes do fill ficam fora do cálculo.
- Aviso "amostra insuficiente" quando nº de trades < 30.

## 6. Tratamento de erros

| Falha | Comportamento |
|---|---|
| Alpaca indisponível / rate limit | 3 tentativas com backoff; falhando, pula o ticker sem avançar cursor. 3 ciclos consecutivos com falha → `/health` degradado e webhook de alerta |
| yfinance / FMP indisponíveis | Dividendos não validados → `NEEDS_REVIEW`, sem crédito; conferência de fim de dia adiada |
| Sinal inválido | `422` com motivo |
| Worker cai no meio do ciclo | Transação por ordem; retoma pelo cursor |
| Workers concorrentes | Advisory lock por ciclo |
| Alteração de histórico | Role do banco sem `UPDATE`/`DELETE` em `order_events` + trigger que rejeita |
| Projeção inconsistente | Comando `rebuild-projections` a partir dos eventos |

## 7. Testes (TDD)

- **Unitários puros** — cada linha das tabelas 4.2 e 4.3 e regras 3.4/4.4 como caso com candles sintéticos.
- **Propriedades (Hypothesis)** sobre sequências aleatórias de candles:
  - `qty_open ≥ 0`; P&L realizado igual à soma dos fills e dividendos.
  - Determinismo: mesma entrada → mesmos eventos.
  - Sem look-ahead: eventos gerados com candles até *t* são prefixo dos eventos com a série completa.
- **Métricas** — exemplos calculados à mão.
- **Integração** — Postgres real em Docker: idempotência do ledger, trigger append-only,
  advisory lock, rotas da API via `httpx`.
- **Dados de mercado** — fixtures gravadas de Alpaca, yfinance e FMP; nenhum teste acessa rede.
- **Ponta a ponta** — pregão histórico gravado: sinal → ordem → eventos → projeção → métricas
  comparados a resultado esperado.

## 8. Configuração

| Variável | Padrão |
|---|---|
| `API_KEY` | — (obrigatória) |
| `DATABASE_URL` | — (obrigatória) |
| `ALPACA_API_KEY`, `ALPACA_SECRET_KEY` | — (obrigatórias) |
| `FMP_API_KEY` | — (obrigatória) |
| `EVAL_INTERVAL_MINUTES` | 2 |
| `DEFAULT_RISK_AMOUNT` | 100 |
| `SLIPPAGE_BPS` | 5 |
| `DATA_GAP_MINUTES` | 30 |
| `CROSSCHECK_TOLERANCE_PCT` | 0.5 |
| `COMMISSION_PER_ORDER` | 0 |
| `N8N_WEBHOOK_URL` | vazio (desligado) |

## 9. Stack

Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 + Alembic, PostgreSQL 16, APScheduler,
`pandas_market_calendars`, `alpaca-py`, `yfinance`, `httpx`, Streamlit, Plotly,
pytest + Hypothesis, Docker Compose, `uv` para dependências.
