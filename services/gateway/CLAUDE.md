# services/gateway/

`trade-signals` を購読し、キルスイッチ・2% ルール・ロット計算・モード別ルーティングを経て `OrderRequest` を `live-orders` または `paper-orders` にパブリッシュする Risk & Routing Gateway。

アーキテクチャ全体・スキーマはルート [CLAUDE.md](../../CLAUDE.md) と [contracts/](../../contracts/) を参照。ここは `services/gateway/` 配下で作業するときの運用ルール。

## 責務 / 非責務

**責務**
- `trade-signals` (UnifiedTradeSignal) の購読
- Supabase `system_status` / `positions` の読み取り
- キルスイッチ評価（`is_trading_allowed` / 日次・週次・月次損失上限）
- 2% ルールに基づくロット数の算出と強制切り詰め
- `trade_mode` に応じた `live-orders` / `paper-orders` へのルーティング
- 日次損失上限到達時の `system_status.is_trading_allowed = false` 書き戻し

**非責務**
- シグナル生成・合議 → strategy-rule / strategy-ai / aggregator
- 注文の実発注・擬似約定 → OMS Live / OMS Paper
- 約定記録・PnL 集計（`daily_pnl` 更新は OMS 側） → OMS Live / OMS Paper
- 板情報・テクニカル指標 → Feature Engine
- デイクローズアウト実行 → OMS（Gateway は判断しない）

## 実装フェーズ

aggregator / strategy-rule と同じ 3 フェーズパターン。段階コミット → `--no-ff` マージ。

### Phase 1: リスクロジックのコア（純関数 + unit test）

- **kill_switch 評価**: 入力 `KillSwitchState` → `RiskCheck`
  - `is_trading_allowed == false` なら即座に reject
  - `daily_pnl <= -daily_loss_limit` / `weekly_pnl <= -weekly_loss_limit` / `monthly_pnl <= -monthly_loss_limit` のいずれかで reject
  - `trade_mode=paper` のときは pnl 系チェックをスキップ（paper は資金リスクがないため）
- **ロット計算器** `lot_calculator.py`: 入力 `UnifiedTradeSignal`, `capital`, 現在価格 → 最大株数
  - 1トレード最大許容損失 = `capital * MAX_RISK_PER_TRADE_PCT`（デフォルト 0.02）
  - `stop_loss_price` があればそれを使用。`None` なら `DEFAULT_STOP_LOSS_SPREAD_PCT`（デフォルト 0.02）で代替
  - スイング (`holding_type=swing`) は `SWING_RISK_SCALE`（デフォルト 0.5）で保守的にスケール
  - `MIN_LOT_SIZE`（デフォルト 100、単元株）でフロア。未達なら reject
- **action → side 変換**: `BUY → BUY`、`SELL → SELL`、`HOLD` は reject（Gateway では建玉しない）
- **ルーティング**: `trade_mode=live` → `live-orders` / `trade_mode=paper` → `paper-orders`
- **OrderRequest 組み立て**: `UnifiedTradeSignal.signal_id` を `unified_signal_id` に、`signal_source` / `symbol` を継承、`order_type=MARKET` 固定（Phase 1）
- I/O・時刻・DB・Pub/Sub を持ち込まない純関数だけで構成する

### Phase 2: バックテストランナー

- 入力:
  - `UnifiedTradeSignal` JSONL（aggregator Phase 2 の出力）
  - 初期 `KillSwitchState`（JSON / env）+ 仮想 `capital`
  - `daily_pnl` は JSONL 側に疑似約定結果があれば差し引く簡易シミュレータ（詳細は Phase 2 で確定）
- 出力: `OrderRequest` JSONL（approved 分）+ 拒否ログ JSONL（rejected 分と理由）
- CLI: `uv run python -m gateway backtest --input unified.jsonl --state initial_state.json --capital 1000000 --output-approved orders.jsonl --output-rejected rejects.jsonl`
- pnl シミュレートは Gateway の責務ではないため、**オプション機能**として最小限（フラット想定）に留める。本格的な pnl 検証は OMS Paper の backtest 側で行う

### Phase 3: ストリーミング実装

- `trade-signals` 購読
- シグナル到着ごとに Supabase から `system_status`（シングルトン `id=1`）と当該 `symbol` の `positions` を取得
- 現ポジションがある場合の取扱いは以下の最小ルールに限定:
  - `action=BUY` かつ既存 LONG あり → reject（2 段乗せを Phase 3 では不許可、Phase 4 以降で拡張）
  - `action=SELL` かつ LONG なし → reject（空売りはしない）
  - `action=SELL` かつ LONG あり → `quantity = position.quantity` で全決済
- リスク検証で拒否された場合は `live-orders` / `paper-orders` に publish せず、拒否理由のみログ出力
- キルスイッチ発動時 (`daily_pnl <= -daily_loss_limit` 等) は `system_status.is_trading_allowed = false` を UPDATE してから reject
- 再起動時のインフライト状態は失う前提 (at-least-once、下流で冪等判定)

## ディレクトリ構成（想定）

```
services/gateway/
├── CLAUDE.md                    # 本ファイル
├── pyproject.toml               # uv プロジェクト (trade-contracts ローカル参照)
├── .env.example
├── src/gateway/
│   ├── __init__.py
│   ├── __main__.py              # エントリポイント (CLI: stream / backtest)
│   ├── config.py                # pydantic-settings ベースの env 読み込み
│   ├── kill_switch.py           # Phase 1 キルスイッチ評価 (純関数)
│   ├── lot_calculator.py        # Phase 1 2%ルール + ロット算出 (純関数)
│   ├── validator.py             # Phase 1 上記を組み合わせた総合判定
│   ├── router.py                # Phase 1 trade_mode → topic 判定
│   ├── order_builder.py         # Phase 1 UnifiedTradeSignal → OrderRequest
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
│       └── runner.py
└── tests/
    ├── conftest.py
    ├── unit/
    ├── integration/
    └── fixtures/
```

## リスクロジックの規約（Phase 1）

- 通貨・金額・価格は **必ず `Decimal`**（`float` 禁止）。ロット数は `int`
- `RiskCheck.passed=False` のときは `reason` に簡潔な機械可読コード（例: `kill_switch_off`, `daily_loss_limit`, `below_min_lot`, `no_position_for_sell`, `missing_stop_loss`）を入れる
- `adjusted_quantity` はロット計算が切り詰めた場合に設定。拒否時は `None`
- 2% ルールの式:
  ```
  risk_amount = capital * MAX_RISK_PER_TRADE_PCT (swing なら * SWING_RISK_SCALE)
  risk_per_share = max(entry_price - stop_loss_price, 0)
  raw_qty = risk_amount / risk_per_share
  adjusted_qty = floor(raw_qty / MIN_LOT_SIZE) * MIN_LOT_SIZE
  ```
- `stop_loss_price` が `entry_price` 以上（BUY 時の無意味な損切り）は reject
- 計算は `decimal.ROUND_DOWN` で保守側に倒す

## Supabase 連携の規約（Phase 3）

- `system_status` は `.eq("id", 1).single()` で 1 行取得
- `positions` は `.eq("symbol", symbol).eq("trade_type", trade_mode)` で当該モードのポジションのみ対象
- キルスイッチ発動時の UPDATE は `.eq("id", 1).update({"is_trading_allowed": False, "updated_at": now_utc()})`
- 書き込み後に `trade-signals` ack、publish は失敗時リトライ（at-least-once）
- Supabase 読み取り失敗時はシグナルを reject（fail-closed）

## Pub/Sub 連携の規約（Phase 3）

- 購読: `trade-signals`（subscription 名は env `PUBSUB_SUBSCRIPTION_TRADE_SIGNALS`、デフォルト `gateway-trade-signals`）
- 発行: `live-orders` / `paper-orders`（topic 名は env で切り替え可能）
- メッセージは `OrderRequest.model_dump_json()`
- `ack` は Supabase 書き戻し + publish 成功後のみ（publish 失敗時は `nack` して再配信）

## 設定（env）

`.env.example` に列挙するキー例:
- `GATEWAY_MODE`: `stream` | `backtest`
- `SUPABASE_URL` / `SUPABASE_SECRET_KEY`
- `PUBSUB_PROJECT_ID` / `PUBSUB_EMULATOR_HOST`
- `PUBSUB_SUBSCRIPTION_TRADE_SIGNALS`: `gateway-trade-signals`
- `PUBSUB_TOPIC_LIVE_ORDERS`: `live-orders`
- `PUBSUB_TOPIC_PAPER_ORDERS`: `paper-orders`
- `CAPITAL`: バックテスト / 初期資金（Phase 3 では Supabase から取得する設計に移行予定）
- `MAX_RISK_PER_TRADE_PCT`: デフォルト `0.02`
- `SWING_RISK_SCALE`: スイング時の追加スケール、デフォルト `0.5`
- `DEFAULT_STOP_LOSS_SPREAD_PCT`: `stop_loss_price` 未設定時の代替、デフォルト `0.02`
- `MIN_LOT_SIZE`: 単元株、デフォルト `100`
- `MARKET_REGIME_GATEWAY_LOG_ONLY_ENABLED` / `MARKET_REGIME_GATEWAY_GUARD_ENABLED`:
  地合い guard の観測 / reject 切り替え
- `MARKET_REGIME_PAPER_GUARD_ENABLED`: paper mode 限定で地合い guard を reject
  有効化する。live mode は `MARKET_REGIME_GATEWAY_GUARD_ENABLED` のみで制御。
- `SOFT_LOSS_THROTTLE_LOG_ONLY_ENABLED` / `SOFT_LOSS_THROTTLE_GUARD_ENABLED` /
  `SOFT_LOSS_LIMIT_JPY`: 日中 soft loss 到達後の RULE-only BUY 抑制
- `EXECUTION_GATE_LOG_ONLY_ENABLED` / `EXECUTION_GATE_GUARD_ENABLED`:
  spread / depth execution gate の観測 / reject 切り替え
- `EXECUTION_GATE_MAX_SPREAD_BPS`: BUY 許容 spread 上限、デフォルト `30`
- `EXECUTION_GATE_MAX_SPREAD_TICKS`: BUY 許容 spread ticks 上限、デフォルト `2`
- `EXECUTION_GATE_MIN_ASK_DEPTH_MULTIPLIER`: BUY 数量に対する 5 本 ask depth の
  最低倍率、デフォルト `3`
- `ENTRY_PRICE_SOURCE`: Phase 1/2 はシグナル外部から与える想定。Phase 3 は Feature Engine の最新価格（`positions.current_price` または Supabase の別テーブル）を使用する設計を Phase 3 着手時に決定

秘密情報は `.env.example` にダミー値で列挙、`.env` はコミットしない。

## テスト方針

- **ユニット**:
  - キルスイッチ真理値表（`is_trading_allowed` × 各 pnl × `trade_mode=live/paper`）
  - ロット計算のエッジケース（stop_loss 未設定 / entry==stop / 極小資金 / swing スケール）
  - action=HOLD の reject
  - trade_mode → topic のルーティング
- **Phase 2 統合**: JSONL 入出力、拒否と許可が混在するケースの end-to-end
- **Phase 3 統合**: Pub/Sub エミュレータ + ローカル Supabase でキルスイッチ UPDATE まで含む end-to-end
- カバレッジ 80%+（ルート方針）
- **リスクルール周りのテストは必須**。エッジケース（オフバイワン・符号ミス）を意識的に増やす

## 開発時の注意

- **Gateway は本番資金の最終防波堤**。リスクルール変更時は必ずエッジケースのユニットテストを先に書く（TDD 推奨）
- **純関数とサイドエフェクトを厳密に分離**: kill_switch / lot_calculator / validator は I/O を持たない。I/O は `clients/` と `streaming/runner.py` に閉じる
- **fail-closed**: 判断に必要な情報が取れない（Supabase 読み取り失敗・`stop_loss_price` 不整合など）場合は必ず reject 側に倒す
- **`trade-contracts` を破らない**: `OrderRequest` / `RiskCheck` への列追加は `contracts/` の 3 層同期手順に従う
- **Phase 1 では Pub/Sub / Supabase を触らない**。Phase 3 で `clients/` にまとめて導入
- **`HOLD` アクションは絶対に注文に変換しない**（`Action` enum に HOLD があるが、Gateway では reject 一択）
- **空売り禁止**: 現物株のため `SELL` は既存 LONG の決済のみ。保有なしの `SELL` は Phase 3 で必ず reject
