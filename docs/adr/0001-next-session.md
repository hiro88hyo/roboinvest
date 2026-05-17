# ADR-0001 Next Session Note

作成日: 2026-05-15

次回セッションでは ADR-0001 の本番デプロイ実装を開始する。
最初に読む順番は以下。

1. `AGENT.md`
2. `docs/HANDOFF.md`
3. `docs/adr/0001-deployment-architecture.md`
4. `docs/adr/0001-implementation-checklist.md`
5. `docs/service-claude-inventory.md`

## Recommended Starting Point

まず `docs/adr/0001-implementation-checklist.md` の **4. Production Compose** から始める。

最初の実装ゴール:

- `infra/docker-compose.prod.yml` の叩き台を作る
- `infra/env.production.tpl` または同等の production env template を作る
- 本番 compose から Pub/Sub emulator / `pubsub-init` を除外する
- `PUBSUB_EMULATOR_HOST` が production compose に入らないことを確認する
- Feeder と OMS Live の `KABU_TOKEN_CACHE_FILE` を同じ volume/path に揃える
- OMS Live に `OMS_LIVE_DRY_RUN`, `OMS_LIVE_MAX_QTY_PER_ORDER`, `OMS_LIVE_ALLOWED_SYMBOLS`, `KABU_DEFAULT_EXCHANGE=9` を明示する
- `docker compose -f infra/docker-compose.prod.yml config` が通るところまで確認する

## Suggested First Commands

```bash
git status --short --branch
sed -n '1,260p' docs/adr/0001-implementation-checklist.md
sed -n '1,220p' infra/docker-compose.dev.yml
rg --files services -g 'Dockerfile' -g '.env.example' | sort
```

## Important Guardrails

- live 発注はまだ触らない。最初は compose / env / config validation まで。
- OMS Live は本番資金に直結するため、変更は最小限にする。
- 本番 kabu order は `KABU_DEFAULT_EXCHANGE=9` が前提。
- `KABU_API_PASSWORD` と `KABU_ORDER_PASSWORD` は別 env として扱う。
- Feeder と OMS Live は `KABU_TOKEN_CACHE_FILE` を共有する。
- production compose では managed GCP Pub/Sub を使うため、`PUBSUB_EMULATOR_HOST` を設定しない。
- secrets の実値は commit しない。template には `op://...` 参照または placeholder のみ置く。

## Current Pre-ADR Commit

ADR 着手前の準備コミット:

- `1bca679 docs: capture deployment readiness notes`

このコミットには ADR-0001 実装チェックリスト、dev setup 更新、service CLAUDE.md 棚卸し、Feeder CLI 軽量テストが含まれる。

## 2026-05-16 Progress Update

作業ブランチ:

- `adr-0001-production-compose`

今回完了:

- `infra/docker-compose.prod.yml` を追加した。
- `infra/env.production.tpl` を追加した。
- `services/Dockerfile` を追加し、build arg で 9 Python services を build できるようにした。
- `.gitignore` に `infra/env.production` / `infra/secrets/` を追加した。
- production compose から Pub/Sub emulator / `pubsub-init` を除外した。
- production compose に `PUBSUB_EMULATOR_HOST` は入れていない。
- Feeder と OMS Live の `KABU_TOKEN_CACHE_FILE` を `kabu-token-cache` volume 上の `/var/lib/kabu/token_cache.json` に揃えた。
- OMS Live に `OMS_LIVE_DRY_RUN`, `OMS_LIVE_MAX_QTY_PER_ORDER`, `OMS_LIVE_ALLOWED_SYMBOLS`, `KABU_DEFAULT_EXCHANGE=9` を明示した。
- Feature Engine の warm/cold storage volume と logs volume を定義した。
- Universe Scanner は日次 batch なので `profiles: ["batch"]` / `restart: "no"` にした。常駐 8 services は `restart: unless-stopped`。
- `docs/adr/0001-implementation-checklist.md` の完了項目と検証メモを更新した。

検証済み:

```bash
docker compose --env-file infra/env.production.tpl -f infra/docker-compose.prod.yml config
docker compose --env-file infra/env.production.tpl -f infra/docker-compose.prod.yml --profile batch config
docker compose --env-file infra/env.production.tpl -f infra/docker-compose.prod.yml --profile batch build
```

さらに 9 services の container CLI `--help` 起動を確認済み。
最初は `uv run --package ...` ENTRYPOINT が root workspace を再解釈して落ちたため、`services/Dockerfile` は `.venv/bin/python -m "$MODULE_NAME"` で起動する形に修正済み。

次回の最初のタスク:

- 次は ADR-0001 の Supabase Cloud 側を進める。
- まず `docs/adr/0001-implementation-checklist.md` の **3. Supabase Cloud** から着手する。
- 書く/作る候補は Supabase Cloud schema apply runbook、`contracts/sql/*.sql` 適用手順、`system_status` 初期 row seed、`master_stocks` / `daily_ohlcv` / `watchlist` 初期投入方針。
- その後 `scripts/health-check.py` を Cloud Supabase に対して通す手順を作る。
- live 発注はまだ触らない。次回も Supabase / env / runbook / validation まで。

今回の追加完了:

- GCP Pub/Sub / Supabase Cloud / 1Password field naming は `docs/adr/0001-production-prerequisites.md` に整理済み。
- managed Pub/Sub 対応は公式 `google-cloud-pubsub` 共通 wrapper へ移行済み。
- GCP Pub/Sub topic/subscription 作成手順は `docs/runbook/adr-0001-gcp-pubsub.md` と `scripts/gcp-pubsub-admin.py` に追加済み。
- 1Password 登録手順は `docs/runbook/adr-0001-1password.md` に追加済み。
- `infra/.op.service-account.env` を作成し、`.gitignore` 済み。`OP_SERVICE_ACCOUNT_TOKEN` はユーザーが設定済み。
- `infra/secrets/gcp-pubsub-sa.json` は存在し、`.gitignore` 済み。
- GCP Pub/Sub topic 7 件 / subscription 9 件は一時的に runtime SA へ `Pub/Sub Admin` を付与して `--apply` 済み。
- TODO: runtime service account から一時付与した `Pub/Sub Admin` を外す。
- production compose 起動手順は `docs/runbook/adr-0001-production-compose.md` に runbook 化済み。
- Production Compose Validation は `op run --env-file infra/env.production -- docker compose -f infra/docker-compose.prod.yml config` 成功。
- `PUBSUB_EMULATOR_HOST` と raw `op://` 参照が compose config に残らないことを確認済み。
- `infra/docker-compose.prod.yml` から `env_file: ./env.production` を外し、`op run --env-file ...` に一本化した。

次回の推奨コマンド:

```bash
git status --short --branch
sed -n '66,82p' docs/adr/0001-implementation-checklist.md
sed -n '1,220p' contracts/sql/*.sql
sed -n '1,220p' infra/supabase/seed.sql
sed -n '1,220p' scripts/health-check.py
```

## 2026-05-16 Vercel Handoff

Vercel Preview deploy は 2026-05-16 に確認済み。
次セッションでは Realtime indicator のブラウザ目視確認、必要なら `/system` 操作確認、Production URL 確認から再開する。

作業ブランチ:

- `adr-0001-production-compose`

Vercel 側で完了済み:

- Vercel project を作成済み。
- GitHub repository と連携済み。
- Root Directory は `dashboard/`。
- Framework Preset は Next.js。
- Install Command は当初 `npm cli` で失敗。`npm ci` に修正し、`dashboard/vercel.json` にも明示済み。
- Build Command は `npm run build`。
- Env は Production / Preview に設定済み。Vercel 1Password Integration は未使用のため、`op://...` ではなく解決済み実値を入力する。
  - `NEXT_PUBLIC_SUPABASE_URL`
  - `NEXT_PUBLIC_SUPABASE_ANON_KEY`
  - `SUPABASE_SECRET_KEY` (server-side only。`NEXT_PUBLIC_` にしない)
  - `NEXT_PUBLIC_APP_TIMEZONE=Asia/Tokyo`

戻ったら読むファイル:

1. `docs/runbook/adr-0001-dashboard-vercel.md`
2. `docs/adr/0001-implementation-checklist.md` の **6. Dashboard / Vercel**
3. `docs/runbook/adr-0001-paper-production-trial.md`
4. `docs/runbook/adr-0001-gcp-pubsub.md`

戻ったら確認すること:

- Preview URL: `https://roboinvest-git-adr-0001-production-compose-hiro88hyos-projects.vercel.app`
- Project URL `https://project-wh73t.vercel.app/` は一時 `DEPLOYMENT_NOT_FOUND`/500 を確認。branch Preview URL を正とする。
- Preview build は通過済み。
- Preview で次を確認済み。
  - `/` が 200。
  - `/positions?type=paper` が 200、7203 paper position が見える。
  - `/trades?type=paper` が 200、7203 paper trade が見える。
  - `/signals` が 200、7203 signal log が見える。
  - `/system` が 200、`is_trading_allowed=true` / `trade_mode=paper` が見える。
  - Realtime indicator のブラウザ目視確認は次回。
  - `/system` の kill switch / trade mode 操作確認は次回。試したら必ず `true/paper` に戻す。
- `SUPABASE_SECRET_KEY` 実値が client bundle に出ていないことは local build artifact で `SECRET_LEAK:no` 済み。Vercel env 名に `NEXT_PUBLIC_` が付いていないことも確認済み。

現在までの重要な完了事項:

- Supabase Cloud schema / seed / Realtime / anon read policies 適用済み。
- `contracts/sql/011_dashboard_anon_read_policies.sql` 適用済み。anon key で `system_status` UPDATE event 受信 OK。
- production compose 常駐 8 services Up。
- managed Pub/Sub 7 topics / 9 subscriptions OK。
- runtime service account から一時付与した `Pub/Sub Admin` はユーザーが Console で削除済み。削除後、runtime key で Pub/Sub check OK。
- Dashboard local build / lint / test / typecheck OK。
- Vercel Preview env 実値 materialize 後、`NEXT_PUBLIC_SUPABASE_URL` host は `cqexdwufmanuqccerdvo.supabase.co` で確認済み。
- Vercel Preview 主要 route は 200: `/`, `/positions?type=paper`, `/trades?type=paper`, `/signals`, `/system`。
- Preview で 7203 paper position、7203 BUY paper trade、`is_trading_allowed=true` / `trade_mode=paper` 表示 OK。
- 一時 diagnostics endpoint `/api/env-check` は確認後に削除済み (`582bc53`)。
- 注意: 作業中に `infra/.op.service-account.env` の token 値を terminal 出力してしまったため、1Password service account token を rotate する。手順は `docs/runbook/adr-0001-1password.md` の "Service Account Token Rotation" に追加済み。
- service-role key 実値の local build artifact 混入なし (`SECRET_LEAK:no`)。
- Dashboard SSR 初期読み込みは `getServiceClient()` に切り替え済み。Client Components は anon key で Realtime。
- `dashboard/.env.local` はローカル Supabase を指していたため `/tmp/dashboard.env.local.20260516T062446Z` に退避済み。
- `infra/secrets/gcp-pubsub-sa.json` は稼働中 compose が read-only mount 中のため残存。stack 停止時に削除する。

Paper trial の残り:

- 14:50 day closeout の実閉鎖確認。事前コード確認は済み。
- 注意: OMS Paper scheduler は曜日判定なし。`trading_style=day` なら 14:50 JST に発火する。
- 現在の paper position は 7203 LONG qty=300 entry/current=2510。

次セッションで使うコマンド候補:

```bash
git status --short --branch
sed -n '122,131p' docs/adr/0001-implementation-checklist.md
sed -n '1,220p' docs/runbook/adr-0001-dashboard-vercel.md
curl -I <vercel-preview-or-production-url>
curl -sS <vercel-url>/system | rg 'true|paper|system_status'
```


## 2026-05-16 PR Readiness Handoff

セッション切替前の状態:

- ブランチ: `adr-0001-production-compose`
- 作業ツリー: clean
- 直近の休日作業コミット:
  - `8d24d01 ci: add production deploy workflow`
  - `244a3b2 docs: add 1password token rotation runbook`
  - `77c8093 docs: document production runner security`
  - `4938a15 docs: draft adr 0001 pr description`
- PR body 草案: `docs/adr/0001-pr-description.md`

次にやること:

1. PR 直前の最終ローカル確認を実行する。
2. `docs/adr/0001-pr-description.md` を最新コミットに合わせて微修正する。
3. 問題なければ PR 作成へ進む。push / `gh pr create` はユーザー確認後に実行する。

推奨コマンド:

```bash
git status --short --branch
git log --oneline --decorate -8
git diff main...HEAD --check
uv run ruff check contracts/python/trade_contracts/pubsub_client.py scripts/gcp-pubsub-admin.py services/*/src/*/clients/pubsub.py
uv run mypy contracts/python/trade_contracts/pubsub_client.py
uv run pytest services/feeder/tests/unit/test_pubsub.py services/feature-engine/tests/unit/test_pubsub_client.py services/strategy-rule/tests/unit/test_pubsub_client.py services/strategy-ai/tests/unit/test_ai_pubsub_client.py services/aggregator/tests/unit/test_pubsub_client.py services/gateway/tests/unit/test_pubsub_client.py services/oms-paper/tests/unit/test_paper_pubsub_client.py services/oms-live/tests/unit/test_live_pubsub_client.py
```

production env を使える場合の追加確認:

```bash
set -a
. infra/.op.service-account.env
set +a
op run --env-file infra/env.production -- docker compose -f infra/docker-compose.prod.yml config >/tmp/roboinvest-compose-config.yml
rg 'PUBSUB_EMULATOR_HOST|op://' /tmp/roboinvest-compose-config.yml
op run --env-file infra/env.production -- uv run python scripts/health-check.py --check supabase services --timeout 30
GOOGLE_APPLICATION_CREDENTIALS=infra/secrets/gcp-pubsub-sa.json uv run scripts/gcp-pubsub-admin.py --project-id roboinvest-445500
```

期待:

- `rg 'PUBSUB_EMULATOR_HOST|op://' ...` は何も出ない。
- Pub/Sub client unit tests は 79 passed。
- Supabase 9 tables / services 9 CLI は OK。
- GCP Pub/Sub は 7 topics / 9 subscriptions OK。

PR 前に残すべき未完了事項:

- 1Password service account token の実 rotate。
- repo-scoped self-hosted runner の実 install。
- Deploy Production workflow の `dry_run=true` 実行。
- 14:50 JST paper day closeout の実観測。
- live readiness gate / first live cutover。

## 2026-05-17 Live Readiness Handoff

現在の状態:

- PR #43 / #44 は main に merge 済み。
- GitHub Actions self-hosted runner `roboinvest-prod-lan` は repo-scoped / label `roboinvest-prod` / service 起動済み。
- Deploy Production `dry_run=true` は service runner で成功済み (`25977298286`)。
- Deploy Production `dry_run=false` による paper mode restart 成功済み (`25977361191`)。
- `OMS_LIVE_ALLOWED_SYMBOLS` を `9432` に変更し、paper/dry-run のまま production restart 成功済み (`25981863922`)。
- `infra/env.production` の安全ノブ: `TRADE_MODE=paper`, `OMS_LIVE_DRY_RUN=true`, `OMS_LIVE_MAX_QTY_PER_ORDER=100`, `OMS_LIVE_ALLOWED_SYMBOLS=9432`, `KABU_DEFAULT_EXCHANGE=9`。
- service health check は Supabase 9/9, services 9/9 OK。
- workflow log の secret 露出パターン検索はヒットなし。

9432 方針:

- 9432 を live e2e 検証銘柄にする。
- kabu read-only probe は 9432 で `ALL OK`。買付余力 `197489`, 9432 current `151.8`, 100 株は概算 `15180` で余力内。
- kabu 実保有に 9432 LONG 2000 株があるが、今回の e2e 対象外とする。
- Supabase `positions(live)` には import しない。e2e は新規 BUY 100 -> SELL 100 の round-trip だけを対象にする。
- `scripts/reconcile-positions.py` は dry-run で `9432 qty=2000 avg=152.0` を to_import として検出するが、これは既知で意図通り。

次のアクション:

1. 市場オープン後、まず `OMS_LIVE_DRY_RUN=true` のまま 9432 / 100 株で live-orders 経路 smoke を確認する。
2. 実 live e2e は人間が監視できる状態でのみ実施する。`trade_mode=live` / `OMS_LIVE_DRY_RUN=false` はまだ触らない。
3. 7203 paper position の 14:50 JST day closeout 実観測は未完了。
