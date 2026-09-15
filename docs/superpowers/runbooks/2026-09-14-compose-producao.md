# Runbook — Compose de produção (Plano 3C)

## Subir

```bash
cp .env.example .env            # troque todos os change-me: mesmo usuário/banco/senha de POSTGRES_USER/POSTGRES_DB/
                                 # POSTGRES_PASSWORD, mas a senha vai percent-encoded dentro de DATABASE_URL quando
                                 # tiver caracteres reservados (/ + = @ : %); ex.:
                                 # python -c "from urllib.parse import quote; print(quote(SENHA, safe=''))"
                                 # o Compose interpola "$" em valores do .env: evite ou escreva "$$"
export GIT_SHA="$(git rev-parse --short HEAD)"   # o mesmo valor para build e up: a imagem não é reconstruída
docker compose config --quiet   # valida o arquivo e a interpolação, sem rede
docker compose build            # imagem do motor (uma vez, pelo migrate) e imagem do dashboard (dashboard/uv.lock)
docker compose up -d
docker compose ps               # migrate "exited (0)"; api "healthy"; worker e dashboard "running"
```

## Smoke manual

Não dê `source`/`.` no `.env` inteiro (evita exportar segredos que não precisam sair do arquivo) e não
coloque a chave no argv do `curl` (visível em `ps` para outros usuários da máquina); use um arquivo de
cabeçalho com permissão restrita, removido logo depois:

```bash
API_KEY="$(grep '^API_KEY=' .env | cut -d= -f2-)"
HEADER_FILE="$(mktemp)"
chmod 600 "$HEADER_FILE"
printf 'X-API-Key: %s\n' "$API_KEY" > "$HEADER_FILE"
curl -fsS -H "@$HEADER_FILE" http://127.0.0.1:8000/health | python -m json.tool
rm -f "$HEADER_FILE"
unset API_KEY
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/health    # 401 sem chave
docker compose logs --tail=50 worker                                       # jobs registrados, lock obtido
```

Abra `http://127.0.0.1:8501`. Em VPS, só por túnel SSH: `ssh -L 8501:127.0.0.1:8501 <host>`.

## Decisões operacionais

- Healthcheck do `api` = `GET /health` com a chave do ambiente (D48): `DEGRADED` continua saudável; `503` (banco ou schema fora da head) não. O `dashboard` sobe assim que o `api` inicia (D58) e mostra "API indisponível" enquanto ele não responde.
- Um único `worker` (D20). `restart: unless-stopped` reinicia em qualquer código de saída; para os três abaixo, confira antes de deixar o loop de restart continuar:
  - `2`: duas causas possíveis, distinguidas pelo log (`docker compose logs worker`). Configuração inválida (`EXIT_CONFIG`) imprime um JSON no stderr, `{"error": "CONFIG_INVALID", "errors": [...]}` — por exemplo `EVAL_INTERVAL_MINUTES` fora do intervalo aceito — e exige corrigir o `.env` e recriar o serviço (`docker compose up -d worker`), senão ele reinicia com a mesma falha indefinidamente. Sem essa mensagem, `2` significa que outro worker já detém o lock (`EXIT_LOCKED`); persistente sem `CONFIG_INVALID` indica dois stacks apontando para o mesmo banco.
  - `3` (`EXIT_LOCK_LOST`, D39): o worker perdeu o lock em execução — confira a conectividade com o banco e se outro processo assumiu o lock.
  - `4` (`EXIT_DATABASE_UNAVAILABLE`, D49): o banco estava inacessível na subida — confira se `postgres` está `healthy` e se `DATABASE_URL` está correto antes de esperar o próximo restart.
- `docker compose stop worker` espera até 150 s: um fim de dia ou recheck em andamento termina.
- Reconstruir projeções: `docker compose run --rm worker python -m virtual_orders.worker rebuild-projections` (não disputa o lock do worker).

## Segurança — limitações conhecidas

- **Dashboard sem autenticação (D59):** o Streamlit não autentica; a porta fica só em `127.0.0.1` e o acesso remoto é por túnel SSH. Nunca publique a porta fora de `127.0.0.1`. Autenticação e proxy TLS ficam para o endurecimento de VPS.
- **Desvio registrado da spec 6 (D55):** `api` e `worker` conectam com o dono do banco, não com uma role sem `UPDATE`/`DELETE`. Os triggers de append-only rejeitam mutação do histórico para qualquer role (`test_app_role_cannot_update_history`); a role separada fica para o endurecimento de VPS.
- `migrate` recebe só `DATABASE_URL` (M12); o `dashboard` recebe só `DASHBOARD_API_URL` e `API_KEY`, e esconde detalhes de erro (`--client.showErrorDetails none`, D58).

## Jobs perdidos (D53 — recuperação manual)

- `OPENING_MISSING`: não rode a abertura depois do horário. Dividendos com ex-date do dia sem validação ficam para revisão; confira as ordens do ticker e registre `NEEDS_REVIEW` manual se preciso.
- `END_OF_DAY_MISSING`: não há comando para rodar o fim de dia de um pregão passado (D4/D12(b)). A validade é finalizada no fim de dia seguinte, e a qualidade do pregão entra em `QUALITY_NOT_EVALUATED` e é coberta pelo `DATA_QUALITY_RECHECK` (D22, D52). Se o feed continuar falhando por 5 pregões, a pendência termina em `PROVIDER_FAILURE_FINAL` com `NEEDS_REVIEW` `DATA_QUALITY_UNVERIFIED`.
- A causa some de `/health` quando o próximo job daquele tipo roda para o pregão seguinte e o lookback de 14 dias passa; ela nunca é apagada à mão.

## Observação diária (Plano 4)

Relatório só leitura de um pregão (as-of o relógio do banco, sem chamada a provider e sem escrita):

```bash
docker compose exec api python -m virtual_orders.observation report --day 2026-09-15            # Markdown
docker compose exec api python -m virtual_orders.observation report --day 2026-09-15 --format json
docker compose exec api python -m virtual_orders.observation summary --from 2026-09-15 --to 2026-09-26
```

- Saídas: `0` ok; `1` erro interno (`INTERNAL_ERROR`, ou `DATABASE_ERROR` para erro de SQL que não é de conexão; só o tipo); `2` uso inválido ou `DATABASE_URL` ausente; `3` dia sem pregão, no futuro ou intervalo inválido (`OBSERVATION_REQUEST_INVALID`); `4` banco inacessível (`OperationalError`, `InterfaceError` ou timeout do pool).
- Pela API/dashboard: `GET /observation/report?day=…` e `GET /observation/summary?from=…&to=…`; página "Observação". Cada pedido lê um único snapshot do banco.
- Com `N8N_WEBHOOK_URL`, o fim de dia enfileira um alerta `OBSERVATION_DAILY:<pregão>` só com contagens (fotografia do primeiro fim de dia que roda; `complete: false`). O primeiro enfileiramento vence: se ele falhar (log `observation alert failed: <Tipo>`) e o pregão se resolver no fim de dia seguinte, não há alerta para esse pregão — use a CLI. Para o dia inteiro, rode o relatório depois da meia-noite ET.
- Reinícios do worker vêm de `worker_sessions`. "Sessão aberta no fim" = sem linha de parada: o worker está rodando **ou** morreu sem passar pelo `finally`; só o próximo início distingue. "Fins sem parada" (SIGKILL, OOM, queda do host) aparecem no relatório do pregão em que o worker **voltou** a iniciar — confira `docker compose ps`/`docker inspect` do contêiner.
- A pressão × resultado é **estimativa descritiva** sobre poucos trades: não é evidência causal nem sinal de trading.
