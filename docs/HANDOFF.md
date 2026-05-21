# Handoff Memo (for coding AIs)

最終更新: 2026-05-20 / HEAD: `main`

別のコーディング AI（Claude Code 別セッション / Cursor / Copilot 等）がこのリポジトリに着手するときに、最初に目を通すための引き継ぎメモ。詳細は本ファイルではなく各リンク先で確認すること。

---

## 1. このリポジトリは何か

日本国内現物株（auカブコム証券）向けの自律トレードシステム。ルールベース + AI（LLM）のハイブリッド戦略を、Pub/Sub で疎結合した 9 つの Python マイクロサービスで構成する。

- アーキテクチャ図・コンポーネント詳細: [CLAUDE.md](../CLAUDE.md)
- 本番デプロイ方針: [docs/adr/0001-deployment-architecture.md](adr/0001-deployment-architecture.md)
- 開発環境セットアップ: [docs/dev-setup.md](dev-setup.md)

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

1. ルート [CLAUDE.md](../CLAUDE.md) — アーキ図 / リポジトリ構成 / Pub/Sub トピック / Supabase テーブル / リスク管理ルール / コーディング規約
2. 該当サービスの `services/<name>/CLAUDE.md` — そのサービス特有の責務と設計メモ
3. [contracts/](../contracts/) — Pydantic / SQL / TS の **Single Source of Truth**。スキーマ変更は必ずここから
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
- **production compose の paper 始業手順**: [`docs/runbook/paper-open-checklist.md`](runbook/paper-open-checklist.md) を参照。Universe Scanner 手動実行 → watchlist 確認 → services 起動の順。
- **実売買 `GO` の判定基準**: [`docs/runbook/live-go-checklist.md`](runbook/live-go-checklist.md) を参照。`paper GO` / `Weak GO` / `Strong GO` を分けて潰す。

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
- 2026-05-21 夜に managed Pub/Sub の check-only は成功したが、`scripts/gcp-pubsub-admin.py --smoke-test --cleanup-smoke` は runtime SA で `PermissionDenied`。project / topics / subscriptions / API 自体は存在するが、smoke 用 publish/pull/cleanup に必要な IAM は未確認。

### Next Session TODO

- 最優先は寄り付き後の live ログ観測。`gateway` の数量抑制が効いて `qty>100` が `oms-live` まで流れないこと、`kabu Code 21` が減ることを確認する。
- managed Pub/Sub の IAM 整理は市場時間外でよい。`scripts/gcp-pubsub-admin.py --smoke-test --cleanup-smoke` が runtime SA で `PermissionDenied` になる理由を切り分け、必要権限を確定する。
- paper `14:50 closeout` の再観測は保留。ADR チェックリスト上の残件だが、live 運用の阻害条件ではない。

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
- 運用 runbook: [docs/runbook/oms-live-phase3.md](runbook/oms-live-phase3.md)

memory ファイルは個人ホームディレクトリにあるため別 AI からは読めない場合がある。重要事項はこのファイルに転記する方針。
