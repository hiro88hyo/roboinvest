# Live GO Checklist

作成日: 2026-05-20

実売買の `GO` を出すための判定用チェックリスト。
目的は「いつまでテストを続けるか」を曖昧にせず、`paper` 検証から `live` 切替までを段階的に潰すこと。

本 runbook は **実売買の開始判定** にのみ使う。
`paper` の寄り付き前確認は [`paper-open-checklist.md`](paper-open-checklist.md)、OMS Live の単独 round-trip 実発注手順は [`oms-live-phase3.md`](oms-live-phase3.md) を参照。

## 1. Exit Criteria

`live GO` は次の 2 段階で定義する。

- **Weak GO**: `system_status.trade_mode=live` に切り替える前提が揃い、live 経路に進んでも誤発注・設定ミス・状態不整合のリスクが低い
- **Strong GO**: Weak GO を満たし、さらに最小サイズ 1 round-trip の実発注 e2e が完了している

原則として、通常運用へ入る前に **Strong GO** まで到達する。

## 2. No-Go Conditions

次のどれか 1 つでも当てはまるなら `live GO` は出さない。

- `paper-open-checklist.md` の項目が未完了
- `watchlist` が空、または当日 `valid_date` でない
- `daily_ohlcv` が直近営業日まで埋まっていない
- `feeder` が kabu 登録または WebSocket 維持に失敗している
- `strategy-ai` / `aggregator` / `gateway` / `oms-paper` の downstream が止まっている
- `system_status.is_trading_allowed=false`
- `TRADE_MODE=live` / `OMS_LIVE_DRY_RUN=false` にする理由と観測者が曖昧
- `KABU_API_PASSWORD` / `KABU_ORDER_PASSWORD` / `KABU_DEFAULT_EXCHANGE` / `OMS_LIVE_ALLOWED_SYMBOLS` / `OMS_LIVE_MAX_QTY_PER_ORDER` を確認していない
- kabu 実保有と Supabase `positions(live)` のズレを未確認
- 最小サイズ実発注の rollback 手順が未確認

## 3. Stage A: Paper GO

まず `paper` の始業手順を完了させる。

- [ ] [`paper-open-checklist.md`](paper-open-checklist.md) を完了
- [ ] Universe Scanner が `done: valid_date=YYYY-MM-DD watchlist_size=N` で終了
- [ ] `watchlist_size > 0`
- [ ] `health-check.py --check supabase services` が green
- [ ] `feeder` が当日 watchlist を読み、`raw-market-data` publish を開始
- [ ] `feature-engine -> strategy-rule / strategy-ai -> aggregator -> gateway -> oms-paper` が流れている

この段階で `paper GO`。

## 4. Stage B: Weak Live GO

`paper GO` の上に、実売買へ切り替える前提確認を積む。

### 4.1 Live Safety Knobs

- [ ] `infra/env.production` または live 起動 env で `KABU_DEFAULT_EXCHANGE=9` を確認
- [ ] `OMS_LIVE_ALLOWED_SYMBOLS` が対象銘柄のみに絞られている
- [ ] `OMS_LIVE_MAX_QTY_PER_ORDER` が最小単元相当になっている
- [ ] `OMS_LIVE_DRY_RUN` の現在値を把握している
- [ ] `KABU_API_PASSWORD` と `KABU_ORDER_PASSWORD` が正しい組で、別 field 管理になっている
- [ ] kill switch の操作場所を把握している

### 4.2 System Status / Routing

- [ ] `system_status.trade_mode` の現在値を確認している
- [ ] `system_status.is_trading_allowed=true` を確認
- [ ] `system_status.trading_style` が意図どおり (`day` / `swing`)
- [ ] `daily_loss_limit` / `weekly_loss_limit` / `monthly_loss_limit` を確認
- [ ] `gateway` が reject 偏重になっていない

### 4.3 Kabu / Position Integrity

- [ ] kabu 本番 API の token 取得が通る
- [ ] kabu `/wallet/cash` / `/positions` / `/orders` が読める
- [ ] kabu 実保有と Supabase `positions(live)` の差分を確認
- [ ] 必要なら `uv run python scripts/reconcile-positions.py --dry-run` を実行
- [ ] ズレを `--apply` または手作業でどう扱うか決めている
- [ ] 未約定注文が残っていない

### 4.4 Observability / Abort Readiness

- [ ] kabu GUI を開ける
- [ ] Dashboard / SQL で kill switch を即座に落とせる
- [ ] `trades_live` / `positions(live)` / `system_status.daily_pnl` の確認手段を用意している
- [ ] 異常時に止める担当者と連絡手段がある

ここまで完了で **Weak GO**。

## 5. Stage C: Strong Live GO

Weak GO の上に、最小サイズの実発注 e2e を完了させる。

- [ ] [`oms-live-phase3.md`](oms-live-phase3.md) の Step 1 を再確認
- [ ] 対象銘柄 1 つ・最小数量・市場時間内で実施する
- [ ] 必要なら Step 2 の単発 sendorder + cancel を先に行う
- [ ] `OMS_LIVE_DRY_RUN=true` で Runner pull -> ack を確認する
- [ ] `OMS_LIVE_DRY_RUN=false` に切り替えて最小 round-trip を 1 回通す
- [ ] BUY 約定後に `positions(live)` が期待どおり増える
- [ ] SELL 約定後に `positions(live)` が空へ戻る
- [ ] `trades_live` に buy/sell の 2 行が残る
- [ ] `system_status.daily_pnl` が妥当な値で更新される
- [ ] kabu GUI 上でも未約定注文・想定外ポジションが残っていない

ここまで完了で **Strong GO**。

## 6. Recommended Sequence

実務上は次の順で潰す。

1. `paper-open-checklist.md` を完了して `paper GO`
2. 本 runbook の Stage B を潰して `Weak GO`
3. `oms-live-phase3.md` を最小サイズで実施して `Strong GO`
4. 初めて通常の `trade_mode=live` 運用へ進む

## 7. Record Template

実施ごとに最低限これを残す。

```text
date:
operator:
paper_go: yes/no
weak_live_go: yes/no
strong_live_go: yes/no
target_symbol:
target_qty:
kill_switch_checked: yes/no
position_reconcile_checked: yes/no
notes:
```

`HANDOFF.md` または当日の運用メモに転記すること。
