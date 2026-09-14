# Runbook — Compose de produção (Plano 3C)

## Subir

```bash
cp .env.example .env            # troque todos os change-me; a senha de DATABASE_URL = POSTGRES_PASSWORD
docker compose config --quiet   # valida o arquivo e a interpolação, sem rede
export GIT_SHA="$(git rev-parse --short HEAD)"   # o mesmo valor para build e up: a imagem não é reconstruída
docker compose build            # imagem do motor (uma vez, pelo migrate) e imagem do dashboard (dashboard/uv.lock)
docker compose up -d
docker compose ps               # migrate "exited (0)"; api "healthy"; worker e dashboard "running"
```

## Smoke manual

```bash
set -a; . ./.env; set +a
curl -fsS -H "X-API-Key: $API_KEY" http://127.0.0.1:8000/health | python -m json.tool
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/health    # 401 sem chave
docker compose logs --tail=50 worker                                       # jobs registrados, lock obtido
```

Abra `http://127.0.0.1:8501`. Em VPS, só por túnel SSH: `ssh -L 8501:127.0.0.1:8501 <host>`.

## Decisões operacionais

- Healthcheck do `api` = `GET /health` com a chave do ambiente (D48): `DEGRADED` continua saudável; `503` (banco ou schema fora da head) não. O `dashboard` sobe assim que o `api` inicia (D58) e mostra "API indisponível" enquanto ele não responde.
- Um único `worker` (D20). Códigos de saída: `2` outro worker tem o lock; `3` lock perdido (D39); `4` banco inacessível na subida (D49). `restart: unless-stopped` reinicia 3 e 4; um `2` persistente indica dois stacks no mesmo banco.
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
