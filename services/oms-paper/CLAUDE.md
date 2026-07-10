# services/oms-paper/

`paper-orders` を購読し、`raw-market-data`（`OrderBookSnapshot`）を擬似約定の判定材料として、約定を `trades_paper` に書き込み `positions`（`trade_type=paper`）を更新する Paper OMS。

アーキテクチャ全体・スキーマはルート [CLAUDE.md](../../CLAUDE.md) と [contracts/](../../contracts/) を参照。ここは `services/oms-paper/` 配下で作業するときの運用ルール。

## 責務 / 非責務

**責務**
- `paper-orders` (OrderRequest) の購読
- `raw-market-data` (OrderBookSnapshot) の購読・symbol 別の最新板キャッシュ
- 板情報を元にした擬似約定判定（フル約定 / 部分約定 / 不約定）
- 約定行を Supabase `trades_paper` に INSERT
- `positions`（`trade_type=paper`）の UPSERT（BUY=新規 or 平均取得単価更新、SELL=全決済で DELETE）
- `system_status.trading_style=day` のとき 14:50 (JST) に
  `holding_type=day` の paper 建玉だけを仮想成行決済（swing は保持）
- `holding_type=swing` の paper 建玉に対する自動決済（`stop_loss_price` 割れ / `target_price` 到達 / `max_hold_days` 経過）と `trailing_stop_pct` による `stop_loss_price` 切り上げ。板更新トリガで評価し、決済時は closeout と同形式の `unified_signal_id=None` / `signal_source=CONSENSUS` で約定行を書く

**非責務**
- リスク検証・ロット計算 → Gateway（OMS は Gateway が承認した OrderRequest を信頼する）
- `system_status.daily_pnl` 更新（paper はキルスイッチ集計外） → そもそも対象外
- 実発注 → OMS Live
- 板情報・テクニカル指標生成 → Feature Engine

## 実装フェーズ

aggregator / strategy-rule / gateway と同じ 3 フェーズパターン。段階コミット → `--no-ff` マージ。

### Phase 1: 擬似約定のコア（純関数 + unit test）

- **fill_simulator** `fill_simulator.py`: 入力 `OrderRequest` + `OrderBookSnapshot` → `FillResult`
  - `OrderType.MARKET` の `BUY` は asks を低い価格から食い潰し、`SELL` は bids を高い価格から食い潰す
  - 必要数量を満たせない場合は部分約定（`filled_quantity < quantity`）
  - 板が空 / 該当方向の価格レベルが無い場合は不約定（`filled_quantity=0`）
  - 約定価格は数量加重平均価格（VWAP）。`Decimal` で計算し、最終丸めは `ROUND_HALF_UP` で 1 円単位（市場慣習）に揃える
  - `OrderType.LIMIT` の `BUY` は `ask.price <= limit_price` の板のみを食い、
    `SELL` は `bid.price >= limit_price` の板のみを食う。条件に合う板がなければ
    不約定 + 理由 `limit_not_crossed`
- **position_updater** `position_updater.py`: 入力 `FillResult` + 既存 `PaperPosition | None` → `PositionUpdate`
  - `BUY` で既存ポジションなし → 新規ポジション（`entry_price = fill_price`）。
    `stop_loss_pct` があれば実約定値から
    `stop_loss_price = fill_price * (1 - stop_loss_pct)` を固定
  - `BUY` で既存 LONG あり → 数量加算 + 平均取得単価更新。既存の
    holding/stop/max-hold/scheduled-exit metadata は維持
  - `SELL` で LONG あり → 数量減算（残量 0 で `delete=True`）
  - `SELL` で LONG なし → エラー（Gateway 側で reject されている前提なのでログ + 約定スキップ）
- **closeout** `closeout.py`: 入力 `list[PaperPosition]` → 決済用 `OrderRequest` リスト（純関数）
  - `holding_type=day` の各ポジションに対し `Side.SELL` /
    `OrderType.MARKET` / `quantity = position.quantity` の `OrderRequest` を生成。
    `holding_type=swing` は対象外
  - closeout 由来の注文は対応する `aggregator_logs` 行を持たないため、`unified_signal_id` は `None` で出力する（`trades_paper.unified_signal_id` は nullable FK）
- I/O・時刻・DB・Pub/Sub を持ち込まない純関数だけで構成する

### Phase 2: バックテストランナー

- 入力:
  - `OrderRequest` JSONL（gateway Phase 2 の `--output-approved`）
  - `OrderBookSnapshot` JSONL（feature-engine の Phase 2 出力 or 専用フィクスチャ）
  - 初期 `PaperPosition` JSON（オプション）
- 動作: 時刻順マージしながらシンボル別に最新の板を保持し、注文が来たら直前の板でフルフィル試行 → 結果を出力
- 出力:
  - 約定 JSONL（`PaperFillRecord` = 約定行 + 元 OrderRequest 参照）
  - 終了時 positions JSON
  - 不約定ログ JSONL（理由付き）
- CLI: `uv run python -m oms_paper backtest --orders ... --books ... --positions ... --output-fills ... --output-positions ... --output-rejected ...`

### Phase 3: ストリーミング実装

- 3a: `clients/pubsub.py`（subscriber × 2、publisher は不要）/ `clients/supabase.py`（`positions`, `trades_paper`, `system_status` の R/W）
- 3b: `streaming/runner.py` — paper-orders と raw-market-data を joiner で突き合わせる
  - シンボル別に最新の `OrderBookSnapshot` をメモリ保持
  - paper-orders 受信時、対応シンボルの板を引いて擬似約定 → Supabase 書き込み → ack
  - 板未受信のシンボルへの注文は短時間（数百 ms）待機 → タイムアウトで不約定 ack（再配信は冪等性のため避ける）
  - 14:50 (JST) cron タスクが `system_status.trading_style=day` を確認し、
    `holding_type=day` の paper positions だけを closeout へ
- 3c: CLI `stream` サブコマンド + e2e テスト（Pub/Sub エミュレータ + ローカル Supabase）

### Phase 4: スイング自動決済

- `swing_monitor.py` (純関数 `evaluate_swing_exit`): `PaperPosition` + 最新価格 + `now` から `SwingDecision`（`exit` / `trail` / `hold`）を返す。判定優先順位は `stop_loss > target > max_hold_days > trail > hold`。trailing は単調増加のみで `ROUND_HALF_UP` で 1 円丸め
- `clients/supabase.py` に `update_paper_position_stop_loss`（`stop_loss_price` 列のみ PATCH）を追加
- `streaming/runner.py`:
  - `swing_position_cache: dict[symbol, PaperPosition]` を 30 秒 TTL で `list_paper_positions` から再フェッチ（`holding_type=swing` のみ保持）
  - `_consume_books` で更新があった symbol について `evaluate_swing_exit` を呼ぶ
  - `exit` → closeout と同形式の SELL 注文を組んで `simulate_fill` → `apply_fill` → `trades_paper` INSERT → `positions` DELETE。`unified_signal_id=None`、`signal_source=CONSENSUS`
  - `trail` → `update_paper_position_stop_loss` のみ。`trades_paper` には書かない
  - 評価に使う最新価格は `book.bids[0].price`（最良買気配）。bids 空なら `swing_no_fills` で計上してスキップ
  - 書込失敗時は cache を維持し、次の板で retry
- `BatchStats` に `swing_exits` / `swing_trails` / `swing_no_fills` / `swing_write_errors` を追加（default=0）

## ディレクトリ構成（想定）

```
services/oms-paper/
├── CLAUDE.md                    # 本ファイル
├── pyproject.toml               # uv プロジェクト (trade-contracts ローカル参照)
├── .env.example
├── src/oms_paper/
│   ├── __init__.py
│   ├── __main__.py              # エントリポイント (CLI: stream / backtest)
│   ├── config.py                # pydantic-settings ベースの env 読み込み
│   ├── fill_simulator.py        # Phase 1 板情報からの擬似約定 (純関数)
│   ├── position_updater.py      # Phase 1 ポジション遷移 (純関数)
│   ├── closeout.py              # Phase 1 14:50 強制決済の OrderRequest 生成 (純関数)
│   ├── swing_monitor.py         # Phase 4 スイング自動決済の判定 (純関数)
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

## 擬似約定ロジックの規約（Phase 1）

- 通貨・金額・価格は **必ず `Decimal`**（`float` 禁止）。数量は `int`
- `FillResult` は不約定時も同じ型で返す（`filled_quantity=0`, `fill_price=None`, `reason="empty_book"` 等）
- VWAP 計算:
  ```
  remaining = order.quantity
  consumed = []  # [(price, qty), ...]
  for level in book_side:  # BUY なら asks の昇順, SELL なら bids の降順
      take = min(level.quantity, remaining)
      consumed.append((level.price, take))
      remaining -= take
      if remaining == 0:
          break
  filled_qty = sum(qty for _, qty in consumed)
  vwap = sum(price * qty for price, qty in consumed) / filled_qty   # ROUND_HALF_UP, 1 円単位
  ```
- 単元株未満の部分約定も Phase 1 では許容する（実運用でも約定通知は単元未満で来うる）
- `unified_signal_id` は `OrderRequest` から `trades_paper` 行へそのまま継承する。closeout 由来は `None`（`trades_paper.unified_signal_id` は nullable FK で対応する `aggregator_logs` 行を持たない）

## Supabase 連携の規約（Phase 3）

- `positions` は `(symbol, trade_type='paper')` で UPSERT / DELETE
  - DELETE は `quantity=0` になったとき（部分約定で残量があれば UPDATE）
- `trades_paper` INSERT は約定 1 回につき 1 行
- `system_status` は 14:50 cron で `.eq("id", 1).single()` のみ読み取り（OMS Paper は更新しない）
- `unrealized_pnl` 更新は **Feature Engine の責務**。OMS Paper は `entry_price` / `quantity` のみ書く
- 書き込み順序: `trades_paper` INSERT → `positions` UPSERT/DELETE → ack
- 上記 2 書き込みは現状 1 transaction ではない。trade INSERT 後の position
  書き込み失敗を redelivery だけでは修復できないため、event publisher を
  解除する前に idempotent な transactional RPC へ置き換える

## Pub/Sub 連携の規約（Phase 3）

- 購読:
  - `paper-orders`（subscription 名は env `PUBSUB_SUBSCRIPTION_PAPER_ORDERS`、デフォルト `oms-paper-paper-orders`）
  - `raw-market-data`（env `PUBSUB_SUBSCRIPTION_RAW_MARKET_DATA`、デフォルト `oms-paper-raw-market-data`）
- メッセージ型は payload 中の `symbol` / 板構造で `OrderBookSnapshot` か `TickData` を判別。`TickData` は無視
- `ack` は Supabase 書き込み成功後のみ（書き込み失敗時は `nack` して再配信）
- `OrderRequest.order_id` の重複 trade は検出するが、trade と position の
  atomicity は未解決。上記 transactional RPC と emulator E2E が必要
- `trade_mode != paper` の誤配送は DB read 前に structured reject し safe-ack
- 通常注文の板鮮度は OMS wall clock から評価し、`received_at` があれば exchange
  timestamp より優先する。PAPER_ONLY は設定値に関係なく `received_at` 必須で、
  stale / future skew / freshness check 無効化を fail-closed にする

## 設定（env）

`.env.example` に列挙するキー例:
- `OMS_PAPER_MODE`: `stream` | `backtest`
- `SUPABASE_URL` / `SUPABASE_SECRET_KEY`
- `PUBSUB_PROJECT_ID` / `PUBSUB_EMULATOR_HOST`
- `PUBSUB_SUBSCRIPTION_PAPER_ORDERS`: `oms-paper-paper-orders`
- `PUBSUB_SUBSCRIPTION_RAW_MARKET_DATA`: `oms-paper-raw-market-data`
- `ORDER_BOOK_MAX_AGE_SECONDS`: 通常 `10`、production default `45`
- `ORDER_BOOK_MAX_FUTURE_SKEW_SECONDS`: デフォルト `5`
- `ORDER_BOOK_REQUIRE_RECEIVED_AT`: 全体 strict toggle。production は `true`、
  legacy/replay 互換の local だけ `false` を許容。PAPER_ONLY はこの値に関係なく
  常に required
- `PAPER_DAY_STOP_MONITOR_ENABLED`: `true` で day position の `stop_loss_price` / `target_price` / `trailing_stop_pct` を raw book 更新ごとに評価する。paper 観測用の safety path
- `DAY_CLOSEOUT_TIME`: デフォルト `14:50`
- `DAY_CLOSEOUT_TIMEZONE`: デフォルト `Asia/Tokyo`

秘密情報は `.env.example` にダミー値で列挙、`.env` はコミットしない。

## テスト方針

- **ユニット**:
  - fill_simulator: 板の各ケース（フル約定 / 部分約定 / 板空 / LIMIT 拒否 / 反対方向不足）
  - VWAP 丸め（複数価格レベルにまたがる約定）
  - position_updater: 新規 BUY / relative stop の actual-fill 固定 / 既存への
    BUY 加算 / 部分 SELL / 全 SELL / SELL 失敗
  - closeout: 0 件 / day と swing の混在 / swing の除外 / すでに 0 株のポジション
- **Phase 2 統合**: JSONL 入出力、注文と板のタイムスタンプ整合
- **Phase 3 統合**: Pub/Sub エミュレータ + ローカル Supabase で約定 → DB 反映 → ack まで含む end-to-end
- カバレッジ 80%+（ルート方針）
- **約定価格 / 数量の計算は必ずエッジケースを書く**（ROUND モード, 単元未満残量, 板枯渇）

## 開発時の注意

- **OMS Paper はキルスイッチに影響しない**。`daily_pnl` は live のみ集計するため、paper の約定で `system_status` を書き換えない
- **純関数とサイドエフェクトを厳密に分離**: fill_simulator / position_updater / closeout は I/O を持たない。I/O は `clients/` と `streaming/runner.py` に閉じる
- **fail-closed**: 板が無い場合は約定しない。Supabase 書き込み失敗時は nack
  するが、現行の trade/position 分割書き込みは trade だけが残り得るため、
  transactional RPC 完了までは event publication の安全条件を満たさない
- **`trade-contracts` を破らない**: 既存型で表現できないなら `contracts/` の 3 層同期手順に従って拡張
- **Phase 1 では Pub/Sub / Supabase を触らない**。Phase 3 で `clients/` にまとめて導入
- **空売りは contracts レベルで `PositionSide=LONG` のみ**。SELL は LONG 決済のみ。Gateway で弾かれているはずだが、OMS でも防御的にチェックする
