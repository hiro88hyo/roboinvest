# services/feature-engine/

`raw-market-data` を受けてテクニカル指標を計算し `processed-features` にパブリッシュするストリーミングサービス。併せて生データの永続化（3段階ストレージ）・保有ポジションの時価更新・日次 pnl リセットを担う。バックテスト時は Supabase `daily_ohlcv` を入力に切り替える。

アーキテクチャ全体・スキーマはルート [CLAUDE.md](../../CLAUDE.md) と [contracts/](../../contracts/) を参照。ここは `services/feature-engine/` 配下で作業するときの運用ルール。

## 責務 / 非責務

**責務**
- `raw-market-data`（`TickData` / `OrderBookSnapshot`）の購読
- Polars によるテクニカル指標算出（SMA / RSI / VWAP / Bollinger 等）
- `ProcessedFeatures` の組み立てと `processed-features` へのパブリッシュ
- 生データの 3 段階ストレージ管理（Hot=メモリ / Warm=間引き Parquet / Cold=OHLCV Parquet）
- `TickData` 受信ごとの Supabase `positions.current_price` / `unrealized_pnl` 更新
- 市場開始（9:00 JST）に `system_status.daily_pnl = 0` をリセット。週初/月初に `weekly_pnl` / `monthly_pnl` もリセット
- バックテストモード（`daily_ohlcv` 入力）のサポート

**非責務**
- シグナル生成・売買判断 → Strategy A / B
- キルスイッチの発動判定 → Gateway
- 銘柄の選定 → Universe Scanner
- kabu.com API への接続 → Feeder

## 実装フェーズ

kabu.com 接続端末が未着のため、以下の順で段階的に実装する。フェーズ境界でマージ可能にする。

### Phase 1: 指標計算コア（純関数・Polars）

- `indicators/` に SMA / RSI / VWAP / Bollinger を Polars の純関数として実装
- 入出力は `polars.DataFrame` のみ。Pub/Sub・Supabase・I/O への依存を持たせない
- `daily_ohlcv` 形式（`symbol, date, open, high, low, close, volume, turnover`）と tick 形式の双方を受けられる共通 API
- ユニットテストで既知系列に対する数値を golden 検証

### Phase 2: バックテストランナー

- Supabase `watchlist` と `daily_ohlcv` を読み取り → 指標計算 → `ProcessedFeatures` を生成
- 出力先は当面ローカル（JSONL か Parquet）。Pub/Sub publish は Phase 3 まで保留
- CLI: `uv run python -m feature_engine backtest --date 2026-04-18 --symbols 7203,9984`
- これで `strategy-rule` / `strategy-ai` の開発を本サービスに依存させず進められる

### Phase 3: ストリーミング実装（kabu.com 到着後）

- `raw-market-data` 購読 → 指標計算 → `processed-features` publish
- `positions.current_price` / `unrealized_pnl` のリアルタイム更新
- 9:00 JST / 週初 / 月初の pnl リセットスケジューラ
- 3 段階ストレージの Hot / Warm / Cold 振り分け（`STORAGE_TICK_RESOLUTION` 制御）

## ディレクトリ構成（想定）

```
services/feature-engine/
├── CLAUDE.md                    # 本ファイル
├── pyproject.toml               # uv プロジェクト (trade-contracts ローカル参照)
├── Dockerfile
├── .env.example
├── src/feature_engine/
│   ├── __init__.py
│   ├── __main__.py              # エントリポイント (CLI: stream / backtest)
│   ├── config.py                # pydantic-settings ベースの env 読み込み
│   ├── indicators/              # Phase 1
│   │   ├── __init__.py
│   │   ├── moving_average.py    #   SMA / EMA
│   │   ├── rsi.py
│   │   ├── vwap.py
│   │   └── bollinger.py
│   ├── backtest/                # Phase 2
│   │   ├── __init__.py
│   │   ├── runner.py            #   daily_ohlcv → ProcessedFeatures の変換
│   │   └── writer.py            #   JSONL / Parquet への書き出し
│   ├── clients/                 # Phase 2 以降
│   │   ├── supabase.py          #   positions / system_status / daily_ohlcv
│   │   └── pubsub.py            #   Phase 3: raw-market-data 購読 / processed-features publish
│   ├── streaming/               # Phase 3
│   │   ├── __init__.py
│   │   ├── session.py           #   tick 集約の状態管理
│   │   └── position_updater.py  #   positions.current_price / unrealized_pnl
│   ├── storage/                 # Phase 3
│   │   ├── __init__.py
│   │   ├── warm.py              #   間引き Parquet 書き出し
│   │   └── cold.py              #   OHLCV アーカイブ
│   └── scheduler.py             # Phase 3: 9:00 / 週初 / 月初のリセット
└── tests/
    ├── conftest.py
    ├── unit/                    # 指標計算・pure functions
    ├── integration/             # Supabase 書き込みを含む
    └── fixtures/                # 指標検証用のゴールデン系列
```

## 指標計算の規約（Phase 1）

- **入出力は Polars の `DataFrame` / `LazyFrame`**。pandas を依存に入れない
- **価格は `Decimal`**。内部計算で `float` に落とす場合は最終出力前に `Decimal` へ戻す
- 各指標関数は以下を満たす:
  - 同一入力に対して副作用なく同じ結果を返す（純関数）
  - `symbol` ごとに独立して計算できるよう、group by しやすい列設計
  - ウォームアップ期間（window 不足）は `null` を返し、例外で落とさない
- 指標追加時は `tests/fixtures/` にゴールデンを置き、TA-Lib 等の既知実装との差分を確認する

## バックテスト（Phase 2）の入力契約

- Supabase `daily_ohlcv` から `symbol, date, open, high, low, close, volume, turnover` を取得
- `symbol` は引数 or `watchlist.valid_date = --date` から自動解決
- 出力 `ProcessedFeatures` は `timestamp = date` とし、日足を 1 本の特徴量として出す
- 日中 tick を模擬するモード（OHLC 4 点展開等）は Phase 3 以降の課題

## ストリーミング（Phase 3）の不変条件

- **順序保証**: 同一銘柄の tick は受信順に処理する。遅延到着は破棄してよいがログを出す
- **冪等性**: Pub/Sub のリトライで同一 tick が複数回届く可能性があるため、下流に副作用を持つ処理（position 更新）は `(symbol, timestamp)` のベキ等キーで重複排除
- **バックプレッシャ**: 指標計算が遅延した場合は publish をスキップせず、直近状態を保持したまま追いつく（欠損を作らない）
- **障害時**: Supabase 書き込み失敗は指数バックオフでリトライ。publish は at-least-once で十分

## ストレージ階層（Phase 3）

| 層 | 期間 | 実体 | 用途 |
|---|---|---|---|
| Hot | 当日 | メモリ + Pub/Sub | リアルタイム処理 |
| Warm | 1〜3 ヶ月 | `STORAGE_TICK_RESOLUTION` で集約した Parquet | 直近のリプレイ・検証 |
| Cold | それ以前 | 1 分足 / 5 分足 OHLCV Parquet | 長期バックテスト |

- `STORAGE_TICK_RESOLUTION=raw|1s|1m|5m` で Warm 層の粒度を制御
- Warm → Cold 移行は本サービスではなく `scripts/warm-to-cold-migration.py` に委譲（日次バッチ）
- Parquet のパーティション: `symbol=XXXX/date=YYYY-MM-DD/*.parquet`

## pnl リセット（Phase 3）

- 9:00 JST: `system_status.daily_pnl = 0`
- 月曜 9:00 JST: `weekly_pnl = 0`
- 月初営業日 9:00 JST: `monthly_pnl = 0`
- **`is_trading_allowed` は変更しない**。手動オペレーションを尊重する
- 祝日判定は `jpholiday` に委譲（universe-scanner と同じ扱い）

## 設定（env）

`.env.example` に列挙するキー例:
- `FEATURE_ENGINE_MODE`: `stream` | `backtest`
- `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY`
- `PUBSUB_PROJECT_ID` / `PUBSUB_SUBSCRIPTION_RAW` / `PUBSUB_TOPIC_FEATURES`（Phase 3）
- `STORAGE_TICK_RESOLUTION`: `raw` | `1s` | `1m` | `5m`（Phase 3）
- `STORAGE_WARM_DIR` / `STORAGE_COLD_DIR`: Parquet 書き出し先（Phase 3）
- `INDICATOR_SMA_SHORT_WINDOW` / `INDICATOR_SMA_LONG_WINDOW`: 移動平均の窓
- `INDICATOR_RSI_PERIOD`: RSI の期間
- `INDICATOR_BOLLINGER_PERIOD` / `INDICATOR_BOLLINGER_STDDEV`: ボリンジャーの窓と倍率

秘密情報は `.env.example` にダミー値で列挙、`.env` はコミットしない。

## テスト方針

- **ユニット**: 指標関数は入力 DataFrame とゴールデン出力の対で検証。外部依存なし
- **統合**: Phase 2 以降は Supabase（ローカル）への書き込みと `daily_ohlcv` 入力を組み合わせて検証
- **プロパティベース**: 指標の数学的性質（例: SMA は最小値と最大値の間に収まる）を `hypothesis` で確認
- カバレッジ 80%+（ルート方針）

## 開発時の注意

- **`trade-contracts` を破らない**: `ProcessedFeatures` に列を足すときは `contracts/` の変更手順（Pydantic → SQL → TS）に従う
- **Polars のみ**。pandas を依存に入れない
- 指標計算は**銘柄ごとに独立**に保つ。サービス全体で単一の巨大な状態機械を作らない
- Phase 境界では `strategy-rule` / `aggregator` など下流サービスの契約を壊さないこと（`ProcessedFeatures` は後方互換で拡張する）
- Pub/Sub の実装は Phase 3 で一括導入する。Phase 1 / 2 で仮実装を入れない
