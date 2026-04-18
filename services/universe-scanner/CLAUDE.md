# services/universe-scanner/

毎営業日 8:00 JST に起動する日次バッチ。J-Quants API から当日の監視銘柄リストを生成し、Supabase `watchlist` に書き込む。併せて参照データ（銘柄マスタ・日次 OHLCV）を `master_stocks` / `daily_ohlcv` に upsert する。

アーキテクチャ全体・スキーマはルート [CLAUDE.md](../../CLAUDE.md) と [contracts/](../../contracts/) を参照。ここは `services/universe-scanner/` 配下で作業するときの運用ルール。

## 責務 / 非責務

**責務**
- J-Quants API からの銘柄マスタ・日次 OHLCV 取得と Supabase へのキャッシュ
- 2段階フィルタによる当日監視銘柄（20〜50 銘柄）の選定
- `watchlist` への書き込み（`valid_date = 当営業日`）

**非責務**
- リアルタイム価格・板情報の取得 → Feeder
- テクニカル指標の算出（日次 OHLCV から計算する最小限のスコアリング指標を除く） → Feature Engine
- シグナル生成・売買判断 → Strategy A/B 以降
- 配信後の監視銘柄の動的な差し替え（日中追加/除外はしない。翌営業日の再実行で更新）

## ディレクトリ構成（想定）

```
services/universe-scanner/
├── CLAUDE.md                    # 本ファイル
├── pyproject.toml               # uv プロジェクト (trade-contracts ローカル参照)
├── Dockerfile
├── .env.example
├── src/universe_scanner/
│   ├── __init__.py
│   ├── __main__.py              # エントリポイント (asyncio)
│   ├── config.py                # pydantic-settings ベースの env 読み込み
│   ├── clients/
│   │   ├── jquants.py           # J-Quants API クライアント (リトライ・トークン更新)
│   │   └── supabase.py          # Supabase 書き込みクライアント (upsert ヘルパ)
│   ├── ingest/
│   │   ├── master_stocks.py     # 銘柄マスタ取得 → master_stocks upsert
│   │   └── daily_ohlcv.py       # 日次 OHLCV 取得 → daily_ohlcv upsert
│   ├── filters/
│   │   ├── static.py            # 第1段階: 流動性・価格帯・市場区分フィルタ
│   │   └── dynamic.py           # 第2段階: ボラ・テクニカル・出来高・モメンタムのスコアリング
│   ├── pipeline.py              # 全体オーケストレーション (ingest → filter → write)
│   └── calendar.py              # 営業日判定 (日本の株式市場カレンダー)
└── tests/
    ├── conftest.py
    ├── unit/                    # 各フィルタ・スコアリング関数
    ├── integration/             # Supabase (ローカル) への書き込みを含む
    └── fixtures/                # J-Quants API レスポンスのゴールデン
```

## パイプラインの段階

### 0. 営業日判定
- 当日が東証営業日でなければ何もせず終了（`exit 0`）
- 祝日カレンダーはハードコードせず、ライブラリ（`jpholiday` 等）に委譲

### 1. 参照データ取り込み (Ingest)
- **銘柄マスタ**: J-Quants `/listed/info` → `master_stocks` に upsert。`is_active=false` は論理削除のみ（行削除しない）
- **日次 OHLCV**: 前営業日終値までを `daily_ohlcv` に upsert。スコアリングで必要な過去日数（例: 60 営業日）分を確実に満たすこと

### 2. 第1段階フィルタ（静的）
- 全上場銘柄から 100〜300 銘柄に絞り込む
- 条件例: 市場区分（プライム/グロース）, 直近20日平均売買代金, 株価レンジ, ETF/REIT 除外
- 閾値は `config.py` で env 化し、ハードコードしない

### 3. 第2段階フィルタ（動的スコアリング）
- 100〜300 銘柄を対象に Polars で指標計算し、スコアで 20〜50 銘柄に絞り込む
- 指標例: 過去 20 日 ATR, RSI, 出来高急増率, セクター相対モメンタム
- スコアは重み付き合成。重みは `config.py` で env 化
- `selected_reasons` (jsonb) に各指標の寄与を残し、Dashboard で振り返り可能にする

### 4. 書き込み
- `watchlist` に `(symbol, valid_date)` で upsert。`valid_date = 当営業日（JST）`
- 書き込みはトランザクション 1 回にまとめ、中途半端な watchlist を残さない
- 旧 `valid_date` の行は削除しない（履歴として保持）

## J-Quants API の扱い

- リフレッシュトークンで ID トークンを定期更新。ID トークン有効期限（24h）に備えて自動更新
- レート制限に対応するため、指数バックオフ付きリトライを `clients/jquants.py` に集約
- プラン差（ライト/スタンダード/プレミアム）で利用可能エンドポイントが変わるため、`config.py` でプラン種別を明示し、使える指標のみに機能を制限する
- レスポンスは可能な限り `Polars.DataFrame` に早期変換し、pandas を介さない

## Supabase 書き込みの冪等性

- すべての書き込みは upsert（`ON CONFLICT` 相当）で実装し、同日再実行しても副作用が積み重ならないこと
- `watchlist` の同一 `(symbol, valid_date)` 再実行は**最新のスコア・選定理由で上書き**する仕様とする
- 書き込み途中での部分失敗をリカバリしやすいよう、ingest / filter / write の境界で明示的なログ（構造化ログ）を出す

## スケジューリング

- 本サービスは単体では cron を持たない。起動＝1回実行で終了する CLI として実装する
- 本番の日次実行は Kubernetes CronJob / Cloud Scheduler 等から呼ぶ（インフラ範囲外）
- 手動実行: `uv run python -m universe_scanner --date 2026-04-20`（省略時は当日 JST）

## バックフィル

- `--date` オプションで任意の過去営業日について再計算可能。過去日の watchlist を再現できるようにする
- `daily_ohlcv` が不足している場合、指定日以前の必要期間を自動取得してから処理する

## 設定（env）

`.env.example` に列挙するキー例:
- `JQUANTS_REFRESH_TOKEN`: J-Quants リフレッシュトークン
- `JQUANTS_PLAN`: `light` | `standard` | `premium`
- `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY`: 書き込み用
- `SCAN_STATIC_MIN_TURNOVER_JPY`: 静的フィルタの最低売買代金
- `SCAN_STATIC_PRICE_MIN` / `SCAN_STATIC_PRICE_MAX`: 許容株価レンジ
- `SCAN_DYNAMIC_TOP_N`: 最終的な watchlist サイズ上限（デフォルト 30）
- `SCAN_LOOKBACK_DAYS`: スコアリング用の過去日数（デフォルト 60）

秘密情報は `.env.example` にはダミー値で列挙、`.env` はコミットしない（ルート方針に準拠）。

## テスト方針

- **ユニット**: 各フィルタ・スコアリング関数は Polars の DataFrame を入出力とし、J-Quants / Supabase への依存を持たせない
- **統合**: `infra/docker-compose.test.yml` で起動した Supabase に対して実際に書き込み、`watchlist` の内容を検証する
- **ゴールデン**: J-Quants のレスポンスは `tests/fixtures/` に JSON で固定し、API 変更の検知に使う
- カバレッジ 80%+（ルート方針）

## 開発時の注意

- **価格・売買代金は `Decimal`**。`float` 演算で閾値判定しない（ルート contracts/ 規約に準拠）
- **Polars を使う**。pandas は依存に入れない
- スコアリング実装は `filters/dynamic.py` 内でプラガブルにし、アルゴリズムの A/B 比較をしやすくする
- `watchlist` に載らないと Feeder が購読しないため、**空の watchlist を絶対に書かない**（最低件数を下回る場合は前日リストを保持し、明示的にアラートログを出す）
- 本サービスは Pub/Sub を使わない（日次バッチで完結）。Feeder への通知は Supabase 側の変更通知 or 起動時読み取りに任せる
