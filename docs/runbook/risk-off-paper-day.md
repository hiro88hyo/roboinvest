# Risk-Off Paper Day Runbook

作成日: 2026-06-06

米国市場・日経先物が大幅下落している日の運用メモ。live は止め、production pipeline は paper に回して、急落日のシグナル・reject・約定品質を観測する。

## 方針

- live 新規注文は出さない。
- live 建玉が空なら `trade_mode=paper` / `OMS_LIVE_DRY_RUN=true` で運用する。
- live 建玉がある日は paper 化の前に close / reconcile を優先する。
- paper は通常稼働し、寄り付き急落・板薄・closeout の挙動を記録する。

## 前日または寄り前準備

```bash
cd /home/hiroyuki/workspaces/roboinvest
set -a && . infra/.op.service-account.env && set +a
```

`infra/env.production` が以下になっていることを確認する。

```text
TRADE_MODE=paper
OMS_LIVE_DRY_RUN=true
```

Cloud Supabase の状態を dry-run で確認する。

```bash
op run --env-file infra/env.production -- \
  uv run python scripts/prepare-risk-off-paper-day.py
```

live 建玉が空であることを確認してから system_status を paper に切り替える。

```bash
op run --env-file infra/env.production -- \
  uv run python scripts/prepare-risk-off-paper-day.py --apply
```

## 当日朝

最短手順:

```bash
cd /home/hiroyuki/workspaces/roboinvest
set -a && . infra/.op.service-account.env && set +a

op run --env-file infra/env.production -- \
  uv run python scripts/prepare-risk-off-paper-day.py

bash scripts/run-production-universe-scanner.sh

op run --env-file infra/env.production -- \
  uv run python scripts/health-check.py --check supabase --timeout 30

op run --env-file infra/env.production -- \
  docker compose --env-file infra/env.production \
  -f infra/docker-compose.prod.yml up -d --build

op run --env-file infra/env.production -- \
  uv run python scripts/health-check.py --check supabase services --timeout 30
```

期待値:

- `prepare-risk-off-paper-day.py` で `trade_mode=paper`、`live_positions: empty`。
- Universe Scanner が `done: valid_date=YYYY-MM-DD watchlist_size=N` で終わる。
- Supabase health check が `OK supabase ok=9 ng=0`。
- 起動後、Gateway の注文先が `paper-orders` で、live 注文が流れない。

## 寄り付き後に見るもの

- `strategy_logs`: rule / AI が急落をどう判断したか
- `aggregator_logs`: single-source / consensus と confidence
- `gateway` logs: `signal_rejected`, reject reason, paper order publish
- `trades_paper`: 約定価格が板に対して楽観的すぎないか
- `positions`: paper の含み損益、14:50 closeout 後に空になるか

ログ確認:

```bash
op run --env-file infra/env.production -- \
  docker compose --env-file infra/env.production -f infra/docker-compose.prod.yml logs --tail=100 \
  feeder feature-engine strategy-rule strategy-ai aggregator gateway oms-paper
```

## Abort Conditions

- live positions が残っている。
- `system_status.trade_mode` が `paper` になっていない。
- `OMS_LIVE_DRY_RUN=true` ではない。
- watchlist が空、または当日 `valid_date` ではない。
- feeder が kabu WebSocket に接続できない。
- paper order が流れず reject だけが増え続ける。
