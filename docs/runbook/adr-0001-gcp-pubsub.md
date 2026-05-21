# ADR-0001 GCP Pub/Sub Runbook

作成日: 2026-05-16

ADR-0001 の managed GCP Pub/Sub を作成・検証する手順。
topic / subscription の正は `infra/pubsub/topics.json` と `infra/pubsub/subscriptions.json`。

方針:

- ローカル環境を汚しやすいので、`gcloud` は必須にしない。
- project / API / service account / key / IAM は Google Cloud Console で一回だけ作る。
- topic / subscription の作成と smoke test は repo の `scripts/gcp-pubsub-admin.py` で行う。
- Python 実装は公式 `google-cloud-pubsub` client を使う。

## 1. Preconditions

- 課金が有効な GCP project があること。
- project id は `docs/adr/0001-production-prerequisites.md` の方針どおり `trade-ai-prod` を基本とする。
- production services 用には service account key JSON を 1Password の `production/GOOGLE_APPLICATION_CREDENTIALS_JSON` に登録すること。
- 登録手順は `docs/runbook/adr-0001-1password.md` に従う。
- 作業端末には `gcloud` を入れなくてよい。必要なのは repo の `uv` と、1Password CLI の `op read`。

## 2. Project Setup In Console

Google Cloud Console で以下を行う。

1. project を作成または選択する。
2. project id を控える。基本は `trade-ai-prod`。
3. Billing を有効化する。
4. APIs & Services で `Cloud Pub/Sub API` を有効化する。

project id が異なる場合、この runbook の `trade-ai-prod` を実 project id に置き換える。

## 3. Service Accounts And Roles In Console

権限は bootstrap と runtime で分ける。
`--apply` は topic / subscription を作成するため、runtime service account の最小権限だけでは足りない。

### 3.1 Bootstrap Identity

Google Cloud Console で topic / subscription 作成に使う一時的な identity を決める。
候補は以下のどちらか。

- 管理者ユーザーで一度だけ `scripts/gcp-pubsub-admin.py --apply` を実行する。
- 一時 service account `trade-ai-pubsub-bootstrap` を作り、作成後に key / 権限を削除する。

bootstrap identity には次のいずれかを付ける。

- 推奨: `Pub/Sub Admin` を一時付与し、作成・smoke test 後に外す。
- より細かくする場合: topic / subscription の create / get / delete / publish / consume が可能な custom role を使う。

`--smoke-test --cleanup-smoke` は一時 topic / subscription の作成と削除も行うため、削除権限も必要。

### 3.2 Runtime Service Account

Google Cloud Console で production services 用の service account を作る。

- Name: `trade-ai-pubsub-runtime`
- Service account ID: `trade-ai-pubsub-runtime`
- Description: `Trade AI Pub/Sub runtime`

runtime service account に付ける roles は以下に絞る。

- `Pub/Sub Publisher`
- `Pub/Sub Subscriber`
- `Pub/Sub Viewer`

runtime service account email は以下の形になる。

```text
trade-ai-pubsub-runtime@trade-ai-prod.iam.gserviceaccount.com
```

runtime service account は `--apply` 用ではなく、production services の publish / pull / ack 用。
通常運用では topic / subscription 作成権限を持たせない。

初回 trial では Console から runtime service account key JSON を作成してダウンロードする。

1. IAM & Admin -> Service Accounts
2. `trade-ai-pubsub-runtime` を開く
3. Keys -> Add key -> Create new key
4. JSON を選択して作成
5. ダウンロードされた JSON の中身を 1Password に登録

登録先:

```text
op://Trade AI/production/GOOGLE_APPLICATION_CREDENTIALS_JSON
```

登録後、ダウンロードした JSON ファイルは端末から削除する。
長期的には service account key ではなく ADC / Workload Identity Federation 相当へ寄せる。

## 4. Materialize Credentials

production compose を起動する LAN host では、1Password から key JSON を materialize する。

```bash
mkdir -p /dev/shm/roboinvest
op read "op://Trade AI/production/GOOGLE_APPLICATION_CREDENTIALS_JSON" > /dev/shm/roboinvest/gcp-pubsub-sa.json
chmod 600 /dev/shm/roboinvest/gcp-pubsub-sa.json
```

`infra/env.production` は container 内 path を指す。

```bash
GOOGLE_APPLICATION_CREDENTIALS=/run/secrets/gcp-pubsub-sa.json
```

`infra/docker-compose.prod.yml` は `/dev/shm/roboinvest/gcp-pubsub-sa.json` を read-only で `/run/secrets/gcp-pubsub-sa.json` に mount する。

## 5. Check Current State

まず runtime credentials で check-only の差分を見る。
check-only は get/list 系なので runtime service account の `Pub/Sub Viewer` で足りる。

```bash
GOOGLE_APPLICATION_CREDENTIALS=/dev/shm/roboinvest/gcp-pubsub-sa.json \
  uv run scripts/gcp-pubsub-admin.py --project-id trade-ai-prod
```

missing が出る場合だけ次の apply に進む。

## 6. Create Topics And Subscriptions

`infra/pubsub/topics.json` / `infra/pubsub/subscriptions.json` に基づいて不足分を作る。
この `--apply` は topic / subscription 作成権限が必要なため、bootstrap identity で実行する。
runtime service account key (`/dev/shm/roboinvest/gcp-pubsub-sa.json`) では通常実行しない。

```bash
GOOGLE_APPLICATION_CREDENTIALS=/path/to/bootstrap-pubsub-admin.json \
  uv run scripts/gcp-pubsub-admin.py --project-id trade-ai-prod --apply
```

作成後、もう一度 check-only を実行する。

```bash
GOOGLE_APPLICATION_CREDENTIALS=/dev/shm/roboinvest/gcp-pubsub-sa.json \
  uv run scripts/gcp-pubsub-admin.py --project-id trade-ai-prod
```

全 topic / subscription が `OK` になること。

## 7. Smoke Test

一時 topic / subscription を使って publish / pull / ack を確認する。
production pipeline の 7 topics / 9 subscriptions にはテストメッセージを流さない。

`--cleanup-smoke` は一時 topic / subscription を削除するため、bootstrap identity で実行する。

```bash
GOOGLE_APPLICATION_CREDENTIALS=/path/to/bootstrap-pubsub-admin.json \
  uv run scripts/gcp-pubsub-admin.py \
    --project-id trade-ai-prod \
    --smoke-test \
    --cleanup-smoke
```

runtime service account の疎通だけを確認したい場合は、既存 production topic/subscription に影響しない別の read-only/publish-only smoke 手順を追加してから実施する。

期待する結果:

- `OK smoke-publish ...`
- `OK smoke-pull-ack ...`
- `DEL smoke-sub:adr-0001-smoke-test-sub`
- `DEL smoke-topic:adr-0001-smoke-test`


## 8. Temporary Admin Grant Cleanup

今回の初回 setup では、簡略化のため runtime service account `trade-ai-pubsub-runtime` に一時的に `Pub/Sub Admin` を付与して `--apply` を実行した。
apply / smoke test が終わったら、runtime service account から `Pub/Sub Admin` を外し、通常運用権限だけに戻す。

2026-05-16 note: LAN host には `gcloud` がなく、runtime service account で Cloud Resource Manager `projects.getIamPolicy` を読むと `403 Forbidden` になる。
これは runtime key では project IAM を確認・変更できないためで、Pub/Sub Admin cleanup は owner / IAM admin 権限を持つ Google Cloud Console または管理者 `gcloud` 認証で実施する。

2026-05-16: Google Cloud Console で手動削除済み。削除後、runtime credentials で `scripts/gcp-pubsub-admin.py --project-id roboinvest-445500` を実行し、7 topics / 9 subscriptions がすべて `OK` になることを確認済み。

残す roles:

- `Pub/Sub Publisher`
- `Pub/Sub Subscriber`
- `Pub/Sub Viewer`

外す role:

- `Pub/Sub Admin`

戻した後、runtime credentials で check-only を実行する。

```bash
GOOGLE_APPLICATION_CREDENTIALS=/dev/shm/roboinvest/gcp-pubsub-sa.json \
  uv run scripts/gcp-pubsub-admin.py --project-id trade-ai-prod
```

`--apply` が再度必要になった場合は、作業前だけ `Pub/Sub Admin` を一時付与し、作業後に外す。

## 9. Production Compose Validation

Pub/Sub 側の作成後、production compose が credentials mount を含めて解決できることを確認する。

```bash
op run --env-file infra/env.production -- \
  docker compose -f infra/docker-compose.prod.yml config
```

`PUBSUB_EMULATOR_HOST` が出ないことも確認する。

```bash
op run --env-file infra/env.production -- \
  docker compose -f infra/docker-compose.prod.yml config | rg PUBSUB_EMULATOR_HOST
```

このコマンドは何も出力しないこと。

## 10. Cleanup / Rotation Notes

- `/dev/shm/roboinvest/gcp-pubsub-sa.json` は `.gitignore` 対象だが、LAN host には実値が残る。trial 後に削除するか、権限・保管場所を明確にする。
- service account key を再作成したら、1Password の `GOOGLE_APPLICATION_CREDENTIALS_JSON` を更新し、古い key を GCP 側で disable / delete する。
- production services は `google-cloud-pubsub` 公式 client 経由で ADC / `GOOGLE_APPLICATION_CREDENTIALS` を使う。

## 11. Optional gcloud Reference

`gcloud` を使う場合だけ以下を参照する。通常運用では不要。

```bash
gcloud auth login
gcloud config set project trade-ai-prod
gcloud services enable pubsub.googleapis.com
gcloud pubsub topics create raw-market-data
gcloud pubsub subscriptions create feature-engine-raw-market-data --topic=raw-market-data
```

topic / subscription は `scripts/gcp-pubsub-admin.py --apply` を正とするため、手作業で個別作成した場合も必ず check-only を通す。
