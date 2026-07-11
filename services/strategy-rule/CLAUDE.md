# services/strategy-rule/

`processed-features` を購読し、テクニカル指標の閾値・クロスオーバー等に基づいて数値的な売買シグナルを生成するルールベース戦略エンジン (Strategy Engine A)。出力は `strategy-signals-a` トピックと Supabase `strategy_logs`。因果検証済み event-cluster artifact については、通常 stream と分離した paper-only one-shot publisher も持つ。

アーキテクチャ全体・スキーマはルート [CLAUDE.md](../../CLAUDE.md) と [contracts/](../../contracts/) を参照。ここは `services/strategy-rule/` 配下で作業するときの運用ルール。

## 責務 / 非責務

**責務**
- `processed-features` (`ProcessedFeatures`) の購読
- `strategies/` 配下のルールプラグインを評価しシグナルを生成
- `StrategySignal` (source=RULE) の組み立てと `strategy-signals-a` へのパブリッシュ
- Supabase `strategy_logs` への書き込み（source=RULE）
- schema v3 event artifact と fresh book から、二重ラッチ・paper preflight・durable claim を経て `PAPER_ONLY` signal を一度だけ選定する one-shot publish

**非責務**
- 指標計算 → Feature Engine
- AI / LLM 推論 → Strategy AI
- 戦略間の合議・統合 → Aggregator
- リスク検証・ロット計算・キルスイッチ → Gateway
- 注文執行 → OMS Live / OMS Paper

## 実装フェーズ

下流サービス (aggregator / gateway / oms-paper) も未実装のため、段階的に積み上げる。フェーズ境界でマージ可能にする。

### Phase 1: 戦略プラグイン基盤 + 純関数ルール

- `Strategy` Protocol を定義し、`evaluate(features: ProcessedFeatures) -> StrategySignal | None` を満たすプラグインを `strategies/` に置く
- 同梱ルール:
  - `sma_crossover`: 短期 SMA が長期 SMA を上抜け→BUY、下抜け→SELL
  - `rsi_threshold`: RSI が下限を下回る→BUY、上限を超える→SELL
  - `bollinger_breakout`: 終値が下バンドを下抜け→BUY、上バンドを上抜け→SELL
- 入出力は純関数。Pub/Sub・Supabase・I/O を持たせない
- 設定（窓・閾値）は `StrategyConfig` (pydantic) を渡し、戦略は値を読むだけ
- `StrategyRegistry` で名前→ファクトリを束ね、設定 (env / 引数) で有効化を制御
- ユニットテストは `ProcessedFeatures` の前後 2 点（クロス前後など）を渡し、出力の `action` / `confidence` を検証

### Phase 2: バックテストランナー

- 入力: feature-engine の backtest 出力 (`ProcessedFeatures` JSONL)
- 各 ProcessedFeatures に対し全有効戦略を順番に評価、出力 `StrategySignal` を JSONL に書き出す
- CLI: `uv run python -m strategy_rule backtest --input path/to/features.jsonl --output path/to/signals.jsonl`
- 戦略の状態管理が必要な場合 (例: 直前のクロス検出) は、戦略インスタンスを silbol 単位で保持
- これで aggregator 側の入力が用意できる

### Phase 3: ストリーミング実装

- `processed-features` 購読 → 全戦略評価 → `strategy-signals-a` publish + Supabase `strategy_logs` 書き込み
- 戦略状態は in-process。再起動時の歴史は失われるが、Phase 1 のルールは数本の特徴量で判定できる前提
- `at-least-once`: 同一 features の重複は許容。Aggregator 側で重複排除する想定

### Event paper one-shot publisher

- CLI: `uv run python -m strategy_rule event-paper-publish ...`
- detector 内蔵の旧 `--publish-paper` は引き続き fail closed。one-shot publisher だけが実行経路を持つ
- detector は凍結済み選定の `data_available_at/feature_cutoff_at` を disclosure time のまま保持し、翌朝の実受信は `source_received_at` に分離する。受信時刻で PER/valuation の bar vintage を進めない
- feature cutoff から定まる必須OHLCV session（15:30 JST以降は signal-date、それ以前は直前TSE営業日）が欠ける候補は、古いbarを使わず cohort から除外せず `feature_data_complete=false` にする。artifact は reportable だが pre-open/watchlist/publisher では実行不可
- 現 publisher は `opening_transport_stress_v1`。signal strategy key も selection key と分離し、receipt/report は `comparable_to_registered_backtest=false`。next-open / 20日目 close を再現する frozen-v1 paper evidence には数えない
- `--publish-paper` と `EVENT_CLUSTER_PAPER_PUBLISH_ENABLED=true` の両方を必須とし、環境変数の既定値は必ず `false`
- `event-paper-raw-books` だけを pull し、09:00〜09:30 JST の `received_at` 付き fresh ask を使う。現 CLI は managed Pub/Sub を network I/O 前に全面拒否し、loopback emulator の明示的な `--no-seek`、loopback Supabase、allowlist 済み dev project の stress test だけ許可する。Supabase HTTP と emulator gRPC は proxy 継承なし
- 1 invocation で publish する occurrence は必ず1件。複数候補 artifact は `--execution-candidate-id` を必須とし、receipt も occurrence ごとに分ける
- Supabase が paper/allowed、必要な OMS Paper RPC が利用可能、期限到来 swing exit が残っていないことを publish 前に確認する
- quote は `strategy_logs.reasoning` へ claim し、raw book ack と最終 preflight 後に CAS RPC で publication attempt を一度だけ所有してから Pub/Sub を1 RPCだけ実行する。成功時は message ID/time を同じ claim へ checkpoint する。attempt 済み・checkpoint なしは ambiguous として復元し、再送しない
- 出力は detector artifact を変更せず、artifact digest と attempt/message lineage を持つ別 receipt JSON に no-clobber で書く。同一 filesystem namespace の同時実行は lock で拒否し、共有 subscription のため host/container をまたぐ運用 coordinator も1つに固定する
- 本番実行可否と手順は `docs/runbook/event-cluster-paper-publish.md` を正とする。現時点の target 実行は禁止

## ディレクトリ構成（想定）

```
services/strategy-rule/
├── CLAUDE.md                    # 本ファイル
├── pyproject.toml               # uv プロジェクト (trade-contracts ローカル参照)
├── .env.example
├── src/strategy_rule/
│   ├── __init__.py
│   ├── __main__.py              # CLI: stream / backtest / event-paper-publish
│   ├── config.py                # pydantic-settings ベースの env 読み込み
│   ├── registry.py              # 戦略名 → ファクトリのレジストリ
│   ├── base.py                  # Strategy Protocol / StrategyContext
│   ├── strategies/              # Phase 1
│   │   ├── __init__.py
│   │   ├── sma_crossover.py
│   │   ├── rsi_threshold.py
│   │   └── bollinger_breakout.py
│   ├── backtest/                # Phase 2
│   │   ├── __init__.py
│   │   ├── reader.py            #   ProcessedFeatures JSONL 読み込み
│   │   ├── runner.py            #   features → signals 変換
│   │   └── writer.py            #   StrategySignal JSONL 書き出し
│   ├── clients/                 # Phase 3
│   │   ├── pubsub.py            #   processed-features 購読 / strategy-signals-a publish
│   │   └── supabase.py          #   strategy_logs 書き込み
│   ├── event_paper/             # causal artifact / fresh-book one-shot publisher
│   └── streaming/               # Phase 3
│       ├── __init__.py
│       └── runner.py
└── tests/
    ├── conftest.py
    ├── unit/                    # 戦略ロジック・registry
    ├── integration/             # Pub/Sub + Supabase 接続を含む
    └── fixtures/                # ProcessedFeatures サンプル
```

## 戦略プラグインの規約（Phase 1）

- `Strategy` Protocol:
  ```python
  class Strategy(Protocol):
      name: str
      def evaluate(self, features: ProcessedFeatures, state: StrategyState) -> StrategySignal | None: ...
  ```
- `evaluate` は副作用なし。状態（直前値・クロス検出）は外部 `StrategyState` に保持
- 必要な特徴量が `None`（ウォームアップ未到達）のときは `None` を返す。例外で落とさない
- `confidence` は 0.0〜1.0。閾値からの乖離度で連続値を作るのが望ましい
- HOLD を返したいケースは「シグナルを出さない」(= `None`) で表現する。Action.HOLD は基本使わない
- 各戦略は新規 `.py` ファイルとして追加し、`StrategyRegistry.register` で登録

## バックテスト（Phase 2）の入力契約

- 入力 JSONL は `ProcessedFeatures` (Pydantic) の `model_dump_json()` 形式
- feature-engine の backtest 出力をそのまま入力できることを保証する
- 出力 JSONL も `StrategySignal` を `model_dump_json()` で 1 行 1 シグナル

## ストリーミング（Phase 3）の不変条件

- **at-least-once publish**: 同一 features に対する重複 signal は Aggregator 側で排除
- **戦略間の独立性**: ある戦略が例外を投げても他戦略は評価する。失敗はログに残しつつ続行
- **冪等性**: 通常 stream は既存の重複処理契約に従う。event-paper は `strategy_key + execution_candidate_id + source + symbol + action` から deterministic ID を作り、durable claim で quote を固定する

## 設定（env）

`.env.example` に列挙するキー例:
- `STRATEGY_RULE_MODE`: `stream` | `backtest`
- `SUPABASE_URL` / `SUPABASE_SECRET_KEY`
- `PUBSUB_PROJECT_ID` / `PUBSUB_EMULATOR_HOST`
- `PUBSUB_SUBSCRIPTION_FEATURES`: `strategy-rule-processed-features`
- `PUBSUB_TOPIC_SIGNALS`: `strategy-signals-a`
- `STRATEGIES_ENABLED`: 有効化する戦略名のカンマ区切り（例: `sma_crossover,rsi_threshold`）
- `RSI_BUY_THRESHOLD` / `RSI_SELL_THRESHOLD`: RSI 閾値
- `SMA_MIN_GAP_RATIO`: SMA クロス判定の最小ギャップ
- `BOLLINGER_BREAKOUT_TOLERANCE`: バンド逸脱の許容比率

秘密情報は `.env.example` にダミー値で列挙、`.env` はコミットしない。

## テスト方針

- **ユニット**: 戦略ロジックは `ProcessedFeatures` のサンプル列を渡して期待 `action` を検証
- **統合**: Phase 2 以降は実 JSONL を読み書きして整合を確認
- **Phase 3 統合**: Pub/Sub エミュレータ + ローカル Supabase で end-to-end
- **event-paper 統合**: Publisher → Aggregator → Gateway → OMS Paper、重複 delivery、fill-anchored stop、期限日の partial/full exit、live topic が空であることを実 emulator/RPC で検証
- カバレッジ 80%+（ルート方針）

## 開発時の注意

- **`trade-contracts` を破らない**: `StrategySignal` に列を足すときは `contracts/` の変更手順（Pydantic → SQL → TS）に従う
- **戦略は数値ロジックのみ**。ニュース・市況などコンテキスト系は Strategy AI の責務
- **戦略間で状態を共有しない**。Aggregator がそれぞれを独立に受け取り合議する前提
- 1 ProcessedFeatures から複数の戦略が同時にシグナルを出すことを許容する
- Pub/Sub の実装は Phase 3 で一括導入する。Phase 1 / 2 で仮実装を入れない
