# services/strategy-rule/

`processed-features` を購読し、テクニカル指標の閾値・クロスオーバー等に基づいて数値的な売買シグナルを生成するルールベース戦略エンジン (Strategy Engine A)。出力は `strategy-signals-a` トピックと Supabase `strategy_logs`。

アーキテクチャ全体・スキーマはルート [CLAUDE.md](../../CLAUDE.md) と [contracts/](../../contracts/) を参照。ここは `services/strategy-rule/` 配下で作業するときの運用ルール。

## 責務 / 非責務

**責務**
- `processed-features` (`ProcessedFeatures`) の購読
- `strategies/` 配下のルールプラグインを評価しシグナルを生成
- `StrategySignal` (source=RULE) の組み立てと `strategy-signals-a` へのパブリッシュ
- Supabase `strategy_logs` への書き込み（source=RULE）

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

## ディレクトリ構成（想定）

```
services/strategy-rule/
├── CLAUDE.md                    # 本ファイル
├── pyproject.toml               # uv プロジェクト (trade-contracts ローカル参照)
├── .env.example
├── src/strategy_rule/
│   ├── __init__.py
│   ├── __main__.py              # エントリポイント (CLI: stream / backtest)
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
- **冪等性**: signal_id は各 evaluate 呼び出しで新規生成。Aggregator は `(symbol, timestamp, source)` で重複判定

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
- カバレッジ 80%+（ルート方針）

## 開発時の注意

- **`trade-contracts` を破らない**: `StrategySignal` に列を足すときは `contracts/` の変更手順（Pydantic → SQL → TS）に従う
- **戦略は数値ロジックのみ**。ニュース・市況などコンテキスト系は Strategy AI の責務
- **戦略間で状態を共有しない**。Aggregator がそれぞれを独立に受け取り合議する前提
- 1 ProcessedFeatures から複数の戦略が同時にシグナルを出すことを許容する
- Pub/Sub の実装は Phase 3 で一括導入する。Phase 1 / 2 で仮実装を入れない
