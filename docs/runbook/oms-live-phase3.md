# OMS Live Phase 3: 検証環境 (kabuステーション 18081) e2e Runbook

OMS Live を **検証環境** (kabu 18081) に対して動かし、`live-orders` トピック → 実 sendorder → fill 確認 → Supabase 書込までを 1 round-trip 通す手順。本番 (18080) には繋がない。Phase 4 で「ステージング期間」を経て段階導入する。

## 実施タイミング

- **平日 9:00-15:00 JST**（kabu 検証環境は約定が市場時間内のみ）
- 想定実施日: **2026-05-07 (Thu)** 以降。Golden Week 中 (5/4-5/6) は市場休
- 1 round-trip = 5〜10 分程度を見込む

## 前提条件チェックリスト

走らせる前に **必ず** 全項目確認:

- [ ] Windows 機 (kabuステーション稼働) と Linux 開発機の SSH 疎通
- [ ] kabuステーション API パスワード / 注文パスワードを把握 (kabuステーション GUI で発行)
- [ ] kabu 検証環境 18081 が稼働している (Windows GUI で確認)
- [ ] Linux 側で `docker compose -f infra/docker-compose.dev.yml up -d` で Pub/Sub エミュレータ起動済み
- [ ] Linux 側で `cd infra && supabase start` で Supabase ローカル稼働中 (`supabase status` で確認)
- [ ] Pub/Sub topic `live-orders` 作成済み (`infra/pubsub/init-topics.sh`)
- [ ] **対象銘柄を 1 つ決めて**、最低株数 (100 株 等) と現在価格を把握 (low-priced ETF 推奨)。`scripts/probe-kabu-oms.py --symbol <code>` の `board` ステップで現在値・最良気配が確認できる
- [ ] 対象銘柄の買付余力が十分にあること (`scripts/probe-kabu-oms.py` で `wallet/cash` 確認)
- [ ] **Dashboard を開いて kill switch 操作の場所を把握** (`http://localhost:3001/system`)

## SSH トンネル

kabuステーション API は Windows http.sys レイヤーで `localhost` 限定。Linux から叩くには SSH ポートフォワード必須。

```bash
ssh -N -L 18081:127.0.0.1:18081 user@<windows-ip>
```

別ターミナルで動かしたままにする。トンネル断は kabu API 全部失敗の原因なので、走行中は監視必須。

## 段階的導入手順 (4 ステップ)

### Step 1: REST 疎通確認 (no send, 5 分)

`scripts/probe-kabu-oms.py` で GET 系を全部叩いて、トークン取得 / 残高 / 銘柄情報 / **板情報 (現在値・最良気配)** / 現物残高 / 注文一覧の応答を観測。

```bash
export KABU_API_PASSWORD=<api-pw>
uv run scripts/probe-kabu-oms.py --env test --symbol 7203
```

**期待**:
- `ALL OK` で終わる
- `wallet/cash` の `StockAccountWallet` が買付余力を反映していること
- `board` ステップで `CurrentPrice` / `BidPrice` / `AskPrice` が出力され、対象銘柄の現在値が把握できること (検証環境では市場時間外に `CurrentPrice=null` が返る場合あり)

### Step 2: 単発 sendorder + cancel (no Supabase, 3 分)

実注文を kabu に投げてすぐ取消す。Supabase / Pub/Sub は触らない。kabu 側で「注文受付 → 取消」が走ることを確認。

```bash
export KABU_API_PASSWORD=<api-pw>
export KABU_ORDER_PASSWORD=<order-pw>
uv run scripts/probe-kabu-oms.py --env test --symbol 7203 \
  --send --order-qty 100 --auto-cancel
```

**期待**: `Result: 0` + `OrderId` 取得 → 即 cancel で `Result: 0`。kabuステーション GUI の「注文一覧」に取消済の行が出ること。

### Step 3: DRY_RUN モードで Pub/Sub → Runner 経路の疎通 (5 分)

`OMS_LIVE_DRY_RUN=true` で実発注は走らせず、Pub/Sub publish → Runner pull → safety check → ack の経路だけ確認。**ここでの主目的は Runner ログの観測**であり、pytest の assert 結果は二次的。

```bash
export OMS_LIVE_PHASE3_E2E=1
export OMS_LIVE_DRY_RUN=true
export OMS_LIVE_PHASE3_SYMBOL=7203
export OMS_LIVE_PHASE3_QUANTITY=100
export KABU_API_BASE_URL=http://localhost:18081/kabusapi
export KABU_API_PASSWORD=<api-pw>
export KABU_ORDER_PASSWORD=<order-pw>
export PUBSUB_EMULATOR_HOST=localhost:8085
export PUBSUB_PROJECT_ID=trade-ai-dev
export SUPABASE_URL=http://127.0.0.1:54321
export SUPABASE_SECRET_KEY=<sb_secret_*>

uv run pytest services/oms-live/tests/integration/test_phase3_kabu_e2e.py -v -s
```

**期待**:
- Runner ログ (stdout) に `live order skipped (DRY_RUN): order_id=... symbol=7203 side=BUY qty=100` が **少なくとも 1 行** 観測できる (publish された OrderRequest 数だけ出る)
- `BatchStats.dry_run_skipped` が publish 件数と一致
- pytest 自体は assert (positions(live) 出現 / trades_live INSERT) で **FAILED になる**。これは DRY_RUN で sendorder と Supabase 書込が両方スキップされる仕様による想定内の挙動。pytest の赤は無視し、上のログが出ていれば疎通 OK
- Supabase の `positions` / `trades_live` には何も書かれていないこと (`select * from trades_live where symbol='7203';` が空)

### Step 4: 本番走行 (10 分、市場時間中)

DRY_RUN を外して実発注を走らせる。安全装備 (`MAX_QTY_PER_ORDER` / `ALLOWED_SYMBOLS`) を必ず効かせる。

```bash
unset OMS_LIVE_DRY_RUN
export OMS_LIVE_MAX_QTY_PER_ORDER=100
export OMS_LIVE_ALLOWED_SYMBOLS=7203
# 他の env は Step 3 と同じ

uv run pytest services/oms-live/tests/integration/test_phase3_kabu_e2e.py -v -s
```

**期待**:
- BUY 100 → fill (`positions(live)` 1 行追加)
- SELL 100 → fill (`positions(live)` 削除 + `trades_live` 2 行 + `daily_pnl` 加算)
- テストが PASSED で終了
- kabuステーション GUI の「注文一覧」に約定 2 件 (買 + 売) が見える

## 想定 DB 遷移

| ステップ | `positions(live)` | `trades_live` | `system_status.daily_pnl` |
|---|---|---|---|
| 開始時 | (空) | (空) | 任意 |
| BUY fill 後 | 1 行 (`quantity=100`, `entry_price=現在値`) | 1 行 (`side=BUY`, `order_id` 付き) | 不変 (BUY は損益確定なし) |
| SELL fill 後 | (空) | 2 行 (`side=BUY` + `side=SELL`, 各 `order_id` 付き) | `(sell_price - buy_price) * 100` 加算 |

`trades_live.unified_signal_id` は `aggregator_logs` の seed 行を指す。テストの `finally` で 3 テーブル全部 cleanup される。

## psql 用クエリ集

頻出操作は `scripts/oms-live-phase3/` に SQL ファイルとして用意してある。`psql -f` で叩ける。

| 用途 | ファイル | 引数 |
|---|---|---|
| 状態確認 (system_status / positions(live) / trades_live 直近 10 件) | `check-state.sql` | なし |
| kill switch ON | `kill-switch-on.sql` | なし |
| kill switch OFF (再開) | `kill-switch-off.sql` | なし |
| 指定 symbol の positions(live) / trades_live を全削除 | `cleanup-symbol.sql` | `-v symbol=7203` |

ローカル Supabase に対する接続文字列例:
```
postgresql://postgres:postgres@127.0.0.1:54322/postgres
```

## 中止手順 (異常時)

走行中に何か変だったら **すぐ実行**:

1. **Pub/Sub publish を止める** (テストプロセス `Ctrl+C`)。Runner pull もループ抜けで止まる
2. **kill switch を立てる**: Dashboard `http://localhost:3001/system` の **取引許可: OFF** ボタンを押す。または psql で:
   ```bash
   psql "$SUPABASE_DB_URL" -f scripts/oms-live-phase3/kill-switch-on.sql
   ```
   これで以降の Gateway 経路の注文は全 reject される。**ただし Phase 3 e2e は Gateway を介さず `live-orders` トピックに直接 publish するため、kill switch は次の publish 以降にしか効かない**。すでに live-orders にバッファされている message は Runner が pull する。Runner を止めるには pytest プロセス停止 + `python -m oms_live` を起動していたら `Ctrl+C`
3. **kabuステーション GUI** で未約定の発注が残っていないか確認、残っていれば GUI から手動取消
4. **`positions(live)` に意図しないポジションが残っていないか確認**:
   ```bash
   psql "$SUPABASE_DB_URL" -f scripts/oms-live-phase3/check-state.sql
   ```
   残っていれば手動 SELL (kabuステーション GUI から成行売) → 14:50 のデイクローズアウトに任せても良い

## kill switch の解除

異常対応後に Phase 3 を再開する場合:

```bash
psql "$SUPABASE_DB_URL" -f scripts/oms-live-phase3/kill-switch-off.sql
```

または Dashboard で **取引許可: ON** に戻す。再開前に `daily_pnl` がリセットされているか / `daily_loss_limit` を超えていないか確認すること (`check-state.sql` で確認可能)。

## 後始末

走行が終わったら:

- [ ] Pub/Sub テストプロセス停止
- [ ] SSH トンネル切断
- [ ] `positions(live)` が空になっていることを確認 (`psql "$SUPABASE_DB_URL" -f scripts/oms-live-phase3/check-state.sql`)
- [ ] kabuステーション GUI で未約定注文が残っていないか確認
- [ ] `system_status.daily_pnl` の値を記録 (Phase 3 結果として残す)
- [ ] テスト走行で対象 symbol にゴミ行が残った場合は `psql "$SUPABASE_DB_URL" -v symbol=<code> -f scripts/oms-live-phase3/cleanup-symbol.sql` で掃除

## 残課題 / Phase 4 候補

- 「sendorder 成功 + Supabase 書込失敗で再配信」のケースは現状再走する。Runner を fail-fast にする方向で別フェーズ要 (`PR #5` のコミットメッセージに記載)
- 検証環境 (18081) では `wallet/symbol` が null を返すケースあり。本番 (18080) で wallet 経由の余力チェックを実装する場合は別途調整必要
- 1 トレードリスク (2% ルール) のロット数自動切詰めは Gateway 責務だが、Phase 4 でフィードバックループの動作確認が必要
- jpholiday カレンダーは未導入。Phase 3 e2e の skip ガードは「平日 + 9:00-15:00」のみで、祝日は env を立てた人の責任で skip する
