# Handoff Memo (for coding AIs)

最終更新: 2026-05-18 / HEAD: `1bdfb87` (PR #49 マージ後、Dashboard Auth/RLS PR #50 open)

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
| Universe Scanner 本番自動化 | ❌ J-Quants 有料化待ち、現状は手動 seed |
| 24/7 運用（監視・アラート・バックアップ） | ❌ 未整備 |

**要約**: 「ローカルで paper を回せる」段階までは完成。「本番運用」は ADR-0001 実装と J-Quants 有料移行の 2 本が次の山。

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

### 5.2 kabu.com API

- **kabuステーションは localhost 限定**（http.sys URL ACL の制約）。本番は Windows 上の Caddy リバプロ（28080/28081）、開発機からは SSH トンネル経由。WS の Host ヘッダー上書き禁止。
- **本番は SOR 必須（Exchange=9）**。`KABU_DEFAULT_EXCHANGE` のデフォルトは `9`（commit 885ed7b）。`1` だと `Code:100378` で reject。
- **検証 18081 は sendorder を黙殺する**。実発注検証は本番 28080 のみ。
- **Feeder と OMS Live の kabu トークンは共有ファイル経由**（`KABU_TOKEN_CACHE_FILE`、デフォルト `/tmp/kabu_token_cache.json`、PR #42）。`KABU_API_PASSWORD` を同じにしても奪い合いにならない。
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

### 2026-05-18 セッションメモ

- OMS Live Phase 3 本番 28080 の 9432 / 100 株 round-trip は市場時間中に完了。`docs/runbook/oms-live-phase3.md` に詳細を記録済み。kabu 保有は 9432 / 2000 株、未約定注文なし。
- `OMS_LIVE_DRY_RUN=true` が Phase 3 e2e で無視されるバグを修正済み。PR #49 `Fix OMS live Phase 3 dry run` は merge 済み、main CI 緑。
- Dashboard Auth/RLS 設計 docs は main に commit 済み (`1bdfb87`)。main CI 緑。
- Dashboard Auth/RLS 実装は branch `implement-dashboard-auth-rls` / commit `65527e2`。PR #50: https://github.com/hiro88hyo/roboinvest/pull/50
- PR #50 は CI 全 green。まだ merge しない方針で停止。本番 DB は変更していない。RLS SQL (`contracts/sql/012_dashboard_auth_rls.sql`) はローカル Supabase にのみ適用して検証済み。
- PR #50 のローカル検証: anon role は `system_status` SELECT 拒否、`dashboard_admins` 登録済み authenticated user は `system_status` SELECT / UPDATE 可。`cd dashboard && npm run lint`、`npm run typecheck`、`npm test` は pass。
- 次回は PR #50 のレビューから再開。merge 前に Vercel/Supabase Auth provider/admin user/Deployment Protection の本番適用順を再確認すること。

| 優先度 | タスク | 備考 |
|---|---|---|
| 高 | **ADR-0001 実装** | GCP Pub/Sub / Supabase Cloud Pro / Vercel Hobby / self-hosted runner / 1Password CLI。月額 ~$30 |
| 高 | **J-Quants 有料プラン移行** | 無料は 2026-02-17 までのデータ上限。移行後 Universe Scanner を本番自動化 |
| 中 | **24/7 運用整備** | プロセス監視 / ログ集約 / アラート / バックアップ |
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
