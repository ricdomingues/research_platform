# Plano 5 (Motor de pesquisa de candlesticks, contexto, backtest e ML) — Encerramento

- **Especificação:** o brief do responsável (27 seções), tratado como plano desta execução; não há documento em
  `docs/superpowers/plans/` porque o próprio brief fez esse papel.
- **Spec base:** v1.2 (`spec/virtual-order-engine-v1.2`) + D5–D73 (Planos 2, 3A, 3B, 3C, 4) + D74–D93 (este plano)
- **Base:** `main` em `e0329b0` (Plano 4 encerrado); branch `plan-5-research`
- **Núcleo congelado:** `git diff plan/virtual-order-engine-core-complete -- src/core` vazio. `src/core` não foi
  tocado: o motor de pesquisa importa `Bar`, `Direction`, `SignalSpec`, `validate_signal`, `core.metrics.*` e
  `core.domain.hashing` como biblioteca externa estável.
- **Locks e CI:** `uv.lock` (raiz e `dashboard/`), `.github/workflows/ci.yml` e os dois `pyproject.toml` sem diff.
  **Nenhuma dependência nova foi adicionada** — o classificador é NumPy, que já estava no lock.

## Testes executados (do zero, nesta sessão)

| Suíte | Antes | Depois | Resultado |
|---|---|---|---|
| Motor (raiz) | 1372 | **1653** | 1653 passando, 0 falhas, 0 skips, 0 warnings (93s) |
| Dashboard | 56 | **68** | 68 passando, 0 falhas (2,3s) |
| Fronteiras de import | 632 | **689** | 689 passando |
| Integração (Postgres real) | — | **454** | 454 passando (49s) |
| Unitários de pesquisa | — | **126** | 126 passando |
| `ruff` | limpo | limpo | raiz (`src tests migrations`) e dashboard |
| `mypy --strict` | 121 arquivos | **144 arquivos** | limpo; dashboard 17 arquivos limpo |
| Migrations do zero | OK | **OK** | `upgrade head` → `downgrade base` → `upgrade head` |

Fixtures do contrato gravado: **27** (as 19 anteriores byte-idênticas, 8 novas de pesquisa).

## Decisões D74–D93

- **D74** (timeframes ancorados na abertura do pregão, contados em minutos esperados; `D1` = o pregão inteiro;
  candle só existe quando todo o seu balde já decorreu) — custo se errada: um candle de 1h atravessaria o
  fechamento ou misturaria dois pregões, e todo padrão detectado nele seria ficção.
- **D75** (geometria separada de contexto; `PriorContext` por comprimento de padrão; limiares versionados em
  `PatternThresholds`) — custo se errada: "martelo" e "homem enforcado" virariam o mesmo sinal, e a tendência
  anterior de um padrão de 3 candles incluiria os próprios candles do padrão.
- **D76** (indicadores causais, alinhados ao índice, `None` até a janela encher; VWAP/CMF/OBV/pressão reusados de
  `analytics`, não reimplementados) — custo se errada: um valor "preenchido" no início da série contaminaria toda
  a estatística seguinte.
- **D77** (pivôs estritos, só utilizáveis após `SWING_RIGHT` candles de confirmação) — custo se errada: seria o
  bug clássico de look-ahead, classificando um sinal histórico com um pivô que só existiu depois dele.
- **D78** (snapshot imutável e hasheável por candidato, com `feature_version`) — custo se errada: uma previsão
  guardada não seria explicável meses depois.
- **D79** (labels são o único módulo que lê candles posteriores; política de ambiguidade
  `STOP_FIRST_ON_SAME_CANDLE_V1`, igual à do `fill_model v1`; D3 aplicada ao MFE) — custo se errada: o backtest
  assumiria silenciosamente a ordem favorável dentro do candle e inflaria todo win rate.
- **D80** (níveis vêm de estrutura e ATR, nunca do modelo; validados pela regra do próprio motor) — custo se
  errada: um classificador inventaria preços, e uma cadeia que a plataforma recusa chegaria ao `/signals`.
- **D81** (features de `[:i]`, labels de `[i+1:]`, no mesmo lugar; split cronológico com purga e embargo só do
  treino; sem split aleatório) — custo se errada: o backtest seria irreprodutível fora da amostra.
- **D82** (score determinístico com pesos versionados e `SCORE_INTERPRETATION` sempre ao lado do número) — custo
  se errada: um 0–1 ao lado de um ticker seria lido como probabilidade.
- **D83** (`SetupCandidate` é observação; `client_signal_id` sem `data_as_of`, para não quebrar a idempotência do
  intake) — custo se errada: cada varredura criaria um sinal novo para a mesma ocorrência.
- **D84** (layout de colunas fixo em código, vocabulários vindos de enums; ausente permanece NaN, nada é
  imputado; barreira não resolvida é excluída e contada, nunca virada em perda) — custo se errada: a média de um
  split vazaria para o outro, e "não atingiu o alvo em 20 candles" viraria "foi estopado".
- **D85** (GBDT logístico em NumPy, sem dependência nova, sem aleatoriedade; artefato em JSON legível e
  hasheável) — custo se errada: um modelo não reproduzível, ou um lock alterado num plano que prometia não mexer
  no deploy.
- **D86** (fronteira de promoção explícita; corpo montado aqui, submissão continua sendo o intake existente) —
  custo se errada: uma observação de pesquisa viraria ordem sem passar pela validação da plataforma.
- **D87** (walk-forward expansivo; métricas em Decimal na fronteira; fold sem amostra é pulado e contado) — custo
  se errada: números "de treino" seriam apresentados como desempenho.
- **D88** (previsão carrega modelo, features e labels; recusa snapshot de outra versão) — custo se errada: um
  modelo pontuaria colunas desalinhadas com aparência de confiança.
- **D89** (fatos de pesquisa append-only com unicidade por hash de conteúdo; `research_runs` é o único mutável) —
  custo se errada: a varredura agendada acumularia duplicatas a cada execução.
- **D90** (registry nunca sobrescreve: `model_version` único, artefato guardado junto) — custo se errada: uma
  previsão antiga deixaria de ser explicável pelo modelo que a produziu.
- **D91** (`RESEARCH_SCAN` é job próprio, fora do `LIVE_CYCLE`, sem gateway; um watermark por execução;
  isolamento por ticker; retomada incremental) — custo se errada: a pesquisa atrasaria um fill, ou um símbolo
  ruim derrubaria a varredura inteira.
- **D92** (rotas de pesquisa só GET; nenhuma rota inicia varredura ou promove candidato) — custo se errada:
  olhar uma lista teria efeito colateral.
- **D93** (página "Pesquisa" só por HTTP, view-models puros, toda taxa ao lado da amostra, probabilidade vazia
  enquanto não houver modelo) — custo se errada: o dashboard mostraria uma porcentagem sem n, ou um candidato
  sem modelo pareceria ter probabilidade baixa.

## Limitações conhecidas (registradas, não escondidas)

1. **Nenhum número deste plano é evidência de edge.** Os backtests rodaram sobre séries sintéticas
   determinísticas; o AUC alto dos testes de ML é artefato de um zigue-zague aprendível, não do mercado.
2. **EMA 200 em `1d`** exige ~200 pregões de barras de 1 minuto guardadas; com `lookback_sessions` padrão de 30
   ela fica `None` e o snapshot registra isso.
3. **Padrões de 3 candles disparam em toda tripla consecutiva** de uma tendência monótona: é a leitura literal da
   regra; a deduplicação é responsabilidade da camada de setup/ranking.
4. **Nenhum modelo está aprovado.** O registry guarda modelos; a promoção exige política explícita do
   responsável, e `require_ml_probability` está desligado por padrão.
5. **Visão computacional não foi implementada** (Fase futura, por decisão do brief): nenhuma dependência de
   PyTorch/YOLO/OpenCV entrou no projeto.
6. **Promoção não foi ligada a nenhuma automação**: `promotion.decide()` monta e valida o corpo, mas nada o envia.

## Entradas para o próximo plano

1. Rodar `RESEARCH_SCAN` sobre a watchlist real durante a observação em paper e medir: quantos candidatos por
   pregão, quantos com cadeia de níveis válida, e qual a distribuição do score determinístico.
2. Só depois disso, calibrar limiares (`PatternThresholds`, pesos de `scoring`, `min_risk_reward`) com amostra
   real — nunca com os números sintéticos deste plano.
3. Treinar o primeiro modelo com dados reais e decidir, com o walk-forward, se a probabilidade acrescenta algo
   ao score determinístico; se não acrescentar, não promover.
4. Avaliar guardar candles agregados materializados se a varredura de 30 pregões × N tickers ficar cara na VPS.
5. As entradas 1, 3, 4, 6–11 do encerramento do 3C e 1–9 do Plano 4 seguem abertas (fora deste plano).
