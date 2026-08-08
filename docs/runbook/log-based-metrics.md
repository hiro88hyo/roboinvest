# Log-based Metrics Runbook

作成日: 2026-07-11

Cloud Logging に集約済みの構造化ログから、運用介入候補を数える counter metric と
market-data summary の具体値を抽出する distribution metric を再現可能に作成する手順。定義の正本は
`infra/monitoring/log-based-metrics.json` とする。

## 対象

- `severity>=ERROR`
- OMS Live の `closeout_invariant`
- `market_data_stale`
- OMS Live の `broker_order_rejected`
- Feature Engine の `received` / `ticks_processed` / `books_processed`
- OMS Paper の `books_pulled` / `books_applied`
- Feature Engine / OMS Paper の最新データ age と処理エラー数

distribution metric は `market_data_summary` が出す約1分間の summary window ごとの値を
保持する。件数を1秒当たりのrateへ変換せず、Dashboardでは `ALIGN_MEAN` で最新window値と
時系列を表示する。ageは秒単位。metric作成前のログは遡及して指標化されない。

Alert policy の初期定義は `infra/monitoring/alert-policies.json` に置く。Google推奨に
合わせて10分窓で集計するが、実ログで誤通知率を確認するまでは全policyを
`enabled=false`、notification channelなしで同期する。

## Dry-run

設定を検証し、実行予定のコマンドを表示する。Cloud 側は変更しない。

```bash
uv run python scripts/sync-log-based-metrics.py --project PROJECT_ID
```

## Apply

runtime用Pub/Sub/Logging writer SAは使用しない。observability構成管理用identityには、
少なくとも`roles/logging.configWriter`、`roles/monitoring.alertPolicyEditor`、
`roles/monitoring.dashboardEditor`相当の権限を
付与する。現在の gcloud account/projectを確認し、権限preflightを通してから
明示的に`--apply`を付ける。
既存 metric は update、未作成 metric は create する。

```bash
gcloud auth list
gcloud config get-value project
uv run python scripts/check-observability-iam.py --project PROJECT_ID
uv run python scripts/sync-log-based-metrics.py --project PROJECT_ID --apply
```

preflightが不足権限を表示した場合、そのidentityでは同期しない。特にアプリruntime SAへ
構成管理権限を追加して回避しない。

## Operations dashboard

Dashboardの正本は`infra/monitoring/operations-dashboard.json`。dry-run後に同期する。

```bash
uv run python scripts/sync-monitoring-dashboard.py --project PROJECT_ID
uv run python scripts/sync-monitoring-dashboard.py --project PROJECT_ID --apply
```

Dashboardは異常件数、注文フロー、market-data summary、具体的なmarket-data流量・遅延、
処理エラー、Pub/Sub backlog、ERRORログを同じ画面に表示する。アプリ由来metricは作成後の
ログだけを集計し、過去ログを遡及しない。

作成後は Cloud Monitoring の Metrics Explorer で
`logging.googleapis.com/user/<metric name>` を選び、実ログから増分が入ることを確認する。

## Alert policy

Dry-runでは無効状態のpolicy JSONを表示する。

```bash
uv run python scripts/sync-alert-policies.py --project PROJECT_ID
```

metricを作成した後、無効状態のpolicyを同期する。

```bash
uv run python scripts/sync-alert-policies.py --project PROJECT_ID --apply
```

同期後もpolicyは無効で通知先を持たない。Metrics Explorerで最低1営業日の発生頻度と
monitored resourceが`global`であることを確認してから、閾値・通知先・有効化を別変更で行う。

## Rollback

自動削除は行わない。対象 metric と利用中の alert policy がないことを確認してから、
必要な metric だけを手動削除する。

```bash
gcloud logging metrics delete METRIC_NAME --project PROJECT_ID
```
