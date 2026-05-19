# Paper Open Checklist

作成日: 2026-05-19

production compose / Cloud Supabase / managed Pub/Sub を前提に、寄り付き前に paper trading を安全に始めるための最小手順。
明日は Universe Scanner を手動実行し、その結果を確認してから常駐 services を起動する。

## 1. Preconditions

- `main` が最新であること
- 1Password service account token が `infra/.op.service-account.env` で読めること
- `infra/env.production` が J-Quants API v2 / Supabase / kabu / GCP secrets を参照していること
- `infra/secrets/gcp-pubsub-sa.json` が存在すること

確認:

```bash
set -a && . infra/.op.service-account.env && set +a
op run --env-file infra/env.production -- docker compose -f infra/docker-compose.prod.yml config >/dev/null
```

## 2. Run Universe Scanner

寄り付き前に当日 watchlist を生成する。

```bash
set -a && . infra/.op.service-account.env && set +a
op run --env-file infra/env.production --   docker compose -f infra/docker-compose.prod.yml --profile batch run --rm universe-scanner
```

成功条件:

- ログ末尾が `done: valid_date=YYYY-MM-DD watchlist_size=N`
- `watchlist_size` が 0 ではない

補足:

- `JQUANTS_API_VERSION=v2` では `JQUANTS_API_KEY` を使う
- `daily_ohlcv` は chunk upsert のため数分かかることがある

## 3. Verify Supabase State

当日の `watchlist` と `daily_ohlcv` の最新日付を確認する。

```bash
set -a && . infra/.op.service-account.env && set +a
op run --env-file infra/env.production --   uv run python scripts/health-check.py --check supabase --timeout 30
```

最低限見たいこと:

- `watchlist` が空でない
- `daily_ohlcv` が直近営業日まで入っている
- `system_status` が読める

必要なら SQL / REST で当日 `watchlist` 件数も spot check する。

## 4. Start Production Compose

paper mode のまま compose services を起動する。

```bash
set -a && . infra/.op.service-account.env && set +a
op run --env-file infra/env.production --   docker compose -f infra/docker-compose.prod.yml up -d --build
```

確認:

```bash
set -a && . infra/.op.service-account.env && set +a
op run --env-file infra/env.production --   uv run python scripts/health-check.py --check supabase services --timeout 30
```

## 5. First Checks After Start

寄り付き直後は次を重点確認する。

- `feeder` が kabu WebSocket に接続している
- `raw-market-data` が流れ始める
- `feature-engine -> strategy-rule / strategy-ai -> aggregator -> gateway -> oms-paper` が止まっていない
- `gateway` reject だけが増えていない

必要なログ確認:

```bash
set -a && . infra/.op.service-account.env && set +a
op run --env-file infra/env.production --   docker compose -f infra/docker-compose.prod.yml logs --tail=100 feeder feature-engine strategy-rule strategy-ai aggregator gateway oms-paper
```

## 6. Abort Conditions

次のどれかなら寄り付き前でも無理に進めない。

- `universe-scanner` が失敗する
- `watchlist` が空、または当日 `valid_date` でない
- `daily_ohlcv` が極端に古い
- `health-check.py` で service / Supabase エラーが出る
- `feeder` が kabu 接続できない

## 7. Tomorrow's Minimum Path

明日はまず次の順で十分。

1. `universe-scanner` を手動実行
2. `health-check.py --check supabase` で `watchlist` / `daily_ohlcv` を確認
3. `docker compose ... up -d --build` で paper services 起動
4. `health-check.py --check supabase services` を確認
5. 寄り付き後に logs を監視
