# Cloud Logging Runbook

作成日: 2026-05-31

LAN host の production compose で、Python サービスの stdout/stderr ログを
OpenTelemetry Collector 経由で Cloud Logging へ送るための手順。

## 1. 前提

- production compose は `infra/docker-compose.prod.yml` を使う。
- アプリはデフォルトで 1 行 JSON ログを stdout/stderr に出す。
- Cloud Logging への認証情報は Collector コンテナだけに mount する。
- アプリコンテナには Cloud Logging 送信用の追加 secret を配らない。
- GCP サービスアカウントに `roles/logging.logWriter` があること。

## 2. Env

`infra/env.production` に以下を設定する。

```bash
APP_ENV=production
JSON_LOGS=true
MARKET_DATA_STALE_WARN_SECONDS=180
OTEL_COLLECTOR_IMAGE=otel/opentelemetry-collector-contrib:0.153.0
GOOGLE_APPLICATION_CREDENTIALS=/run/secrets/gcp-pubsub-sa.json
GOOGLE_APPLICATION_CREDENTIALS_HOST_PATH=/dev/shm/roboinvest/gcp-pubsub-sa.json
```

ローカル調査などでプレーンテキストへ戻す場合だけ `JSON_LOGS=false` を使う。
Collector は `service` と `event` を持つアプリ JSON だけを Cloud Logging へ送る。
parser前に `timestamp` / `service` / `event` を持たない行を除外する。JSON parseできない行や、
health responseなど別基盤由来のDocker JSON logsはCloud Loggingへ送らない。

## 3. Config Validation

Cloud Logging に送る前に、fixture で Docker envelope とアプリ JSON の parse を確認する。

```bash
bash scripts/verify-otel-collector-parse.sh
```

通常サービスのみ:

```bash
op run --env-file infra/env.production -- \
  docker compose --env-file infra/env.production -f infra/docker-compose.prod.yml config
```

Collector を含める:

```bash
op run --env-file infra/env.production -- \
  docker compose --env-file infra/env.production -f infra/docker-compose.prod.yml --profile observability config
```

## 4. Start Collector

既存サービスを動かしたまま Collector だけ追加起動する。

```bash
op run --env-file infra/env.production -- \
  docker compose --env-file infra/env.production -f infra/docker-compose.prod.yml --profile observability up -d otel-collector
```

Collector 状態確認:

```bash
op run --env-file infra/env.production -- \
  docker compose --env-file infra/env.production -f infra/docker-compose.prod.yml --profile observability ps otel-collector

curl -fsS http://127.0.0.1:13133/
```

Collector ログ確認:

```bash
op run --env-file infra/env.production -- \
  docker compose --env-file infra/env.production -f infra/docker-compose.prod.yml --profile observability logs --tail=100 otel-collector
```

## 5. Cloud Logging Checks

Collector 自体の到達確認では、一時コンテナから probe JSON を 1 行だけ出す。
既存の常駐サービスは再起動しない。

```bash
op run --env-file infra/env.production -- \
  docker compose --env-file infra/env.production -f infra/docker-compose.prod.yml --profile observability \
  run --rm --no-deps --entrypoint python gateway \
  -c 'import json, time; print(json.dumps({"timestamp":"2026-05-31T08:38:00.000000+00:00","severity":"NOTICE","service":"collector-probe","environment":"production","event":"collector_cloud_probe","message":"probe log emitted from docker container for collector ingestion"}), flush=True); time.sleep(8)'
```

Cloud Logging で以下を確認する。

Collector 到達確認:

```text
logName:"roboinvest"
jsonPayload.event="collector_cloud_probe"
```

または:

```text
jsonPayload.service="collector-probe"
```

Gateway 構造化イベント確認:

```text
logName:"roboinvest"
jsonPayload.service="gateway"
jsonPayload.event="signal_rejected"
```

ERROR severity の確認:

```text
logName:"roboinvest"
severity>=ERROR
```

注文 publish の確認:

```text
logName:"roboinvest"
jsonPayload.event="order_published"
jsonPayload.destination_topic=~"orders"
```

OMS Live 約定:

```text
logName:"roboinvest"
jsonPayload.service="oms-live"
jsonPayload.event="order_filled"
```

OMS Paper day stop:

```text
logName:"roboinvest"
jsonPayload.service="oms-paper"
jsonPayload.event="day_stop_exit"
```

OMS Paper day trailing stop:

```text
logName:"roboinvest"
jsonPayload.service="oms-paper"
jsonPayload.event="day_stop_trail"
```

OMS Live stop monitor should normally be absent unless explicitly enabled:

```text
logName:"roboinvest"
jsonPayload.service="oms-live"
(jsonPayload.event="live_stop_exit" OR jsonPayload.event="live_stop_trail")
```

kabu API 発注 reject:

```text
logName:"roboinvest"
jsonPayload.event="broker_order_rejected"
```

可能額不足など broker message で絞る場合:

```text
logName:"roboinvest"
jsonPayload.event="broker_order_rejected"
jsonPayload.broker_message:"可能額"
```

Closeout 完了:

```text
logName:"roboinvest"
jsonPayload.service="oms-live"
jsonPayload.event="closeout_completed"
```

Market data 集計:

```text
logName:"roboinvest"
jsonPayload.event="market_data_summary"
```

Market data stale 警告:

```text
logName:"roboinvest"
jsonPayload.event="market_data_stale"
severity>=WARNING
```

Market data 復旧:

```text
logName:"roboinvest"
jsonPayload.event="market_data_recovered"
```

Closeout invariant:

```text
logName:"roboinvest"
jsonPayload.service="oms-live"
jsonPayload.event="closeout_invariant"
```

Gateway reject 集計:

```text
logName:"roboinvest"
jsonPayload.service="gateway"
jsonPayload.event="signal_reject_summary"
```

## 6. Saved Queries

Cloud Logging Console には以下の名前で保存しておく。

| Name | Query |
| --- | --- |
| `roboinvest-errors` | `logName:"roboinvest"`<br>`severity>=ERROR` |
| `roboinvest-gateway-rejections` | `logName:"roboinvest"`<br>`jsonPayload.service="gateway"`<br>`jsonPayload.event="signal_rejected"` |
| `roboinvest-gateway-reject-summary` | `logName:"roboinvest"`<br>`jsonPayload.service="gateway"`<br>`jsonPayload.event="signal_reject_summary"` |
| `roboinvest-order-published` | `logName:"roboinvest"`<br>`jsonPayload.event="order_published"` |
| `roboinvest-order-filled` | `logName:"roboinvest"`<br>`jsonPayload.service="oms-live"`<br>`jsonPayload.event="order_filled"` |
| `roboinvest-paper-day-stop-exit` | `logName:"roboinvest"`<br>`jsonPayload.service="oms-paper"`<br>`jsonPayload.event="day_stop_exit"` |
| `roboinvest-paper-day-stop-trail` | `logName:"roboinvest"`<br>`jsonPayload.service="oms-paper"`<br>`jsonPayload.event="day_stop_trail"` |
| `roboinvest-live-stop-monitor` | `logName:"roboinvest"`<br>`jsonPayload.service="oms-live"`<br>`(jsonPayload.event="live_stop_exit" OR jsonPayload.event="live_stop_trail")` |
| `roboinvest-broker-rejected` | `logName:"roboinvest"`<br>`jsonPayload.event="broker_order_rejected"` |
| `roboinvest-closeout-completed` | `logName:"roboinvest"`<br>`jsonPayload.service="oms-live"`<br>`jsonPayload.event="closeout_completed"` |
| `roboinvest-closeout-invariant` | `logName:"roboinvest"`<br>`jsonPayload.service="oms-live"`<br>`jsonPayload.event="closeout_invariant"` |
| `roboinvest-market-data-summary` | `logName:"roboinvest"`<br>`jsonPayload.event="market_data_summary"` |
| `roboinvest-market-data-stale` | `logName:"roboinvest"`<br>`jsonPayload.event="market_data_stale"`<br>`severity>=WARNING` |
| `roboinvest-market-data-recovered` | `logName:"roboinvest"`<br>`jsonPayload.event="market_data_recovered"` |
| `roboinvest-oms-live-warnings` | `logName:"roboinvest"`<br>`jsonPayload.service="oms-live"`<br>`severity>=WARNING` |
| `roboinvest-opening-buy-guard` | `logName:"roboinvest"`<br>`jsonPayload.service="gateway"`<br>`jsonPayload.event="signal_rejected"`<br>`jsonPayload.reason="opening_live_buy"` |

## 7. Stop Collector

```bash
op run --env-file infra/env.production -- \
  docker compose --env-file infra/env.production -f infra/docker-compose.prod.yml --profile observability stop otel-collector
```

`otel-collector-state` volume には Docker log の読み取り offset が残る。
重複送信を避けるため、検証中に理由なく volume を削除しない。

## 8. Rollback

Cloud Logging への転送だけ止める場合:

```bash
op run --env-file infra/env.production -- \
  docker compose --env-file infra/env.production -f infra/docker-compose.prod.yml --profile observability stop otel-collector
```

Python サービスのログ形式を旧テキスト形式へ戻す場合:

1. `infra/env.production` で `JSON_LOGS=false` にする。
2. 影響範囲を小さくするため、サービスを段階的に recreate する。

```bash
op run --env-file infra/env.production -- \
  docker compose --env-file infra/env.production -f infra/docker-compose.prod.yml up -d --no-deps \
  feature-engine strategy-rule strategy-ai aggregator

op run --env-file infra/env.production -- \
  docker compose --env-file infra/env.production -f infra/docker-compose.prod.yml up -d --no-deps feeder

op run --env-file infra/env.production -- \
  docker compose --env-file infra/env.production -f infra/docker-compose.prod.yml up -d --no-deps \
  gateway oms-paper oms-live
```

戻した後は `scripts/production-preopen-check.py --timeout 30` を実行する。

## 9. 注意点

- `/var/lib/docker/containers` を read-only mount するため、host の Docker log directory が標準位置であることを確認する。
- Collector は Docker log file 読み取りと offset volume 書き込みのため root user で起動する。
- Collector 起動直後は `start_at: end` により、既存の古いログは送らず新規ログから読む。
- Collector はコンテナ内 `HOSTNAME` を使って自分自身の Docker log file を除外する。
- Collector は `jsonPayload.service` と `jsonPayload.event` を持たないログを drop する。
- Console では `logName:"roboinvest"` を付けて見る。Vercel / Supabase / GCP managed logs は別 logName に出る。
- Google Cloud API 障害時は Collector 側で retry / batch するが、長時間障害時は Collector ログを確認する。
- Cloud Logging の保持期間、除外フィルタ、ログ量コストは別途運用判断する。
