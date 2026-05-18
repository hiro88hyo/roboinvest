# ADR-0001 Implementation Checklist

ADR-0001「本番デプロイアーキテクチャ」を実装に落とすためのチェックリスト。
本番資金に触れる構成なので、各段階は小さく区切り、paper で確認してから live に進む。

## 0. Scope

対象:

- LAN 内 Linux host に 9 Python services を Docker Compose で常駐させる
- GCP Pub/Sub managed topic/subscription を本番 messaging として使う
- Supabase Cloud を本番 DB として使う
- Dashboard を Vercel Hobby に deploy する
- 1Password CLI の `op run` で secrets を起動時注入する
- GitHub Actions self-hosted runner で LAN host deploy を自動化する

対象外:

- J-Quants 有料プラン移行
- 24/7 監視、ログ集約、バックアップの本格整備
- 複数 OMS Live プロセス化
- Pub/Sub 以外の messaging への移行

## 1. Preflight

- [ ] LAN host の OS / CPU / RAM / SSD / Docker version を記録する
- [ ] LAN host から kabu Windows 機の Caddy `28080` / `28081` に到達できることを確認する
- [ ] `KABU_API_BASE_URL=http://<win-ip>:28080/kabusapi` で `scripts/probe-kabu.py` が通る
- [ ] `scripts/probe-kabu-oms.py --env prod --skip send` で wallet / positions など read-only API が通る
- [x] 本番用 GCP project id 命名方針を決める（`docs/adr/0001-production-prerequisites.md`）
- [x] 本番用 Supabase project と region 方針を決める（`docs/adr/0001-production-prerequisites.md`）
- [x] Vercel project 名と GitHub repository 連携方針を決める（`trade-ai-dashboard`, root `dashboard/`, `docs/runbook/adr-0001-dashboard-vercel.md`）
- [x] 1Password vault / item / field naming を決める（`docs/adr/0001-production-prerequisites.md`）
- [x] 1Password 登録手順を runbook 化する（`docs/runbook/adr-0001-1password.md`）
- [ ] `OMS_LIVE_DRY_RUN` が本番テンプレートで明示管理される方針を決める

## 2. GCP Pub/Sub

- [ ] 本番 GCP project を作成する
- [ ] Pub/Sub API を有効化する
- [ ] service account を作成する
- [x] service account の権限方針を Pub/Sub publisher/subscriber/viewer に絞る（`docs/adr/0001-production-prerequisites.md`）
- [x] 初回 apply 用に一時付与した `Pub/Sub Admin` を runtime service account から外す（Console で手動削除、runtime key で Pub/Sub check 7 topics/9 subscriptions OK）
- [x] managed Pub/Sub 用に各 service の Pub/Sub client を公式 `google-cloud-pubsub` 共通 wrapper へ移行する
- [x] topic 7 件を作成する（一時的に runtime SA へ `Pub/Sub Admin` を付与して apply 済み）
  - `raw-market-data`
  - `processed-features`
  - `strategy-signals-a`
  - `strategy-signals-b`
  - `trade-signals`
  - `live-orders`
  - `paper-orders`
- [x] subscription 9 件を作成する（一時的に runtime SA へ `Pub/Sub Admin` を付与して apply 済み）
  - `feature-engine-raw-market-data`
  - `oms-paper-raw-market-data`
  - `strategy-rule-processed-features`
  - `strategy-ai-processed-features`
  - `aggregator-strategy-signals-a`
  - `aggregator-strategy-signals-b`
  - `gateway-trade-signals`
  - `oms-live-live-orders`
  - `oms-paper-paper-orders`
- [x] `infra/pubsub/topics.json` / `infra/pubsub/subscriptions.json` と本番作成内容の差分を確認する（`scripts/gcp-pubsub-admin.py --apply` 済み）
- [x] 本番用 topic/subscription 作成スクリプトと runbook を追加する（`scripts/gcp-pubsub-admin.py`, `docs/runbook/adr-0001-gcp-pubsub.md`）
- [ ] LAN host から emulator なしで `scripts/gcp-pubsub-admin.py --smoke-test --cleanup-smoke` を通す

注意: subscription は `infra/pubsub/subscriptions.json` を正とする。ADR/HANDOFF の古い記述に 7 件とある場合でも、現行ファイルでは `raw-market-data` と order 系を含めて 9 件になっている。

## 3. Supabase Cloud

- [x] Supabase Cloud project を作成する（`cqexdwufmanuqccerdvo.supabase.co` 接続確認済み）
- [x] Pro plan / PITR の有効化タイミングを決める（paper trial は任意、live readiness gate 前に必須化）
- [x] `contracts/sql/*.sql` を本番 project に適用する手順を作る（`docs/runbook/adr-0001-supabase-cloud.md`）
- [x] `system_status` の初期行を seed する（`id=1`, `trade_mode=paper`, `trading_style=day` 確認済み）
- [x] `master_stocks` / `daily_ohlcv` / `watchlist` の初期投入方針を決める（`docs/runbook/adr-0001-supabase-cloud.md`）
- [x] `SUPABASE_URL` / `SUPABASE_SECRET_KEY` / dashboard 用 anon key を 1Password に登録する（`SUPABASE_ANON_KEY` 読取確認済み）
- [x] Dashboard で必要な Realtime publication を確認する（`supabase_realtime` 設定済み）
- [x] RLS 本番化の要否を決める（paper trial は service role server-side 限定、live readiness gate 前に再設計）
- [x] `scripts/health-check.py` が Cloud Supabase に対して通ることを確認する（9 tables OK）
- [x] `scripts/reconcile-positions.py --dry-run` が本番 kabu + Cloud Supabase の組み合わせで read-only 実行できることを確認する（kabu only: 9432 qty=2000 avg=152.0）

## 4. Production Compose

- [x] `infra/docker-compose.prod.yml` を追加する
- [x] Pub/Sub emulator / `pubsub-init` を prod compose から除外する
- [x] 9 services の Dockerfile / build context を compose に定義する
- [x] 常駐 8 services に `restart: unless-stopped` を設定する
- [x] `KABU_TOKEN_CACHE_FILE` を Feeder と OMS Live で同じ volume/path に揃える
- [x] warm/cold storage や logs の永続 volume を定義する
- [x] `PUBSUB_EMULATOR_HOST` が本番 compose に入らないことを確認する
- [x] `PUBSUB_PROJECT_ID` が本番 GCP project を指すことを確認する
- [x] `TRADE_MODE=paper` から起動できることを確認する（production compose 8 services Up, `OMS_LIVE_DRY_RUN=true`）
- [x] `OMS_LIVE_DRY_RUN=true` で live-orders 経路の dry-run smoke test を通す（order_id=ece93922-4f57-4311-8b53-d8702c613a2b pull→DRY_RUN skip→ack, trades_live writeなし）
- [x] `OMS_LIVE_MAX_QTY_PER_ORDER` / `OMS_LIVE_ALLOWED_SYMBOLS` を live service に必ず設定する
- [x] feeder register 用 `FEEDER_KABU_DEFAULT_EXCHANGE=1` と OMS Live order 用 `KABU_DEFAULT_EXCHANGE=9` を明示する
- [x] `op run --env-file infra/env.production -- docker compose -f infra/docker-compose.prod.yml config` を検証する

検証メモ (2026-05-16):

- `docker compose --env-file infra/env.production.tpl -f infra/docker-compose.prod.yml config` 成功。
- `docker compose --env-file infra/env.production.tpl -f infra/docker-compose.prod.yml --profile batch config` 成功。
- `docker compose --env-file infra/env.production.tpl -f infra/docker-compose.prod.yml --profile batch build` 成功。
- 9 services の container CLI `--help` 起動確認済み。
- Universe Scanner は日次 batch のため `profiles: ["batch"]` / `restart: "no"` とし、常駐 8 services は `restart: unless-stopped`。
- production compose 起動手順は `docs/runbook/adr-0001-production-compose.md` に記録済み。
- managed Pub/Sub 対応は `google-cloud-pubsub` ベースの `trade_contracts.pubsub_client` に集約し、各 service は re-export する形に変更済み。
- 公式 Pub/Sub client 移行後に Pub/Sub client unit tests 79 件、ruff、py_compile、mypy targeted、production compose config、`--profile batch build` 成功。
- `OP_SERVICE_ACCOUNT_TOKEN` を使った `op run --env-file infra/env.production -- docker compose -f infra/docker-compose.prod.yml config` 成功。
- `PUBSUB_EMULATOR_HOST` と raw `op://` 参照が production compose config に残らないことを確認済み。
- `infra/docker-compose.prod.yml` から `env_file` を外し、secret 注入は `op run --env-file ...` に一本化。

## 5. Secrets

- [x] `.env.production.template` または `infra/env.production.tpl` を追加する
- [x] template には `op://...` 参照だけを置き、実値を commit しない
- [x] kabu API password と order password を別 field として登録する（1Password `kabu/KABU_API_PASSWORD`, `kabu/KABU_ORDER_PASSWORD` 読取確認済み）
- [x] GCP credentials の扱いを決める（初回 trial は 1Password から service account key JSON を一時 materialize、将来 ADC 相当に移行）
- [x] `op run -- docker compose -f infra/docker-compose.prod.yml up -d` の起動手順を runbook 化する
- [ ] 起動後に host 上へ secret 実値ファイルが残らないことを確認する（`infra/secrets/gcp-pubsub-sa.json` は稼働中 compose が ro mount 中のため停止時に削除）
- [x] `.gitignore` に本番 secret materialize 先を追加する

## 6. Dashboard / Vercel

- [x] Vercel project を作成して `dashboard/` を root directory に設定する
- [x] `NEXT_PUBLIC_SUPABASE_URL` を設定する
- [x] `NEXT_PUBLIC_SUPABASE_ANON_KEY` を設定する
- [x] `SUPABASE_SECRET_KEY` を server-side env として設定する
- [x] `SUPABASE_SECRET_KEY` が client bundle に出ていないことを確認する（local build artifact で実値リークなし）
- [x] Vercel preview で build が通り、主要 route が 200 になることを確認する（branch preview `roboinvest-git-adr-0001-production-compose-hiro88hyos-projects.vercel.app`、env 実値 materialize 後）
- [x] Dashboard から `system_status` の kill switch / trade mode 操作が Cloud Supabase に反映されることを確認する（service-role 更新で false→paper→true/paper 復元、`/system` 表示 OK）
- [x] Realtime が production dashboard で購読できることを確認する（anon key で `system_status` UPDATE event 受信 OK）

## 7. GitHub Actions Deploy

- [ ] LAN host に repo-scoped self-hosted runner をインストールする
- [ ] repository は private のまま運用する
- [x] runner user の権限を Docker 操作に必要な範囲へ絞る方針を runbook 化する（Docker group は host root 相当として扱う）
- [x] deploy workflow を追加する（`.github/workflows/deploy-production.yml`）
- [x] deploy workflow は `workflow_dispatch` で動かす（`dry_run=true` がデフォルト、`production` environment 承認前提）
- [x] workflow 内で `git pull` / `docker compose up -d --build` 相当を実行する（persistent checkout `/home/hiroyuki/workspaces/roboinvest` 前提）
- [x] deploy 前に `make test-all` の成功を require する（対象 ref の `ci.yml` successful run を確認）
- [x] deploy 前に `docker compose -f infra/docker-compose.prod.yml config` を実行する
- [x] runner logs に secrets が出ないことを確認する手順を runbook 化する
- [x] rollback 手順を runbook 化する（`docs/runbook/adr-0001-github-actions-deploy.md`）

## 8. Paper Production Trial

- [x] `trade_mode=paper` / `trading_style=day` で本番構成を起動する（production compose 8 services Up）
- [x] `scripts/health-check.py` で topics / subscriptions / services / Supabase を確認する（Supabase/services OK, managed Pub/Sub は `gcp-pubsub-admin.py` check OK）
- [x] `watchlist` に最小銘柄を seed する（7203, valid_date=2026-05-16）
- [x] Feeder が Caddy 経由で WebSocket 接続できることを確認する（`ws://192.168.2.21:28080/kabusapi/websocket` connected、時間外のため 5s tick なし）
- [x] `raw-market-data` から `processed-features` まで流れることを確認する（7203 tick publish→feature-engine pull/publish/ack）
- [x] Strategy A/B から Aggregator/Gateway まで流れることを確認する（A/B smoke signals→aggregator_logs→trade-signals→paper-orders）
- [x] OMS Paper が約定を作り `trades_paper` / `positions` を更新することを確認する（7203 BUY qty=300 price=2510）
- [ ] 14:50 day closeout が paper positions を閉じることを確認する（事前コード確認済み: scheduler 有効、JST 14:50、`trading_style=day`、7203 paper day qty=300。実閉鎖確認は未実施）
- [x] Dashboard の realtime 表示が更新されることを確認する（Cloud 初期表示 7203 OK、anon Realtime `system_status` UPDATE event OK）
- [x] paper trial のログと Supabase 状態を runbook に記録する（`docs/runbook/adr-0001-paper-production-trial.md`）

## 9. Live Readiness Gate

- [x] `scripts/reconcile-positions.py --dry-run` で kabu 実保有と Supabase positions の差分を確認する（2026-05-17: dry-run, kabu only 9432 qty=2000 avg=152.0, Supabase live empty）
- [x] `system_status.is_trading_allowed=false` で live 注文が拒否されることを確認する（2026-05-17: Gateway unit）
- [x] `daily_pnl <= -daily_loss_limit` の kill switch 経路を paper または dry-run で確認する（2026-05-17: Gateway unit + local integration）
- [x] `OMS_LIVE_DRY_RUN=true` で live-orders 消費経路が ack まで進むことを確認する（2026-05-17: 9432/100 BUY dry-run, acked=1, dry_run_skipped=1）
- [x] `OMS_LIVE_ALLOWED_SYMBOLS` を検証銘柄だけに絞る（2026-05-17: 9432）
- [x] `OMS_LIVE_MAX_QTY_PER_ORDER` を最小単元に絞る（2026-05-17: 100）
- [x] `KABU_ORDER_PASSWORD` が API password と別値で設定されていることを確認する（2026-05-17: values present and distinct）
- [x] `KABU_DEFAULT_EXCHANGE=9` であることを確認する（2026-05-17）
- [x] `docs/runbook/oms-live-phase3.md` の手動回復手順を手元で開ける状態にする（2026-05-17）
- [ ] Dashboard production URL を一般公開のまま live に進めない（`docs/adr/0002-dashboard-auth-rls.md`）
- [ ] Dashboard OAuth2 + RLS の実装前設計を確定する（`docs/adr/0002-dashboard-auth-rls.md`, `docs/runbook/adr-0002-dashboard-auth-rls.md`）
- [ ] Dashboard の anon read policies を廃止し、authenticated admin RLS に移行する
- [ ] `/system` の Server Action が `SUPABASE_SECRET_KEY` で user-triggered update しない構成に移行する
- [ ] live 初回は市場時間中に人間が監視して実行する

## 10. First Live Cutover

- [ ] Dashboard で `trade_mode=live` に切り替える
- [ ] kill switch を有効に戻す前に `positions(live)` が期待状態であることを確認する
- [ ] `OMS_LIVE_DRY_RUN` を unset または `false` にする
- [ ] 最小数量 / allowlist 限定で 1 注文だけ通す
- [ ] kabu 側の注文照会と `trades_live` を突合する
- [ ] `positions(live)` と kabu 実保有を突合する
- [ ] 問題があれば即 kill switch を入れ、`docs/runbook/oms-live-phase3.md` の回復手順に従う
- [ ] 初回 live 結果を `docs/HANDOFF.md` または runbook に追記する

## 11. Done Criteria

- [ ] `make test-all` が通る
- [ ] production compose が LAN host で `paper` mode として継続稼働する
- [ ] managed Pub/Sub / Supabase Cloud / Vercel の本番接続がすべて確認済み
- [ ] self-hosted runner から deploy できる
- [ ] secrets が平文ファイルとして永続化されない
- [ ] paper trial の E2E と 14:50 closeout が本番構成で確認済み
- [ ] live 初回の最小注文が kabu / Supabase / Dashboard で突合済み
- [ ] rollback / kill switch / reconcile の手順が runbook 化済み
