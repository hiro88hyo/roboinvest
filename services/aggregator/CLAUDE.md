# services/aggregator/

`strategy-signals-a` と `strategy-signals-b` を購読し、合議制ロジックで統合して `UnifiedTradeSignal` を生成する Signal Aggregator。出力は `trade-signals` トピックと Supabase `aggregator_logs`。

アーキテクチャ全体・スキーマはルート [CLAUDE.md](../../CLAUDE.md) と [contracts/](../../contracts/) を参照。ここは `services/aggregator/` 配下で作業するときの運用ルール。

## 責務 / 非責務

**責務**
- `strategy-signals-a` (Rule) / `strategy-signals-b` (AI) の購読
- シグナルの合議制統合（重み付け平均・一致度評価・矛盾時フォールバック）
- `UnifiedTradeSignal` の組み立てと `trade-signals` へのパブリッシュ
- Supabase `aggregator_logs` への書き込み（`signal_source` は `RULE` / `AI` / `CONSENSUS` のいずれか）

**非責務**
- 戦略ロジックそのもの → strategy-rule / strategy-ai
- リスク検証・ロット計算・キルスイッチ → Gateway
- 注文執行 → OMS Live / OMS Paper
- 板情報・テクニカル指標の計算 → Feature Engine

## 実装フェーズ

下流 (gateway) も未実装のため、strategy-rule と同じパターンで段階コミット。

### Phase 1: 合議ロジックのコア (純関数 + unit test)

- 入力: `list[StrategySignal]` （A/B 片方のみでも可）
- 出力: `UnifiedTradeSignal | None`
- 合議ルール:
  - **片方のみ存在** → そのシグナルをそのまま採用 (`signal_source` は元の `source` を継承)
  - **両方一致** → `signal_source=CONSENSUS`、`confidence` は重み付け平均
  - **両方矛盾** (action が BUY vs SELL) → 設定可能なフォールバック: `skip`（デフォルト） / `prefer_rule` / `prefer_ai`
  - **confidence 下限未達** → `skip`
- `UnifiedTradeSignal.holding_type` は **Phase 1 では config 固定値** で埋める（system_status.trading_style 参照は Phase 3）
- `stop_loss_price` / `target_price` / `trailing_stop_pct` / `max_hold_days` は Phase 1 では `None` パススルー（Gateway 側のデフォルトに委ねる）
- 重みは `SOURCE_WEIGHT_RULE` / `SOURCE_WEIGHT_AI` の env で制御
- 状態管理 (time-window pairing) は Phase 1 では扱わない（入力をバッチで受ける純関数）

### Phase 2: バックテストランナー

- 入力: strategy-rule Phase 2 の出力 (`StrategySignal` JSONL) を 1 つまたは 2 つ (A/B)
- `(symbol, created_at_bucket)` で A/B をペアリングし、合議ロジックへ渡す
- 出力: `UnifiedTradeSignal` JSONL（Gateway のバックテスト入力に使う想定）
- CLI: `uv run python -m aggregator backtest --input-a signals_a.jsonl --input-b signals_b.jsonl --output unified.jsonl`
- `--input-b` 省略時は A 単独パススルーモード（strategy-ai 未実装でも回せる）

### Phase 3: ストリーミング実装

- `strategy-signals-a` / `strategy-signals-b` 並行購読
- タイムウィンドウ (`PAIRING_WINDOW_MS`、デフォルト 1000ms) 内で到着した同一 symbol の A/B シグナルをバッファしてペアリング
- ウィンドウ終了時に合議ロジックへ投入、結果を `trade-signals` publish + `aggregator_logs` INSERT
- `system_status.trading_style` を Supabase から読んで `holding_type` に反映（Phase 1 の config 固定値を上書き）
- 再起動時のバッファは失う前提 (at-least-once)

## ディレクトリ構成（想定）

```
services/aggregator/
├── CLAUDE.md                    # 本ファイル
├── pyproject.toml               # uv プロジェクト (trade-contracts ローカル参照)
├── .env.example
├── src/aggregator/
│   ├── __init__.py
│   ├── __main__.py              # エントリポイント (CLI: stream / backtest)
│   ├── config.py                # pydantic-settings ベースの env 読み込み
│   ├── consensus.py             # Phase 1 合議ロジック (純関数)
│   ├── pairing.py               # Phase 2/3 の A/B ペアリング
│   ├── backtest/                # Phase 2
│   │   ├── __init__.py
│   │   ├── reader.py
│   │   ├── runner.py
│   │   └── writer.py
│   ├── clients/                 # Phase 3
│   │   ├── pubsub.py
│   │   └── supabase.py
│   └── streaming/               # Phase 3
│       ├── __init__.py
│       ├── buffer.py            #   時間ウィンドウ付きペアリングバッファ
│       └── runner.py
└── tests/
    ├── conftest.py
    ├── unit/
    ├── integration/
    └── fixtures/
```

## 合議ロジックの規約（Phase 1）

- 入力 `StrategySignal` のリストを `source` (RULE / AI) でバケット化
- 同一 source 内に複数シグナルがある場合は `confidence` が最大のものを代表にする
- 重み付け confidence:
  ```
  w_rule, w_ai = env SOURCE_WEIGHT_RULE, SOURCE_WEIGHT_AI (デフォルト 1.0 / 1.0)
  unified.confidence = (rule.confidence * w_rule + ai.confidence * w_ai) / (w_rule + w_ai)
  ```
- source 別 confidence 下限未満は `None` を返す (= シグナル破棄)
  - `MIN_CONFIDENCE_RULE_ONLY`: RULE 単独採用の下限（デフォルト 0.5）
  - `MIN_CONFIDENCE_AI_ONLY`: AI 単独採用の下限（デフォルト 0.5）
  - `MIN_CONFIDENCE_CONSENSUS`: RULE + AI 一致採用の下限（デフォルト 0.3）
- 矛盾時フォールバック: `CONFLICT_POLICY` (`skip` | `prefer_rule` | `prefer_ai`、デフォルト `skip`)
- `strategy_signal_id_a` / `strategy_signal_id_b` は入力 `StrategySignal.signal_id` をそのまま入れる
- Aggregator は **新しい `signal_id` (UUIDv4) を自分で生成**し、`UnifiedTradeSignal.signal_id` に入れる

## ペアリング（Phase 2 / 3）の規約

- **Phase 2 (バックテスト)**: `(symbol, created_at_bucket)` の bucket は `PAIRING_BUCKET_MS` (env、デフォルト 1000ms) 単位で丸める
- **Phase 3 (ストリーミング)**: 到着時刻ではなく `StrategySignal.created_at` でウィンドウを判定
- ウィンドウタイムアウト後に片方しか来ていなければ、その片方だけで合議ロジックを呼ぶ (Phase 1 の「片方のみ」ルール)
- 遅延到着は遅れた方の新しいウィンドウに入る（過去に遡ってペアリングしない）

## Supabase `aggregator_logs` の書き込み規約

- `signal_id` = `UnifiedTradeSignal.signal_id`
- `signal_source` = 合議結果 (`RULE` / `AI` / `CONSENSUS`)
- `strategy_signal_id_a` / `strategy_signal_id_b` は FK → `strategy_logs.signal_id`。片方のみ存在する場合は NULL
- `trade-signals` publish の前に INSERT 完了を待つ (at-least-once、重複許容、下流で冪等判定)

## 設定（env）

`.env.example` に列挙するキー例:
- `AGGREGATOR_MODE`: `stream` | `backtest`
- `SUPABASE_URL` / `SUPABASE_SECRET_KEY`
- `PUBSUB_PROJECT_ID` / `PUBSUB_EMULATOR_HOST`
- `PUBSUB_SUBSCRIPTION_SIGNALS_A`: `aggregator-strategy-signals-a`
- `PUBSUB_SUBSCRIPTION_SIGNALS_B`: `aggregator-strategy-signals-b`
- `PUBSUB_TOPIC_TRADE_SIGNALS`: `trade-signals`
- `SOURCE_WEIGHT_RULE` / `SOURCE_WEIGHT_AI`: 合議の重み（デフォルト各 1.0）
- `CONSENSUS_MIN_CONFIDENCE`: 旧来の統合後 confidence 下限（デフォルト 0.3、互換用）
- `MIN_CONFIDENCE_RULE_ONLY` / `MIN_CONFIDENCE_AI_ONLY` / `MIN_CONFIDENCE_CONSENSUS`: source 別 confidence 下限（デフォルト 0.5 / 0.5 / 0.3）
- `CONFLICT_POLICY`: `skip` / `prefer_rule` / `prefer_ai`（デフォルト `skip`）
- `PAIRING_BUCKET_MS` / `PAIRING_WINDOW_MS`: バケット粒度・ウィンドウ長（デフォルト 1000ms）
- `DEFAULT_HOLDING_TYPE`: Phase 1/2 の固定値 (`day` | `swing`、デフォルト `day`)

秘密情報は `.env.example` にダミー値で列挙、`.env` はコミットしない。

## テスト方針

- **ユニット**: 合議ロジックの真理値表（A/B 有無 × 一致/矛盾 × confidence 上下）を網羅
- **Phase 2 統合**: A/B の JSONL を読み込み、ペアリング + 合議の end-to-end
- **Phase 3 統合**: Pub/Sub エミュレータ + ローカル Supabase で publish → subscribe 確認
- カバレッジ 80%+（ルート方針）

## 開発時の注意

- **`trade-contracts` を破らない**: `UnifiedTradeSignal` に列を足すときは `contracts/` の 3 層同期手順に従う
- **合議ロジックは純関数**: Pub/Sub / Supabase / 時刻依存を持ち込まない。ペアリングは別モジュール
- **`strategy_signal_id_a/b` の FK 制約**: `aggregator_logs` INSERT 前に `strategy_logs` 行が存在する前提。Phase 3 で順序が逆転しないよう at-least-once で遅延を許容
- **`signal_source` の決まり方は下流が参照する**: Gateway・OMS・Dashboard は `CONSENSUS` / `RULE` / `AI` を見て挙動を変える。勝手に値を足さない
- Phase 1 では Pub/Sub 仮実装を入れない。Phase 3 で一括導入する
