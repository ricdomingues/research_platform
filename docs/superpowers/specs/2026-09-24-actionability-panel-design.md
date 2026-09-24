# Painel de actionability — desenho (Plano 7)

- **Spec base:** v1.2 + D5–D73 (Planos 2–4) + D74–D94 (Plano 5 e página Terminal) + D95–D102 (Plano 6).
  Este plano abre **D103**.
- **Data:** 2026-09-24
- **Escopo fechado pelo dono:** o painel de revisão manual. Fora de escopo, explicitamente: order flow,
  bid/ask, book, decisão sub-minuto e qualquer probabilidade calibrada no rótulo.

## Contexto

O pedido original era um loop de scalp de 1 segundo com order flow, spread e `P(TP antes SL) >= 72%`, piscando
BUY/WATCH/SELL para revisão manual. O levantamento mostrou que isso não é implementável neste motor:

- `Bar.__post_init__` (`src/core/domain/models.py:149`) **rejeita** timestamp com segundos. A cadência base é
  1 minuto; o Plano 6 assentou em 15m. O loop de 1s é recusado pelo modelo de domínio, não pela implementação.
- Não existe bid/ask, book ou tick em `src/virtual_orders/marketdata/`. "Order flow comprador" e "spread
  aceitável" não são computáveis a partir de OHLCV. O que existe é o CMF em `analytics/pressure.py`, rotulado
  pelo próprio repo como estimativa derivada de OHLCV (D27).
- Não há modelo calibrado. O Plano 6 separou deliberadamente frequência observada de probabilidade calibrada
  (D95–D99); um rótulo "72%" piscando na tela violaria essa separação de frente.
- Um stop de 0,15% com revisão humana é estruturalmente inviável: a decisão manual leva 5–30s, e spread mais
  slippage consomem o stop inteiro antes da ordem chegar.

A decisão do dono foi separar os dois produtos: o motor atual (OHLCV, 1m/15m, human-in-the-loop) recebe o
painel; a hipótese de scalp, se algum dia for testada, nasce como experimento isolado que primeiro responde se
existe edge líquido de fee e slippage — e só então justifica execução em tempo real.

## Estados do painel

| Estado | Origem no domínio | Significado |
|---|---|---|
| `WATCH` | `stored_promotion_errors` devolve blockers | O setup existe, mas a política ainda barra a entrada. |
| `ACTIONABLE` | `signal_actionability` → `ACTIONABLE` | A entrada continua válida agora. |
| `EXIT` | a ordem **real**: `needs_review`, `OPEN`/`PARTIAL`, ou fechada por `STOPPED` / `TARGET_FINAL` / `TIME_EXIT` | Há posição em papel que o dono precisa espelhar ou revisar. |
| `INVALIDATED` | `signal_actionability` → `INVALIDATED` | O setup deixou de valer. |
| `EXPIRED` | `signal_actionability` → `SIGNAL_EXPIRED`, `STOPPED`, `TARGET_REACHED` ou `ENTRY_OPPORTUNITY_ALREADY_OCCURRED` | A entrada não está mais disponível. |
| `STALE` | cobertura não verificada (`STRICT_PRIMARY`) | O dado não sustenta afirmação nenhuma. |

**`EXIT` é lido da ordem armazenada, nunca do hipotético.** A distinção importa: `signal_actionability`
responde sobre uma ordem que *teria sido* criada, e usar isso para dizer "saia" inventaria posição onde não há
nenhuma. Quando existe ordem real, ela decide; quando não existe, as razões do hipotético que significam
"a entrada acabou" (`STOPPED`, `TARGET_REACHED`, `ENTRY_OPPORTUNITY_ALREADY_OCCURRED`) caem em `EXPIRED`
carregando a razão do domínio à vista.

Precedência, e o motivo de cada degrau:

1. **Ordem real** — é fato já gravado pelo avaliador. Um furo de dado não o torna falso, e esconder "seu stop
   bateu" por causa de cobertura incompleta erraria para o lado perigoso.
2. **Expirado** — decidido antes da cobertura, exatamente como o caminho de escrita o decide antes do ingest.
   Um sinal vencido está vencido qualquer que seja o dado, e reportá-lo como problema de frescor seria mentira.
3. **Cobertura** — `STALE`.
4. **Actionability** — só aqui um estado verde pode nascer.

Assim "STALE vence ACTIONABLE" vale exatamente onde o dono pediu: `STALE` só disputa com estados derivados das
barras, nunca com um fato já gravado.

---

### D103 — O estado de actionability é leitura, nunca uma corrida

`GET /signals/actionability` **não grava `evaluation_runs`, não chama o gateway e não ingere barra alguma.**
Lê as barras já armazenadas as-of e decide.

O caminho de escrita (`POST /signals/{id}/orders`) continua exatamente como está: abre uma run `ACTIONABILITY`,
ingere pelo gateway e audita a decisão que criou a ordem. Isso é certo lá, porque cada clique é um fato que
precisa de auditoria.

O painel é o oposto: ele consulta a cada poucos segundos. Se cada consulta abrisse uma run,
`evaluation_runs` cresceria por polling, e duas leituras que existem hoje passariam a mentir —
`actionability_outcomes` (`src/virtual_orders/evaluator/manual.py:190`), que alimenta o `/health` por D7, e
`actionability_requests` no relatório de observação (D70), que conta *cliques do dono*. Um painel aberto a
tarde inteira inflaria as duas até perderem sentido. Portanto: a leitura não deixa rastro, e a auditoria
continua sendo do clique.

Consequência aceita: o painel só enxerga o que o worker já ingeriu. Ele nunca é mais fresco que o ciclo de
ingestão — e é justamente isso que D104 obriga a mostrar na tela.

### D104 — STALE vence ACTIONABLE, e frescor é a política de cobertura que já existe

Frescor não ganha limiar novo. O painel aplica `STRICT_PRIMARY_COVERAGE`
(`src/virtual_orders/evaluator/coverage.py:33`) sobre a mesma janela que o `POST` usaria: todo minuto esperado
pelo calendário de sessão entre o início da avaliação e `min(floor_minute(as_of), valid_until_ts)`.

Se sobrar minuto não resolvido, o estado é `STALE`, qualquer que fosse o estado calculado. Nenhum verde pode
aparecer sobre dado furado.

Duas propriedades caem de graça dessa escolha, e ambas são o motivo dela:

1. **Medir contra o calendário, e não contra o relógio, elimina o falso STALE fora do pregão.** Depois do
   fechamento não há minuto esperado, então não há minuto faltando.
2. **O painel não pode contradizer o motor.** É a mesma política, sobre a mesma janela, sobre as mesmas
   barras: onde o painel diz `ACTIONABLE`, o `POST` não pode responder 503 `ACTIONABILITY_UNVERIFIABLE`. Essa
   concordância é pinada por teste de integração, não por convenção.

A diferença que resta entre os dois caminhos é o ingest: o `POST` busca barras antes de decidir, o painel não.
Isso só pode mover o estado na direção segura — o painel vê menos barra que o `POST`, logo pode dizer `STALE`
onde o `POST` conseguiria decidir. O contrário é que está proibido.

### D105 — O painel não cria taxonomia

O dashboard não inventa nome de estado, não recalcula estratégia, não estima probabilidade e não decide se algo
é comprável. Ele renderiza o que o backend respondeu.

`EXIT` é rótulo de apresentação sobre razões que o domínio já nomeia, e a tela sempre mostra a razão do domínio
junto (`Target reached`, `Stop reached`, `Signal invalidated`, `Manual review required`). `WATCH` são os
blockers que `stored_promotion_errors` (`src/virtual_orders/research/promotion.py:185`) já produz para a
promoção — os mesmos códigos, na mesma ordem, sem segunda implementação.

Nenhum número aparece sem a frase que diz o que ele não é: `score_interpretation` continua viajando junto,
como no Terminal (spec 18). O painel não exibe probabilidade calibrada porque não existe nenhuma.
