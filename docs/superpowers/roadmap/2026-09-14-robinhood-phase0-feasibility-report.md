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
