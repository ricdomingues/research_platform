# Fixtures de providers

Toda fixture deste diretório é **synthetic/documentation-derived fixture**: foi construída a partir do formato
documentado da API do provider e **não** é captura real de rede. Testes normais nunca acessam a rede.
Fixtures reais sanitizadas, quando existirem, entram com rótulo próprio e não substituem estas.

| Arquivo | Rótulo | Formato de referência |
|---|---|---|
| `alpaca/bars_page1.json` | synthetic/documentation-derived fixture | Alpaca Market Data v2 `GET /v2/stocks/{symbol}/bars` (página com `next_page_token`) |
| `alpaca/bars_page2.json` | synthetic/documentation-derived fixture | Alpaca Market Data v2 `GET /v2/stocks/{symbol}/bars` (última página) |
| `alpaca/corporate_actions.json` | synthetic/documentation-derived fixture | Alpaca `GET /v1/corporate-actions` (forward/reverse splits) |
| `fmp/dividends.json` | synthetic/documentation-derived fixture | FMP `GET /stable/dividends` |
