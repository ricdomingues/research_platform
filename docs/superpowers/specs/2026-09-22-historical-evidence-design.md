# Plano 6 — Evidência histórica: dataset, supersessão e estatística empírica de setups

- **Estado:** desenho aprovado pelo responsável em 2026-09-22; nenhum código escrito.
- **Base:** `main` em `6baffeb` (Plano 5 encerrado, tag `plan/candlestick-research-engine-complete`).
- **Spec base:** v1.2 + D5–D73 (Planos 2–4) + D74–D94 (Plano 5 e página Terminal). Este plano abre **D95**.
- **Não altera:** `src/core`, `fill_model v1`, o ledger de eventos, o evaluator, a semântica de replay, nem a
  proveniência existente. `bars_1m` permanece canônico para execução e pesquisa recente.

## 1. A pergunta que este plano responde

O motor sabe dizer *o que está acontecendo agora*. Não sabe dizer *o que costumou acontecer depois*. A peça
que falta é uma só:

> Quando um setup comparável a este ocorreu no passado, com que frequência o alvo foi atingido antes do stop —
> e com que amostra, com que intervalo de confiança, sobre qual universo e qual dataset?

Tudo mais neste plano existe para que essa frase possa ser dita sem mentir.

## 2. O que este plano deliberadamente **não** faz

| Fora de escopo | Onde vive |
|---|---|
| Motor de estruturas gráficas (flag, H&S, triângulo, cup & handle) | Plano seguinte |
| Cockpit / nova arquitetura de informação | Plano posterior, ver `cockpit-ux-direction` |
| Composição point-in-time de índice | Evolução futura; o schema nasce pronto para ela |
| Estatística histórica em `5m` | Nunca derivada de `15m`. A UI declara o piso do dataset |
| Probabilidade calibrada exibida ao usuário | Só depois de walk-forward e out-of-sample; este plano entrega **frequência observada** |
| Backfill amplo | Só depois da Fase 0 aprovar os limiares |

## 3. Decisões

### D95 — Base histórica

A pesquisa histórica usa candles de **15 minutos nativos do fornecedor** como dataset atômico. Timeframes
acima (`30m`, `1h`, `4h`, `1d`) são derivados por nós, com o mesmo calendário e a mesma ancoragem na abertura
do pregão que o `timeframes.py` já aplica. Candles horários nativos do fornecedor **não** são usados como série
oficial: a Alpaca ancora no relógio e o motor ancora na sessão, e a estatística descreveria um gráfico que o
produto nunca mostra. O diário nativo entra como *cross-check*, não como autoridade.

`bars_1m` continua canônico para fills e pesquisa recente. D74 permanece válido para o dataset operacional; um
dataset histórico pode declarar `base_granularity` diferente **desde que** preserve calendário, ancoragem de
sessão, causalidade e semântica de completude. A identidade do dataset inclui a granularidade-base, e
resultados de datasets com bases diferentes nunca são agrupados silenciosamente.

*Custo se errada:* a geometria histórica e a geometria exibida divergem, e toda estatística descreve outro
gráfico.

### D96 — Supersessão de fatos de pesquisa

Observações de pesquisa continuam append-only e nunca são apagadas ou reescritas. Uma revisão posterior dos
dados pode **superseder** ou **retratar** uma observação através de um fato próprio, também append-only.

```text
research_supersessions
  id
  fact_type            PATTERN_DETECTION | SETUP_CANDIDATE
  superseded_fact_id
  replacement_fact_id  NULL quando retratada
  reason               SUPERSEDED_BY_REVISION | NO_LONGER_DETECTED
  source_run_id
  superseded_at
  input_content_hash   identidade das barras que motivaram a revisão
```

Dois casos:

- **A — o candle corrigido produz outra leitura.** A nova detecção é gravada ao lado (já é o comportamento da
  unicidade por `evidence_hash`) e um fato de supersessão liga a antiga à nova.
- **B — o candle corrigido não produz padrão nenhum.** A observação antiga é **retratada**, com
  `replacement_fact_id` nulo e `reason = NO_LONGER_DETECTED`. Nenhuma "detecção vazia" é inventada.

O caso B é o que o gate do Plano 5 encontrou e não conseguiu registrar.

Consequência obrigatória: **nenhuma estatística histórica lê as tabelas de fatos diretamente.** Toda leitura
estatística passa pela visão ativa do dataset — `fatos − superseded − retratados` — enquanto a auditoria
continua enxergando tudo e consegue reconstruir qualquer versão anterior.

*Custo se errada:* em 300 tickers × 10 anos, observações produzidas por dados incompletos permanecem contadas,
e o viés é invisível porque cada linha isolada parece correta.

### D97 — Identidade semântica separada de proveniência

A identidade de um fato é **o que foi observado**; não **quando foi observado**.

Hoje `FeatureSnapshot.feature_hash` inclui `data_as_of`, de modo que duas varreduras sobre exatamente os mesmos
candles produzem identidades diferentes. Isso já causou um defeito real (candidatos duplicados a cada revisit,
corrigido em `8b883fb` contornando o sintoma).

O hash semântico passa a depender de: ticker, timeframe, timestamp do setup, padrão, `feature_version`,
`aggregation_version`, os valores das features e a identidade do conteúdo das barras de entrada. **Não** depende
do relógio da varredura. `data_as_of`, `research_run_id` e `observed_at` continuam gravados como proveniência,
fora do hash.

Mudar a identidade é mudança de versão: o snapshot passa a `features-v2`, e observações `features-v1` nunca são
agrupadas com `features-v2` na mesma população estatística. O contorno introduzido em `8b883fb` (derivar
candidato só quando a detecção foi inserida) é revisto quando D97 entrar, porque deixa de ser necessário.

*Custo se errada:* revarreduras inflam contagens, e "quantas ocorrências existem" passa a depender de quantas
vezes o scan rodou.

### D98 — Portão de cobertura do dataset

Pertencer ao universo estatístico exige critérios financeiros **e** qualidade medida do dataset:

```text
candidato → regras de liquidez → disponibilidade histórica → PORTÃO DE COBERTURA → universo estatístico
```

Os limiares **não são adivinhados**: saem da distribuição medida na Fase 0. O gate do Plano 5 mostrou por que o
portão é necessário — cobertura de minutos do IEX foi 100% (NVDA, SPY), 99,3% (AAPL), 63,3% (PLUG) e 33,3%
(CROX). Liquidez de mercado não é proxy de cobertura de feed.

A cobertura que importa aqui é a de **base bars de 15m nativos**, não a de 1 minuto: é inteiramente possível que
a CROX tenha 33% em `1m` e cobertura alta em `15m`, e nesse caso é perfeitamente utilizável. Medir isso é
objetivo da Fase 0.

*Custo se errada:* metade de um ativo entra na população como se fosse o ativo inteiro.

## 4. Modelo de domínio

```text
research_datasets
  dataset_id, dataset_version
  provider, feed                     ex.: ALPACA / IEX
  base_timeframe                     15m
  range_from, range_to
  universe_id, universe_version
  aggregation_version, calendar_version
  adjustment_version                 política de ajuste por splits/proventos
  survivorship_bias_status           PRESENT | ABSENT
  created_at

universes
  universe_id, universe_version, name
  membership_model                   STATIC_CURATED | POINT_IN_TIME
  rules                              limiares versionados, incluindo o portão de cobertura
  methodology, created_at

universe_membership
  universe_id, symbol, valid_from, valid_to
  inclusion_reason, exclusion_reason, source

research_bars                        candles de base, 15m nativos
  dataset_id, ticker, ts, session_day
  open, high, low, close, volume
  batch_id                           proveniência pelo mesmo caminho de bar_batches
  PK (dataset_id, ticker, ts)

research_supersessions               ver D96
```

Integridade dos candles derivados passa de minutos para **base bars**: `base_bars_expected` /
`base_bars_present` (26 baldes de 15m num pregão regular, 14 num meio-pregão, 4 por hora derivada). Um balde
faltante **nunca** é preenchido sinteticamente.

Ajuste por eventos corporativos: `bars_1m` permanece *raw*, porque uma ordem executou no preço que existiu. O
dataset de pesquisa usa série ajustada derivada, com fator de ajuste versionado em `adjustment_version`.
Backfill de 2016 em preços não ajustados tornaria ficção todo padrão em torno de um split.

## 5. Estatística empírica

Uma coorte é definida por, no mínimo: `dataset_version`, `universe_version`, provider/feed, timeframe,
`setup_version`, `feature_version`, `label_version`, padrão e direção. Estratificações adicionais previstas:
ticker, regime de mercado.

Para cada coorte, calculados sobre a visão **ativa** do dataset:

```text
occurrences, target1_before_stop, target2_before_stop, stop_before_target,
median_return, expectancy_r, median_mfe_r, median_mae_r, confidence_interval
```

Os rótulos reusam `labels.py` sem alteração, incluindo a política `STOP_FIRST_ON_SAME_CANDLE_V1` para
ambiguidade intrabar, e as comparações permanecem estritamente *as-of*.

Portões de amostra, com os valores configuráveis e versionados:

```text
n < 30      INSUFFICIENT    nenhum número exibido
30 ≤ n < 100 EXPLORATORY    exibido marcado como exploratório
n ≥ 100     exibível com intervalo de confiança
```

**Dois campos distintos, nunca fundidos:** *estatística observada do setup* (frequência histórica, sempre ao
lado de `n` e do IC) e *probabilidade calibrada* (ausente até haver walk-forward e out-of-sample). Um score
determinístico nunca é apresentado como probabilidade — regra já vigente, agora estendida à frequência.

Toda taxa exibida carrega dataset, universo, período e a limitação de viés de sobrevivência em texto visível,
não em tooltip.

## 6. Decomposição das rejeições de níveis

O gate mediu 171 cadeias válidas em 879 candidatos (19,5%). Isso **não** é sinal de rigor excessivo e nenhuma
regra será relaxada para aumentar o número: detectar uma figura é fácil, transformá-la numa oportunidade com
entrada, stop e alvos coerentes é seletivo por natureza. Este plano apenas passa a decompor os motivos de
rejeição por padrão, timeframe e ticker, para que o histórico diga depois se alguma regra descarta boas
oportunidades.

## 7. Fase 0 — Spike (bloqueia todo o resto)

Sem backfill amplo. Entre 5 e 10 tickers deliberadamente heterogêneos: duas mega caps/ETFs, duas large caps,
duas mid caps e **PLUG e CROX mantidos de propósito**, por serem os casos adversariais que o gate revelou.

| Questão | Medição |
|---|---|
| Cobertura | base bars de 15m nativos por ticker e por ano |
| Gaps | distribuição e maior sequência contígua ausente |
| Volume | número de barras desde 2016 |
| Storage | tabela **e índices** reais no Postgres |
| Ingestão | tempo, chamadas de API, rate limits |
| Query | p50/p95 das consultas reais do cockpit |
| Timeframes | `1h` derivado vs `1h` nativo da Alpaca |
| Diário | `1d` derivado vs vendor e vs yfinance |
| Padrões | ocorrências por tipo e timeframe |
| Sensibilidade a gaps | quanto barras ausentes alteram a contagem de detecções |
| Níveis | % válidos e motivos de rejeição |
| Supersessão | uma correção supersede ou retrata corretamente |
| Identidade | revarreduras idênticas não duplicam fatos |

### Prova obrigatória da Fase 0

```text
ingerir dataset V1              → detectar padrão P
corrigir uma barra histórica    → ingerir revisão V2 → P desaparece

asserções:
  o fato antigo continua existindo
  o fato antigo está retratado
  a visão ativa não retorna mais P
  a estatística histórica não conta mais P
  a auditoria ainda reconstrói V1
```

Enquanto essa prova não passar, nenhum backfill amplo começa.

As sete detecções obsoletas de AAPL do canary (ids 17, 24, 28, 29, 39, 40, 41) **não entram em estatística
alguma** e não são apagadas: assim que a supersessão existir, viram fixtures reais de regressão. Foram
produzidas por uma falha genuína — dados incompletos, padrão detectado, dados corrigidos, padrão desaparece —
e vale mais como teste do que qualquer fixture sintética.

## 8. Fases seguintes

1. **Fase 0** — spike e a prova de supersessão. Decide limiares de cobertura, granularidade e storage.
2. **Fase 1** — supersessão e identidade semântica (D96, D97) sobre o dataset atual, com as fixtures do canary.
3. **Fase 2** — universo versionado, membership e portão de cobertura (D98).
4. **Fase 3** — dataset histórico e backfill 15m (D95), com ajuste versionado e cross-check diário.
5. **Fase 4** — estatística empírica por coorte, com portões de amostra e intervalos de confiança.
6. **Fase 5** — exposição somente leitura da evidência histórica na API e no dashboard atual, sem cockpit novo.

## 9. Testes exigidos

- **Look-ahead**: estender `tests/research/test_leakage.py` ao caminho histórico — features de `[:i]`, labels de
  `[i+1:]`, pivôs só após confirmação, agora também sobre base de 15m.
- **Ancoragem**: baldes derivados do calendário, jamais por subtração de minutos. O gate do Plano 5 produziu
  dois falsos positivos exatamente aí: o último balde truncado da sessão (`19:30–19:59` no `1h`) e um padrão de
  dois candles em `1d` fundindo dois dias num OHLC.
- **Supersessão**: os dois casos de D96, incluindo a prova completa da Fase 0.
- **Identidade**: a mesma varredura sobre os mesmos candles produz o mesmo hash, em relógios diferentes.
- **Falsa probabilidade**: nenhuma superfície exibe taxa sem `n`; abaixo do mínimo, o estado é exibido em vez do
  número; `deterministic_score` nunca é rotulado como probabilidade; frequência observada e probabilidade
  calibrada nunca ocupam o mesmo campo.
- **Não agrupamento**: datasets com base, universo, feed ou versões diferentes não se misturam numa coorte.
- **Cobertura**: um ativo abaixo do portão não entra no universo estatístico.

## 10. O que a Fase 0 decide, e portanto não está decidido aqui

1. Limiares de cobertura (não `95%` nem `98%` — a distribuição medida decide).
2. Limiares financeiros do `US_LIQUID_RESEARCH v1` (preço mínimo, volume financeiro mediano, histórico mínimo).
3. Postgres particionado vs Parquet para as barras de base — a hipótese inicial é Postgres, e só evidência de
   volume a derruba, porque Parquet quebra leitura as-of, proveniência por `batch_id` e replay.
4. Tamanho do universo inicial (100, 300 ou outro), por extrapolação a partir do spike.
5. Se `1m` sob demanda continua necessário para a watchlist diária de até 3 ativos.
