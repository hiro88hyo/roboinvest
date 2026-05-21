# ADR-0001 1Password Runbook

作成日: 2026-05-16

ADR-0001 production deploy 用 secret を 1Password に登録し、`op://...` 参照で compose / scripts から使うための手順。
secret 実値は repo に書かない。`infra/env.production.tpl` は `op://...` 参照または placeholder だけを持つ。

## 1. Naming

Vault:

```text
Trade AI
```

Items and fields:

| item | field | 用途 |
|---|---|---|
| `production` | `PUBSUB_PROJECT_ID` | GCP project id |
| `production` | `SUPABASE_URL` | Supabase Cloud URL |
| `production` | `SUPABASE_SECRET_KEY` | Supabase service role key |
| `production` | `SUPABASE_ANON_KEY` | Dashboard client-side anon key |
| `production` | `GOOGLE_APPLICATION_CREDENTIALS_JSON` | GCP Pub/Sub service account key JSON |
| `jquants` | `JQUANTS_API_KEY` | Universe Scanner v2 API key |
| `jquants` | `JQUANTS_REFRESH_TOKEN` | Universe Scanner legacy v1 only |
| `kabu` | `KABU_API_PASSWORD` | kabu token API password |
| `kabu` | `KABU_ORDER_PASSWORD` | kabu sendorder password |
| `ai` | `GEMINI_API_KEY` | Strategy AI |

field 名は env var 名と完全一致させる。
`JQUANTS_API_VERSION=v2` を使う通常構成では `JQUANTS_API_KEY` が必須で、`JQUANTS_REFRESH_TOKEN` は legacy v1 用の任意項目として扱う。
`KABU_API_PASSWORD` と `KABU_ORDER_PASSWORD` は別 field とし、同値にしない。

## 2. Create Vault And Items

1Password app または web console で vault `Trade AI` を作る。
その中に以下の item を作る。

- `production`
- `jquants`
- `kabu`
- `ai`

item type は `Secure Note` でも `Password` でもよいが、fields を明示できる形式にする。

## 3. Register Scalar Fields

`production` item:

- `PUBSUB_PROJECT_ID`: 例 `trade-ai-prod`
- `SUPABASE_URL`: Supabase Cloud project URL
- `SUPABASE_SECRET_KEY`: Supabase service role key
- `SUPABASE_ANON_KEY`: Supabase anon key

`jquants` item:

- `JQUANTS_API_KEY`
- `JQUANTS_REFRESH_TOKEN`（legacy v1 を使う場合のみ）

`kabu` item:

- `KABU_API_PASSWORD`
- `KABU_ORDER_PASSWORD`

`ai` item:

- `GEMINI_API_KEY`

登録後、`infra/env.production.tpl` の参照と一致していることを確認する。

```bash
op read "op://Trade AI/production/PUBSUB_PROJECT_ID"
op read "op://Trade AI/production/SUPABASE_URL"
op read "op://Trade AI/jquants/JQUANTS_API_KEY"
op read "op://Trade AI/kabu/KABU_API_PASSWORD"
op read "op://Trade AI/kabu/KABU_ORDER_PASSWORD"
op read "op://Trade AI/ai/GEMINI_API_KEY"
```

画面共有やログ保存中は実行しない。値が表示されるため、確認後は shell history の扱いに注意する。

## 4. Register GCP Service Account JSON

Google Cloud Console で `trade-ai-pubsub-runtime` の service account key JSON を作成し、ダウンロードする。
ダウンロードした JSON は 1Password の以下 field に、改行を含む JSON 全体として保存する。

```text
op://Trade AI/production/GOOGLE_APPLICATION_CREDENTIALS_JSON
```

登録後、端末に残ったダウンロードファイルは削除する。
以後、LAN host では必要時に 1Password から materialize する。

```bash
mkdir -p /dev/shm/roboinvest
op read "op://Trade AI/production/GOOGLE_APPLICATION_CREDENTIALS_JSON" > /dev/shm/roboinvest/gcp-pubsub-sa.json
chmod 600 /dev/shm/roboinvest/gcp-pubsub-sa.json
```

JSON として読めることを確認する。

```bash
uv run python -m json.tool /dev/shm/roboinvest/gcp-pubsub-sa.json >/dev/null
```

`client_email` が想定 service account であることだけ確認する。secret 値は表示しない。

```bash
uv run python -c 'import json; print(json.load(open("/dev/shm/roboinvest/gcp-pubsub-sa.json"))["client_email"])'
```

期待値:

```text
trade-ai-pubsub-runtime@trade-ai-prod.iam.gserviceaccount.com
```

project id が異なる場合は実 project id に置き換える。

## 5. Create Production Env File

LAN host で template をコピーする。

```bash
cp infra/env.production.tpl infra/env.production
```

`infra/env.production` には secret 実値を書かず、`op://...` 参照を残す。
placeholder は host 固有値に置き換える。

必ず確認する値:

```bash
PUBSUB_PROJECT_ID=op://Trade AI/production/PUBSUB_PROJECT_ID
GOOGLE_APPLICATION_CREDENTIALS=/run/secrets/gcp-pubsub-sa.json
SUPABASE_URL=op://Trade AI/production/SUPABASE_URL
SUPABASE_SECRET_KEY=op://Trade AI/production/SUPABASE_SECRET_KEY
JQUANTS_API_KEY=op://Trade AI/jquants/JQUANTS_API_KEY
JQUANTS_API_VERSION=v2
# Optional only when running legacy v1 flow:
# JQUANTS_REFRESH_TOKEN=op://Trade AI/jquants/JQUANTS_REFRESH_TOKEN
KABU_API_PASSWORD=op://Trade AI/kabu/KABU_API_PASSWORD
KABU_ORDER_PASSWORD=op://Trade AI/kabu/KABU_ORDER_PASSWORD
TRADE_MODE=paper
OMS_LIVE_DRY_RUN=true
```

`KABU_API_BASE_URL` / `KABU_WS_URL` は LAN の Windows host IP に合わせる。

## 6. Validate op run

secret を compose config に注入できることを確認する。

```bash
op run --env-file infra/env.production -- \
  docker compose -f infra/docker-compose.prod.yml config
```

`PUBSUB_EMULATOR_HOST` が出ないことを確認する。

```bash
op run --env-file infra/env.production -- \
  docker compose -f infra/docker-compose.prod.yml config | rg PUBSUB_EMULATOR_HOST
```

このコマンドは何も出力しないこと。

## 7. Validate Pub/Sub Credentials

GCP Pub/Sub resource 作成・smoke test は以下で行う。

```bash
GOOGLE_APPLICATION_CREDENTIALS=/dev/shm/roboinvest/gcp-pubsub-sa.json \
  uv run scripts/gcp-pubsub-admin.py --project-id trade-ai-prod
```

不足がある場合:

```bash
GOOGLE_APPLICATION_CREDENTIALS=/dev/shm/roboinvest/gcp-pubsub-sa.json \
  uv run scripts/gcp-pubsub-admin.py --project-id trade-ai-prod --apply
```

一時 topic / subscription で smoke test:

```bash
GOOGLE_APPLICATION_CREDENTIALS=/dev/shm/roboinvest/gcp-pubsub-sa.json \
  uv run scripts/gcp-pubsub-admin.py \
    --project-id trade-ai-prod \
    --smoke-test \
    --cleanup-smoke
```

## 8. Service Account Token Rotation

`infra/.op.service-account.env` には 1Password service account token を置く。
token が terminal / log / screen recording に出た疑いがある場合は、次の手順で rotate する。

前提:

- 古い token を削除する前に、新 token で `op run` が通ることを確認する。
- `infra/.op.service-account.env` は `.gitignore` 対象で、commit しない。
- token 値を terminal に表示しない。`printenv OP_SERVICE_ACCOUNT_TOKEN` は使わない。

### 8.1 Create New Token

1Password 管理画面で ADR-0001 用 service account を開く。
新しい token を作成し、必要な vault access が `Trade AI` に限定されていることを確認する。

権限は読み取りだけから始める。
本 runbook の用途では `op run --env-file infra/env.production -- ...` と `op read` が使えればよい。

### 8.2 Replace Local Env File

LAN host の deploy checkout で `infra/.op.service-account.env` を更新する。

```bash
umask 077
tmp="$(mktemp /tmp/op-service-account.XXXXXX)"
printf 'OP_SERVICE_ACCOUNT_TOKEN=' > "$tmp"
# Paste the new token after the equals sign, then save the file.
${EDITOR:-vi} "$tmp"
mv "$tmp" infra/.op.service-account.env
chmod 600 infra/.op.service-account.env
```

形式だけを確認する。値は表示しない。

```bash
test -f infra/.op.service-account.env
test "$(wc -l < infra/.op.service-account.env)" -eq 1
rg -n '^OP_SERVICE_ACCOUNT_TOKEN=.+$' infra/.op.service-account.env >/dev/null
git check-ignore infra/.op.service-account.env
```

### 8.3 Validate New Token

新 token で 1Password 参照を解決できることを確認する。

```bash
set -a
. infra/.op.service-account.env
set +a

op run --env-file infra/env.production -- \
  docker compose -f infra/docker-compose.prod.yml config >/tmp/roboinvest-compose-config.yml

if rg 'PUBSUB_EMULATOR_HOST|op://' /tmp/roboinvest-compose-config.yml; then
  echo "NG: unresolved or forbidden production value"
  exit 1
fi
```

Cloud Supabase / service CLI の軽量 health check も通す。

```bash
op run --env-file infra/env.production -- \
  uv run python scripts/health-check.py --check supabase services --timeout 30
```

### 8.4 Revoke Old Token

新 token の検証が通った後、1Password 管理画面で古い token を revoke / delete する。
revoke 後、runner や手元 shell が古い token を保持していないように、必要なら runner service を再起動する。

```bash
sudo systemctl restart actions.runner.* || true
```

再起動後に `op run` の compose config validation をもう一度実行する。

### 8.5 Log Hygiene

token 値を表示してしまった terminal scrollback / screen recording / shared logs がある場合は削除する。
GitHub Actions log に token が出ていないことも確認する。

```bash
rg -n 'OP_SERVICE_ACCOUNT_TOKEN=' docs .github infra --glob '!infra/.op.service-account.env'
```

この検索は variable name のみを検出してもよいが、実 token 値が repo に存在してはいけない。

## 9. Cleanup

- `infra/env.production` は `.gitignore` 対象。commit しない。
- `/dev/shm/roboinvest/gcp-pubsub-sa.json` は `.gitignore` 対象。必要時だけ置く。
- 作業端末にダウンロードした service account key JSON は削除する。
- shell history、terminal scrollback、screen recording に secret が残っていないか注意する。
- key を rotate したら、1Password field を更新し、古い key は Google Cloud Console で disable / delete する。
