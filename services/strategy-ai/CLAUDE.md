# services/strategy-ai/

`processed-features` を購読し、LLM 推論によりコンテキストを加味した売買シグナルを生成する AI 戦略エンジン (Strategy Engine B)。出力は `strategy-signals-b` トピックと Supabase `strategy_logs` (source=AI)。

アーキテクチャ全体・スキーマはルート [CLAUDE.md](../../CLAUDE.md) と [contracts/](../../contracts/) を参照。ここは `services/strategy-ai/` 配下で作業するときの運用ルール。

## 責務 / 非責務

**責務**
- `processed-features` (`ProcessedFeatures`) の購読
- 板情報・直近の指標値からプロンプトを組み立て、LLM に推論を依頼
- LLM 応答 (JSON) をパースして `StrategySignal` (source=AI) を組み立て
- `strategy-signals-b` へのパブリッシュと Supabase `strategy_logs` (source=AI, reasoning 入り) への書き込み
- 銘柄ごとの最小推論間隔（レート制御）でコスト爆発を抑止

**非責務**
- 指標計算 → Feature Engine
- 数値ロジックのみのシグナル → Strategy Rule
- 戦略間の合議・統合 → Aggregator
- リスク検証・ロット計算・キルスイッチ → Gateway
- 注文執行 → OMS Live / OMS Paper

## 実装フェーズ

### Phase 1: LLM クライアント抽象 + AI 戦略コア (純関数 / 純 async)

- `LLMClient` Protocol を定義し、`GeminiClient` を `llm/gemini.py` に実装
- `prompt.py`: `ProcessedFeatures` から決定論的にプロンプトを組み立てる純関数
- `parser.py`: LLM 応答 JSON を `ParsedDecision` (action / confidence / reasoning) にパース
- `strategy.py`: `AiStrategy` クラスは `LLMClient` を DI で受け取り、`evaluate(features, state)` を実装
  - 銘柄ごとの最小推論間隔 (`min_interval_seconds`) を `state` で管理
  - LLM タイムアウト・パース失敗は `None` を返してログのみ
- `engine.py`: 複数 `AsyncStrategy` をオーケストレーション（実質は AiStrategy 1 本だが将来拡張用）
- ユニットテストは `FakeLLMClient` を使い、ProcessedFeatures 入力に対して期待 action/confidence/reasoning が返ることを検証

### Phase 2: バックテストランナー

- 入力: feature-engine の backtest 出力 (`ProcessedFeatures` JSONL)
- 各 ProcessedFeatures に対し AI 戦略を評価、出力 `StrategySignal` を JSONL に書き出す
- CLI: `uv run python -m strategy_ai backtest --input path/to/features.jsonl --output path/to/signals.jsonl`
- バックテストはフィクスチャ LLM (決定論的) で再現性を担保。実 LLM は別途オプションで叩ける

### Phase 3: ストリーミング実装

- `processed-features` 購読 → `AiStrategy.evaluate` → `strategy-signals-b` publish + Supabase `strategy_logs` 書き込み
- レート制御は in-process state（再起動でリセット。許容）
- `at-least-once`: Aggregator 側で重複排除
- LLM 失敗 (タイムアウト・5xx・パース不能) はメッセージを ack して進む（ポイズン扱い）

## ディレクトリ構成

```
services/strategy-ai/
├── CLAUDE.md
├── pyproject.toml
├── .env.example
├── src/strategy_ai/
│   ├── __init__.py
│   ├── __main__.py              # CLI (Phase 2/3)
│   ├── py.typed
│   ├── config.py                # StrategyAiSettings
│   ├── base.py                  # AsyncStrategy Protocol
│   ├── prompt.py                # ProcessedFeatures -> str (Phase 1)
│   ├── parser.py                # str -> ParsedDecision (Phase 1)
│   ├── strategy.py              # AiStrategy (Phase 1)
│   ├── engine.py                # StrategyAiEngine (Phase 1)
│   ├── llm/
│   │   ├── __init__.py
│   │   ├── base.py              # LLMClient Protocol + LLMError
│   │   ├── gemini.py            # GeminiClient (google-genai)
│   │   └── factory.py           # build_llm_client(settings) -> LLMClient
│   ├── backtest/                # Phase 2
│   ├── clients/                 # Phase 3 (pubsub / supabase)
│   └── streaming/               # Phase 3
└── tests/
    ├── conftest.py
    ├── unit/                    # 純ロジック
    └── integration/             # Phase 2/3 で追加
```

## LLM クライアント抽象（Phase 1）

```python
class LLMClient(Protocol):
    async def complete(self, prompt: str) -> str: ...
```

- 入力はテキスト 1 本、出力もテキスト 1 本。JSON モードや response schema は実装側の責務
- 失敗時は `LLMError` を投げる。呼び出し側はキャッチして `None` を返す
- `GeminiClient` は `google-genai` SDK を内部で使用し、`response_mime_type=application/json` を強制
- 将来 Claude / OpenAI / ローカル LLM を足すときは `llm/{claude,openai,local}.py` を追加し `factory.py` に登録するだけ

## プロンプト規約（Phase 1）

- プロンプトは決定論的（同じ ProcessedFeatures は同じ文字列）
- 含める情報: 銘柄・タイムスタンプ・現在価格・SMA(short/long)・RSI・VWAP・ボリンジャーバンド・板スナップショット (best bid/ask)
- 出力フォーマットは厳密な JSON: `{"action": "BUY|SELL|HOLD", "confidence": 0.0-1.0, "reasoning": "..."}`
- HOLD は parser でフィルタして `None` を返す（Aggregator 側に HOLD を出さない）

## レート制御（Phase 1）

- `state["last_call_at"]` (datetime) を保持、`features.timestamp - last_call_at < min_interval_seconds` の間はスキップ
- レート制限スキップ時は `None` を返す（ログは debug レベル）
- 銘柄ごとに独立。`AiStrategy` は state を mutate する責務

## 設定（env）

`.env.example` に列挙するキー例:
- `LOG_LEVEL`
- `LLM_PROVIDER`: `gemini`（Phase 1 は gemini のみ）
- `GEMINI_API_KEY` / `GEMINI_MODEL` (default: `gemini-2.0-flash`) / `GEMINI_TIMEOUT_SECONDS`
- `AI_MIN_INTERVAL_SECONDS`: 銘柄ごとの最小推論間隔（デフォルト 60）
- `AI_TEMPERATURE`: LLM の temperature（デフォルト 0.0、決定論寄り）
- `SUPABASE_URL` / `SUPABASE_SECRET_KEY`（Phase 3）
- `PUBSUB_PROJECT_ID` / `PUBSUB_EMULATOR_HOST`（Phase 3）
- `PUBSUB_SUBSCRIPTION_FEATURES`: `strategy-ai-processed-features`
- `PUBSUB_TOPIC_SIGNALS`: `strategy-signals-b`
- `BACKTEST_OUTPUT_DIR`: backtest 結果の出力先

秘密情報は `.env.example` にダミー値で列挙、`.env` はコミットしない。

## テスト方針

- **ユニット**: prompt/parser/strategy/engine を `FakeLLMClient` で網羅
- **統合 (Phase 2 以降)**: 実 JSONL を流して整合確認、Pub/Sub + Supabase は emulator
- 実 LLM (Gemini) を叩くテストは別マーカー (`@pytest.mark.live_llm`) で分離し、デフォルトでは skip

## 開発時の注意

- **API キー漏洩防止**: `.env` をコミットしない。テストはモックで完結させる
- **コスト管理**: Phase 1 は実 LLM を叩かない。Phase 2 でも fixtures 優先、実 LLM は手動オプション
- **`trade-contracts` を破らない**: `StrategySignal.reasoning` は既存フィールドなので contracts 変更は不要
- **HOLD は出さない**: Aggregator は BUY/SELL のみ受け付ける前提で設計しているため、HOLD はパース後に握り潰す
