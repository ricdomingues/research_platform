# Plano 6 — Evidência histórica: dataset, supersessão e estatística empírica de setups

- **Estado:** desenho revisado pelo responsável em 2026-09-22; nenhum código escrito.
- **Base:** `main` em `6baffeb` (Plano 5 encerrado, tag `plan/candlestick-research-engine-complete`).
- **Spec base:** v1.2 + D5–D73 (Planos 2–4) + D74–D94 (Plano 5 e página Terminal). Este plano abre **D95**.
- **Não altera:** `src/core`, `fill_model v1`, o ledger de eventos, o evaluator, a semântica de replay, nem a
  proveniência existente. `bars_1m` permanece canônico para execução e pesquisa recente.

## 1. A pergunta que este plano responde

O motor sabe dizer *o que está acontecendo agora*. Não sabe dizer *o que costumou acontecer depois*. A peça
que falta é uma só:

> Quando um setup comparável a este ocorreu no passado, com que frequência o alvo foi atingido antes do stop —
> sob qual política de execução, com que amostra resolvida, com que incerteza, sobre qual universo e qual
> revisão de dataset?

Tudo mais neste plano existe para que essa frase possa ser dita sem mentir. A parte mais difícil não é obter o
histórico: é **fixar com precisão qual experimento o número na tela representa**.

## 2. O que este plano deliberadamente **não** faz

| Fora de escopo | Onde vive |
|---|---|
| Motor de estruturas gráficas (flag, H&S, triângulo, cup & handle) | Plano seguinte |
| Cockpit / nova arquitetura de informação | Plano posterior, ver `cockpit-ux-direction` |
| Composição point-in-time de índice | Evolução futura; o schema nasce pronto para ela |
| Estatística histórica em `5m` | Nunca derivada de `15m`. A UI declara o piso do dataset |
| Probabilidade calibrada exibida ao usuário | Só depois de walk-forward e out-of-sample; este plano entrega **frequência observada** |
| Ajuste por proventos em OHLC técnico | Explicitamente recusado nesta versão (D101) |
| Estratificação por regime de mercado | Não existe definição de regime no projeto; a coorte reserva o eixo |
| Backfill amplo | Só depois da Fase 0B aprovar os limiares |

## 3. Decisões

### D95 — Base histórica

A pesquisa histórica usa candles de **15 minutos nativos do fornecedor** como dataset atômico. Timeframes acima
(`30m`, `1h`, `4h`, `1d`) são derivados por nós, com o mesmo calendário e a mesma ancoragem na abertura do
pregão que o `timeframes.py` já aplica. Candles horários nativos do fornecedor **não** são a série oficial: a
Alpaca ancora no relógio e o motor ancora na sessão, e a estatística descreveria um gráfico que o produto nunca
mostra. O diário nativo entra como *cross-check*, não como autoridade.

`bars_1m` continua canônico para fills e pesquisa recente. D74 permanece válido para o dataset operacional; um
dataset histórico pode declarar `base_granularity` diferente **desde que** preserve calendário, ancoragem de
sessão, causalidade e semântica de completude. A identidade do dataset inclui a granularidade-base, e resultados
de datasets com bases diferentes nunca são agrupados silenciosamente.

Derivar acima de `15m` exige estender `timeframes.py`, hoje escrito para resample a partir de barras de 1
minuto: a função passa a receber a granularidade-base do dataset em vez de assumi-la. Ancoragem, truncamento do
último balde e contagem de completude ficam inalterados — apenas deixam de contar minutos e passam a contar base
bars.

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
  input_content_hash   conteúdo das barras que motivaram a reavaliação
```

Dois casos:

- **A — o candle corrigido produz outra leitura.** A nova detecção é gravada ao lado (já é o comportamento da
  unicidade por `evidence_hash`) e um fato de supersessão liga a antiga à nova.
- **B — o candle corrigido não produz padrão nenhum.** A observação antiga é **retratada**, com
  `replacement_fact_id` nulo e `reason = NO_LONGER_DETECTED`. Nenhuma "detecção vazia" é inventada.

O caso B é o que o gate do Plano 5 encontrou e não conseguiu registrar.

**O gatilho é conteúdo, não lote.** Reavaliação ocorre quando muda o **hash de conteúdo da barra vencedora**,
nunca porque um `batch_id` novo apareceu. Uma reingestão idêntica é um não-evento: mesmo conteúdo, mesma
identidade, nenhuma supersessão.

**A visão ativa é as-of.** `active_facts(as_of=X)` responde *o que estava ativo naquele instante*, não apenas o
estado mais recente — sem isso não se consegue reconstruir o que a plataforma afirmava antes de uma correção.
Nenhuma estatística lê as tabelas de fatos diretamente: toda leitura estatística passa pela visão ativa da
revisão correspondente, enquanto a auditoria continua enxergando tudo.

*Custo se errada:* em 300 tickers × 10 anos, observações produzidas por dados incompletos permanecem contadas, e
o viés é invisível porque cada linha isolada parece correta.

### D97 — Identidade semântica separada de proveniência

A identidade de um fato é **o que foi observado**; não **quando foi observado**.

Hoje `FeatureSnapshot.feature_hash` inclui `data_as_of`, de modo que duas varreduras sobre exatamente os mesmos
candles produzem identidades diferentes. Isso já causou um defeito real (candidatos duplicados a cada revisit,
contornado em `8b883fb` tratando o sintoma).

O hash semântico passa a depender de: ticker (via `instrument_id`), timeframe, timestamp do setup, padrão,
`feature_version`, `aggregation_version`, os valores das features e a identidade do **conteúdo** das barras de
entrada. **Não** depende do relógio da varredura. `data_as_of`, `research_run_id` e `observed_at` continuam
gravados como proveniência, fora do hash.

Mudar a identidade é mudança de versão: o snapshot passa a `features-v2`, e observações `features-v1` nunca são
agrupadas com `features-v2` na mesma população estatística. O contorno de `8b883fb` é removido quando D97
entrar, porque deixa de ser necessário.

*Custo se errada:* revarreduras inflam contagens, e "quantas ocorrências existem" passa a depender de quantas
vezes o scan rodou.

### D98 — Portão de cobertura do dataset

Pertencer ao universo estatístico exige critérios financeiros **e** qualidade medida do dataset:

```text
candidato → regras de liquidez → disponibilidade histórica → PORTÃO DE COBERTURA → universo estatístico
```

Os limiares **não são adivinhados**: saem da distribuição medida na Fase 0B. O gate do Plano 5 mostrou por que o
portão é necessário — cobertura de minutos do IEX foi 100% (NVDA, SPY), 99,3% (AAPL), 63,3% (PLUG) e 33,3%
(CROX). Liquidez de mercado não é proxy de cobertura de feed.

A cobertura que importa é a de **base bars de 15m nativos**, não a de 1 minuto: é inteiramente possível que a
CROX tenha 33% em `1m` e cobertura alta em `15m`, e nesse caso é perfeitamente utilizável.

**O portão cria o seu próprio viés e ele é registrado separadamente.** Excluir ativos por qualidade de dados não
é o mesmo que excluí-los por liquidez: o dataset passa a carregar `data_availability_bias` ao lado de
`survivorship_bias_status`, porque são vieses diferentes com causas diferentes. Se a cobertura é avaliada por
ano, por janela móvel ou pelo período inteiro é decisão da Fase 0B — as três produzem universos diferentes e a
distribuição medida decide qual é defensável.

*Custo se errada:* metade de um ativo entra na população como se fosse o ativo inteiro, ou um ativo perfeitamente
utilizável é excluído por uma métrica agregada que escondia um único ano ruim.

### D99 — A política de outcome é o experimento

Este é o ponto que separa uma interface convincente de uma interface correta.

O `backtest.py` do Plano 5 rotula entrando no **close do candle que detectou o padrão**, com barreiras por ATR.
O `SetupCandidate` que o produto exibe tem outra coisa: `entry_zone_low`, `entry_zone_high`, `stop`, `target1`,
`target2`, `levels_version` e validade. Se a tela mostra entrada 184–185, stop 180, T1 191 e ao lado "T1 antes
do stop = 67%", esses 67% precisam medir **essas regras**, não `close + 1 ATR / 2 ATR`.

A estatística histórica passa a ser produzida por uma `HistoricalOutcomePolicy` versionada — `HISTORICAL_OUTCOME_V1` —
cuja identidade inclui, no mínimo:

```text
entry policy            como a zona é atingida e o que conta como entrada
zone semantics          toque, fechamento dentro, ou atravessar
validity                quantas sessões a oportunidade permanece viva
stop policy
target1 policy
target2 policy
breakeven-after-T1 policy
same-candle ambiguity   herdada de STOP_FIRST_ON_SAME_CANDLE_V1
gap policy              o que acontece quando o preço abre além de um nível
timeout policy
base resolution         15m
```

**A resolução do outcome é evidência, não rodapé.** Uma simulação em 15m não equivale a um fill em 1 minuto e o
plano não finge que equivale: `outcome_resolution = 15m` e `ambiguity_policy = conservative` viajam junto de todo
número produzido.

*Custo se errada:* o produto exibe uma taxa histórica que responde a uma pergunta diferente da que o usuário vai
operar, e o erro é invisível porque os dois números têm a mesma aparência.

### D100 — Continuidade temporal

Uma sequência histórica usada para detecção, estrutura ou features **conserva continuidade temporal**. Base bars
ausentes são gaps explícitos, e uma janela que cruza um gap não é equivalente a uma janela contínua.

```text
09:30  barra
09:45  AUSENTE
10:00  barra
```

não pode virar dois candles consecutivos para um Engulfing, e uma EMA não pode comprimir o relógio e fingir que o
intervalo nunca existiu. **"Pular o ausente e continuar como se fosse adjacente" é proibido.** Quanto warm-up é
exigido após um gap é detalhe de implementação versionada; a proibição não é.

*Custo se errada:* padrões e níveis são detectados sobre uma linha do tempo que não existiu, e o erro é
silencioso — o gráfico parece normal.

### D101 — Ajuste por eventos corporativos

`research_bars` guarda o 15m **raw**, como veio do fornecedor. A série de pesquisa ajustada é **derivada**, por
fatores de split versionados em `SPLIT_ADJUSTMENT_V1`, com o volume ajustado de forma correspondente.

```text
Alpaca 15m RAW → research_bars → fatores de split versionados → série split-adjusted derivada
```

**Proventos em dinheiro não retroajustam OHLC técnico nesta versão.** Dividendo continua sendo evento
corporativo registrado, mas uma política de retorno total alterando retroativamente a geometria de um Hammer, de
uma Bull Flag ou de um suporte é uma mudança de significado que ninguém pediu. `bars_1m` permanece raw, porque
uma ordem executou no preço que existiu.

*Custo se errada:* todo padrão em torno de um split vira ficção, ou a geometria técnica passa a depender de uma
convenção de retorno total.

### D102 — Revisões de dataset são a unidade reproduzível

`dataset_version` sozinho tenta representar ao mesmo tempo *configuração* e *estado do histórico*, e não
consegue: a Alpaca pode corrigir vinte candles amanhã sem que nenhuma configuração mude. Família e revisão são
separadas.

```text
research_datasets            a família: provider, feed, base, universo, versões de regra
research_dataset_revisions
  revision_id
  dataset_id
  revision_number
  data_as_of                 o watermark que define o que ler
  manifest_hash              identidade do conteúdo lido nessa revisão
  created_at
```

`research_bars` continua append-only e **não** é duplicado a cada revisão. Uma revisão apenas diz: *para esta
execução estatística, leia o dataset como ele era em `data_as_of = X`*. Toda estatística publicada aponta para
uma `dataset_revision_id`, e continua reproduzível depois de qualquer correção posterior.

*Custo se errada:* um número citado hoje não pode ser reproduzido amanhã, e ninguém consegue dizer se mudou por
correção de dados ou por mudança de regra.

## 4. Modelo de domínio

```text
instruments
  instrument_id                identidade estável do ativo
  provider_asset_id            id do fornecedor, quando existir
  primary_symbol
  first_seen_at, last_seen_at

instrument_symbols             símbolo observado ao longo do tempo
  instrument_id, symbol, valid_from, valid_to, source

research_datasets              ver D102
  dataset_id, dataset_family_version
  provider, feed               ex.: ALPACA / IEX
  base_timeframe               15m
  universe_id, universe_version
  aggregation_version, calendar_version, adjustment_version
  survivorship_bias_status     PRESENT | ABSENT
  data_availability_bias       PRESENT | ABSENT
  created_at

research_dataset_revisions     revision_id, dataset_id, revision_number,
                               data_as_of, manifest_hash, created_at

universes
  universe_id, universe_version, name
  membership_model             STATIC_CURATED | POINT_IN_TIME
  rules                        limiares versionados, incluindo o portão de cobertura
  methodology, created_at

universe_membership
  universe_id, universe_version, instrument_id, symbol
  valid_from, valid_to
  inclusion_reason, exclusion_reason, source

research_bars                  candles de base, 15m nativos, raw
  dataset_id, instrument_id, ticker, ts, session_day
  open, high, low, close, volume
  bar_content_hash             identidade do conteúdo, o gatilho de D96
  batch_id                     proveniência pelo mesmo caminho de bar_batches
  PK (dataset_id, instrument_id, ts, batch_id)

research_supersessions         ver D96
```

A chave inclui `batch_id` pelo mesmo motivo que `bars_1m` a inclui: uma correção precisa coexistir com a barra
original, não substituí-la. A leitura é as-of, pelo mesmo padrão de `read_bars_as_of` — a revisão mais recente
cujo `ingested_at` não ultrapassa o watermark vence. É a mudança do **`bar_content_hash` da barra vencedora**
que dispara reavaliação (D96), nunca a mera existência de um lote novo.

`ticker` continua gravado porque é o que se lê nas evidências e nas telas, mas a identidade é `instrument_id`:
símbolos são reciclados e renomeados, e uma estatística de dez anos que confunda dois emissores sob o mesmo
símbolo é indefensável.

Integridade dos candles derivados passa de minutos para **base bars**: `base_bars_expected` /
`base_bars_present` (26 baldes de 15m num pregão regular, 14 num meio-pregão, 4 por hora derivada). Um balde
faltante **nunca** é preenchido sinteticamente, e D100 governa o que uma janela pode atravessar.

## 5. Estatística empírica

Uma coorte é identificada por, no mínimo:

```text
dataset_revision_id
universe_id + universe_version
provider + feed
timeframe
setup_version
feature_version
outcome_policy_version + outcome_policy_hash      ← D99, não apenas label_version
statistics_version
pattern + direction
```

`statistics_version` existe desde a primeira linha de código, para que mudar o método estatístico seja uma
mudança declarada e não uma correção silenciosa.

### Denominadores explícitos

Uma taxa sem o seu denominador é uma afirmação sem sujeito. Toda coorte publica:

```text
occurrences_total             ocorrências elegíveis
entry_filled                  quantas atingiram a zona de entrada
resolved                      quantas terminaram em target ou stop
timeouts                      expiraram vivas
insufficient_future_data      série acabou antes de resolver
target1_hits
target2_hits
stops
median_return, expectancy_r, median_mfe_r, median_mae_r
```

A diferença não é cosmética:

```text
100 ocorrências · 70 entraram · 40 resolveram · 27 atingiram o alvo
27 / 40 = 67,5%
```

Exibir apenas `67,5%` esconde que 60% da população não resolveu.

**O portão de amostra usa `resolved`**, e `occurrences_total`, `entry_filled` e `timeouts` ficam sempre visíveis
ao lado:

```text
resolved < 30        INSUFFICIENT   nenhum número exibido
30 ≤ resolved < 100  EXPLORATORY    exibido marcado como exploratório
resolved ≥ 100       exibível com medida de incerteza
```

Os limiares são configuráveis e versionados.

### Incerteza

**Nenhum intervalo de confiança genérico é produzido por método não especificado.** Bull Flags do mesmo ticker
em janelas sobrepostas não são observações independentes, e um bootstrap IID aplicado a elas produz um intervalo
estreito demais que parece rigoroso. A metodologia de incerteza é versionada e **precisa ser aprovada antes da
Fase 4**, com base no que a Fase 0B medir sobre sobreposição e agrupamento temporal. Até lá a coorte publica
contagens, não intervalos.

### Dois campos, nunca fundidos

*Estatística observada do setup* (frequência histórica, sempre ao lado de `resolved` e da medida de incerteza) e
*probabilidade calibrada* (ausente até haver walk-forward e out-of-sample) são campos distintos no domínio,
não uma convenção de tela. Um score determinístico nunca é apresentado como probabilidade.

Toda taxa exibida carrega revisão de dataset, universo, período, resolução do outcome e as limitações de viés em
texto visível, não em tooltip.

## 6. Decomposição das rejeições de níveis

O gate mediu 171 cadeias válidas em 879 candidatos (19,5%). Isso **não** é sinal de rigor excessivo e nenhuma
regra será relaxada para aumentar o número: detectar uma figura é fácil, transformá-la numa oportunidade com
entrada, stop e alvos coerentes é seletivo por natureza. Este plano apenas passa a decompor os motivos de
rejeição por padrão, timeframe e instrumento, para que o histórico diga depois se alguma regra descarta boas
oportunidades.

## 7. Fases

A ordem foi invertida em relação ao rascunho anterior: a prova obrigatória do spike exige supersessão e
identidade, então elas vêm **antes** dele.

### Fase 0A — Revisão, supersessão e identidade (bloqueia o spike)

O mínimo de D96, D97 e D102 sobre o dataset que já existe: fatos de supersessão, visão ativa as-of, gatilho por
`bar_content_hash`, identidade semântica fora do relógio, revisões de dataset. Sem isso a prova da Fase 0B é
impossível de escrever.

### Fase 0B — Spike (bloqueia todo o backfill)

Entre 5 e 10 instrumentos deliberadamente heterogêneos: duas mega caps/ETFs, duas large caps, duas mid caps e
**PLUG e CROX mantidos de propósito**, por serem os casos adversariais que o gate revelou.

| Questão | Medição |
|---|---|
| Cobertura | base bars de 15m nativos por instrumento e por ano |
| Gaps | distribuição e maior sequência contígua ausente |
| Volume | número de barras desde 2016 |
| Storage | tabela **e índices** reais no Postgres, **e** o mesmo conjunto em Parquet |
| Ingestão | tempo, chamadas de API, rate limits |
| Query | p50/p95 das consultas reais do cockpit, nos dois storages |
| Timeframes | `1h` derivado vs `1h` nativo da Alpaca |
| Diário | `1d` derivado vs vendor e vs yfinance |
| Padrões | ocorrências por tipo e timeframe |
| Sensibilidade a gaps | quanto barras ausentes alteram a contagem de detecções |
| Níveis | % válidos e motivos de rejeição |
| Dependência | taxa de sobreposição, ocorrências por instrumento/sessão, agrupamento temporal |
| Supersessão | uma correção supersede ou retrata corretamente |
| Identidade | revarreduras idênticas não duplicam fatos |

Parquet é medido em vez de descartado por asserção. Para ser elegível precisa satisfazer leitura as-of,
proveniência por lote e replay; se não satisfizer, é descartado por evidência e não por preferência.

#### Prova obrigatória

```text
ingerir dataset V1              → detectar padrão P
corrigir uma barra histórica    → ingerir revisão V2 → P desaparece

asserções:
  o fato antigo continua existindo
  o fato antigo está retratado
  a visão ativa não retorna mais P
  a estatística histórica não conta mais P
  active_facts(as_of=antes da correção) ainda retorna P
  a auditoria ainda reconstrói V1
```

Enquanto essa prova não passar, nenhum backfill amplo começa.

As sete detecções obsoletas de AAPL do canary **não entram em estatística alguma** e não são apagadas. Elas são
**exportadas como fixture de dados** — barras originais, detecção original, barras corrigidas, retratação
esperada — e não referenciadas por id do banco, para que o teste sobreviva a banco novo, migration, restore e
VPS diferente. Foram produzidas por uma falha genuína e valem mais que qualquer fixture sintética.

### Fases seguintes

1. **Fase 1** — universo versionado, membership com `universe_version`, instrumentos e portão de cobertura (D98).
2. **Fase 2** — dataset histórico, backfill 15m e ajuste por split derivado (D95, D101), com cross-check diário.
3. **Fase 3** — `HistoricalOutcomePolicy` e produção de outcomes sobre níveis reais de setup (D99, D100).
4. **Fase 4** — estatística empírica por coorte, com denominadores explícitos e a metodologia de incerteza já
   aprovada.
5. **Fase 5** — exposição somente leitura da evidência histórica na API e no dashboard atual, sem cockpit novo.

## 8. Testes exigidos

- **Look-ahead**: estender `tests/research/test_leakage.py` ao caminho histórico — features de `[:i]`, labels de
  `[i+1:]`, pivôs só após confirmação, agora também sobre base de 15m.
- **Ancoragem**: baldes derivados do calendário, jamais por subtração de minutos. O gate do Plano 5 produziu dois
  falsos positivos exatamente aí: o último balde truncado da sessão (`19:30–19:59` no `1h`) e um padrão de dois
  candles em `1d` fundindo dois dias num OHLC.
- **Continuidade (D100)**: uma janela que cruza um gap não produz o mesmo resultado que uma janela contínua, e
  candles não adjacentes nunca formam um padrão de dois ou três candles.
- **Supersessão (D96)**: os dois casos, a prova completa da Fase 0B, e a não-supersessão de uma reingestão de
  conteúdo idêntico.
- **Identidade (D97)**: a mesma varredura sobre os mesmos candles produz o mesmo hash, em relógios diferentes.
- **Outcome (D99)**: a estatística usa zona, stop, alvos e validade do candidato; um teste falha se o caminho
  histórico reverter para `close + ATR`.
- **Denominadores**: nenhuma superfície exibe taxa sem `resolved`; abaixo do mínimo, o estado é exibido em vez do
  número; frequência observada e probabilidade calibrada nunca ocupam o mesmo campo.
- **Incerteza**: nenhum intervalo é produzido enquanto a metodologia não estiver aprovada e versionada.
- **Ajuste (D101)**: um padrão em torno de um split conhecido é medido sobre a série ajustada e não sobre a
  raw; um provento em dinheiro **não** altera nenhum OHLC técnico; `research_bars` permanece raw sob os dois.
- **Reprodutibilidade (D102)**: uma coorte fixada a uma `dataset_revision_id` produz o mesmo número depois que
  uma revisão posterior corrige barras, e a mesma coorte na revisão nova produz um número diferente — a
  diferença é atribuível a dados, não a regra.
- **Não agrupamento**: coortes com base, universo, feed, revisão ou versões diferentes não se misturam.
- **Cobertura**: um instrumento abaixo do portão não entra no universo estatístico, e o viés de disponibilidade
  fica registrado no dataset.

## 9. O que a Fase 0B decide, e portanto não está decidido aqui

1. Limiares de cobertura, e se a cobertura é avaliada por ano, por janela móvel ou pelo período inteiro.
2. Limiares financeiros do `US_LIQUID_RESEARCH v1` (preço mínimo, volume financeiro mediano, histórico mínimo).
3. Postgres particionado vs Parquet, pelos números dos dois.
4. Tamanho do universo inicial, por extrapolação a partir do spike.
5. A metodologia de incerteza, a partir da dependência medida entre ocorrências.
6. Se `1m` sob demanda continua necessário para a watchlist diária de até 3 instrumentos.
