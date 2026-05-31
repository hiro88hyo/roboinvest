# Cloud Monitoring Plan

作成日: 2026-05-31

Google Cloud Monitoring を roboinvest の一次監視基盤として使うための将来実装メモ。
本番運用では、Vercel Dashboard を取引状況の表示画面、Google Cloud Monitoring /
Logging を監視・アラート・障害調査の基盤として棲み分ける。

## 1. 方針

- サービスメトリクス、トレードメトリクス、インフラメトリクスを Google Cloud Monitoring に集約する。
- Cloud Logging は詳細調査用の構造化ログ、Cloud Monitoring は数値化された状態とアラートに使う。
- Vercel Dashboard は Supabase Realtime を使った取引オペレーション画面とし、監視基盤そのものにはしない。
- 将来 Datadog / Grafana Cloud / Better Stack へ送る場合も、まず Google Cloud を一次集約点にする。

## 2. Scope

### Service Metrics

- service heartbeat / up-down
- service loop duration
- processed message count
- Pub/Sub ack / nack count
- Pub/Sub processing latency
- kabu API latency / error count
- unhandled exception count

### Trade Metrics

- realized PnL
- unrealized PnL
- open positions count
- trades count
- submitted / filled / rejected orders count
- Gateway risk reject count
- kill switch state
- closeout result count

### Infra Metrics

- Cloud Run CPU / memory / restart count
- Pub/Sub backlog / oldest unacked message age
- Cloud Scheduler / Cloud Run Jobs success and failure
- OpenTelemetry Collector health
- Supabase connectivity from production checks
- Cloud Logging ingestion errors

## 3. Recommended Architecture

```text
Cloud Run / production compose services
  -> structured logs
  -> Cloud Logging

GCP managed resources
  -> native metrics
  -> Cloud Monitoring

Supabase trades / positions / system_status
  -> metrics-exporter job
  -> Cloud Monitoring custom metrics

Cloud Monitoring
  -> dashboards
  -> alert policies
  -> notification channels

Supabase Realtime
  -> Vercel Dashboard
```

PnL や建玉数はイベントログではなく現在状態に近いため、OMS/Gateway の処理中に直接
push するより、Supabase を正として `metrics-exporter` が定期集計して送る方針にする。

## 4. Metric Naming Draft

カスタムメトリクス名の草案:

```text
custom.googleapis.com/roboinvest/trading/pnl/realized_yen
custom.googleapis.com/roboinvest/trading/pnl/unrealized_yen
custom.googleapis.com/roboinvest/trading/positions/open_count
custom.googleapis.com/roboinvest/trading/orders/submitted_count
custom.googleapis.com/roboinvest/trading/orders/filled_count
custom.googleapis.com/roboinvest/trading/orders/rejected_count
custom.googleapis.com/roboinvest/trading/risk/rejected_count
custom.googleapis.com/roboinvest/trading/kill_switch_active
custom.googleapis.com/roboinvest/service/messages/processed_count
custom.googleapis.com/roboinvest/service/messages/nack_count
custom.googleapis.com/roboinvest/service/loop/duration_ms
custom.googleapis.com/roboinvest/service/kabu_api/error_count
custom.googleapis.com/roboinvest/service/kabu_api/latency_ms
```

低カーディナリティを守る。`order_id`, `unified_signal_id`, raw error, stack trace は
メトリクス label には入れず、Cloud Logging の `jsonPayload` に残す。

推奨 label:

- `service`
- `environment`
- `mode` (`live` / `paper`)
- `result` (`success` / `failure`)
- `reason` (`risk_reject`, `kabu_error`, `validation_error`, `opening_live_buy` など)

## 5. Initial Alerts

最初に作る候補:

- `kill_switch_active == 1`
- `realized_pnl_yen < -daily_loss_limit`
- `orders_rejected_count > 3` in 5 minutes
- `kabu_api/error_count > 0` in 5 minutes
- Pub/Sub oldest unacked message age が閾値超過
- OMS Live の heartbeat 欠落
- Gateway の heartbeat 欠落
- closeout 後に live position が残る
- market open 後、一定時間 watchlist / daily_ohlcv / feeder が不健全

## 6. Vercel Dashboard Boundary

Vercel Dashboard は人間が取引状況を見る画面として使う。

表示するもの:

- today realized / unrealized PnL
- open positions
- trades
- watchlist
- strategy signals
- risk rejects
- OMS live / paper status
- kill switch state

監視の正は Google Cloud Monitoring に置く。Dashboard が落ちてもアラートが止まらない
構成にする。

## 7. External Tools

Datadog などの外部 SaaS を使う場合は、Cloud Logging の Log Router から Pub/Sub topic
へ流し、Datadog / Grafana Cloud / Better Stack へ転送する。取引系 Pub/Sub topic とは
分けて、ログ転送専用 topic を使う。

```text
Cloud Logging
  -> Log Router sink
  -> logging-export Pub/Sub topic
  -> external observability service
```

最初から外部 SaaS を一次監視基盤にせず、Google Cloud Monitoring / Logging を一次集約点
として設計する。
