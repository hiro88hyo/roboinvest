# Trade AI Agent

日本国内の現物株（auカブコム証券）を対象とした自律型トレードシステム。
数値に強い「ルールベース」と文脈に強い「AI（LLM）ベース」のハイブリッド戦略を採用し、
完全な疎結合マイクロサービスアーキテクチャで構築する。

## 技術スタック

- **Backend**: Python 3.12+ (Polars, Pydantic v2, asyncio)
- **AI Inference**: Gemini, Claude, OpenAI, ローカルLLM
- **Frontend**: TypeScript, Next.js (App Router), Tailwind CSS
- **Infrastructure**: Docker Compose
- **Messaging**: Google Cloud Pub/Sub
- **Database/BaaS**: Supabase (PostgreSQL, Realtime, Auth)

## アーキテクチャ概要

```
J-Quants API
     │
     ▼
┌──────────────────┐
│ Universe Scanner │──▶ Supabase (watchlist)
│  (日次 8:00 JST) │          │
└──────────────────┘          │ watchlist 読取
kabu.com API (WebSocket)      │
    │                         ▼
    ▼               ┌──────────┐   raw-market-data   ┌──────────────────┐
                    │  Feeder  │──────────────────▶  │ Feature Engine    │
                    └──────────┘                     │ (Polars指標計算)   │
                                                     └──────┬───────────┘
                                                            │ processed-features
                                                  ┌─────────┴──────────┐
                                                  ▼                    ▼
                                         ┌────────────────┐  ┌─────────────────┐
                                         │ Strategy A     │  │ Strategy B      │
                                         │ (ルールベース)  │  │ (AI/vLLM推論)   │
                                         └───────┬────────┘  └────────┬────────┘
                                                 │ strategy-signals-a │ strategy-signals-b
                                                 └────────┬───────────┘
                                                          ▼
                                                 ┌────────────────┐
                                                 │  Aggregator    │
                                                 │  (合議制統合)   │
                                                 └───────┬────────┘
                                                         │ trade-signals
                                                         ▼
                                                 ┌────────────────┐
                                                 │   Gateway      │
                                                 │ (リスク検証)    │──▶ Supabase (状態読取)
                                                 └──┬──────────┬──┘
                                        live-orders │          │ paper-orders
                                                    ▼          ▼
                                              ┌─────────┐ ┌──────────┐◀─ raw-market-data
                                              │OMS Live │ │OMS Paper │   (OrderBookSnapshot)
                                              └────┬────┘ └─────┬────┘
                                                   │            │
                                                   ▼            ▼
                                                Supabase (約定記録)
                                                   │
                                                   ▼
                                             ┌───────────┐
                                             │ Dashboard │ (Realtime購読)
                                             └───────────┘
```

## リポジトリ構成

```
trade-ai-agent/
├── CLAUDE.md                    # 本ファイル
├── pyproject.toml               # uv ワークスペースルート
├── Makefile                     # lint-all, test-all, deploy
│
├── contracts/                   # ★ 共有データ契約 (Single Source of Truth)
│   ├── python/                  #   Pydanticモデル (trade-contracts パッケージ)
│   │   └── trade_contracts/
│   │       ├── market.py        #     TickData, OrderBookSnapshot
│   │       ├── features.py      #     ProcessedFeatures
│   │       ├── signal.py        #     StrategySignal, UnifiedTradeSignal (stop_loss_price を含む)
│   │       ├── order.py         #     OrderRequest, OrderResult
│   │       ├── risk.py          #     RiskCheck, KillSwitchState
│   │       └── enums.py         #     Side, SignalSource, OrderStatus
│   ├── typescript/              #   supabase gen types 出力先
│   │   └── src/generated/database.types.ts
│   └── sql/                     #   Supabaseマイグレーション
│       ├── 001_system_status.sql
│       ├── 002_positions.sql
│       ├── 003_strategy_logs.sql
│       ├── 004_aggregator_logs.sql
│       ├── 005_trades_live.sql
│       ├── 006_trades_paper.sql
│       ├── 007_watchlist.sql
│       ├── 008_master_stocks.sql
│       └── 009_daily_ohlcv.sql
│
├── services/
│   ├── universe-scanner/        # 0. Universe Scanner (日次バッチ)
│   ├── feeder/                  # 1. Market Data Provider
│   ├── feature-engine/          # 2. Feature Engineering
│   ├── strategy-rule/           # 3. Strategy Engine A (ルールベース)
│   ├── strategy-ai/             # 4. Strategy Engine B (AI推論)
│   ├── aggregator/              # 5. Signal Aggregator (合議制)
│   ├── gateway/                 # 6. Risk & Routing Gateway
│   ├── oms-live/                # 7a. OMS Live (実発注)
│   └── oms-paper/               # 7b. OMS Paper (擬似約定)
│
├── dashboard/                   # 8. Next.js ダッシュボード
├── infra/                       # Pub/Sub, Supabase
├── scripts/                     # deploy, gen-types, replay, health-check, warm-to-cold
└── docs/                        # architecture, runbook, dev-setup
```

各サービスは独自の `CLAUDE.md`, `pyproject.toml`, `Dockerfile`, `tests/` を持つ。

## コンポーネント詳細

### 0. Universe Scanner (`services/universe-scanner/`) — 日次バッチ
- 毎営業日 8:00 JST に起動し、当日の監視銘柄リストを生成
- **第1段階（静的フィルタ）**: J-Quants API から全上場銘柄を取得し、流動性・価格帯・市場区分で 100〜300 銘柄に絞り込み
- **第2段階（動的スコアリング）**: ボラティリティ・テクニカル条件・出来高急増・セクターモメンタムでスコアリングし 20〜50 銘柄に絞り込み
- 結果を Supabase `watchlist` テーブルに書き込み
- 参照データ（銘柄マスタ・日次 OHLCV・財務データ）は J-Quants API から取得し Supabase `master_stocks` / `daily_ohlcv` に保存

### 1. Feeder (`services/feeder/`)
- 起動時および `watchlist` 更新時に Supabase から監視銘柄リストを読み取り
- kabu.com API (WebSocket) で watchlist 銘柄のみを購読
- Tick データを `TickData`、板情報を `OrderBookSnapshot` に変換し Pub/Sub `raw-market-data` にパブリッシュ
- 接続断時は指数バックオフで自動再接続

### 2. Feature Engine (`services/feature-engine/`)
- `raw-market-data` をサブスクライブ（`TickData` および `OrderBookSnapshot` を処理）
- Polars で テクニカル指標（移動平均, RSI, VWAP, ボリンジャーバンド等）を計算
- 板情報スナップショットを `ProcessedFeatures` に統合し `processed-features` にパブリッシュ
- 生データを Parquet 形式で永続保存（3段階ストレージ階層）
  - **Hot**: リアルタイム〜当日はメモリ / Pub/Sub で処理
  - **Warm**: 直近 1〜3 ヶ月は間引き済み Parquet（`STORAGE_TICK_RESOLUTION` で集約レベル制御）
  - **Cold**: それ以降は OHLCV 1分足/5分足 Parquet にアーカイブ
- `STORAGE_TICK_RESOLUTION=raw|1s|1m|5m` 環境変数で Tick 保存粒度を制御
- `TickData` 受信のたびに Supabase `positions.current_price` と `unrealized_pnl` を更新
- 9:00 JST（市場開始）に `system_status.daily_pnl = 0` をリセット（`is_trading_allowed` は手動操作を尊重し変更しない）
- バックテストモード時は Supabase `daily_ohlcv` を入力データとして使用

### 3. Strategy Engine A - ルールベース (`services/strategy-rule/`)
- `processed-features` を受信
- テクニカル指標の閾値・クロスオーバー等に基づき数値的シグナルを生成
- `strategies/` ディレクトリにプラグイン形式で個別戦略を格納
- `StrategySignal` (source=RULE) を `strategy-signals-a` にパブリッシュ
- シグナルを Supabase `strategy_logs` に記録

### 4. Strategy Engine B - AI推論 (`services/strategy-ai/`)
- `processed-features` を受信（板情報スナップショットを含む）
- 板情報・直近の価格推移・テクニカル指標をプロンプトに変換し AI で推論
- LLM応答をパースし `StrategySignal` (source=AI) を `strategy-signals-b` にパブリッシュ
- シグナルを Supabase `strategy_logs` に記録（`reasoning` フィールドに推論根拠を保存）

### 5. Signal Aggregator (`services/aggregator/`)
- `strategy-signals-a` と `strategy-signals-b` を受信
- 合議制ロジックでシグナルを統合（重み付け、一致度評価）
- `UnifiedTradeSignal` を `trade-signals` にパブリッシュ
- 両エンジンが矛盾する場合のフォールバックルールを持つ
- 統合結果を Supabase `aggregator_logs` に記録

### 6. Risk & Routing Gateway (`services/gateway/`)
- `trade-signals` を受信
- Supabase から `system_status`(キルスイッチ・`trade_mode`), `positions`(現ポジション) を読み取り
- `daily_pnl <= -daily_loss_limit` の場合は `is_trading_allowed = false` に更新して注文を拒否
- リスクルール（2%ルール、日次損失上限）を検証
- 許容損失からロット数を計算・強制制限
- `system_status.trade_mode` に応じて `live-orders` or `paper-orders` にルーティング

### 7a. OMS Live (`services/oms-live/`)
- `live-orders` を受信
- kabu.com API へ実発注（成行/指値）
- 約定後に Supabase `trades_live` に記録、`positions`（trade_type=live）を更新、`system_status.daily_pnl` を加算
- `system_status.trading_style = day` の場合のみ 14:50 デイクローズアウト実行（全 live ポジションを成行で強制決済し、`positions` の live 行を削除）

### 7b. OMS Paper (`services/oms-paper/`)
- `paper-orders` および `raw-market-data`（`OrderBookSnapshot`）をサブスクライブ
- 受信した板情報を元に擬似約定ロジックで仮想的に約定
- 約定後に Supabase `trades_paper` に記録、`positions`（trade_type=paper）を更新
- `system_status.trading_style = day` の場合のみ 14:50 デイクローズアウト実行（`holding_type=day` の paper ポジションだけを仮想的に強制決済し、swing は保持）

### 8. Dashboard (`dashboard/`)
- Next.js (App Router) + Tailwind CSS
- Supabase Realtime で以下をリアルタイム表示:
  - 現在ポジション・損益
  - 約定履歴
  - Strategy A/B のシグナルログ
  - システムステータス（キルスイッチ操作含む）
- `contracts/typescript/src/generated/database.types.ts` をインポートして型安全に

## Pub/Sub トピック一覧

| トピック | Publisher | Subscriber | スキーマ |
|---|---|---|---|
| `raw-market-data` | feeder | feature-engine, oms-paper | `TickData \| OrderBookSnapshot` |
| `processed-features` | feature-engine | strategy-rule, strategy-ai | `ProcessedFeatures` |
| `strategy-signals-a` | strategy-rule | aggregator | `StrategySignal` |
| `strategy-signals-b` | strategy-ai | aggregator | `StrategySignal` |
| `trade-signals` | aggregator | gateway | `UnifiedTradeSignal` |
| `live-orders` | gateway | oms-live | `OrderRequest` |
| `paper-orders` | gateway | oms-paper | `OrderRequest` |

## Supabase テーブル設計

### `system_status`
システムの稼働状態を管理するシングルトンテーブル（`id=1` の固定行）。
- `id` (int PK, default 1)
- `is_trading_allowed` (bool): キルスイッチ。false で全注文停止
- `trade_mode` (text): `live` | `paper`。起動時に環境変数 `TRADE_MODE` で初期化し、以降は Dashboard から変更可
- `trading_style` (text): `day` | `swing`。デイクローズアウトの発動条件に使用
- `daily_pnl` (numeric): 当日の実取引（live）累計損益。キルスイッチ判定に使用
- `weekly_pnl` (numeric): 当週の実取引（live）累計損益
- `monthly_pnl` (numeric): 当月の実取引（live）累計損益
- `daily_loss_limit` (numeric): 日次最大許容損失額
- `weekly_loss_limit` (numeric): 週次最大許容損失額
- `monthly_loss_limit` (numeric): 月次最大許容損失額
- `updated_at` (timestamptz)

### `positions`
現在保有中のポジションを管理。
- `symbol` (text): 銘柄コード　┐ 複合 PK
- `trade_type` (text): `live` | `paper`　┘
- `side` (text): `LONG`
- `quantity` (int): 保有株数
- `entry_price` (numeric): 平均取得単価
- `current_price` (numeric): 最新価格
- `unrealized_pnl` (numeric): 評価損益
- `holding_type` (text): `day` | `swing`
- `target_price` (numeric): 利確目標価格
- `stop_loss_price` (numeric): 現在のストップロス価格（トレーリング更新あり）
- `max_hold_days` (int): 最大保有日数（スイング用）
- `scheduled_exit_date` (date): fixed-hold swing の予定決済日
- `trailing_stop_pct` (numeric): トレーリングストップ率（スイング用）
- `opened_at` (timestamptz)

### `trades_live`
実発注の約定履歴。
- `trade_id` (uuid PK)
- `symbol`, `side`, `quantity`, `price`
- `signal_source` (text): `RULE` | `AI` | `CONSENSUS`
- `unified_signal_id` (uuid FK → `aggregator_logs.signal_id`): 元の統合シグナルへの参照
- `executed_at` (timestamptz)

### `trades_paper`
擬似約定の履歴。
- `trade_id` (uuid PK)
- `order_id` (uuid nullable, non-null partial unique): closeout/monitor を含む
  OMS Paper fill の第一冪等性キー
- `symbol`, `side`, `quantity`, `price`
- `signal_source` (text): `RULE` | `AI` | `CONSENSUS`
- `unified_signal_id` (uuid nullable FK → `aggregator_logs.signal_id`): 元の統合シグナルへの参照。closeout/monitor fill は `NULL`
- `executed_at` (timestamptz)

OMS Paper の fill は `oms_paper_apply_fill` RPC だけで書き込み、対応する
`positions` 遷移と同一 transaction で確定する。monitor/closeout SELL と
`oms_paper_update_stop_loss` は `opened_at` を position generation として照合し、
trailing stop は DB transaction 内でも引き下げを拒否する。

### `aggregator_logs`
Aggregator が出力した統合シグナルのログ。約定テーブルの参照元。
- `signal_id` (uuid PK)
- `symbol`, `action`, `confidence`
- `signal_source` (text): `RULE` | `AI` | `CONSENSUS`
- `strategy_signal_id_a` (uuid FK → `strategy_logs.signal_id`): Strategy A の元シグナル（存在する場合）
- `strategy_signal_id_b` (uuid FK → `strategy_logs.signal_id`): Strategy B の元シグナル（存在する場合）
- `created_at` (timestamptz)

### `strategy_logs`
Strategy A/B の出力ログ。Dashboard での分析・振り返り用。
- `signal_id` (uuid PK)
- `source` (text): `RULE` | `AI`
- `symbol`, `action`, `confidence`
- `reasoning` (text): AI の推論根拠（Strategy B のみ）
- `created_at` (timestamptz)

### `watchlist`
Universe Scanner が生成した当日の監視銘柄リスト。
- `symbol` (text): 銘柄コード　┐ 複合 PK
- `valid_date` (date): 有効日　　┘
- `symbol_name` (text): 銘柄名
- `score` (numeric): スコアリング結果
- `selected_reasons` (jsonb): 選定理由（流動性・ボラティリティ等）
- `created_at` (timestamptz)

### `master_stocks`
銘柄マスタ。J-Quants API から日次更新。
- `symbol` (text PK): 銘柄コード
- `symbol_name` (text): 銘柄名
- `market_segment` (text): 市場区分（プライム/スタンダード等）
- `sector` (text): 業種
- `is_active` (bool): 上場中フラグ
- `updated_at` (timestamptz)

### `daily_ohlcv`
日次 OHLCV データ。J-Quants API から取得。バックテスト・Universe Scanner の入力に使用。
- `symbol` (text): 銘柄コード　┐ 複合 PK
- `date` (date): 日付　　　　　┘
- `open`, `high`, `low`, `close` (numeric)
- `volume` (bigint)
- `turnover` (numeric): 売買代金

## リスク管理・ビジネスルール

これらのルールは **Gateway が強制執行** する。他コンポーネントでは判断しない。

### キルスイッチ
- `system_status.daily_pnl`（live 取引のみ集計、損失は負の値）が `-daily_loss_limit` 以下になったら `is_trading_allowed = false`
- 同様に `weekly_pnl <= -weekly_loss_limit` または `monthly_pnl <= -monthly_loss_limit` でも発動
- `weekly_pnl` / `monthly_pnl` は OMS Live が約定のたびに加算。週初/月初に Feature Engine がリセット
- Dashboard から手動で ON/OFF 可能
- Gateway は毎回 Supabase を確認し、false なら全注文を即座に拒否
- `trade_mode=paper` 中は各 pnl が更新されないため、キルスイッチは自動発動しない（paper は資金リスクがないため意図的）

### 2%ルール（1トレードリスク制限）
- 1トレードの最大許容損失 = 総資金 × 2%
- Gateway の `lot_calculator.py` が `UnifiedTradeSignal.stop_loss_price`、または paper BUY の `stop_loss_pct` とエントリー価格から最大ロット数を算出
- absolute/relative stop がともに未設定の場合はデフォルトのスプレッド幅で代替計算
- スイングトレード時はオーバーナイトリスク（ギャップダウン）を考慮し、通常より保守的なポジションサイジングを適用
- シグナルのロット数がこれを超える場合は強制的に切り詰め

### デイ・クローズアウト
- `system_status.trading_style = day` の場合のみ 14:50 (JST) に day closeout を実行。OMS Paper は `holding_type=day` の建玉だけを決済し、swing を保持
- `trading_style = swing` の場合はクローズアウトを行わず、ポジションを翌日へ持ち越す

### スイングトレード管理
- `positions.stop_loss_price` を下回ったら OMS が成行で損切り
- `positions.target_price` に達したら OMS が成行で利確
- `positions.trailing_stop_pct` が設定されている場合、OMS が高値更新のたびにストップロスを切り上げ
- `positions.max_hold_days` を超過したポジションは OMS が翌営業日始値で強制決済

### Dry Run モード
- 環境変数 `TRADE_MODE=paper` で起動すると `system_status.trade_mode` を `paper` で初期化
- 以降は Gateway が `system_status.trade_mode` を参照し、全注文を OMS Paper にルーティング
- モード変更は Dashboard から随時可能（再起動不要）
- 本番 API を一切叩かずにシステム全体を検証可能
- Dashboard はモード表示を明示（誤認防止）

## 共有スキーマ運用ルール

`contracts/` は全サービスの Single Source of Truth である。

1. **スキーマ変更は contracts/ から始める**: Pydantic モデルを変更 → SQL を更新 → TypeScript 型を再生成
2. **contracts/ の変更時は全サービスの CI を実行**: 1つでも失敗したらマージしない
3. **後方互換を意識する**: フィールド追加は Optional にし、削除は非推奨期間を設ける
4. **TypeScript 型は自動生成**: `scripts/gen-supabase-types.sh` で Supabase CLI から生成、手動編集しない

## パッケージ依存関係

全 Python サービスは contracts をローカルパッケージとして参照する:

```toml
# 各サービスの pyproject.toml
[project]
dependencies = [
    "trade-contracts @ file://../../contracts/python",
]
```

Dashboard は contracts/typescript をインポート:

```json
// dashboard/tsconfig.json paths
{
  "paths": {
    "@contracts/*": ["../contracts/typescript/src/*"]
  }
}
```

全ゾーン間の通信は Pub/Sub 経由。直接のサービス間通信は禁止。

## コーディング規約

### Python
- パッケージ管理: `uv`（`pip` / `poetry` 直接呼び出し禁止）
  - 依存追加: `uv add <pkg>`
  - 実行: `uv run <cmd>`（グローバル環境を汚さない）
  - 同期: `uv sync`
- フォーマッター: `uv run ruff format`
- リンター: `uv run ruff check`
- 型チェック: `uv run mypy --strict`
- テスト: `uv run pytest` (カバレッジ目標 80%+)
- 非同期: すべてのサービスエントリポイントは `asyncio` ベース
- データ処理: pandas ではなく Polars を使用

### TypeScript (Dashboard)
- Node.js バージョン管理: `volta`（グローバル環境を汚さない）
  - `dashboard/package.json` の `volta` フィールドで Node/npm バージョンを固定
  - `volta run` 経由で実行するため、グローバルインストール不要
- パッケージ管理: `npm`（volta が管理するプロジェクトローカルの npm を使用）
- フォーマッター/リンター: Biome（`npx @biomejs/biome` ではなく `npm run lint`）
- テスト: vitest
- コンポーネント: React Server Components 優先、必要な箇所のみ `"use client"`

### 共通
- コミットメッセージ: Conventional Commits (`feat:`, `fix:`, `refactor:` 等)
- ブランチ戦略: `main` ← `feature/*`, `fix/*`
- PR 単位: 1コンポーネント or 1機能

### Push 前ゲート
- `git push` の前に必ず最終差分へ `make lint-all` を実行し、`ruff format --check .` / `ruff check .` / `mypy` / Dashboard lint がすべて通っていることを確認する。
- コードを変更したサービスは、push 前に対象 unit test を実行する。例: `uv run pytest services/oms-live/tests/unit services/oms-paper/tests/unit`。
- `contracts/`、共通基盤、複数サービス横断、Dashboard を変更した場合は、対象 test に加えて `make test-all` または該当する Dashboard test を実行する。
- formatter 適用後にコミットを追加した場合も、push 前にもう一度 `make lint-all` を通す。CI で初めて format/test 漏れを見つけないこと。
- 時間や外部依存で一部を実行できない場合は、push 前にユーザーへ未実行チェックと理由を明示する。
- 機械的な忘れ防止として `git config core.hooksPath .githooks` を設定し、`.githooks/pre-push` から `make pre-push` を実行する。`make pre-push` は `make lint-all` と `make test-all` の両方を必ず実行する。

## 開発コマンド

```bash
# 全サービスのリント・型チェック
make lint-all

# 全サービスのテスト
make test-all

# push 前の全検証
make pre-push

# 特定サービスのテスト
cd services/gateway && uv run pytest tests/ -v

# Supabase TypeScript型の再生成
./scripts/gen-supabase-types.sh

# ローカル全サービス起動 (docker-compose)
docker compose -f infra/docker-compose.dev.yml up

# 過去データによるバックテスト再生
uv run python scripts/replay-market-data.py --date 2025-01-15

# ヘルスチェック
uv run python scripts/health-check.py

# Dashboard 開発サーバー起動（volta が Node バージョンを自動切替）
cd dashboard && npm run dev

# Warm → Cold ストレージ移行（日次バッチ）
uv run python scripts/warm-to-cold-migration.py --date 2025-01-15
```

## Claude Code での作業ガイド

### サービス単体の開発
```bash
cd services/gateway
claude
# gateway/CLAUDE.md + ルート CLAUDE.md が自動で読み込まれる
```

### 横断的な変更（スキーマ追加など）
```bash
cd trade-ai-agent  # ルートから起動
claude
# 例: "StrategySignal に confidence_score: float を追加し、
#      全サービスの影響箇所を更新して"
```

### サブエージェントの活用
コンテキスト節約のため、調査はサブエージェントに委譲する:
```
"サブエージェントを使って strategy-rule/ と strategy-ai/ の
 現在のシグナル生成ロジックを調査し、aggregator の合議制設計に
 必要な情報をまとめて"
```

### 注意事項
- contracts/ を変更したら必ず `make test-all` で全サービスの整合性を確認すること
- Gateway のリスクルールは最重要ロジック。変更時はエッジケースのテストを必ず書くこと
- oms-live は本番資金に直結する。変更は最小限にし、必ず oms-paper で先行検証すること
