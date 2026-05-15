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
- [ ] 本番用 GCP project id を決める
- [ ] 本番用 Supabase project と region を決める
- [ ] Vercel project 名と GitHub repository 連携方針を決める
- [ ] 1Password vault / item / field naming を決める
- [ ] `OMS_LIVE_DRY_RUN` が本番テンプレートで明示管理される方針を決める

## 2. GCP Pub/Sub

- [ ] 本番 GCP project を作成する
- [ ] Pub/Sub API を有効化する
- [ ] service account を作成する
- [ ] service account の権限を Pub/Sub publisher/subscriber に絞る
- [ ] topic 7 件を作成する
  - `raw-market-data`
  - `processed-features`
  - `strategy-signals-a`
  - `strategy-signals-b`
  - `trade-signals`
  - `live-orders`
  - `paper-orders`
- [ ] subscription 9 件を作成する
  - `feature-engine-raw-market-data`
  - `oms-paper-raw-market-data`
  - `strategy-rule-processed-features`
  - `strategy-ai-processed-features`
  - `aggregator-strategy-signals-a`
  - `aggregator-strategy-signals-b`
  - `gateway-trade-signals`
  - `oms-live-live-orders`
  - `oms-paper-paper-orders`
- [ ] `infra/pubsub/topics.json` / `infra/pubsub/subscriptions.json` と本番作成内容の差分を確認する
- [ ] 本番用 topic/subscription 作成スクリプトを追加するか、手順を runbook 化する
- [ ] LAN host から emulator なしで `PUBSUB_PROJECT_ID=<prod>` の publish/subscribe smoke test を通す

注意: subscription は `infra/pubsub/subscriptions.json` を正とする。ADR/HANDOFF の古い記述に 7 件とある場合でも、現行ファイルでは `raw-market-data` と order 系を含めて 9 件になっている。

## 3. Supabase Cloud

- [ ] Supabase Cloud project を作成する
- [ ] Pro plan / PITR の有効化タイミングを決める
- [ ] `contracts/sql/*.sql` を本番 project に適用する手順を作る
- [ ] `system_status` の初期行を seed する
- [ ] `master_stocks` / `daily_ohlcv` / `watchlist` の初期投入方針を決める
- [ ] `SUPABASE_URL` / `SUPABASE_SECRET_KEY` / dashboard 用 anon key を 1Password に登録する
- [ ] Dashboard で必要な Realtime publication を確認する
- [ ] RLS 本番化の要否を決める
- [ ] `scripts/health-check.py` が Cloud Supabase に対して通ることを確認する
- [ ] `scripts/reconcile-positions.py --dry-run` が本番 kabu + Cloud Supabase の組み合わせで read-only 実行できることを確認する

## 4. Production Compose

- [ ] `infra/docker-compose.prod.yml` を追加する
- [ ] Pub/Sub emulator / `pubsub-init` を prod compose から除外する
- [ ] 9 services の Dockerfile / build context を compose に定義する
- [ ] 各サービスに `restart: unless-stopped` を設定する
- [ ] `KABU_TOKEN_CACHE_FILE` を Feeder と OMS Live で同じ volume/path に揃える
- [ ] warm/cold storage や logs の永続 volume を定義する
- [ ] `PUBSUB_EMULATOR_HOST` が本番 compose に入らないことを確認する
- [ ] `PUBSUB_PROJECT_ID` が本番 GCP project を指すことを確認する
- [ ] `TRADE_MODE=paper` から起動できることを確認する
- [ ] `OMS_LIVE_DRY_RUN=true` で live-orders 経路の dry-run smoke test を通す
- [ ] `OMS_LIVE_MAX_QTY_PER_ORDER` / `OMS_LIVE_ALLOWED_SYMBOLS` を live service に必ず設定する
- [ ] `KABU_DEFAULT_EXCHANGE=9` を本番デフォルトとして明示する
- [ ] `docker compose -f infra/docker-compose.prod.yml config` を CI または deploy 前 hook で検証する

## 5. Secrets

- [ ] `.env.production.template` または `infra/env.production.tpl` を追加する
- [ ] template には `op://...` 参照だけを置き、実値を commit しない
- [ ] kabu API password と order password を別 field として登録する
- [ ] GCP credentials の扱いを決める
  - service account key file を `op run` で一時 materialize する
  - または host の workload identity / ADC 相当に寄せる
- [ ] `op run -- docker compose -f infra/docker-compose.prod.yml up -d` の起動手順を runbook 化する
- [ ] 起動後に host 上へ secret 実値ファイルが残らないことを確認する
- [ ] `.gitignore` に本番 secret materialize 先を追加する

## 6. Dashboard / Vercel

- [ ] Vercel project を作成して `dashboard/` を root directory に設定する
- [ ] `NEXT_PUBLIC_SUPABASE_URL` を設定する
- [ ] `NEXT_PUBLIC_SUPABASE_ANON_KEY` を設定する
- [ ] `SUPABASE_SECRET_KEY` を server-side env として設定する
- [ ] `SUPABASE_SECRET_KEY` が client bundle に出ていないことを確認する
- [ ] Vercel preview / production で `npm run lint` / `npm test` / build が通ることを確認する
- [ ] Dashboard から `system_status` の kill switch / trade mode 操作が Cloud Supabase に反映されることを確認する
- [ ] Realtime が production dashboard で購読できることを確認する

## 7. GitHub Actions Deploy

- [ ] LAN host に repo-scoped self-hosted runner をインストールする
- [ ] repository は private のまま運用する
- [ ] runner user の権限を Docker 操作に必要な範囲へ絞る
- [ ] deploy workflow を追加する
- [ ] deploy workflow は `main` push 後に手動承認または `workflow_dispatch` で動かす
- [ ] workflow 内で `git pull` / `docker compose pull` / `docker compose up -d --build` 相当を実行する
- [ ] deploy 前に `make test-all` の成功を require する
- [ ] deploy 前に `docker compose -f infra/docker-compose.prod.yml config` を実行する
- [ ] runner logs に secrets が出ないことを確認する
- [ ] rollback 手順を runbook 化する

## 8. Paper Production Trial

- [ ] `trade_mode=paper` / `trading_style=day` で本番構成を起動する
- [ ] `scripts/health-check.py` で topics / subscriptions / services / Supabase を確認する
- [ ] `watchlist` に最小銘柄を seed する
- [ ] Feeder が Caddy 経由で WebSocket 接続できることを確認する
- [ ] `raw-market-data` から `processed-features` まで流れることを確認する
- [ ] Strategy A/B から Aggregator/Gateway まで流れることを確認する
- [ ] OMS Paper が約定を作り `trades_paper` / `positions` を更新することを確認する
- [ ] 14:50 day closeout が paper positions を閉じることを確認する
- [ ] Dashboard の realtime 表示が更新されることを確認する
- [ ] paper trial のログと Supabase 状態を runbook に記録する

## 9. Live Readiness Gate

- [ ] `scripts/reconcile-positions.py --dry-run` で kabu 実保有と Supabase positions の差分を確認する
- [ ] `system_status.is_trading_allowed=false` で live 注文が拒否されることを確認する
- [ ] `daily_pnl <= -daily_loss_limit` の kill switch 経路を paper または dry-run で確認する
- [ ] `OMS_LIVE_DRY_RUN=true` で live-orders 消費経路が ack まで進むことを確認する
- [ ] `OMS_LIVE_ALLOWED_SYMBOLS` を検証銘柄だけに絞る
- [ ] `OMS_LIVE_MAX_QTY_PER_ORDER` を最小単元に絞る
- [ ] `KABU_ORDER_PASSWORD` が API password と別値で設定されていることを確認する
- [ ] `KABU_DEFAULT_EXCHANGE=9` であることを確認する
- [ ] `docs/runbook/oms-live-phase3.md` の手動回復手順を手元で開ける状態にする
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
