# ADR-0001 Production Prerequisites

作成日: 2026-05-16

ADR-0001 の paper production trial に進む前に固定する前提。
ここでは実値や secret は書かず、命名・登録先・未実装 blocker だけを記録する。

## 1. Resource Naming

| 種別 | 推奨名 | 備考 |
|---|---|---|
| GCP project id | `trade-ai-prod` | `PUBSUB_PROJECT_ID` に設定する実 project。既に使用済みなら suffix を付ける。 |
| Pub/Sub service account | `trade-ai-pubsub-runtime` | LAN host の production services が publish / pull / ack に使う。 |
| Supabase project | `trade-ai-prod` | リージョンは ADR の候補どおり Singapore または Seoul。 |
| Vercel project | `trade-ai-dashboard` | `dashboard/` を root directory にする。 |
| 1Password vault | `Trade AI` | `op://Trade AI/...` 参照の vault 名。 |

## 2. 1Password Items And Fields

`infra/env.production.tpl` の参照に合わせ、以下の item / field を作る。
field 名は env var 名と完全一致させる。

| 1Password item | field |
|---|---|
| `production` | `PUBSUB_PROJECT_ID` |
| `production` | `SUPABASE_URL` |
| `production` | `SUPABASE_SECRET_KEY` |
| `production` | `SUPABASE_ANON_KEY` |
| `production` | `GOOGLE_APPLICATION_CREDENTIALS_JSON` |
| `jquants` | `JQUANTS_REFRESH_TOKEN` |
| `kabu` | `KABU_API_PASSWORD` |
| `kabu` | `KABU_ORDER_PASSWORD` |
| `ai` | `GEMINI_API_KEY` |

`KABU_API_PASSWORD` と `KABU_ORDER_PASSWORD` は別 field とし、同値にしない。
Dashboard / Vercel は server-side に `SUPABASE_SECRET_KEY`、client-side に `NEXT_PUBLIC_SUPABASE_URL` と `NEXT_PUBLIC_SUPABASE_ANON_KEY` を設定する。

## 3. GCP Pub/Sub

本番では `PUBSUB_EMULATOR_HOST` を設定しない。
topic / subscription は `infra/pubsub/topics.json` と `infra/pubsub/subscriptions.json` を正とする。

Runtime service account の権限は最小限として以下から始める。

- `roles/pubsub.publisher`
- `roles/pubsub.subscriber`
- `roles/pubsub.viewer`

鍵の扱いは、初回 production trial では 1Password の `production/GOOGLE_APPLICATION_CREDENTIALS_JSON` に service account key JSON を登録し、起動時に一時ファイルとして materialize する方針にする。
将来 LAN host 側で Workload Identity Federation または ADC 相当へ寄せる場合は、この runbook と compose env を更新する。

## 4. Pub/Sub Client Auth

2026-05-16 に各 service の Pub/Sub client は `google-cloud-pubsub` ベースの共通 wrapper へ寄せた。
実運用では公式 client が ADC / service account credentials を使い、unit test で `httpx` transport が注入された場合のみ旧 REST 互換 path を使う。

必要な対応:

- `PUBSUB_EMULATOR_HOST` がある場合は従来どおり no-auth emulator へ接続する。
- `PUBSUB_EMULATOR_HOST` がない場合は ADC / `GOOGLE_APPLICATION_CREDENTIALS` を公式 client に処理させる。
- feeder / feature-engine / strategy-rule / strategy-ai / aggregator / gateway / oms-paper / oms-live の Pub/Sub client は `trade_contracts.pubsub_client` を re-export する。
- production compose / env template は `GOOGLE_APPLICATION_CREDENTIALS=/run/secrets/gcp-pubsub-sa.json` を前提にする。

## 5. Supabase Cloud

Supabase Cloud は paper production trial 前に以下を完了する。
Paper trial は Free plan でも進めてよい。Pro plan / PITR は live readiness gate 前に有効化する。

- project 作成。
- `contracts/sql/*.sql` の適用手順を確定。
- `system_status` 初期行の seed。
- `master_stocks` / `daily_ohlcv` / `watchlist` の初期投入方針の確定。
- `SUPABASE_URL` / `SUPABASE_SECRET_KEY` / `SUPABASE_ANON_KEY` を 1Password に登録。

RLS 本番化は live readiness gate 前の後続項目とし、paper production trial では service role key を server-side service / Vercel server action のみに注入する。browser client には anon key だけを渡し、service role key を client-side bundle に出さない。

## 6. Trial Gate

paper production trial に進む条件:

- GCP Pub/Sub の topic / subscription が作成済み。
- Pub/Sub client が公式 `google-cloud-pubsub` wrapper 経由で ADC / `GOOGLE_APPLICATION_CREDENTIALS` を使う状態になっている。
- `op run --env-file infra/env.production -- docker compose -f infra/docker-compose.prod.yml config` が通る。
- `TRADE_MODE=paper` と `OMS_LIVE_DRY_RUN=true` が `infra/env.production` に入っている。
- Supabase Cloud に schema と初期 `system_status` が入っている。
- secret 実値が repo / logs / host の永続ファイルに残らない運用になっている。
