# services/oms-live/

`live-orders` を購読し、kabu.com (auカブコム証券 kabuステーション API) へ実発注して、約定を Supabase `trades_live` に書き込み `positions`（`trade_type=live`）と `system_status.daily_pnl` を更新する Live OMS。

アーキテクチャ全体・スキーマはルート [CLAUDE.md](../../CLAUDE.md) と [contracts/](../../contracts/) を参照。ここは `services/oms-live/` 配下で作業するときの運用ルール。

## 責務 / 非責務

**責務**
- kabuステーション REST API への認証（トークン発行・保持・期限切れ再発行）
- `live-orders` (OrderRequest) の購読
- kabu `/sendorder` への実発注（成行 / 指値）
- 約定確定の確認（`/orders` 経由でのポーリング、または kabu PUSH 経由は将来検討）
- 約定行を Supabase `trades_live` に INSERT
- `positions`（`trade_type=live`）の UPSERT（BUY=新規 or 平均取得単価更新、SELL=全決済で DELETE）
- `system_status.daily_pnl` / `weekly_pnl` / `monthly_pnl` の加算（live 約定のみ）
- `system_status.trading_style=day` のとき 14:50 (JST) に live 全建玉を成行決済
- kabu `/positions` と Supabase `positions(live)` の整合性チェック (`reconciler.py`、`scripts/reconcile-positions.py` から起動)

**非責務**
- リスク検証・ロット計算 → Gateway（OMS は Gateway が承認した OrderRequest を信頼する）
- 板情報取得・PUSH 配信 → Feeder
- 擬似約定 → OMS Paper
- 指標計算 → Feature Engine
- スイング自動決済（stop_loss / target / trailing / max_hold_days） → 本サービスのスコープ外（Phase 4 以降）

## 接続経路の前提（重要）

**kabuステーション API は Windows http.sys レイヤーで `http://localhost:<port>/` でしか応答しない**（user メモ `kabu_localhost_only.md` 参照）。Feeder と同じ前提で、本サービスも Windows 機外から叩く場合は SSH ポートフォワード or Windows リバプロ (Caddy / nginx) を経由する。

- **開発・疎通**: SSH ポートフォワード `ssh -N -L 18081:127.0.0.1:18081 user@<win-ip>` で Linux 側 `localhost:18081` に張り付け
- **本番**: Windows 機の Caddy リバプロ経由で LAN IP の `28080` (本番) / `28081` (検証) に公開 (`Host: localhost` 強制で http.sys を満たす)。Linux 側からは `http://<win-ip>:28080/kabusapi` で叩ける
- 疎通確認は `scripts/probe-kabu.py` または `scripts/probe-kabu-oms.py` (`--port` で 28080/28081 を上書き可)

`KABU_API_BASE_URL` を env で受け、トンネル / リバプロのいずれでも差し替え可能にする。

## kabuステーション API の前提（実装で踏むべき仕様）

### 認証
- `POST /kabusapi/token` body `{"APIPassword": "..."}` → `{"Token": "<32 文字>"}`
  - **Feeder と OMS Live が同じ API パスワードを使うとトークンが奪い合いになる**。
    - 別の API パスワードを発行して別アカウント扱いにするか、Phase 3 で「トークン共有層」を立てるかは Phase 2 着手時に判断
    - 暫定は **Feeder と別パスワードを使う**前提でドキュメント化
  - 全 REST リクエストは `X-API-KEY: <token>` ヘッダ必須
  - 401/403 を踏んだら即 `invalidate_token()` → `fetch_token()` で再発行

### 発注 (現物株)
- `POST /kabusapi/sendorder`
  - `Symbol` (str): 銘柄コード
  - `Exchange` (int): **本番 (au カブコム証券) は `9` (SOR) 必須**で、`KABU_DEFAULT_EXCHANGE` のデフォルトは `9`。`1` (東証直) を指定すると `Code: 100378 "指定された市場でのお取引はお受けできません。"` で reject される (2026-05-07 本番実機検証)。東証直接ルーティングや `3=名証` 等を試したい場合のみ env で上書きする (現状 ETF / 立会外取引銘柄での `9` の可否は未検証で、本番常用前に individual override 経路を要検討)
  - `SecurityType` (int): 1=株式
  - `Side` (str): "1"=売, "2"=買
  - `CashMargin` (int): 1=現物
  - `DelivType` (int): BUY=2 (お預り), SELL=0 (現物売却時は不要)
  - `AccountType` (int): 4=特定 (推奨), 2=一般
  - `FundType` (str): BUY=`"AA"` (現物買付), SELL=`"  "` (半角空白 2 つ, 現物売却)
  - `Qty` (int): 株数
  - `FrontOrderType` (int): 10=成行, 20=指値
  - `Price` (float): 指値価格、成行時は 0
  - `ExpireDay` (int): 0=当日中
  - `Password` (str): **注文パスワード**（API パスワードとは別、`KABU_ORDER_PASSWORD`）
  - レスポンス: `{"Result": 0, "OrderId": "..."}` (Result=0 が成功)

### 注文取消
- `PUT /kabusapi/cancelorder` body `{"OrderId": "...", "Password": "..."}`
  - レスポンスは sendorder と同形式
  - Phase 1 では発注後の取消は実装しない（Runner 側でタイムアウト時に呼ぶ）

### 約定確認
- `GET /kabusapi/orders?id=<OrderId>` → 1 要素のリスト
  - `State` (int) / `OrderState` (int): 1=待機, 2=処理中, 3=処理済, 4=訂正中, 5=**終端** (約定完了 / 取消完了 / 失効 を包括)
    - 公式ドキュメントは「5=取消中」と表記するが、本番実機 (2026-05-07) ではライフサイクルの **終端ステータス** として返る (約定完了でも State=5 + CumQty>0)。
    - **State=3 は中間状態としても出現する**: 取引所に流れた直後 (Details RecType=1/4 のみ、約定 Detail RecType=8 未着) で `CumQty=0` のことがある。`State==3 + CumQty==0` を「拒否」と解釈してはならない (poll を継続する必要あり)。
  - `CumQty` (int): 約定済み数量
  - `OrderQty` (int): 発注数量
  - `Details` (list): 約定明細 `[{ExecutionID, ExecutionTime, Price, Qty, RecType, ...}, ...]`
    - `RecType` の意味: 1=受付, 4=発注, 8=約定 (約定 Detail を確実に判定するなら RecType=8 を見る)
- 約定確定の判定 (実機ベース):
  - 全量約定: `State==5 and CumQty == OrderQty`、または `State==3 and CumQty == OrderQty`
  - 部分約定: `0 < CumQty < OrderQty`
  - 取消 / 失効: `State==5 and CumQty==0`
  - **中間状態 (poll 継続)**: `State==3 and CumQty==0` または `State in {1,2,4}`
- 詳細な実装は `order_parser.py` の `to_fill_result` の docstring を参照

### ポジション・口座
- `GET /kabusapi/positions` (証券種別フィルタあり) → 残高一覧
  - 起動時に Supabase `positions` (live) と乖離していないかチェックする目的で使用
- `GET /kabusapi/wallet/cash` → 買付余力
- `GET /kabusapi/symbol/{code}@{exchange}` → 銘柄情報（呼値・上下限）
  - 検証環境（18081）では wallet/symbol が null を返す。Phase 3 e2e は本番（28080 / Caddy 経由）で実施

### 検証環境の制約
- 検証環境（18081）はトークン発行・GET 系 REST 経路（残高照会・板取得）まで 24h 通る
- ただし **`POST /sendorder` は黙殺される** (`Result: 0, OrderId: null` を返すが /orders にも /positions にも一切反映されない、2026-05-07 確認)。約定経路の検証には使えない
- **Phase 3 round-trip e2e は本番 (28080) 必須**。検証 (18081) で sendorder を試みないこと
- 本番接続も平日 9:00-15:00 JST の市場時間内に限る (約定が成立しないと poll がタイムアウトする)

## 実装フェーズ

oms-paper / gateway と同じ 3 フェーズパターン。段階コミット → `--no-ff` マージ。

### Phase 1: 純関数 + KabuClient + ユニットテスト

- `services/oms-live/` を uv workspace に追加 (`pyproject.toml` の members に追記)
- 依存: `httpx`, `pydantic-settings`, `tenacity`, `trade-contracts`
- **`kabu_client.py`** (薄い REST ラッパー):
  - `class KabuLiveClient`: `base_url` / `api_password`、`async fetch_token()` / `ensure_token()` / `invalidate_token()`
  - `async send_order(payload: dict) -> dict`: `POST /sendorder`（payload は order_builder が組み立てたもの）
  - `async cancel_order(order_id: str, password: str) -> dict`: `PUT /cancelorder`
  - `async get_order(order_id: str) -> dict`: `GET /orders?id=<id>` の 1 件目
  - `async list_positions(...) -> list[dict]`: `GET /positions`
  - `async get_symbol(symbol: str, exchange: int) -> dict`: `GET /symbol/{code}@{exchange}`
  - 4xx/5xx は `KabuApiError` で本文を露出（feeder の KabuClient と同じ方針）
- **`order_builder.py`** (純関数):
  - `build_sendorder_payload(order: OrderRequest, *, password: str, exchange: int, account_type: int) -> dict`
  - 現物株前提: `CashMargin=1`、`SecurityType=1`、BUY/SELL で `Side`/`DelivType`/`FundType` を出し分け
  - `OrderType.MARKET` → `FrontOrderType=10`, `Price=0`
  - `OrderType.LIMIT` → `FrontOrderType=20`, `Price=order.limit_price` (LIMIT は Phase 1 で型として通るが Runner では当面使わない)
- **`order_parser.py`** (純関数):
  - `parse_order_state(payload: dict) -> KabuOrderState`: kabu `/orders` 1 件分を内部モデルに変換
  - `to_fill_result(state: KabuOrderState) -> FillResult`: 約定確定済みの `KabuOrderState` から `FillResult` を抽出
  - 部分約定 (`State==3 and CumQty < OrderQty`)、未約定 (`CumQty==0`)、取消 (`State==5`) を `reason` で区別
- **`models.py`**: `KabuOrderState` / `ExecutionDetail` / `FillResult` / `LiveFillRecord`
- **`_testing.py`**: テスト用ファクトリ（`make_order_request`、`make_kabu_order_payload`）
- **テスト**: `tests/unit/test_live_*.py` プレフィックス（mypy duplicate-module 衝突回避）
  - `kabu_client`: `httpx.MockTransport` で 200/4xx 双方
  - `order_builder`: BUY/SELL/MARKET/LIMIT の payload 整合
  - `order_parser`: 完全約定 / 部分約定 / 未約定 / 取消 / 中間状態 (State=3 + CumQty=0 → pending) の各ケース

### Phase 2: ストリーミング Runner

- 2a: `clients/pubsub.py` (subscriber: `live-orders`)
- 2b: `clients/supabase.py` (`positions` / `trades_live` / `system_status` の R/W)
- 2c: `streaming/runner.py`
  - `live-orders` 受信 → `build_sendorder_payload` → `KabuLiveClient.send_order`
  - 即時の `OrderId` を取得後、`get_order` を短間隔ポーリング（数秒、上限 30s）で約定確定を待つ
  - 約定確定 → `parse_order_state` → `FillResult` → Supabase 書込（`trades_live` INSERT → `positions` UPSERT/DELETE → `system_status.daily_pnl` 加算）→ ack
  - タイムアウト時は `cancel_order` → ack（再送は冪等性問題があるので避ける）
- 2d: `streaming/closeout.py` + `position_updater.py`（oms-paper の同名モジュールをミラー）
- 2e: 14:50 JST cron で `system_status.trading_style=day` を確認 → live positions 全件 closeout
- LIMIT 注文は当面サポートしない (Aggregator が出さない前提)

### Phase 3: 本番環境での e2e (検証 18081 では sendorder 黙殺のため不可)

- **本番 (28080 / Caddy 経由) で実発注 round-trip e2e** を回す integration test
  - 平日 9:00-15:00 JST に限る（trading_calendar で skip マーカー切替）
  - 1 銘柄に対して buy → fill 確認 → sell → fill 確認 → positions / trades_live / daily_pnl が期待通り遷移するかチェック
- 安全装備 (`OMS_LIVE_MAX_QTY_PER_ORDER` / `OMS_LIVE_ALLOWED_SYMBOLS`) を必ず効かせる
- `OMS_LIVE_PHASE3_EXCHANGE=9` (SOR) を export すること。Exchange=1 (東証直) は本番で reject される
- 詳細手順は [docs/runbook/oms-live-phase3.md](../../docs/runbook/oms-live-phase3.md) 参照

## Positions reconciler

`reconciler.py` は kabu `/positions` と Supabase `positions(live)` の差分を分類する純関数を提供する。

**Why**: OMS Live の write path は `trades_live` 経由の約定だけを `positions` に反映するため、個人取引・kabu ステーションアプリ手動発注で発生した建玉は Supabase に乗らない。reconciler はこのズレを検出するためのもの (memory `positions_integrity.md` 参照)。

- 純関数: `parse_kabu_position` / `parse_kabu_positions` / `compute_position_diff` / `build_imported_position`
- I/O 適用層: `apply_reconcile_actions(actions, supabase, *, now, apply_imports, holding_type)`
- 起動 CLI: `scripts/reconcile-positions.py` (workspace ルートから `uv run python scripts/reconcile-positions.py`)

差分カテゴリと挙動:

| カテゴリ | dry-run (default) | `--apply` |
| --- | --- | --- |
| `to_import` (kabu only) | warning ログのみ | `insert_live_position` で取り込み (holding_type=swing 既定、各種閾値 None) |
| `quantity_mismatches` | warning ログのみ | warning のみ (**自動修正しない**) |
| `supabase_orphans` (supabase only) | warning ログのみ | warning のみ (**強制 DELETE しない** — 個人保有を巻き込まないため) |
| `matched` | no-op | no-op |

実行前提:
- **OMS Live と並行起動しないこと**。`positions` の write path 競合を避ける
- kabu `/positions` は GET なので市場時間外でも叩ける
- env: `KABU_API_BASE_URL` / `KABU_API_PASSWORD` / `SUPABASE_URL` / `SUPABASE_SECRET_KEY`
- `parse_kabu_position` は `Side="2"` (買建) のみ受け付ける。`LeavesQty + HoldQty == 0` はスキップ

将来 Runner の起動シーケンスに組み込む場合は `compute_position_diff` を流用。`apply_reconcile_actions` は dry-run/apply の分岐を持つので、起動時は `apply_imports=False` で warning のみ出すのが安全 (現行 CLI と同じ挙動)。

## ディレクトリ構成（想定）

```
services/oms-live/
├── CLAUDE.md                    # 本ファイル
├── pyproject.toml               # uv プロジェクト (trade-contracts ローカル参照)
├── .env.example
├── src/oms_live/
│   ├── __init__.py
│   ├── __main__.py              # CLI: stream (Phase 2 以降)
│   ├── config.py                # pydantic-settings ベースの env 読み込み
│   ├── models.py                # Phase 1 内部型 (KabuOrderState, FillResult, LiveFillRecord)
│   ├── kabu_client.py           # Phase 1 REST ラッパー
│   ├── order_builder.py         # Phase 1 OrderRequest -> sendorder payload (純関数)
│   ├── order_parser.py          # Phase 1 /orders 応答 -> KabuOrderState / FillResult (純関数)
│   ├── reconciler.py            # kabu /positions と Supabase positions(live) の整合性チェック
│   ├── _testing.py              # テスト用ファクトリ
│   ├── clients/                 # Phase 2
│   │   ├── pubsub.py
│   │   └── supabase.py
│   └── streaming/               # Phase 2
│       ├── __init__.py
│       ├── position_updater.py
│       ├── closeout.py
│       └── runner.py
└── tests/
    ├── __init__.py
    ├── unit/
    │   ├── __init__.py
    │   ├── test_live_kabu_client.py
    │   ├── test_live_order_builder.py
    │   └── test_live_order_parser.py
    └── integration/
        └── test_e2e.py
```

## 設計上の規約（Phase 1）

- 価格・金額は **必ず `Decimal`**（`float` 禁止）。kabu API は JSON で `float` 由来の値を返すので、パース時に `Decimal(str(value))` で必ず文字列経由
- 数量は `int`
- `KabuOrderState.state` は kabu の `State` フィールド (1〜5) をそのまま保持（StrEnum で抽象化しない、kabu 側仕様変更時の影響を最小化）
- `FillResult.reason` は機械可読コード:
  - `"filled"` 全量約定 (`State==3 and CumQty==OrderQty`)
  - `"partial"` 部分約定 (`State==3 and 0 < CumQty < OrderQty`)
  - `"pending"` 未約定 (`CumQty==0` かつ `State in {1,2,4}`)
  - `"cancelled"` 取消済 (`State==5`)
  - `"rejected"` 受付失敗（`Result != 0` を sendorder で踏んだ場合に Runner が組み立て）
- `unified_signal_id` は `OrderRequest` から `LiveFillRecord` へそのまま継承（`trades_live.unified_signal_id` は nullable FK）。closeout 由来は対応する `aggregator_logs` 行を持たないため `None` で書き込む
- 純関数は I/O・時刻・乱数を持ち込まない。`uuid4()` は `LiveFillRecord` のデフォルト値で発生するが、テストでは固定の `trade_id` を渡せる
- LIMIT 注文は payload まで生成可能だが、Phase 1 のテストは MARKET を主軸（LIMIT 1 ケースで型整合確認のみ）

## Supabase 連携の規約（Phase 2）

- `positions` は `(symbol, trade_type='live')` で UPSERT / DELETE
- `trades_live` INSERT は約定 1 回につき 1 行（部分約定は 1 件を 1 行で記録、複数 ExecutionDetail は VWAP に集約してから 1 行で書く）
- `system_status` は live 約定のたびに `daily_pnl` 等を加算
  - 売却 SELL → `(price - entry_price) * qty` を加算（買付 BUY 時は損益確定なし）
  - リセットは Feature Engine の責務（9:00 JST に daily_pnl=0）
- 書き込み順序: `trades_live` INSERT → `positions` UPSERT/DELETE → `system_status.daily_pnl` 加算 → ack

## Pub/Sub 連携の規約（Phase 2）

- 購読: `live-orders`（subscription 名は env `PUBSUB_SUBSCRIPTION_LIVE_ORDERS`、デフォルト `oms-live-live-orders`）
- `ack` は Supabase 書込 + kabu 約定確定後のみ。kabu 取消含む確定状態に達したらすべて ack（再送は冪等性問題のため避ける）
- 二重約定回避: `OrderRequest.order_id` を `LiveFillRecord.order_id` に carry し、`trades_live.order_id`（partial unique index、SQL マイグレーション 010）に書き込む。Runner は sendorder の前に `SupabaseClient.live_trade_exists_for_order_id` で重複検知し、True なら sendorder せず ack して `BatchStats.skipped_duplicate` を計上する。これで「前回 sendorder + Supabase 書込が完全成功した後に redeliver」のケースは二重発注を完全に防ぐ。`closeout` 由来の `OrderRequest` は `order_id=uuid4()` で常時 unique なので check は不要 (Runner では行わない)。なお「sendorder 成功 + Supabase 書込失敗で再配信」のケースは trades_live に行が無いため check は通り再走する — このシナリオは別途 `_process_order` を fail-fast にする方向で扱う Phase が必要

## 設定（env）

`.env.example` に列挙するキー例:
- `OMS_LIVE_MODE`: `stream` | `dry-run`
- `KABU_API_BASE_URL`: 例 `http://192.168.x.y:28080/kabusapi` (本番、Caddy 経由)。SSH トンネル経由なら `http://localhost:18081/kabusapi` (検証) など
- `KABU_API_PASSWORD`: API パスワード（**Feeder と別パスワード推奨**）
- `KABU_ORDER_PASSWORD`: 注文パスワード（API パスワードとは別）
- `KABU_DEFAULT_EXCHANGE`: `9` (SOR、デフォルト)。**au カブコム証券では SOR 必須**で、`1` (東証直) は `Code: 100378` で reject されるため本番常用に対応してデフォルトを `9` にしている。東証直 / 名証等を試す場合のみ env で上書き
- `KABU_ACCOUNT_TYPE`: `4` (特定)
- `KABU_HTTP_TIMEOUT_SECONDS`: `10.0`
- `SUPABASE_URL` / `SUPABASE_SECRET_KEY`
- `PUBSUB_PROJECT_ID` / `PUBSUB_EMULATOR_HOST`
- `PUBSUB_SUBSCRIPTION_LIVE_ORDERS`: `oms-live-live-orders`
- `DAY_CLOSEOUT_TIME`: `14:50`
- `DAY_CLOSEOUT_TIMEZONE`: `Asia/Tokyo`
- `ORDER_FILL_POLL_INTERVAL_SECONDS`: `1.0`
- `ORDER_FILL_TIMEOUT_SECONDS`: `30.0`

**Phase 3 安全装備 (本番投入前のセーフティネット)**:
- `OMS_LIVE_MAX_QTY_PER_ORDER`: 1 注文あたり最大株数。空欄で無制限。Runner の `_process_order` で existence check の後に評価。違反は `safety_rejected` で ack。**closeout には適用しない** (持ち越し決済を阻害しないため)
- `OMS_LIVE_ALLOWED_SYMBOLS`: カンマ区切り (例: `7203,9984`)。空欄で全銘柄許可。違反は `safety_rejected`。**closeout には適用しない**
- `OMS_LIVE_DRY_RUN`: `true` で sendorder/Supabase 書込を一切行わず ack のみ。`run_closeout` も即 `skipped_reason=dry_run` で no-op。Phase 3 検証中の安全弁

秘密情報は `.env.example` にダミー値で列挙、`.env` はコミットしない。

## テスト方針

- **ユニット (Phase 1)**:
  - `kabu_client`: `httpx.MockTransport` で token / sendorder / get_order / cancelorder / positions / symbol を網羅。4xx は `KabuApiError` の本文露出を確認、`invalidate_token` で再発行が走ることを確認
  - `order_builder`: BUY/SELL の `Side`/`DelivType`/`FundType` 分岐、MARKET/LIMIT の `FrontOrderType`/`Price`、Password がペイロードに乗ること
  - `order_parser`: 完全約定 / 部分約定 / 未約定 / 取消 / 想定外 State の reason マッピング、ExecutionDetail の Decimal 化
- **統合 (Phase 2)**: Pub/Sub エミュレータ + ローカル Supabase + httpx MockTransport の kabu モックで end-to-end
- **e2e (Phase 3)**: 本番 (28080 / Caddy 経由) で実発注（市場時間内、SOR Exchange=9 必須）。検証 (18081) は sendorder 黙殺で round-trip 不可
- カバレッジ 80%+ (ルート方針)

## 開発時の注意

- **本番資金に直結する**。oms-paper で先行検証してから OMS Live にミラーする
- **fail-closed**: kabu 4xx / Supabase 書込失敗時は約定を確定しない（at-least-once + 上流 retry）
- **`OrderRequest.trade_mode != live` は OMS Live で受けた時点で reject**（Gateway がルーティングを誤った場合の防御）
- **空売り禁止**: `PositionSide=LONG` のみ。SELL は LONG 決済のみ
- **キルスイッチ判定の更新は本サービスが行う**（OMS Paper との最大の差分）。`daily_pnl` 加算後の `<= -daily_loss_limit` 検出は Gateway の責務だが、`daily_pnl` を書き込むのは OMS Live
- **Phase 1 では Pub/Sub / Supabase / 時刻を一切触らない**。`clients/` と `streaming/` は Phase 2 でまとめて導入
- **`trade-contracts` を破らない**: 既存型で表現できないなら `contracts/` の 3 層同期手順に従って拡張
- **kabu API のテストは httpx MockTransport で完結**。実 API を叩く integration test は Phase 3 まで書かない
