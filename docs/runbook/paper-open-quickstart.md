# Paper Open Quickstart

明日の寄り付き前に最初に打つコマンドだけを抜いた最短版。
詳細な判断基準は `docs/runbook/paper-open-checklist.md` を参照。

## 1. Load 1Password Service Account

```bash
cd /home/hiroyuki/workspaces/roboinvest
set -a && . infra/.op.service-account.env && set +a
```

## 2. Validate Production Env

```bash
op run --env-file infra/env.production --   docker compose -f infra/docker-compose.prod.yml config >/dev/null
```

## 3. Run Universe Scanner

```bash
bash scripts/run-production-universe-scanner.sh
```

直接 compose を叩く場合:

```bash
op run --env-file infra/env.production --   docker compose -f infra/docker-compose.prod.yml --profile batch run --rm universe-scanner
```

期待する終端ログ:

```text
done: valid_date=YYYY-MM-DD watchlist_size=N
```

## 4. Check Supabase / Services

```bash
op run --env-file infra/env.production --   uv run python scripts/health-check.py --check supabase --timeout 30
```

## 5. Start Paper Services

```bash
op run --env-file infra/env.production --   docker compose -f infra/docker-compose.prod.yml up -d --build
```

## 6. Final Check

```bash
op run --env-file infra/env.production --   uv run python scripts/health-check.py --check supabase services --timeout 30
```

## 7. Watch Logs After Open

```bash
op run --env-file infra/env.production --   docker compose -f infra/docker-compose.prod.yml logs --tail=100 feeder feature-engine strategy-rule strategy-ai aggregator gateway oms-paper
```
