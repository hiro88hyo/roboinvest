# services/feeder/

kabu.com（auカブコム証券 kabuステーション API）の WebSocket PUSH を購読し、約定 Tick と板情報を `TickData` / `OrderBookSnapshot` に変換して Pub/Sub `raw-market-data` にパブリッシュするストリーミングサービス。Supabase `watchlist` の差分を監視し、対象銘柄を kabuステーションに `/register` / `/unregister` する。

アーキテクチャ全体・スキーマはルート [CLAUDE.md](../../CLAUDE.md) と [contracts/](../../contracts/) を参照。ここは `services/feeder/` 配下で作業するときの運用ルール。

## 責務 / 非責務

**責務**
- kabuステーション REST API への認証（トークン発行・保持・期限切れ再発行）
- Supabase `watchlist` の起動時読込 + Realtime 購読、差分に応じた `/register` / `/unregister`
- WebSocket `/kabusapi/websocket` への接続維持、PUSH メッセージのパース
- PUSH JSON → `TickData`（約定 1 件） / `OrderBookSnapshot`（板スナップショット）の純粋変換
- live WebSocket の板に、exchange event 時刻とは別の Feeder 受信時刻
  `received_at` を付与（replay / legacy payload は `None`）
- Pub/Sub `raw-market-data` への publish
- WS 切断時の指数バックオフ再接続 + 再 `/register`

**非責務**
- 指標計算・テクニカル分析 → Feature Engine
- 監視銘柄の選定 → Universe Scanner（Feeder は watchlist を「与えられたもの」として扱う）
- 板から約定を組み立てる擬似約定 → OMS Paper
- Tick の永続化 → Feature Engine（Hot/Warm/Cold ストレージは Feature Engine 側）
- バックテスト用の入力供給 → Feature Engine が `daily_ohlcv` から直接読む

## 接続経路の前提（重要）

**kabuステーション API は Windows http.sys レイヤーで `http://localhost:<port>/` でしか応答しない**（詳細は user メモ `kabu_localhost_only.md`）。Feeder は kabuステーション機（Windows）と別ホストで運用する方針なので、TCP 経路を「Windows 上で localhost に見える」状態にする必要がある。

- **開発・疎通**: SSH ポートフォワード `ssh -N -L 18081:127.0.0.1:18081 user@<win-ip>` で Linux 側 `localhost:18081` に張り付けて接続
- **本番**: Windows 機に nginx を立てて `http://<win-ip>:<port>/` → `http://127.0.0.1:18081/`（`proxy_set_header Host localhost;` 必須、WebSocket upgrade 設定必須）
- 疎通確認は `scripts/probe-kabu.py` を流用

Feeder のコードは「`KABU_API_BASE_URL` / `KABU_WS_URL` を env で受ける」設計にし、トンネル / リバプロのどちらでも差し替えられるようにする。

## kabuステーション API の前提（実装で踏むべき仕様）

### REST
- `POST /kabusapi/token` body `{"APIPassword": "..."}` → `{"Token": "<32 文字>"}`
  - トークンは 1 つの API パスワードにつき 1 本（新規発行で旧トークン無効化）
  - 全 REST/WS リクエストは `X-API-KEY: <token>` ヘッダ必須（WS 接続もヘッダ必須）
- `PUT /kabusapi/register` body `{"Symbols": [{"Symbol": "7203", "Exchange": 1}, ...]}`
  - **登録した銘柄だけ** PUSH 配信が来る。watchlist の symbol を全部 register する必要あり
  - 一度の register でクリアされない＝差分追加でなく **全置換** で来るかは要検証（Phase 1 で確認、本 CLAUDE.md は後で更新）
- `PUT /kabusapi/unregister` body 同上
- `PUT /kabusapi/unregister/all`
- `GET /kabusapi/symbol/{code}@{exchange}` 銘柄情報（呼値・上下限値幅など）
- 検証環境（18081）は wallet/symbol が null を返す。本番（18080）でしか実データは出ない

### WebSocket
- `ws://<host>:<port>/kabusapi/websocket`
- 接続時 `X-API-KEY` ヘッダで認証
- メッセージは JSON 1 つ = 1 イベント
- イベント種別の判別はトップレベルフィールドで:
  - 約定 Tick: `CurrentPrice`, `CurrentPriceTime`, `TradingVolume` などが入る → `TickData`
  - 板スナップショット: `Buy1`...`Buy10` / `Sell1`...`Sell10` 構造（各レベル `{Price, Qty, Sign}`）→ `OrderBookSnapshot`
    差分配信のためレベル変化がなければ省略される。`BidPrice`/`BidQty` / `AskPrice`/`AskQty` はベスト気配の単一値で、常に含まれることが多い（parser が fallback として 1-level book を生成）
  - 同じメッセージに両方含まれることがある（kabu の WS は「銘柄状態の差分通知」スタイル）→ 1 メッセージから複数の contracts レコードが派生し得る
- 配信は **ザラ場時間（9:00-11:30, 12:30-15:00 JST）のみ**。それ以外は接続維持はできるが PUSH は来ない
- ハートビート / keepalive 仕様は kabuステーション側で定義あり。`websockets` ライブラリの `ping_interval` デフォルトで概ね問題なし、要観測

### 取引所コード
- `1=東証`, `3=名証`, `5=福証`, `6=札証`, `2=日通し PTS`（参考）
- watchlist には `symbol` のみ保持しているが、Feeder は通常 `Exchange=1`（東証）を仮定。将来的に watchlist に `exchange` カラムを足す案は contracts 拡張で扱う

### トークンライフサイクル
- 公式仕様上は当日有効。日付跨ぎ・kabuステーション再起動・API パスワード変更で無効化
- Feeder は 401/403 を検知したら即 `invalidate_token()` → トークン再発行 → 再 register → WS 再接続
- **Feeder と OMS Live のトークン共有**: `KABU_TOKEN_CACHE_FILE`（デフォルト `/tmp/kabu_token_cache.json`）に同じパスを設定する。先に起動したサービスがキャッシュに書き込み、後発のサービスは POST /token を叩かずにキャッシュから読む。両サービスの env を揃えること

## 実装フェーズ

oms-paper / gateway と同じ 3 フェーズパターン。段階コミット → `--no-ff` マージ。Feeder は I/O 中心なので「純関数 Phase 1」は kabu PUSH JSON → contracts の変換に閉じる。

### Phase 1: scaffolding + kabu API クライアント + 純粋変換

- `services/feeder/` を uv workspace に追加 (`pyproject.toml` の members に追記)
- 依存: `httpx`, `websockets`, `pydantic-settings`, `trade-contracts`（あと dev に `pytest-asyncio`）
- `kabu_client.py`: 薄いラッパー
  - `class KabuClient`: コンストラクタで `base_url` / `api_password`、`async fetch_token()` / `async register(symbols)` / `async unregister(symbols)` / `async unregister_all()` / `async get_symbol(code, exchange)`
  - `connect_websocket()` は `async contextmanager` で `websockets.connect` を返す（ハートビートは library 任せ）
  - kabu の 4xx は HTML を返すので `_check()` で本文を露出（`scripts/probe-kabu.py` と同じ方針）
- `parser.py`: 純関数 `parse_push_message(payload: dict) -> list[TickData | OrderBookSnapshot]`
  - フィールド有無で Tick / OrderBook を判別、両方含むメッセージは 2 件返す
  - 価格は **必ず `Decimal`**、`CurrentPriceTime` は `datetime`（タイムゾーンは JST、Pydantic 側で aware 保持）。live session は受信点の aware UTC 時刻を `OrderBookSnapshot.received_at` に別途設定
- ユニットテスト:
  - kabu_client: httpx の MockTransport で 200/4xx 双方
  - parser: kabu PUSH の JSON 例（fixtures に置く）→ TickData / OrderBookSnapshot 期待値

### Phase 2: watchlist 連携 + 再接続 + symbol 差分管理

- `clients/supabase.py`: `watchlist` の `(symbol, valid_date=today)` SELECT、Realtime 購読
- `streaming/registry.py`: 現在 register 済み symbol セットを保持し、新 watchlist との差分から `add` / `remove` を計算（純関数）
- `streaming/reconnect.py`: 指数バックオフ（初期 1s, 最大 60s, jitter あり）
- `streaming/session.py`: 「token 取得 → register → WS 接続 → メッセージループ → 切断検知 → backoff → 再試行」の状態機械
- 単体テスト: registry の差分計算、reconnect の wait sequence、session の状態遷移（kabu / Supabase はモック）
- まだ Pub/Sub publish はしない（コンソールログ or stdout JSONL に流して Phase 3 と切り分け）

### Phase 3: Pub/Sub publisher + streaming runner + CLI + e2e

- 3a: `clients/pubsub.py`（publisher のみ。topic は `raw-market-data`）
- 3b: `streaming/runner.py` を Phase 2 の session に publish を組み込んで完成
  - メッセージ 1 件パース → 派生レコード 0..2 件 → publish → ack ループ
  - publish 失敗時の挙動: 短時間 retry 後に WS 再接続させる（at-least-once は許容）
- 3c: CLI `python -m feeder stream`、e2e は kabu モック WS サーバ + Pub/Sub エミュレータ
  - 録画した PUSH JSONL を replay する `--replay <file>` モードを足すと、本番接続なしで feature-engine 結合確認できる

## ディレクトリ構成（想定）

```
services/feeder/
├── CLAUDE.md                    # 本ファイル
├── pyproject.toml               # uv プロジェクト (trade-contracts ローカル参照)
├── Dockerfile
├── .env.example
├── src/feeder/
│   ├── __init__.py
│   ├── __main__.py              # CLI: stream / probe / replay
│   ├── config.py                # pydantic-settings ベースの env 読み込み
│   ├── kabu_client.py           # Phase 1 REST + WS thin wrapper
│   ├── parser.py                # Phase 1 PUSH JSON → TickData / OrderBookSnapshot (純関数)
│   ├── clients/                 # Phase 2/3
│   │   ├── supabase.py          #   watchlist reader + Realtime
│   │   └── pubsub.py            #   Phase 3: raw-market-data publisher
│   └── streaming/               # Phase 2/3
│       ├── __init__.py
│       ├── registry.py          #   register 済み symbol 集合の差分管理 (純関数)
│       ├── reconnect.py         #   指数バックオフ
│       ├── session.py           #   token → register → WS の状態機械
│       └── runner.py            #   Phase 3: publish 込みのメインループ
└── tests/
    ├── unit/
    ├── integration/             # Phase 3: kabu モック WS + Pub/Sub エミュレータ
    └── fixtures/
        └── push_samples/        #   実機 PUSH JSON サンプル（Phase 1 で収集）
```

`tests/conftest.py` は作らず、共有フィクスチャは `src/feeder/_testing.py` に置く（user メモ `feedback_mypy_test_layout.md` 参照）。

## 設定（env）

`.env.example` に列挙するキー例:
- `FEEDER_MODE`: `stream` | `replay`
- `KABU_API_BASE_URL`: 既定 `http://localhost:18081/kabusapi`（SSH トンネル前提）
- `KABU_WS_URL`: 既定 `ws://localhost:18081/kabusapi/websocket`
- `KABU_API_PASSWORD`: 必須（秘密、`.env` でのみ）
- `KABU_DEFAULT_EXCHANGE`: 既定 `1`（東証）
- `SUPABASE_URL` / `SUPABASE_SECRET_KEY`
- `PUBSUB_PROJECT_ID` / `PUBSUB_EMULATOR_HOST`
- `PUBSUB_TOPIC_RAW_MARKET_DATA`: `raw-market-data`
- `RECONNECT_INITIAL_BACKOFF_SEC`: `1.0`
- `RECONNECT_MAX_BACKOFF_SEC`: `60.0`

`.env.example` はダミー値で全キーを列挙、`.env` はコミット禁止。

## 開発時の注意

- **kabu API は localhost 専用**。Linux 直叩きでテストしようとすると http.sys に弾かれる。必ずトンネル / リバプロ経由で
- **トークンは 1 本制約**: 同じ API パスワードで複数プロセスが `POST /token` を叩くと先発が無効化される。Feeder は単一プロセス前提
- **register は state-full**: WS 切断時に register 状態が kabu 側で保持されるかは未検証。再接続時は念のため `unregister/all` → 再 `register` を行う方が安全（Phase 2 で確認）
- **PUSH は ザラ場のみ**: 開発時間帯で動作確認するときは `--replay` モードか fixtures 利用に倒す
- **Decimal 厳守**: kabu PUSH の数値は JSON では float で来る。受信時に `Decimal(str(value))` で文字列経由で変換し、誤差を避ける
- **検証環境（18081）は dryrun 用途**: wallet/symbol が null を返す。データ流量や JSON 形状の確認には十分だが、銘柄マスタ系のテストは本番（18080）でやる必要あり
- **API クォータ消費に注意**: 本番モード接続テストは銘柄数を最小限に絞る（1〜2 銘柄）。Phase 3 e2e は kabu モック WS で完結させる
- **`trade-contracts` を破らない**: kabu 由来の追加情報（板の枚数、特殊気配など）を表現したくなったら、まず contracts 拡張の 3 層同期手順を踏む
- **fail-closed は不要**: at-least-once 配信を前提とした publish なので、publish 失敗時は WS 再接続で取り直し、重複は下流（feature-engine / oms-paper）の冪等性で吸収
