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

## Uso aprovado pelo dono (2026-09-13): portfólio real no dashboard

- O painel do Plano 3C mostra o portfólio **real** ao lado do portfólio virtual, lendo posições pela conta Robinhood em modo somente leitura.
- **Pré-requisito:** a Fase 0 continua obrigatória. Os itens 1–4, 12, 14, 16 e 17 bloqueiam este uso; sem evidência, o painel mostra só o portfólio virtual.
- **Allowlist:** a leitura de posições entra como uma tool de conta, com método tipado próprio (ex.: `list_positions()`). Nada de ordens, nada de passthrough.
- **Isolamento:** o portfólio real é só exibição. Nunca alimenta o motor, o ledger, o fill ou as métricas, e nunca é misturado ao portfólio virtual num mesmo total sem rótulo.

## Indicadores prontos (RSI, MACD, …)

Se oferecidos, podem ser ingeridos apenas para **cross-validation**. A fonte autoritativa da estratégia continua sendo o Technical Indicator Engine próprio, calculado sobre market data normalizado.
