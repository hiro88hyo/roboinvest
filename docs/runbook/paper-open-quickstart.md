# Paper Open Quickstart

明日の寄り付き前に最初に打つコマンドだけを抜いた最短版。
詳細な判断基準は `docs/runbook/paper-open-checklist.md` を参照。
急落警戒で live を止めて paper 観測にする日は
[`risk-off-paper-day.md`](risk-off-paper-day.md) も先に確認する。

## 1. Load 1Password Service Account

```bash
cd /home/hiroyuki/workspaces/roboinvest
set -a && . infra/.op.service-account.env && set +a
```

## 2. Validate Production Env

```bash
op run --env-file infra/env.production --   docker compose -f infra/docker-compose.prod.yml config >/dev/null
```

`/dev/shm/roboinvest/gcp-pubsub-sa.json` が実ファイルとして使えない場合だけ、
代替 credential を作って compose に明示する。

```bash
op read --out-file /tmp/roboinvest-gcp-pubsub-sa.json --force \
  op://roboinvest/production/GOOGLE_APPLICATION_CREDENTIALS_JSON
chmod 600 /tmp/roboinvest-gcp-pubsub-sa.json
uv run python -m json.tool /tmp/roboinvest-gcp-pubsub-sa.json >/dev/null
export GOOGLE_APPLICATION_CREDENTIALS_HOST_PATH=/tmp/roboinvest-gcp-pubsub-sa.json
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

代替 credential path を使う場合:

```bash
op run --env-file infra/env.production -- \
  env GOOGLE_APPLICATION_CREDENTIALS_HOST_PATH="$GOOGLE_APPLICATION_CREDENTIALS_HOST_PATH" \
    docker compose --env-file infra/env.production -f infra/docker-compose.prod.yml up -d --build
```

## 6. Final Check

```bash
op run --env-file infra/env.production -- \
  uv run python scripts/production-preopen-check.py \
    --timeout 30 \
    --expected-trade-mode paper
```

前日準備で翌営業日を確認する場合:

```bash
op run --env-file infra/env.production -- \
  uv run python scripts/production-preopen-check.py \
    --timeout 30 \
    --expected-trade-mode paper \
    --target-date YYYY-MM-DD \
    --kabu-offline
```

## 7. Watch Logs After Open

```bash
op run --env-file infra/env.production --   docker compose -f infra/docker-compose.prod.yml logs --tail=100 feeder feature-engine strategy-rule strategy-ai aggregator gateway oms-paper
```
