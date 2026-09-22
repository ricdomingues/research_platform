# Plano 5 — Canary com dados reais e fechamento do merge gate

- **Objetivo do gate:** responder uma única pergunta — *o motor de pesquisa já existente funciona corretamente
  sobre dados reais recentes?* Backfill histórico, universo, viés de sobrevivência e probabilidade empírica são
  Plano 6 e foram deliberadamente mantidos fora daqui.
- **Branch:** `plan-5-research` · **Base:** `main` em `e0329b0` · **Stack viva:** `docker compose`, schema 0006
- **Código durante o canary:** `70c3ca2` (inicial) → `d79188b` → `4c4917c` → `c77ae6b` → `8b883fb` (este gate)

## Fase 1 — Entrada ampliada

A watchlist tinha 1 ticker e 5 pregões: amostra insuficiente para inspeção. Passou a 5 tickers com perfis de
liquidez deliberadamente diferentes, com 30 pregões de barras de 1 minuto cada, pelo caminho de ingestão que já
existe (`ingest_bars`, mesma proveniência por `bar_batches`).

| ticker | barras | pregões | cobertura dos 390 minutos |
|---|---:|---:|---:|
| NVDA | 11.698 | 30 | 100,0% |
| SPY | 11.695 | 30 | 100,0% |
| AAPL | 12.009 | 31 | 99,3% |
| PLUG | 7.411 | 30 | 63,3% |
| CROX | 3.901 | 30 | 33,3% |

**Achado que pertence ao Plano 6:** o feed IEX do plano Basic da Alpaca não entrega dois de cada três minutos da
CROX. Uma regra de universo baseada só em volume financeiro não captura isso; o universo precisa de um limiar de
**cobertura medida**. É também a confirmação empírica mais forte da decisão de usar 15m como base histórica.

## Fase 2 — Medições do checklist do PR

| Medida | Resultado |
|---|---|
| Detecções | 1.204 (15m: 919 · 1h: 233 · 1d: 15) |
| Candidatos | 894 |
| Cadeias de níveis válidas | 171 de 879 (19,5%) |
| Recusas | TARGET_BLOCKED_BY_STRUCTURE 374 · RISK_REWARD_BELOW_MINIMUM 299 · TARGET1_NOT_BEYOND_ZONE 58 · STOP_NOT_BEYOND_ZONE 36 · NO_LEVELS 18 |
| Distribuição do score | unimodal em torno de 0,55; cauda superior rala (10 acima de 0,8, 1 acima de 0,9) |
| Tempo de varredura | 1,7 s a 4,6 s para 5 tickers × 3 timeframes × 30 pregões |
| Pico de RSS | 146 MB (crescimento de 20 MB durante a varredura) |
| Idempotência | duas varreduras sobre janela fechada escreveram **0** detecções e **0** candidatos |
| Isolamento do `LIVE_CYCLE` | cadência de 120 s sem desvio acima de 40 ms durante todas as varreduras |

Nenhum modelo foi treinado ou aprovado, e nada foi promovido.

## Fase 3 — Correção das detecções, verificada de forma independente

Cada detecção armazenada foi reconferida **sem chamar os detectores**: as barras de 1 minuto foram relidas, os
baldes reconstruídos a partir do calendário NYSE (ancorados na abertura da sessão, com o último balde truncado
tratado como tal), a geometria recalculada e a regra documentada reaplicada com os limiares reescritos
literalmente no verificador.

```
1.204 verificadas · 1.196 aprovadas · 7 reprovadas · 1 pulada
```

As 7 reprovações são todas do AAPL, ids 17–41, **todas criadas sob `70c3ca2`** — antes do `settled()` — cada uma
exatamente um minuto após o fechamento do seu candle. Das 1.157 detecções criadas pelo código corrigido, em
cinco tickers, **nenhuma** discorda das barras.

Duas reprovações iniciais foram defeito do verificador, não do motor, e valem registro porque são a mesma
armadilha que o Plano 6 vai encontrar: voltar um número fixo de minutos a partir de `end_ts` lê o balde errado
quando o último balde da sessão é truncado (19:30–19:59 no `1h`), e num padrão de dois candles em `1d` isso
funde os dois dias num OHLC só.

## Fase 4 — O último item em aberto, fechado

`settled()` impede ler um balde cedo demais; não ajuda um balde que o fornecedor corrige **depois**, porque a
detecção já empurrou `last_detection_end_ts` além do próprio candle. `ScanConfig.revisit_sessions` (padrão 2)
passa a reexaminar os pregões recentes independentemente do watermark. Um candle inalterado produz o mesmo
`evidence_hash` e não grava nada; um candle corrigido grava ao lado da leitura obsoleta.

O revisit também expôs um segundo defeito: re-derivar um candidato a partir de uma detecção inalterada gravava
duplicata a cada varredura, porque `FeatureSnapshot` carrega o `data_as_of` da varredura e portanto hasheia
diferente mesmo quando todo valor medido é idêntico. Um candidato passa a ser derivado só quando sua detecção
foi de fato inserida.

## Limitações conhecidas que saem deste gate

1. **Uma leitura obsoleta não deixa lápide.** O revisit reexaminou os quatro baldes de 21/09 (hoje completos,
   15/15 e 60/60 minutos) e o motor **não detecta nada** neles. Como o candle corrigido não produz padrão, não há
   linha nova para gravar ao lado, e a tabela append-only não permite marcar a antiga como superada. As 7 linhas
   continuam lá. São artefatos de canary, não estatística — mas **o Plano 6 precisa de supersessão de primeira
   classe**, porque num dataset de dez anos essa classe de linha é erro estatístico silencioso.
2. **Uma correção que chega depois da janela de revisit ainda escapa.** O limite agora é um botão nomeado
   (`revisit_sessions`), não um acidente.
3. **`data_as_of` dentro de `feature_hash`** faz duas observações idênticas terem identidades diferentes. Não foi
   corrigido aqui: mudar essa identidade é decisão de `feature_version`, não de merge gate.
4. Seguem válidas as limitações 1, 2, 4, 5 e 6 do encerramento de 17/09. A limitação 3 (padrões de três candles
   disparando em toda tripla) segue aberta e é pré-requisito de qualquer Radar.

## Suítes executadas do zero após a correção

| Suíte | Resultado |
|---|---|
| Motor (unitários, propriedade, fronteiras, integração Postgres, deploy) | **1.683 passando** (102 s) |
| Dashboard | **80 passando** (3,0 s) |
| `ruff` | limpo (raiz e dashboard) |
| `mypy` | limpo — 144 arquivos na raiz, 18 no dashboard |
| Núcleo congelado | `git diff plan/virtual-order-engine-core-complete -- src/core` vazio |
