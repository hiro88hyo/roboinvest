# OMS Live Phase 3: 本番環境 (kabuステーション 28080 / Caddy 経由) e2e Runbook

OMS Live を **本番環境** (kabu 28080 / Caddy リバプロ経由) に対して動かし、`live-orders` トピック → 実 sendorder → fill 確認 → Supabase 書込までを 1 round-trip 通す手順。

> **検証環境 (18081) は sendorder を黙殺するため round-trip e2e に使えない** (2026-05-07 確認)。
> `POST /sendorder` が `Result: 0, OrderId: null` を返し、`/orders` にも `/positions` にも一切反映されない。
> 検証環境は GET 系の REST 疎通確認 (token / wallet / symbol / positions / orders) と DRY_RUN 経路の確認に限定して使う。

## 実施タイミング

- **平日 9:00-15:00 JST**（本番接続でも約定が成立しないと poll がタイムアウトする）
- 1 round-trip = 5〜10 分程度を見込む
- 実発注は本番資金に直結する。安全装備 (`OMS_LIVE_MAX_QTY_PER_ORDER` / `OMS_LIVE_ALLOWED_SYMBOLS`) を必ず効かせること

## 前提条件チェックリスト

走らせる前に **必ず** 全項目確認:

- [ ] Windows 機 (kabuステーション稼働) の LAN IP を把握 (例: `192.168.x.y`)。Linux 開発機から `<win-ip>:28080` に届くか `curl` で確認
- [ ] Windows 機の Caddy リバプロが起動しており、`http://<win-ip>:28080/kabusapi/...` で kabu **本番** API に到達できること (Caddy が `Host: localhost` を強制)
- [ ] kabuステーション **本番** API パスワード / 注文パスワードを把握 (kabuステーション GUI で発行、検証用と別管理)
- [ ] kabu 本番環境 (Windows GUI でログイン状態) が稼働している
- [ ] Linux 側で `docker compose -f infra/docker-compose.dev.yml up -d` で Pub/Sub エミュレータ起動済み
- [ ] Linux 側で `cd infra && supabase start` で Supabase ローカル稼働中 (`supabase status` で確認)
- [ ] Pub/Sub topic `live-orders` 作成済み (`infra/pubsub/init-topics.sh`)
- [ ] **対象銘柄を 1 つ決めて**、最低株数 (100 株 等) と現在価格を把握。`scripts/probe-kabu-oms.py --symbol <code> --host <win-ip> --port 28080` の `board` ステップで現在値・最良気配が確認できる
- [ ] 対象銘柄の買付余力が十分にあること (`probe-kabu-oms.py` で `wallet/cash` 確認、現物買付は T+2 で余力反映)
- [ ] **Dashboard を開いて kill switch 操作の場所を把握** (`http://localhost:3001/system`)

## 接続経路

kabuステーション API は Windows http.sys レイヤーで `localhost` 限定。本番では Windows 機側の **Caddy リバプロ** が `Host: localhost` を強制して LAN 公開している。

- 本番ポート: `28080` (Caddy 経由 → kabu 18080)
- 検証ポート: `28081` (Caddy 経由 → kabu 18081、Phase 3 e2e では使わない)

Linux 側からは LAN IP 直叩きで OK:
```bash
curl http://<win-ip>:28080/kabusapi/token -d '{"APIPassword":"..."}' -H 'Content-Type: application/json'
```

SSH ポートフォワードを使う場合 (検証用):
```bash
ssh -N -L 18081:127.0.0.1:18081 user@<windows-ip>
```
別ターミナルで動かしたままにする。本番接続では Caddy 経由なので SSH トンネルは不要。

## 段階的導入手順 (4 ステップ)

### Step 1: REST 疎通確認 (no send, 5 分)

`scripts/probe-kabu-oms.py` で GET 系を全部叩いて、トークン取得 / 残高 / 銘柄情報 / **板情報 (現在値・最良気配)** / 現物残高 / 注文一覧の応答を観測。

```bash
export KABU_API_PASSWORD=<本番 api-pw>
uv run scripts/probe-kabu-oms.py --env prod --host <win-ip> --port 28080 --symbol 9432
```

**期待**:
- `ALL OK` で終わる
- `wallet/cash` の `StockAccountWallet` が買付余力を反映していること
- `board` ステップで `CurrentPrice` / `BidPrice` / `AskPrice` が出力され、対象銘柄の現在値が把握できること

### Step 2: 単発 sendorder + cancel (no Supabase, 3 分)

実注文を kabu に投げてすぐ取消す。Supabase / Pub/Sub は触らない。kabu 側で「注文受付 → (約定 or 取消)」が走ることを確認。

> **注意**: 流動性のある銘柄は成行 BUY が ms オーダーで即約定するため、`--auto-cancel` の cancelorder が `Code: 43 "該当注文は既に約定済です"` で reject されることがある (2026-05-07 NTT 9432 で確認)。これは仕様であり、reject 自体は問題ない。約定済ポジションが残るので、Step 4 の round-trip e2e に進む前に手動 SELL するか Step 4 の SELL に任せるかを決めておく。

```bash
export KABU_API_PASSWORD=<本番 api-pw>
export KABU_ORDER_PASSWORD=<本番 order-pw>
uv run scripts/probe-kabu-oms.py --env prod --host <win-ip> --port 28080 --symbol 9432 \
  --send --order-qty 100 --auto-cancel --exchange 9
```

**期待**: `Result: 0` + `OrderId` 取得 → cancel が `Result: 0` または `Code: 43` (既約定)。kabuステーション GUI の「注文一覧」に対応行が出ること。

### Step 3: DRY_RUN モードで Pub/Sub → Runner 経路の疎通 (5 分)

`OMS_LIVE_DRY_RUN=true` で実発注は走らせず、Pub/Sub publish → Runner pull → safety check → ack の経路だけ確認。**ここでの主目的は Runner ログの観測**であり、pytest の assert 結果は二次的。

```bash
export OMS_LIVE_PHASE3_E2E=1
export OMS_LIVE_DRY_RUN=true
export OMS_LIVE_PHASE3_SYMBOL=9432
export OMS_LIVE_PHASE3_QUANTITY=100
export OMS_LIVE_PHASE3_EXCHANGE=9             # SOR 必須 (本番)
export KABU_API_BASE_URL=http://<win-ip>:28080/kabusapi
export KABU_API_PASSWORD=<本番 api-pw>
export KABU_ORDER_PASSWORD=<本番 order-pw>
export PUBSUB_EMULATOR_HOST=localhost:8085
export PUBSUB_PROJECT_ID=trade-ai-dev
export SUPABASE_URL=http://127.0.0.1:54321
export SUPABASE_SECRET_KEY=<sb_secret_*>

uv run pytest services/oms-live/tests/integration/test_phase3_kabu_e2e.py -v -s
```

**期待**:
- Runner ログ (stdout) に `live order skipped (DRY_RUN): order_id=... symbol=9432 side=BUY qty=100` が **少なくとも 1 行** 観測できる (publish された OrderRequest 数だけ出る)
- `BatchStats.dry_run_skipped` が publish 件数と一致
- pytest 自体は assert (positions(live) 出現 / trades_live INSERT) で **FAILED になる**。これは DRY_RUN で sendorder と Supabase 書込が両方スキップされる仕様による想定内の挙動。pytest の赤は無視し、上のログが出ていれば疎通 OK
- Supabase の `positions` / `trades_live` には何も書かれていないこと (`select * from trades_live where symbol='9432';` が空)

### Step 4: 本番走行 (10 分、市場時間中)

DRY_RUN を外して実発注を走らせる。安全装備 (`MAX_QTY_PER_ORDER` / `ALLOWED_SYMBOLS`) を必ず効かせる。

```bash
unset OMS_LIVE_DRY_RUN
export OMS_LIVE_MAX_QTY_PER_ORDER=100
export OMS_LIVE_ALLOWED_SYMBOLS=9432
# 他の env (Exchange=9 含む) は Step 3 と同じ

uv run pytest services/oms-live/tests/integration/test_phase3_kabu_e2e.py -v -s
```

**期待**:
- BUY 100 → fill (`positions(live)` 1 行追加、約定価格は実勢)
- SELL 100 → fill (`positions(live)` 削除 + `trades_live` 2 行 + `daily_pnl` 加算)
- テストが PASSED で終了
- kabuステーション GUI の「注文一覧」に約定 2 件 (買 + 売) が見える

> **2026-05-07 実施時の知見**: kabu の `/orders` 応答は約定確定までに `State=3 + CumQty=0` の中間状態を経由することがある (Details RecType=1/4 のみ、約定 RecType=8 未着)。この状態で poll を抜けないように `order_parser.to_fill_result` は `pending` を返す。最終的に `State=5 + CumQty>0` で `filled` になる。修正前 (`STATE_CANCELLING=5` 解釈) では誤って `cancelled` で抜けて Supabase 不整合を起こしていた

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
- 検証環境 (18081) では `wallet/symbol` が null を返し、`sendorder` も黙殺される。本番 (28080) のみ運用想定でよいか、検証で何ができるかの整理は Phase 4 で
- 1 トレードリスク (2% ルール) のロット数自動切詰めは Gateway 責務だが、Phase 4 でフィードバックループの動作確認が必要
- Phase 3 e2e の skip ガードは「平日 + 9:00-15:00 + 東証営業日 (jpholiday + 12/31, 1/1-1/3)」で発火する。祝日でも自動 skip されるが、半休 (大納会等で短縮立会) には未対応 — 該当日の手動運用は env を解除する側の責任
