# ADR-0001 Production Compose Runbook

作成日: 2026-05-16

ADR-0001 の production compose を LAN host で起動・検証するための手順。
本 runbook の範囲は compose / env / validation / paper 起動までとし、live 発注への切り替えは含めない。

## 1. Preconditions

- LAN host に Docker / Docker Compose v2 / 1Password CLI が入っていること。
- `op signin` が完了し、`op run --env-file infra/env.production -- ...` と `op read` が使えること。
- `infra/env.production` は `infra/env.production.tpl` をコピーして作成し、secret 実値ではなく `op://...` 参照を置くこと。
- `infra/env.production` では初回起動時に必ず `TRADE_MODE=paper` と `OMS_LIVE_DRY_RUN=true` を設定すること。
- production compose では managed GCP Pub/Sub を使うため、`PUBSUB_EMULATOR_HOST` は設定しないこと。
- feeder の register は `FEEDER_KABU_DEFAULT_EXCHANGE=1`、kabu 本番注文の exchange は SOR 前提の `KABU_DEFAULT_EXCHANGE=9` とすること。

## 2. Env File

```bash
cp infra/env.production.tpl infra/env.production
```

`infra/env.production` で最低限確認する値:

```bash
TRADE_MODE=paper
OMS_LIVE_DRY_RUN=true
FEEDER_KABU_DEFAULT_EXCHANGE=1
KABU_DEFAULT_EXCHANGE=9
OMS_LIVE_MAX_QTY_PER_ORDER=100
OMS_LIVE_ALLOWED_SYMBOLS=7203
```

`KABU_API_PASSWORD` と `KABU_ORDER_PASSWORD` は別 field として 1Password に登録する。
`infra/env.production` と `infra/secrets/` は `.gitignore` 対象なので commit しない。

## 3. GCP Credentials

初回 production trial では、1Password の `production/GOOGLE_APPLICATION_CREDENTIALS_JSON` を compose 用の read-only secret file に materialize する。

```bash
mkdir -p infra/secrets
op read "op://Trade AI/production/GOOGLE_APPLICATION_CREDENTIALS_JSON" > infra/secrets/gcp-pubsub-sa.json
chmod 600 infra/secrets/gcp-pubsub-sa.json
```

`infra/env.production` では container 内 path を指定する。

```bash
GOOGLE_APPLICATION_CREDENTIALS=/run/secrets/gcp-pubsub-sa.json
```

`infra/secrets/gcp-pubsub-sa.json` は `.gitignore` 対象で、起動後の残存確認対象にする。

## 4. Config Validation

まず通常サービスだけで compose config を検証する。

```bash
op run --env-file infra/env.production -- \
  docker compose -f infra/docker-compose.prod.yml config
```

Universe Scanner を含む batch profile も検証する。

```bash
op run --env-file infra/env.production -- \
  docker compose -f infra/docker-compose.prod.yml --profile batch config
```

production compose に `PUBSUB_EMULATOR_HOST` が混入していないことを確認する。

```bash
op run --env-file infra/env.production -- \
  docker compose -f infra/docker-compose.prod.yml config | rg PUBSUB_EMULATOR_HOST
```

このコマンドは何も出力しないこと。

## 5. Build

9 services の image を build する。

```bash
op run --env-file infra/env.production -- \
  docker compose -f infra/docker-compose.prod.yml --profile batch build
```

初回 deploy 前や Dockerfile / dependency 変更後は必ず build を通す。

## 6. Paper Startup

初回 production 起動は paper mode / live dry-run のまま行う。

```bash
op run --env-file infra/env.production -- \
  docker compose -f infra/docker-compose.prod.yml up -d --build
```

起動後に状態を確認する。

```bash
op run --env-file infra/env.production -- \
  docker compose -f infra/docker-compose.prod.yml ps

op run --env-file infra/env.production -- \
  docker compose -f infra/docker-compose.prod.yml logs --tail=100 gateway oms-paper oms-live
```

この段階では `gateway` が `TRADE_MODE=paper` で動き、live order 経路は `OMS_LIVE_DRY_RUN=true` のままにする。

## 7. Universe Scanner Batch

Universe Scanner は常駐させず、batch profile で明示実行する。

```bash
op run --env-file infra/env.production -- \
  docker compose -f infra/docker-compose.prod.yml --profile batch run --rm universe-scanner
```

必要に応じて事前に build だけ行う。

```bash
op run --env-file infra/env.production -- \
  docker compose -f infra/docker-compose.prod.yml --profile batch build universe-scanner
```

## 8. Stop / Restart

通常停止:

```bash
op run --env-file infra/env.production -- \
  docker compose -f infra/docker-compose.prod.yml down
```

設定変更後の再起動:

```bash
op run --env-file infra/env.production -- \
  docker compose -f infra/docker-compose.prod.yml up -d --build
```

永続 volume は `kabu-token-cache` / `feature-warm-data` / `feature-cold-data` / `trade-ai-logs` を使う。
volume 削除は本 runbook の範囲外とし、必要な場合はバックアップと影響確認を先に行う。

## 9. Safety Gates

- `TRADE_MODE=live` へ変更しない。
- `OMS_LIVE_DRY_RUN=false` へ変更しない。
- `OMS_LIVE_ALLOWED_SYMBOLS` は検証銘柄だけに絞る。
- `OMS_LIVE_MAX_QTY_PER_ORDER` は最小単元から始める。
- live readiness gate は `docs/adr/0001-implementation-checklist.md` の 9 章を満たしてから実施する。
- 問題があれば Dashboard / Supabase の kill switch を止める前提で確認する。

## 10. Post-Run Checks

- `infra/env.production` に secret 実値が残っていないこと。2026-05-16: raw secret らしき値なしを確認済み。
- host 上の `infra/secrets/` に一時 materialize した secret が残っていないこと。2026-05-16: `infra/secrets/gcp-pubsub-sa.json` は稼働中 compose が read-only mount 中のため、stack 停止時に削除する。
- `docker compose ... logs` に secret 値が出ていないこと。2026-05-16: Supabase/Gemini/kabu 主要 secret 実値の tail logs 混入なしを確認済み。
- `PUBSUB_PROJECT_ID` が本番 GCP project を指していること。
- `SUPABASE_URL` が Supabase Cloud project を指していること。
