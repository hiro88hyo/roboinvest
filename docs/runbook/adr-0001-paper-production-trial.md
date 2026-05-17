# ADR-0001 Paper Production Trial Log

作成日: 2026-05-16

ADR-0001 の production compose + Supabase Cloud + managed Pub/Sub で paper mode の疎通を確認した記録。
本 runbook は検証結果の証跡であり、live 発注への切り替え手順は含めない。

## 1. Safety State

- `TRADE_MODE=paper`
- `OMS_LIVE_DRY_RUN=true`
- `trading_style=day`
- Dashboard / Supabase `system_status.is_trading_allowed=true`
- OMS Live dry-run smoke では `trades_live` への書き込みなしを確認済み。

## 2. Runtime State

production compose の常駐 8 services が `Up`。

- `aggregator`
- `feature-engine`
- `feeder`
- `gateway`
- `oms-live`
- `oms-paper`
- `strategy-ai`
- `strategy-rule`

`universe-scanner` は batch profile のため常駐対象外。

## 3. Supabase Cloud State

`system_status`:

```text
id=1
is_trading_allowed=true
trade_mode=paper
trading_style=day
daily_pnl=0
weekly_pnl=0
monthly_pnl=0
```

`positions`:

```text
symbol=7203
trade_type=paper
side=LONG
quantity=300
entry_price=2510
current_price=2510
unrealized_pnl=0
opened_at=2026-05-16T06:13:20.216236+00:00
```

`trades_paper`:

```text
trade_id=35d9b3f4-d1fa-4c75-a3d8-64e3e4e447b3
unified_signal_id=dd495a3e-4805-429f-89fb-feba7bfbce78
symbol=7203
side=BUY
quantity=300
price=2510
executed_at=2026-05-16T06:13:20.216236+00:00
```

## 4. Verified Flow

- Supabase Cloud schema / seed / health check
  - 9 tables OK。
  - `system_status` singleton row OK。
- managed GCP Pub/Sub
  - 7 topics OK。
  - 9 subscriptions OK。
- production compose
  - 常駐 8 services Up。
  - `PUBSUB_EMULATOR_HOST` なし。
  - `TRADE_MODE=paper` / `OMS_LIVE_DRY_RUN=true`。
- OMS Live dry-run
  - `live-orders` pull。
  - DRY_RUN skip。
  - ack。
  - `trades_live` 書き込みなし。
- Feeder
  - Cloud Supabase `watchlist` poll: `rows=1`。
  - kabu `/unregister/all`: `200 OK`。
  - kabu `/register`: `200 OK`。
  - Caddy 経由 WebSocket connected。
  - 時間外のため 5 秒待機で tick なし。
- market-data pipeline
  - `raw-market-data` publish。
  - `feature-engine` pull / `processed-features` publish / ack。
- strategy / gateway / OMS Paper
  - Strategy A/B smoke signals。
  - Aggregator logs / `trade-signals` publish。
  - Gateway paper order approve。
  - OMS Paper fill。
  - `trades_paper` / `positions` 更新。
- Dashboard
  - Cloud Supabase 初期表示 OK。
  - 7203 paper position / trade / signal 表示 OK。
  - service-role key 実値の build artifact 混入なし。
  - anon key Realtime `system_status` UPDATE event 受信 OK。
  - `system_status` kill switch / trade mode 更新を Cloud Supabase へ反映し、`true/paper` へ復元済み。

## 5. Deferred Checks

- 14:50 day closeout が paper position を閉じること。事前コード確認では scheduler 有効、JST 14:50 発火、`trading_style=day`、paper position 7203 day qty=300 を確認済み。注意: scheduler は曜日判定なしのため、2026-05-17（日）14:50 JST にも発火しうる。
- 市場時間中に Feeder の自然 tick が `raw-market-data` へ流れること。
- Vercel project / env / preview / production build。
- live readiness gate 前に Pro plan / PITR / RLS 詳細設計を完了すること。

## 6. Commands Used For Recheck

```bash
op run --env-file infra/env.production -- \
  docker compose -f infra/docker-compose.prod.yml ps
```

```bash
op run --env-file infra/env.production -- \
  uv run scripts/health-check.py --check supabase services --timeout 30
```

```bash
GOOGLE_APPLICATION_CREDENTIALS=infra/secrets/gcp-pubsub-sa.json \
  uv run scripts/gcp-pubsub-admin.py --project-id "$PUBSUB_PROJECT_ID"
```

```bash
curl http://127.0.0.1:3001/system
```
