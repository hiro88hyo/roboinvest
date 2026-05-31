# Handoff Memo (for coding AIs)

最終更新: 2026-05-25 / HEAD: `main`

> Archived on 2026-05-29 from `docs/HANDOFF.md`.
> This file keeps the long May 2026 operational chronology so the root handoff can stay compact.

別のコーディング AI（Claude Code 別セッション / Cursor / Copilot 等）がこのリポジトリに着手するときに、最初に目を通すための引き継ぎメモ。詳細は本ファイルではなく各リンク先で確認すること。

---

## 1. このリポジトリは何か

日本国内現物株（auカブコム証券）向けの自律トレードシステム。ルールベース + AI（LLM）のハイブリッド戦略を、Pub/Sub で疎結合した 9 つの Python マイクロサービスで構成する。

- アーキテクチャ図・コンポーネント詳細: [CLAUDE.md](../../CLAUDE.md)
- 本番デプロイ方針: [docs/adr/0001-deployment-architecture.md](../adr/0001-deployment-architecture.md)
- 開発環境セットアップ: [docs/dev-setup.md](../dev-setup.md)

---

## 2. 現在の完成度（2026-05-15 時点）

| 項目 | 状態 |
|---|---|
| 全 9 サービス + Dashboard 実装 | ✅ 完成 |
| Paper エンドツーエンド | ✅ 4 ラウンド + 14:50 closeout 秒精度を観測 |
| OMS Live Phase 3（本番 28080 / 実発注 e2e） | ✅ 全 4 ケース PASSED |
| リスク管理（2%ルール / キルスイッチ / day closeout / swing 自動決済） | ✅ 稼働 |
| CI（python / dashboard / e2e の 3 job + Dependabot + coverage） | ✅ 緑 |
| ADR-0001 本番デプロイ | ❌ 文書のみ、未着手 |
| Universe Scanner 本番自動化 | ✅ J-Quants paid cutover 手動実行確認済み、日次自動化は未実装 |
| 24/7 運用（監視・アラート・バックアップ） | ❌ 未整備 |

**要約**: 「ローカルで paper を回せる」段階までは完成。J-Quants paid cutover の手動実行は確認済みで、次の山は ADR-0001 実装の詰めと日次自動化です。

---

## 3. 着手前に最低限読むファイル

1. ルート [CLAUDE.md](../../CLAUDE.md) — アーキ図 / リポジトリ構成 / Pub/Sub トピック / Supabase テーブル / リスク管理ルール / コーディング規約
2. 該当サービスの `services/<name>/CLAUDE.md` — そのサービス特有の責務と設計メモ
3. [contracts/](../../contracts/) — Pydantic / SQL / TS の **Single Source of Truth**。スキーマ変更は必ずここから
4. 直近の `git log --oneline -20` と `git diff main...HEAD`（もし作業ブランチがあれば）

---

## 4. 開発の基本操作

```bash
# 全サービスの lint / type check
make lint-all

# 全サービスのテスト（878+ pass）
make test-all

# Paper trading をローカルで起動（5 ステップを 1 コマンド化）
bash scripts/start-paper-trading.sh

# Supabase 型再生成
./scripts/gen-supabase-types.sh

# ヘルスチェック（topics 7 / subscriptions 7 / services 9）
uv run python scripts/health-check.py
```

- Python: `uv`（`pip` / `poetry` 直叩き禁止）/ `ruff format` / `ruff check` / `mypy --strict` / `pytest`
- Dashboard: `volta`（グローバル Node 汚染禁止）/ `npm run lint` (Biome) / `vitest`
- ブランチ: `feature/*` → PR → CI 緑 → `gh pr merge --merge --delete-branch`（ローカル main は自動 fast-forward）

---

## 5. 落とし穴・地雷（必ず確認）

### 5.1 共通

- **VSCode 拡張版 Claude Code は Bash tool に env を引き継がない**。機微情報は `/tmp/<task>.env` 経由で渡す。
- **Pub/Sub エミュレータは 3 日連続稼働で OOM**。`docker restart trade-ai-pubsub` の後は `infra/pubsub/init-topics.sh` で topics + subscriptions を再 seed が必要。
- **subscription は手動 PUT が必要**だったが、現在は `infra/pubsub/subscriptions.json` + `init-topics.sh` のサブセクションで自動化済み（`strategy-ai-processed-features` を含む 7 件）。
- **市場開始前チェックリスト**: subscription 未作成 / `daily_ohlcv` 空 / `watchlist` 未更新 が 3 大要因。`scripts/start-paper-trading.sh` がカバーする範囲とカバーしない範囲をスクリプトを読んで把握すること。
- **production compose の paper 始業手順**: [`docs/runbook/paper-open-checklist.md`](../runbook/paper-open-checklist.md) を参照。Universe Scanner 手動実行 → watchlist 確認 → services 起動の順。
- **実売買 `GO` の判定基準**: [`docs/runbook/live-go-checklist.md`](../runbook/live-go-checklist.md) を参照。`paper GO` / `Weak GO` / `Strong GO` を分けて潰す。

### 5.2 kabu.com API

- **kabuステーションは localhost 限定**（http.sys URL ACL の制約）。本番は Windows 上の Caddy リバプロ（28080/28081）、開発機からは SSH トンネル経由。WS の Host ヘッダー上書き禁止。
- **本番は SOR 必須（Exchange=9）**。`KABU_DEFAULT_EXCHANGE` のデフォルトは `9`（commit 885ed7b）。`1` だと `Code:100378` で reject。
- **検証 18081 は sendorder を黙殺する**。実発注検証は本番 28080 のみ。
- **Feeder と OMS Live の kabu トークンは共有ファイル経由**（`KABU_TOKEN_CACHE_FILE`、デフォルト `/tmp/kabu_token_cache.json`、PR #42）。ただし **別プロセスが `/token` を再発行すると既存 token は無効化される** ため、Strong GO の実発注では `feeder` や確認用 probe を止めて OMS Live 単独にすること。
- **OMS Live は `KABU_API_PASSWORD` と `KABU_ORDER_PASSWORD` を別 env で読む**。`.env` で同値だと sendorder が通らない（2026-05-13 で踏んだ）。

### 5.3 Supabase / contracts

- **`positions` テーブルは OMS Live 単一プロセス前提で非アトミック**（PostgREST に atomic increment なし）。複数プロセス化が必要なら Postgres RPC に切替。
- **kabu 実保有との乖離**は `scripts/reconcile-positions.py` で照合（PR #15）。`--dry-run` → 確認 → `--apply`。
- **closeout 由来の `unified_signal_id` は `None`**（`trades_*.unified_signal_id` は nullable FK）。`aggregator_logs` に対応行はない。
- **`OMS_LIVE_DRY_RUN` は `.env` で `true` になっていることがある**。Phase 3 e2e の `_build_settings` は `oms_live_dry_run=False` を明示渡しする（commit 032d385）。

### 5.4 テスト / リント規約

- **新サービスは `tests/conftest.py` を作らない**。`src/<service>/_testing.py` に fixture を置く。`tests/__init__.py` も書かない（`mypy --strict` で他サービスと duplicate-module 衝突する）。
- **テストファイル名は `test_<service>_*` プレフィックス**で衝突回避（例: `test_ai_*`、`test_paper_*`）。
- **strategy-ai の `--fixture-responses` は文字列化された JSON object 配列**。`"BUY"` 単独は parser が無視する。

### 5.5 戦略パラメータ

- **`RSI_BUY_THRESHOLD=25` / `RSI_SELL_THRESHOLD=75`**（commit b16d9c8 で 30/70 から締めた）。テスト時に緩めたら戻し忘れに注意。
- **`SMA min_gap_ratio=0.005` / `Bollinger tolerance=0.15`**（同上、40 回転 → 10–15 回転目安）。

---

## 6. 次セッションの優先タスク

### 2026-05-20 paper production test / stabilization wrap-up

- `2026-05-20` の paper production test は日中観測まで完了。Universe Scanner → feeder → feature-engine → strategy-rule / strategy-ai → aggregator → gateway → oms-paper の流れは稼働確認済み。
- Universe Scanner は J-Quants v2 の旧 5 文字コード混在で `feeder` register が落ちていたため、symbol 正規化と legacy symbol 削除を追加。関連ファイル: `services/universe-scanner/src/universe_scanner/symbols.py`、`ingest/master_stocks.py`、`ingest/daily_ohlcv.py`。
- `feeder` は `no close frame received or sent` が 3-4 分周期で再発していたため、WS 受信と Pub/Sub publish の完全直列をやめ、bounded concurrency (`max_pending_sends`) を追加。`2026-05-20 12:55 JST` の再デプロイ以降、`15:00 JST` 越えまで reconnect なしで安定観測。
- `strategy-ai` は Gemini 応答の途中切れに対して parser を強化し、`BUY` / `SELL` / `HOLD` の action token だけ読める断片も安全側で回収するようにした。非正の confidence の signal は strategy で捨てる。
- `strategy-ai` の recovered-partial 系ログは `INFO` から `DEBUG` に落とした。残る warning はごく少数の `{"action": "` レベル断片のみで、運用上は一旦許容。
- 実行済みテスト: universe-scanner 13 pass、feeder 38 pass、strategy-ai 25 pass。production compose 上でも `feeder` 長時間安定と `strategy-ai` warning 大幅減を確認済み。

### 2026-05-21 Strong GO 完了メモ

- `paper GO`、`Weak GO` は production compose / Cloud Supabase / managed Pub/Sub 上で完了。市場オープン後の `gateway` reject は `already_long` / `no_position_for_sell` が主で、reject 偏重ではなかった。
- `2026-05-21 09:10-09:11 JST` に OMS Live の最小 round-trip を実施。対象は `9432 / 100`、`trade_mode=paper` のまま `live-orders` へ直接 publish して `oms-live` のみで e2e を確認した。
- 約定結果は BUY `155.40` → SELL `155.25`、`system_status.daily_pnl=-15.00`。Supabase は `trades_live` 2 行、`positions(live)` は空に復帰。kabu `/orders` にも 2 件の約定履歴が残り、残ポジションはなし。
- 実施中に、`feeder` 稼働中または `probe-kabu-oms.py` 実行中だと kabu `/token` 再発行で OMS Live の token が失効し、`sendorder` が `401 APIキー不一致` になることを再確認した。Strong GO を再実施する場合は `feeder` 停止、`KABU_TOKEN_CACHE_FILE=` で one-shot `oms-live` を起動し、途中で kabu probe を打たないこと。
- 実発注後は `feeder` / 常駐 `oms-live` を production compose で再起動済み。常駐 `oms-live` は `OMS_LIVE_DRY_RUN=true` に戻っている。

### 2026-05-21 live session メモ

- 2026-05-21 は trade_mode=live / OMS_LIVE_DRY_RUN=false で日中 live 運用を実施。trades_live には少なくとも 3905、6232、4047、9880、2874 の round-trip が残り、引け後の positions(live) は空、system_status.daily_pnl=15975.0 を確認した。
- 当日 watchlist 30 銘柄へ OMS_LIVE_ALLOWED_SYMBOLS を拡張して live 運用した。初期は 9432 固定だったため allowed_symbols reject が多かったが、拡張後は自然約定が通った。
- token 競合を避けるための one-shot Strong GO 実施後、通常運用では常駐 feeder / oms-live を再起動済み。
- 運用中に見えた主な課題は 2 つ。
  1. gateway が 200/400/500/1500 株の live BUY を出し、oms-live が OMS_LIVE_MAX_QTY_PER_ORDER=100 で reject していた。
  2. 一部銘柄で kabu Code 21: 可能額が不足 が複数回発生した。
- 課題 1 への対処として、gateway 側に OMS_LIVE_MAX_QTY_PER_ORDER を読み込ませ、live BUY 数量を publish 前に cap する修整を追加済み。production gateway 再ビルド・再起動済み。
- 課題 2 への対処として、gateway 側に positions(live) の評価額合計を差し引いた残予算で live BUY 数量を再計算するガードを追加済み。これにより open live exposure がある状態では新規 BUY が縮小または reject される。production gateway 再ビルド・再起動済み。
- 追加テスト: uv run pytest services/gateway/tests/unit で 125 passed。
- 次セッションの確認ポイントは、翌営業日の寄り付き後に max_qty_per_order reject と kabu Code 21 が実際に減るかを live ログで観測すること。
- 2026-05-21 引け後に production compose の GCP credentials mount を repo 配下 `infra/secrets/gcp-pubsub-sa.json` から tmpfs `/dev/shm/roboinvest/gcp-pubsub-sa.json` へ移行し、旧平文ファイルは削除済み。`Deploy Production` workflow、`run-production-universe-scanner.sh`、関連 runbook も tmpfs 前提へ更新した。
- 2026-05-21 夜時点では managed Pub/Sub の check-only は成功したが、当時の `scripts/gcp-pubsub-admin.py --smoke-test --cleanup-smoke` は一時 smoke resource の create/delete まで行う実装だったため、runtime SA で `PermissionDenied` になった。以後は dedicated smoke resource を常設し、既存 resource は `KEEP` する実装へ修正した。
- 2026-05-21 深夜に runtime SA へ一時的に Pub/Sub 編集者権限を付けて `--apply` を実行し、`adr-0001-smoke-test` / `adr-0001-smoke-test-sub` を作成。その後すぐ権限を `Publisher` / `Subscriber` / `Viewer` の最小構成へ戻し、同じ runtime SA で `scripts/gcp-pubsub-admin.py --smoke-test --cleanup-smoke` が `RESULT OK` となることを再確認した。今後 `--apply` が必要なときだけ一時的に強い権限を付与する。

### Next Session TODO

- 最優先は寄り付き後の live ログ観測。`gateway` の数量抑制が効いて `qty>100` が `oms-live` まで流れないこと、`kabu Code 21` が減ることを確認する。
- managed Pub/Sub smoke test は 2026-05-21 に対応完了。runtime SA に付与した Pub/Sub 編集者権限で `--apply` を実行し、`adr-0001-smoke-test` / `adr-0001-smoke-test-sub` を作成後、同日 `scripts/gcp-pubsub-admin.py --smoke-test --cleanup-smoke` が `RESULT OK` まで通ることを確認した。
- paper `14:50 closeout` の再観測は保留。ADR チェックリスト上の残件だが、live 運用の阻害条件ではない。

### 2026-05-22 Execution Plan

- 2026-05-22 の市場時間中は `live` 確認を最優先し、`paper` と同時並行で進めない。
- 寄り付き前に production compose / Cloud Supabase / managed Pub/Sub の通常起動状態を確認し、`gateway` / `oms-live` / kabu ログ観測の準備をする。
- 寄り付き後は `gateway` の数量抑制が効いて `qty>100` が `oms-live` まで流れないこと、`kabu Code 21` が減ることを live ログで確認する。
- live 観測中は kabu token 競合を避けるため、`paper` 用の切替や追加 probe を挟まない。
- `paper 14:50 closeout` の再観測は 2026-05-22 の live 確認と切り離し、別枠で実施する。

### 2026-05-22 live blocking issue / fix plan

- 2026-05-22 寄り付き後の live 観測で、`feeder -> feature-engine -> strategy-rule -> aggregator -> gateway` の流れ自体は動作していたが、`gateway` は live 新規 `BUY` をほぼ通せなかった。
- 主因は `gateway` の `missing_entry_price` reject。現在の実装では live `BUY` かつ既存ポジション `0` の場合、`positions.current_price` からしか `entry_price` を引かない。`paper` では `daily_ohlcv.close` fallback があるが、live は fail-close のため fallback しない。
- `positions(live)` / kabu 実保有がともに空のとき、watchlist-only 銘柄の live `BUY` は構造的に `missing_entry_price` になり、新規エントリー不能。直近ログでは `missing_entry_price` と `no_position_for_sell` が reject の大半を占め、`trades_live` の当日新規約定は発生しなかった。
- 次セッションの修正方針:
  1. live 用の `entry_price` ソースを `positions` 以外に追加する。候補は watchlist 銘柄の直近価格を別ストアへ保持し、`gateway` がそれを参照する経路。
  2. `daily_ohlcv.close` を live fallback にそのまま使うのは stale price での実発注リスクが高いため避ける。
  3. `gateway` unit/integration test に「live + flat position + 最新価格ありなら BUY を通す」「live + 最新価格なしなら `missing_entry_price` reject」を追加する。
  4. 修正後は production compose で再観測し、`gateway` の `missing_entry_price` 偏重が減ること、`trades_live` 当日新規約定が発生しうることを確認する。

### 2026-05-22 live fix / production outcome

- `StrategySignal` / `UnifiedTradeSignal` に `price` を追加し、`strategy-rule` / `strategy-ai` / `aggregator` で `ProcessedFeatures.price` をそのまま引き回すように修正した。これにより `gateway` は live 新規 `BUY` で既存 `positions.current_price` がなくても `signal.price` から数量計算できる。
- `gateway` には reject 診断ログを追加し、`signal_source` / `has_price` / strategy signal id を残すようにした。production では `missing_entry_price` の大半が `signal_source=RULE` かつ `has_price=False` の backlog signal であることを確認した。新しい経路では `entry_price resolved: source=signal` が出て live `BUY` / `SELL` が実際に約定した。
- `oms-live` は kabu token 失効で `401 APIキー不一致` に詰まっていたため、`send_order` / `get_order` / `cancel_order` で `401/403` を受けたら token を invalidate して 1 回だけ再試行するように修正した。production では `401 -> invalidating token and retrying once -> 200 OK -> 約定` を確認済み。
- `oms-live` の `OMS_LIVE_MAX_QTY_PER_ORDER` は exit の `SELL` まで止めていたため、`BUY` にのみ適用するよう修正した。production では `4392 SELL qty=200` が `sendorder 200 -> 約定 -> positions delete -> pnl_delta=22200.00` まで通った。
- 引け後に `gateway` が `14:50 JST` 以降も live order を publish し続ける設計欠陥が見つかったため、`trade_mode=live` かつ `holding_type=day` では `14:50 Asia/Tokyo` 以降の signal を `reason=market_closed` で fail-close する guard を追加した。production 反映後は `15:44 JST` 以降の live signal が publish されず、すべて `market_closed` reject に変わった。
- 当日観測できた live 約定例は `4392`、`5074`、`9552`、`3810`、`7162`、`6613`。一方で残課題もあり、`RULE has_price=False` の古い signal による `missing_entry_price`、一部重複決済に起因する kabu `Code 8`、引け後 publish 直前に発生した kabu `Code 5: 正しい有効期限を設定してください` は未解決。
- `fatal error: concurrent map writes/read and map write` は、少なくとも今回の再現経路では `gateway` コンテナの Python runtime ではなく `docker compose logs` 側の出力にだけ現れた。`docker compose version v5.1.3`。直近 2 時間の `gateway` 生ログには traceback や process restart はなく、service crash の証拠は取れていない。次回は compose CLI 側の既知不具合も疑って切り分ける。
- 引け後確認時点の `positions(live)` は `3907 x 200 LONG` の 1 件だけ。`opened_at=2026-05-22 05:53:24 UTC` (`2026-05-22 14:53:24 JST`) で closeout 後に建っており、post-close guard 反映後は新しい live 建玉が増えていないことを Supabase で確認済み。さらに kabu `/positions` でも `3907 / 200 / Price=1259 / CurrentPrice=1270` が一致しており、stale row ではなく実ポジション。
- `3907` の net `200 LONG` は、`2026-05-22 12:42:53 JST` の `BUY 100`、`13:15:45 JST` の `SELL 100` を経た後、`2026-05-22 14:53:24-25 JST` に `BUY 100 x2` が約定してできたもの。対応する `SELL` signal は `2026-05-22 14:54 JST` 以降にしか出ておらず、現在は post-close guard で `market_closed` reject されるため、そのまま週末持ち越しになっている。
- 実行テスト: `uv run pytest services/gateway/tests/unit/test_stream_runner.py -q` で `22 passed`、`uv run pytest services/oms-live/tests/unit/test_live_stream_runner.py` で `25 passed`。
- 次セッションの優先確認:
  1. `3907` 残ポジションを `2026-05-25 月曜日` の寄り前にどう扱うか確認する。（月曜朝の判断事項）

### 2026-05-25 live closeout incident / follow-up

- 2026-05-25 の live 運用では、14:50 JST closeout が走ったものの `2693` と `3907` が持ち越しになった。引け後の `scripts/reconcile-positions.py` dry-run では kabu `/positions` と Supabase `positions(live)` が一致し、`3907` / `2693` は stale row ではなく実建玉であることを確認済み。
- closeout 対象は当時 `3907 x200`、`6217 x100`、`2693 x100`。`6217` は `20260525A02N64672246` が約定し、Supabase から削除済み。
- `3907` closeout 注文 `20260525A02N64672205` は `14:50:00 JST` に SELL 200 を受付/発注したが、`CumQty=0` のまま OMS 側が 30 秒で poll timeout し、`14:50:30 JST` に cancelorder。kabu board は `CurrentPrice=null`、`OpeningPrice=null`、`TradingVolume=null`、気配 `1570`、`PreviousClose=1270` で、寄らずストップ高気配のため即時約定しなかったと判断。
- `2693` closeout 注文 `20260525A02N64672252` は `14:50:32 JST` に SELL 100 を受付/発注したが、`CumQty=0` のまま OMS 側が 30 秒で poll timeout し、`14:51:03 JST` に cancelorder。こちらは当日出来高 `2,815,300`、終値 `447` で寄らずではないが、終盤板が薄く 30 秒では約定確認できなかった。
- 直接原因は市場要因だけでなく、closeout が通常注文と同じ `ORDER_FILL_TIMEOUT_SECONDS=30` を使い、未約定の成行SELLを30秒で取り消していたこと。複数銘柄も直列処理だったため、1銘柄の待ちが次銘柄を遅らせていた。
- 修正済み: `services/oms-live/src/oms_live/config.py` に `closeout_order_fill_timeout_seconds` を追加し、`infra/env.production` / `infra/env.production.tpl` に `CLOSEOUT_ORDER_FILL_TIMEOUT_SECONDS=2400` を追加。`run_closeout` は closeout 注文を並列処理し、closeout だけ長い timeout で引け付近まで約定/配分を待つ。通常注文の `ORDER_FILL_TIMEOUT_SECONDS=30` は維持。
- 修正反映: `oms-live` を rebuild/recreate 済み。`op run --env-file infra/env.production -- docker compose ... exec oms-live .venv/bin/python ...` で `closeout_order_fill_timeout_seconds=2400.0` を確認。`oms-live` は Up、直近エラーなし。注意: compose を `op run` なしで起動すると `PUBSUB_PROJECT_ID=op://...` のまま入り起動失敗するため、production recreate は必ず `set -a; . infra/.op.service-account.env; set +a; op run --env-file infra/env.production -- docker compose --env-file infra/env.production -f infra/docker-compose.prod.yml ...` で行う。
- 実行テスト: `uv run pytest services/oms-live/tests/unit/test_live_stream_runner.py` は `27 passed`。`uv run ruff check services/oms-live/src/oms_live/streaming/runner.py services/oms-live/src/oms_live/config.py services/oms-live/tests/unit/test_live_stream_runner.py` は `All checks passed`。
- 次営業日の重点監視: 寄り前/寄り後に `3907` と `2693` の実ポジション、板、約定可否を確認する。特に `3907` は寄らずストップ高が継続する可能性があるため、通常 signal 経路だけでなく closeout/手動決済判断を明示する。
- 残課題: closeout 後に `positions(live)` が残った場合の強いアラート、kabu 注文詳細 (`RecType=6` など) の構造化ログ、持ち越し銘柄の翌営業日 pre-open チェックを運用手順化する。

### 2026-05-26 live close / follow-up fixes

- 2026-05-26 の live 運用は `trades_live` 49 件、`system_status.daily_pnl=44321.0` で終了。ただし `3907 x100` が残ポジションとして持ち越しになった。引け後の `scripts/reconcile-positions.py` では kabu `/positions` と Supabase `positions(live)` は一致し、orphan / quantity mismatch はなし。
- `3907` は朝に持ち越し分 `200` を SELL した後、`14:31 JST` に `100` を再 BUY。`14:50 JST` closeout の SELL は `kabu Code 21: 可能額が不足しております` で sendorder 失敗。現物同一銘柄の「持ち越し売却後の当日再 BUY → 当日再 SELL」が差金決済規制系の制約に当たった可能性が高い。
- 対策済み: `gateway` は live/day BUY に対して、当日 `trades_live` に同一銘柄 SELL がある場合 `same_day_reentry_after_sell` で reject する。さらに `LIVE_DAY_NEW_BUY_CUTOFF_TIME=14:30` 以降の live/day BUY は `late_live_buy` で reject する。
- closeout 並列化に伴い `system_status.daily_pnl` の read-modify-write が競合しうるため、`oms-live` の closeout は各銘柄 worker では PnL 加算せず、全 closeout 約定の `realized_pnl` を合算して最後に 1 回だけ `add_realized_pnl` するよう修正した。通常注文経路は従来どおり 1 約定ごとに加算。
- production 反映済み: `gateway` / `oms-live` を `op run --env-file infra/env.production -- docker compose --env-file infra/env.production -f infra/docker-compose.prod.yml up -d --build --no-deps gateway oms-live` で rebuild/recreate 済み。`gateway` は `live_day_new_buy_cutoff_time=14:30`、`oms-live` は `closeout_order_fill_timeout_seconds=2400.0` をコンテナ内で確認済み。
- 実行テスト: `uv run pytest services/gateway/tests/unit/test_stream_runner.py services/oms-live/tests/unit/test_live_stream_runner.py` は `53 passed`。`uv run ruff check ...` は `All checks passed`。

### 2026-05-26 pre-open check notes

- Universe Scanner は本来 `systemd --user` timer の `roboinvest-universe-scanner.timer` が `07:55 JST` に起動する。寄り前チェックで最初に手動実行するのではなく、まず `systemctl --user status roboinvest-universe-scanner.timer --no-pager` / `systemctl --user list-timers roboinvest-universe-scanner.timer --all --no-pager` / `journalctl --user -u roboinvest-universe-scanner.service -n 100 --no-pager` で前回/次回/当日実行状況を見る。
- 2026-05-26 は `07:46 JST` 頃に寄り前チェック側で `bash scripts/run-production-universe-scanner.sh` を手動実行してしまった。これは自動起動失敗ではなく、`07:55 JST` の定時発火を待たずに先行実行したもの。手動実行は成功し、`done: valid_date=2026-05-26 watchlist_size=30`、Supabase health OK、`feeder` は watchlist 30 件を拾った。
- その後 `roboinvest-universe-scanner.timer` は予定通り `2026-05-26 07:55:08 JST` に発火し、同じ scanner が定時実行としてもう一度走った。手動実行は `07:51:09 JST` に完了しており、`07:55:08 JST` の timer 実行とは同時起動ではなかった。`/tmp/roboinvest-universe-scanner.lock` は同時起動防止であり、完了後の同日2回目実行は防がない。今後、07:55 前に寄り前チェックを始める場合は timer を待つか、明示的に timer を止める判断をしてから手動実行する。
- 手動実行時の副作用として、batch 後の post 処理で `OMS_LIVE_ALLOWED_SYMBOLS` が当日 watchlist 30 銘柄へ同期され、稼働中の `oms-live` が recreate された。現在の production env は `TRADE_MODE=live` / `OMS_LIVE_DRY_RUN=false` なので、paper 手順として扱わないこと。
- 手元の sandbox では `bash scripts/run-production-universe-scanner.sh` が `bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted` で失敗したため、実運用コマンドは sandbox 外で再実行した。寄り前の systemd/journal 確認も sandbox 制限で失敗することがあるため、その場合は権限付きで再確認する。
- Pub/Sub check-only は `op run --env-file infra/env.production -- uv run python scripts/gcp-pubsub-admin.py --check` ではなく、`--project-id "$PUBSUB_PROJECT_ID"` が必要。またローカル host から実行する場合、`GOOGLE_APPLICATION_CREDENTIALS=/run/secrets/gcp-pubsub-sa.json` はコンテナ内パスなので失敗する。`GOOGLE_APPLICATION_CREDENTIALS="$GOOGLE_APPLICATION_CREDENTIALS_HOST_PATH"` に差し替えて実行すること。
- `feeder` は watchlist 30 件取得後に一度 kabu `/register` が `401 Unauthorized` になったが、token invalidate -> `/token` 200 -> `/unregister/all` 200 -> 次回 poll で `/register` 200 OK まで復帰した。再接続サイクルは watchlist poll interval 60 秒を待つため、401 直後に即座に register 成功ログが出ない。

### 2026-05-23 JST Weekend Preflight / Purge Done

- **インフラ/DB疎通確認**: `docker compose config` / Supabase tables (`health-check.py`) / GCP Pub/Sub smoke test (`gcp-pubsub-admin.py --smoke-test`) はすべて正常（`RESULT OK`）。
- **Pub/Sub バックログのパージ**: 旧コードの `RULE has_price=False` などの残存スタールシグナルを一掃するため、`scripts/seek-subscriptions.py` を新規作成・実行し、全サブスクリプションを現在のタイムスタンプまでシークした。これにより月曜朝はバックログなしでクリーンに開始可能。
- **課題3（kabu Code 5）の整理**: `ExpireDay: 0`（当日中注文）の送信が引け後（14:50以降）に行われていたことが原因。金曜夕方に追加した gateway の 14:50 ポストクローズガードにより、以降は時間外の publish が発生しないため解決済み。
- **課題4（gateway concurrent map writes）の整理**: Go製の `docker compose logs` 側が出力した Go runtime panic であり、Python製の `gateway` サービスそのものの不具合やクラッシュではないことを確認。


### 2026-05-21 Night Preflight

- `2026-05-21` 夜に明日用の前準備を実施済み。`docker compose -f infra/docker-compose.prod.yml config` は通過、`health-check.py --check supabase --timeout 30` も `system_status` / `positions` / `strategy_logs` / `aggregator_logs` / `trades_live` / `trades_paper` / `watchlist` / `master_stocks` / `daily_ohlcv` まで `OK` を確認した。
- production image の事前 build は完了。少なくとも `feeder` / `feature-engine` / `strategy-rule` / `strategy-ai` / `aggregator` / `gateway` / `oms-paper` / `oms-live` は `trade-ai-prod-*` イメージとして build 済み。次回起動時の `up -d --build` はキャッシュが効く見込み。
- 新しい AI gating 経路用の managed Pub/Sub resource も作成済み。`strategy-ai-triggers` topic と `strategy-ai-rule-signals` subscription を `scripts/gcp-pubsub-admin.py --apply` で追加した。
- その後の managed Pub/Sub smoke test も `RESULT OK`。`strategy-ai-triggers` / `strategy-ai-rule-signals` を含む production topics/subscriptions と `adr-0001-smoke-test` / `adr-0001-smoke-test-sub` の publish/pull/ack を確認済み。
- 次回営業日 (`2026-05-25 月曜日`) に残るのは、基本的に `universe-scanner` 実行、Supabase 状態再確認、services 起動、寄り付き後の live ログ観測だけ。

### 2026-05-20 08:55 JST 市場オープン中テスト再開メモ

- 現在は `2026-05-20 08:55 JST` の寄り付き前。次セッションはオープン中の paper production test を優先する。
- Universe Scanner 自動化は実質完了。`roboinvest-universe-scanner.timer` は enabled/active、`loginctl show-user hiroyuki --property=Linger` は `yes`、`systemctl --user start roboinvest-universe-scanner.service` は `status=0/SUCCESS` まで確認済み。残る未観測は 07:55 JST の定時発火 1 回だけ。
- 市場オープン中テストは `docs/runbook/paper-open-checklist.md` の Step 4 以降をなぞる。すでに watchlist / daily_ohlcv は生成済みなので、次は `docker compose -f infra/docker-compose.prod.yml up -d --build` で常駐 services を起動し、`health-check.py --check supabase services` を確認する。
- 起動後は `feeder` の kabu WebSocket 接続、`raw-market-data` 流入、`feature-engine -> strategy-rule / strategy-ai -> aggregator -> gateway -> oms-paper` の流れ、`gateway` reject の偏りを重点監視する。
- ログ確認コマンド: `op run --env-file infra/env.production -- docker compose -f infra/docker-compose.prod.yml logs --tail=100 feeder feature-engine strategy-rule strategy-ai aggregator gateway oms-paper`
- 可能なら本日 `2026-05-20` の市場時間中に、paper 構成で寄り付き後の 1 セッション観測を完了させる。次の大きい未完了は production compose / Cloud Supabase 構成での `14:50` closeout 実地確認。

### 2026-05-19 J-Quants paid cutover メモ

- `infra/env.production` を J-Quants API v2 の `JQUANTS_API_KEY` 前提に修正し、`op run --env-file infra/env.production -- docker compose -f infra/docker-compose.prod.yml --profile batch config` が通ることを確認済み。
- `universe-scanner` は `--date` 未指定時に `None` を流して落ちていたため、CLI で JST 当日を補う修正を追加。unit test 追加済み。
- `daily_ohlcv` の一括 PostgREST upsert は `httpx.ReadTimeout` になったため、`SupabaseWriter` を `1000` 件 chunk upsert に変更。unit test 追加済み。
- `2026-05-19` の batch 実行は完走し、`master_stocks=4444`、`watchlist_size=30`、`valid_date=2026-05-19` を確認済み。`daily_ohlcv` も chunk upsert で Cloud Supabase へ反映済み。
- 日次起動方式は「LAN host の systemd user timer `roboinvest-universe-scanner.timer` から 07:55 JST に実行」に決定。runbook: `docs/runbook/adr-0001-universe-scanner-automation.md`。
- `scripts/install-universe-scanner-timer.sh` で timer を有効化し、`loginctl enable-linger hiroyuki` も適用済み。`systemctl --user start roboinvest-universe-scanner.service` は成功し、残る未観測は 07:55 JST の定時発火 1 回だけ。

### 2026-05-18 セッションメモ

- OMS Live Phase 3 本番 28080 の 9432 / 100 株 round-trip は市場時間中に完了。`docs/runbook/oms-live-phase3.md` に詳細を記録済み。kabu 保有は 9432 / 2000 株、未約定注文なし。
- `OMS_LIVE_DRY_RUN=true` が Phase 3 e2e で無視されるバグを修正済み。PR #49 `Fix OMS live Phase 3 dry run` は merge 済み、main CI 緑。
- Dashboard Auth/RLS は PR #50 として main に merge 済み。merge commit は `4df75a5`。PR: https://github.com/hiro88hyo/roboinvest/pull/50
- Preview 検証では OAuth redirect と admin RLS まで確認済み。anon role は `system_status` SELECT 拒否、`dashboard_admins` 登録済み authenticated user は `system_status` SELECT / UPDATE 可。
- `contracts/sql/012_dashboard_auth_rls.sql` は実装済み。本番 DB への適用前に、Vercel / Supabase Auth provider / admin user / Deployment Protection の本番反映順を再確認すること。
- 2026-05-21 夜に Vercel URL を再確認し、candidate production URL `https://project-wh73t.vercel.app/` は未ログインで `/login?next=%2F` へ `307` redirect、preview URL は `401` で保護されることを確認した。さらに production Supabase でも anon key の `system_status` SELECT は `401 permission denied` を確認。ADR-0001 checklist の「Dashboard production URL を一般公開のまま live に進めない」は完了扱い。
- `cd dashboard && npm run lint`、`npm run typecheck`、`npm test`、CI、Vercel Preview は pass。

| 優先度 | タスク | 備考 |
|---|---|---|
| 高 | **ADR-0001 実装** | GCP Pub/Sub / Supabase Cloud Pro / Vercel Hobby / self-hosted runner / 1Password CLI。月額 ~$30 |
| 高 | **Universe Scanner 日次自動化** | systemd timer / runbook / service-path fix まで完了。残りは初回 07:55 JST 発火確認 |
| 中 | **24/7 運用整備** | プロセス監視 / ログ集約 / アラート / バックアップ。未決事項は `docs/runbook/adr-0001-operations-requirements.md` |
| 低 | **Feeder Book ゼロ再現の原因追及** | register API 仕様 / 別 endpoint 要調査 |
| 低 | **Phase 3 残課題** | OrderId 冪等性のハードニング（fail-fast 化済、`docs/runbook/oms-live-phase3.md` の手動回復節を参照） |

---

## 7. 設計上の重要原則

1. **contracts/ を Single Source of Truth とする**。Pydantic 変更 → SQL 更新 → `gen-supabase-types.sh` で TS 再生成。手動編集禁止。
2. **サービス間の直接通信を禁止**。すべて Pub/Sub 経由。
3. **Gateway がリスクルールを単独で執行する**。他コンポーネントでは判断しない。
4. **OMS Live は本番資金に直結する**。変更は最小限。必ず OMS Paper で先行検証。
5. **純関数とストリーミング層を分離**（各サービスの Phase 1 → Phase 3 の構成）。

---

## 8. 困ったときの参照先

- 進捗のスナップショット: `~/.claude/projects/-home-hiroyuki-workspaces-roboinvest/memory/project_status.md`（Claude Code の auto memory）
- Phase 3 本番走行の知見: `~/.claude/projects/.../memory/oms_live_phase3_findings.md`
- Feeder 本番接続の前提: `~/.claude/projects/.../memory/feeder_production_constraints.md`
- ポジション整合: `~/.claude/projects/.../memory/positions_integrity.md`
- 起動チェックリスト: `~/.claude/projects/.../memory/startup_checklist.md`
- 各サービスの責務: `services/<name>/CLAUDE.md`
- 運用 runbook: [docs/runbook/oms-live-phase3.md](../runbook/oms-live-phase3.md)

memory ファイルは個人ホームディレクトリにあるため別 AI からは読めない場合がある。重要事項はこのファイルに転記する方針。

### 2026-05-26 CI / integration-test handoff

- PR #62 は merge 済み。目的は GitHub Actions の integration pytest に `--durations=20` を追加し、遅い integration tests を CI log に露出すること。対象は `.github/workflows/ci.yml`。
- PR #63 は merge 済み。目的は stream-runner integration tests の無駄な idle wait を短縮すること。対象は `services/gateway/tests/integration/test_stream_runner_e2e.py`、`services/aggregator/tests/integration/test_stream_runner_e2e.py`、`services/oms-paper/tests/integration/test_paper_stream_runner_e2e.py`。
- PR #63 の実装は `TEST_PUBSUB_TIMEOUT_SECONDS = 2.0` を追加し、`PubSubSubscriber(...)` にだけ `timeout_seconds=TEST_PUBSUB_TIMEOUT_SECONDS` を渡した。`PubSubPublisher(...)` は変更していない。
- 背景: `contracts/python/trade_contracts/pubsub_client.py` の `PubSubSubscriber.timeout_seconds` default は `60.0`。該当 integration tests では片側の Pub/Sub が意図的に空のケースがあり、`return_immediately=True` でも emulator/client 側で subscriber timeout 近く待つ可能性があった。
- PR #62 時点の CI baseline: integration job total `11m14s`、pytest は `17 passed, 4 skipped, 919 deselected in 423.71s (0:07:03)`。setup overhead は主に Supabase/local stack で約 4 分。
- PR #62 時点の遅い tests: `test_daily_loss_limit_flips_is_trading_allowed` が `118.30s`、gateway / oms-paper / aggregator の複数 integration tests が約 `59s`。
- PR #63 の local verification: `make lint-all` passed。`uv run pytest -m "not integration" services/gateway/tests services/aggregator/tests services/oms-paper/tests` は `352 passed, 9 deselected`。
- PR #63 の integration runtime 改善は GitHub Actions 復旧後に確認済み。`3831580` push の CI run `26449550245` は success、integration job total は `4m12s`。PR #62 baseline `11m14s` から大幅短縮した。local integration environment は未設定のため、local では対象 tests は `PUBSUB_PROJECT_ID not set` で skip する。
- GitHub Actions は PR #63 作成時点で不安定だった。`pull_request` trigger、追加 push、close/reopen、main merge 後の run が見えず、`workflow_dispatch` も `HTTP 500: Failed to run workflow dispatch` で失敗した。Vercel checks は動作、repository Actions setting は enabled、CI workflow state は active だったため、当時は GitHub 側 outage / instability の可能性が高い。workflow file の破損と断定しないこと。
- 一時的に `ci: trigger checks` という empty commit を作ったが、最終 merge 前に削除済み。main に empty commit は残っていない。
- GitHub Actions は復旧確認済み。`3831580` push で CI run `26449550245` が作成され、Python `3m36s`、Dashboard `33s`、Integration `4m12s`、total `4m17s` で success。
- 重い integration checks の PR CI 分離は実装済み。`.github/workflows/ci.yml` の Python pytest は `-m "not integration"` を明示し、`e2e` job は `if: github.event_name != 'pull_request'` にした。PR では軽量 checks、main push / workflow_dispatch では full integration を走らせる。
- Dependabot 残 PR は片付け済み。#46、#59、#60 を merge し、open PR は 0 件。最終 main CI run `26449902531` は success、Python `3m38s`、Dashboard `39s`、Integration `4m20s`。
- 次セッションで間違えてはいけないこと: PR #63 は merged、integration speedup は GitHub Actions 上で確認済み、`PubSubPublisher` timeout は変更していない、GitHub Actions failure の原因は repo config と断定しない、empty commit は main に残っていない、feature branch 上の作業中ではない。
- 安全な初手コマンド: `git status --short --branch`、`git log --oneline -5`、`gh pr view 63 --json state,mergedAt,mergeCommit,url`。


### 2026-05-27 pre-open / live test checklist

目的: 2026-05-26 の live close / follow-up fixes が翌営業日の実運用で効くことを確認する。特に `3907 x100` 持ち越し、`same_day_reentry_after_sell`、`late_live_buy`、`kabu Code 21`、14:50 closeout を重点監視する。

寄り前に確認すること:

- Local / GitHub state: `git status --short --branch` が `main...origin/main` であること。open PR は 0 件。最新 main CI は run `26449902531` success。
- CI workflow: PR CI は integration を除外済み。full integration は main push / `workflow_dispatch` で走る。明日の運用前に CI 修正作業を追加で行う必要はない。
- Universe Scanner: 07:55 JST 前に手動実行しない。まず `systemctl --user status roboinvest-universe-scanner.timer --no-pager`、`systemctl --user list-timers roboinvest-universe-scanner.timer --all --no-pager`、`journalctl --user -u roboinvest-universe-scanner.service -n 100 --no-pager` で前回 / 次回 / 当日実行状況を見る。
- Scanner 結果: 当日 `watchlist` が 30 件前後あること、`daily_ohlcv` が空でないこと、`feeder` が当日 watchlist を拾っていることを確認する。
- Production env: `TRADE_MODE=live`、`OMS_LIVE_DRY_RUN=false`、`KABU_DEFAULT_EXCHANGE=9`、`LIVE_DAY_NEW_BUY_CUTOFF_TIME=14:30`、`CLOSEOUT_ORDER_FILL_TIMEOUT_SECONDS=2400`、`OMS_LIVE_ALLOWED_SYMBOLS` が当日 watchlist と整合することを確認する。
- Credentials: GCP service account は tmpfs `/dev/shm/roboinvest/gcp-pubsub-sa.json` 前提。host から `gcp-pubsub-admin.py` を実行する場合は `GOOGLE_APPLICATION_CREDENTIALS=/run/secrets/...` ではなく `GOOGLE_APPLICATION_CREDENTIALS_HOST_PATH` 側を使う。
- Production compose: `op run --env-file infra/env.production -- docker compose --env-file infra/env.production -f infra/docker-compose.prod.yml ...` 経由で操作する。`op run` なしで起動すると `op://...` が展開されず失敗する。
- Health check: Supabase は `uv run python scripts/health-check.py --check supabase --timeout 30`。managed Pub/Sub は runtime SA の最小権限で `scripts/gcp-pubsub-admin.py --check` / smoke を確認する。
- Kabu token: `feeder` / `oms-live` / probe が同時に `/token` を再発行しないようにする。Strong GO 的な one-shot 実発注を再実施する場合は `feeder` と probe を止める。

寄り付き後に監視すること:

- `3907 x100` の実ポジション、kabu board、Supabase `positions(live)` が一致していること。寄らず / 差金決済制約 / closeout 可否を明示的に見る。
- `gateway` が live `BUY qty>100` を `oms-live` に流していないこと。`OMS_LIVE_MAX_QTY_PER_ORDER=100` reject が減っていること。
- `kabu Code 21: 可能額が不足しております` が減っていること。発生した場合は銘柄、時刻、直前の同日 SELL / BUY、positions、注文 payload を残す。
- 同日 SELL 後の同一銘柄 live/day BUY が `same_day_reentry_after_sell` で reject されること。
- 14:30 JST 以降の live/day BUY が `late_live_buy` で reject されること。
- 14:50 JST 以降の live signal が `market_closed` で fail-close し、live order publish が発生しないこと。
- `strategy-rule` / `strategy-ai` / `aggregator` / `gateway` の流れが詰まっていないこと。reject が `missing_entry_price` に偏っていないこと。

14:50 closeout で確認すること:

- `oms-live` closeout が発火すること。
- closeout 注文が銘柄ごとに並列処理されること。
- closeout 注文だけ `CLOSEOUT_ORDER_FILL_TIMEOUT_SECONDS=2400` で待つこと。通常注文の `ORDER_FILL_TIMEOUT_SECONDS=30` は維持。
- closeout 後に `positions(live)` が残らないこと。残った場合は kabu `/positions` と `scripts/reconcile-positions.py --dry-run` で stale row か実建玉かを切り分ける。
- closeout の realized PnL は worker ごとではなく合算後 1 回だけ `system_status.daily_pnl` に加算されること。

引け後に残すメモ:

- `trades_live` 件数、`system_status.daily_pnl`、`positions(live)` 残有無。
- `3907` の処理結果。SELL 成功 / 失敗、Code 21 有無、持ち越し継続有無。
- reject 理由の分布: `same_day_reentry_after_sell`、`late_live_buy`、`market_closed`、`missing_entry_price`、`already_long`、`no_position_for_sell`。
- kabu エラーの有無: Code 21、Code 5、Code 8、401 / 403 token retry。
- closeout 成否と未約定 / cancel / RecType details。
- `scripts/reconcile-positions.py --dry-run` の結果。必要なら内容確認後に `--apply`。
- 次営業日の TODO と残課題をこの `docs/HANDOFF.md` に追記する。

### 2026-05-28 live close / fix queue

本日の live 運用は `system_status.daily_pnl=11050.0`、`trades_live=33` で終了。ただし `5031` が売れず持ち越しになった。Supabase 表示では `5031 x100 @791 / current=630 / unrealized=-16100` だが、引け後の kabu 照合では実残が `5031 x200 @791`、Supabase は `x100` で quantity mismatch。評価損益込みは Supabase 表示ベースで `-5050`、kabu 実残ベースではさらに `-16100` 悪化する可能性がある。

5031 closeout の事実関係:

- closeout は `2026-05-28 14:50:00 JST` に発火し、注文 `20260528A02N70384146` を送信した。
- 注文詳細は `Symbol=5031`、`Side=1`、`Exchange=9`、`Price=0`、`Details RecType=1/4` まで進み、約定 detail `RecType=8` は無し。
- OMS は closeout timeout `2400s` で `15:30:01 JST` まで poll したが `CumQty=0` のまま timeout。その後 cancelorder は HTTP 200。
- 引け後の注文詳細は `State=5`、`OrderState=5`、`CumQty=0`、`Details` に `RecType=6` があり、最終的に約定なしで終端。
- 5031 board は `CurrentPrice=630`、`PreviousClose=780`、`OpeningPrice=640`、`LowPrice=630`、`TradingVolume=1371300`。`Sell1=630 x 67900`、買い側は実質 0 と見え、ストップ安で買いがなく成行 SELL が約定しなかった可能性が高い。

次セッションから順番に直すこと:

1. **5031 の数量不一致を最優先で補正する**
   - kabu 実残: `5031 x200 @791`
   - Supabase `positions(live)`: `5031 x100 @791`
   - `scripts/reconcile-positions.py --apply` は quantity mismatch を自動修正しない。手動 SQL/RPC で `positions.quantity=200`、評価損益も実残ベースに合わせる。
   - 補正前に kabu GUI または `/positions` で `5031 x200` と未約定注文なしを再確認する。

2. **closeout 後に live positions が残った場合の強いログを追加する**
   - `run_closeout()` 完了後に Supabase `positions(live)` と kabu `/positions` の残を確認し、残があれば `CRITICAL` ログを出す。
   - 残ポジが stale row か実建玉かをログで区別できるようにする。
   - Slack / メール / Dashboard への明示通知は将来機能扱いで、この修正範囲には含めない。

3. **closeout 注文詳細の構造化ログを増やす**
   - 現状ログは HTTP 200 と timeout だけで、`/orders` 本文が残らない。
   - closeout poll の終端時と timeout 時に `symbol`、`order_id`、`State`、`OrderState`、`OrderQty`、`CumQty`、`Side`、`Price`、`Details[].RecType`、`Details[].Qty`、`Details[].TransactTime` を要約ログに残す。
   - これにより「拒否」「市場に出たが未約定」「失効/取消」「部分約定」を後から API probe なしで判断できるようにする。

4. **closeout 数量の信頼元を見直す**
   - 現状 closeout は Supabase `positions(live)` を元に注文を作るため、今日のような mismatch では `x100` しか売りに行けない。
   - closeout 直前に kabu `/positions` と Supabase を突合し、quantity mismatch があれば警告を出す。
   - 安全側の案: closeout では kabu 実残数量を優先して売却対象にする。ただし Supabase 書き込みとの整合、個人手動保有の巻き込み、holding_type の扱いを先に設計する。

5. **reconciler の mismatch 修復手段を用意する**
   - 現行 `reconcile-positions.py --apply` は `to_import` だけを取り込み、quantity mismatch は warning のみ。
   - 運用用に `--fix-quantity-mismatch` のような明示 opt-in、または専用 SQL/RPC を用意する。
   - 自動補正は危険なので、対象 symbol と kabu/Supabase 差分を表示し、明示オプションなしでは実行しない。

6. **5031 持ち越しの翌営業日 pre-open チェックを追加する**
   - 明朝は通常の watchlist より先に `5031 x200` の実残、未約定注文、板状態、Supabase 補正済みかを確認する。
   - `5031` が再びストップ安気配なら、自動 closeout だけに任せず手動判断もできるようにする。
   - `same_day_reentry_after_sell` / `late_live_buy` / `market_closed` guard は今日も効いていたので、明日は持ち越し処理と数量整合を重点監視する。


#### 2026-05-28 20:33 UTC follow-up

- `scripts/reconcile-positions.py` に `--fix-quantity-mismatch` を追加したが、production dry-run 時点では補正は実行していない。
- `op run --env-file infra/env.production -- uv run python scripts/reconcile-positions.py --log-level INFO` の結果、kabu / Supabase ともに `5031` は一致していた。
- 明細確認では kabu `5031 qty=100 avg=791.0 current=630.0 pnl=-16100.0`、Supabase `5031 qty=100 entry=791.0 holding=day`。handoff 上の `x200` mismatch は現時点では再現しないため、`--fix-quantity-mismatch --symbol 5031` は打たないこと。
- OMS Live closeout は、発注前 `precheck` と完了後 `postcheck` の両方で kabu / Supabase positions drift を `CRITICAL` ログに出すよう修正済み。closeout 数量そのものは引き続き Supabase `positions(live)` をソースにし、kabu 実残への自動切替は未実装。
- production `oms-live` は `op run --env-file infra/env.production -- docker compose --env-file infra/env.production -f infra/docker-compose.prod.yml up -d --build --no-deps oms-live` で rebuild/recreate 済み。コンテナ内設定は `OMS_LIVE_DRY_RUN=false`、`CLOSEOUT_ORDER_FILL_TIMEOUT_SECONDS=2400.0`、`ORDER_FILL_TIMEOUT_SECONDS=30.0`、`KABU_DEFAULT_EXCHANGE=9`、allowed symbols 30 件。直近ログは scheduler 待機のみで起動エラーなし。
- `docs/runbook/live-go-checklist.md` に「carry position がある翌営業日の pre-open procedure」を追加済み。通知系（Slack / メール / Dashboard 明示表示）は将来機能扱いで、この修正範囲には含めない。
- closeout 数量ソース方針は fail-close に決定。OMS Live は closeout precheck で kabu / Supabase position drift を検出した場合、sendorder 前に `skipped_reason=position_drift` で closeout を止める。kabu 実残数量への自動切替は実装しない。
- production `oms-live` は fail-close 版も rebuild/recreate 済み。直近ログは scheduler 待機のみで起動エラーなし。

### 2026-05-29 live close / May 2026 performance review

本日の運用終了後に、5月の運用成績（LiveおよびPaper）の振り返りを詳細に実施し、重大な設定バグの特定といくつかの取引ルール改善点をドキュメント化して引き継ぎメモに残した。

#### 1. 運用成績サマリー
- **Live Trading (5/21〜5/29)**: 合計損益 **+46,766円**（123回取引、勝率 50.41%、PF 1.34、最大ドローダウン 69,230円）。全体としては利益着地したが、本日5月29日は朝一の連敗と持ち越し決済により **-45,540円** の大幅マイナス。
- **Paper Trading (5/19〜5/21)**: 合計損益 **+68,100円**（192回取引、勝率 36.98%、PF 1.12）。特大のCONSENSUS取引が牽引。

#### 2. 重大バグの特定：AI戦略の沈黙（Silent AI Bug）
- **現象**: 5/21以降、Live TradingにおいてAI（LLM）のシグナルによる取引が実質的に0件だった。
- **原因**: 稼働中の `gemini-2.5-flash` などの思考型モデルが、出力の前に内部の「思考（Thinking Tokens）」を約240〜255トークン生成する。しかし、`AI_MAX_OUTPUT_TOKENS` が `256` に制限されているため、思考トークンだけで枠を使い切り、JSONが出力される前に `MAX_TOKENS`（強制打ち切り）に達してパースエラーとなっていた。
- **対策（検証済み）**: `infra/env.production` の `AI_MAX_OUTPUT_TOKENS` を `2048` に拡張することで、正常にJSONが生成されることを確認した。

#### 3. 取引データから得られた改善プラン
- **寄り付き制限（09:00〜09:15）の導入**: 5/29の損失の大部分は、09:00〜09:05の寄り付き直後の急変動時のエントリーによるもの。寄り付き後15分間は新規買いシグナルを `gateway` で遮断することを推奨。
- **保有時間制限（タイムアウト決済）**: 15分以内の決済が利益の64%以上を稼ぐ一方、60分を超えるポジションは勝率が 41.7% まで低下。45分前後での時間切れ成行決済の導入を推奨。
- **持ち越し（Carryover）リスク**: 5031 の持ち越し事故（2日保有して本日売却、-17,600円）のようなリスクを防止するため、大引けクローズアウトの堅牢化とアラート機能、差金決済規制（同一銘柄の当日再購入ロック）の徹底が必要。

これらの詳細は、アーティファクト `brain/f2b491b0-d9b8-43c8-bf17-0873c80e7b52/investment_review_may_2026.md` に記載している。

### 2026-05-31 production pre-open automation / kabu recovery

休日中に明営業日の寄り前確認を短縮するため、production pre-open check をスクリプト化した。

- PR #66: `AI_MAX_OUTPUT_TOKENS=2048` と live/day 新規 BUY guard `09:15 JST` を main へ反映済み。
- PR #67: Aggregator source 別 threshold (`RULE_ONLY=0.5`, `AI_ONLY=0.5`, `CONSENSUS=0.3`) を main へ反映済み。
- production secret env `infra/env.production` はローカルで `LIVE_DAY_NEW_BUY_START_TIME=09:15` と source 別 threshold を更新済み。
- `strategy-ai` / `aggregator` / `gateway` は production compose で rebuild/recreate 済み。コンテナ内 env は期待値どおり。
- PR #68: `scripts/production-preopen-check.py` を追加し、`docs/runbook/live-go-checklist.md` に one-command pre-open check を記載した。
- PR #69: feeder の古い 502/401 ログに引っ張られず、最新の kabu 関連ログで復帰判定するように修正した。

実行確認:

```bash
set -a && . infra/.op.service-account.env && set +a
op run --env-file infra/env.production -- \
  uv run python scripts/production-preopen-check.py --timeout 30
```

結果:

- kabu station 起動前は `--kabu-offline` で `OK 58 / WARN 2 / NG 0`。WARN は feeder restart と kabu 502 のみ。
- kabu station 起動直後は feeder が `HTTP 502`、続いて `401 APIキー不一致`。古い shared token cache が残っていた可能性が高い。
- `/var/lib/kabu/token_cache.json` を削除し、`feeder` を restart。続いて `oms-live` も stale token を持たないよう restart。
- 復帰後は `feeder` が `POST /token 200`、`PUT /unregister/all 200` を記録。
- 最終 pre-open check は `OK 60 / WARN 0 / NG 0`。`positions(live)` は空、managed Pub/Sub smoke publish/pull/ack も OK、Supabase 主要 9 tables も OK。

明朝の残タスク:

- kabu station / Windows proxy 起動後に `scripts/production-preopen-check.py --timeout 30` を再実行する。
- `watchlist` が当日分に更新された後、feeder の `/register 200` と raw market data 流入を確認する。
- 09:00-09:15 JST は `opening_live_buy` reject が出ることを確認する。
- 09:15 以降は AI signal 復旧、Aggregator threshold の効き方、`kabu Code 21` / token retry の有無を観測する。
