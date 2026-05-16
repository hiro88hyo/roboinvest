# ADR-0001 GitHub Actions Deploy Runbook

作成日: 2026-05-16

ADR-0001 の LAN host production compose を GitHub Actions self-hosted runner から更新する手順。
本 runbook は deploy automation の土台作りが範囲で、live 切替や `OMS_LIVE_DRY_RUN=false` は扱わない。

## 1. Policy

- Repository は private のまま運用する。
- production deploy workflow は `workflow_dispatch` のみで起動する。
- GitHub Environment `production` を作り、手動承認を必須にする。
- self-hosted runner は repo-scoped とし、organization / public runner として登録しない。
- runner label は `roboinvest-prod` を付ける。
- deploy は persistent checkout `/home/hiroyuki/workspaces/roboinvest` を `git pull --ff-only` して行う。
- 初期状態では `dry_run=true` を選び、compose validation と image build だけを行う。

## 2. LAN Host Preconditions

LAN host に以下があること。

```bash
docker --version
docker compose version
op --version
uv --version
git --version
```

deploy checkout:

```bash
cd /home/hiroyuki/workspaces/roboinvest
git remote -v
git status --short --branch
```

secret materialize:

```bash
test -f infra/env.production
test -f infra/.op.service-account.env
test -f infra/secrets/gcp-pubsub-sa.json
```

`infra/env.production` は `TRADE_MODE=paper` / `OMS_LIVE_DRY_RUN=true` を維持する。

## 3. Runner Install

GitHub repository の Settings > Actions > Runners > New self-hosted runner から Linux 用手順を取得する。

推奨:

- runner user は deploy 専用ユーザーにする。
- runner を `docker` group に入れる場合は、そのユーザーが host root 相当の権限を持つ前提で扱う。
- runner labels に `roboinvest-prod` を追加する。
- runner は service として起動する。

登録後、GitHub UI で runner が `Idle` になることを確認する。

## 4. GitHub Environment

Settings > Environments で `production` を作る。

設定:

- Required reviewers を有効化する。
- Deployment branches は `main` に限定する。
- Environment secrets は使わない。secret は LAN host の 1Password CLI / `infra/.op.service-account.env` から読む。

## 5. Deploy Workflow

workflow:

```text
.github/workflows/deploy-production.yml
```

実行条件:

- `workflow_dispatch`
- `ref=main`
- `dry_run=true` がデフォルト

deploy 前に workflow が確認すること:

- `ci.yml` の対象 SHA に成功 run があること。
- runner 上に persistent deploy checkout があること。
- `infra/env.production` / `infra/.op.service-account.env` / `infra/secrets/gcp-pubsub-sa.json` があること。
- `docker` / `op` が使えること。
- `docker compose config` に `PUBSUB_EMULATOR_HOST` / raw `op://` が残らないこと。

## 6. Dry Run

最初は必ず `dry_run=true` で実行する。

Actions > Deploy Production > Run workflow:

- `ref`: `main`
- `dry_run`: `true`

期待結果:

- Verify CI for ref: success
- Validate production compose: success
- Build production images: success
- `docker compose up` は実行されない

## 7. Production Restart

dry run が通り、paper mode の再起動を許可できる場合だけ `dry_run=false` で実行する。

実行前確認:

```bash
cd /home/hiroyuki/workspaces/roboinvest
rg -n '^(TRADE_MODE|OMS_LIVE_DRY_RUN|KABU_DEFAULT_EXCHANGE|FEEDER_KABU_DEFAULT_EXCHANGE)=' infra/env.production
```

期待値:

```text
TRADE_MODE=paper
OMS_LIVE_DRY_RUN=true
FEEDER_KABU_DEFAULT_EXCHANGE=1
KABU_DEFAULT_EXCHANGE=9
```

実行後確認:

```bash
set -a
. infra/.op.service-account.env
set +a
op run --env-file infra/env.production -- docker compose -f infra/docker-compose.prod.yml ps
op run --env-file infra/env.production -- uv run python scripts/health-check.py --check supabase services --timeout 30
```

## 8. Rollback

直前の commit に戻す場合:

```bash
cd /home/hiroyuki/workspaces/roboinvest
git log --oneline -5
git switch main
git reset --hard <known-good-commit>
set -a
. infra/.op.service-account.env
set +a
op run --env-file infra/env.production -- docker compose -f infra/docker-compose.prod.yml up -d --build
```

`git reset --hard` は deploy checkout 専用でのみ実行する。未保存の手作業変更がある場合は先に退避する。

安全停止だけを行う場合:

```bash
op run --env-file infra/env.production -- docker compose -f infra/docker-compose.prod.yml down
```

問題発生時は Dashboard / Supabase の kill switch を先に確認し、live readiness gate 前は `TRADE_MODE=paper` / `OMS_LIVE_DRY_RUN=true` を維持する。
