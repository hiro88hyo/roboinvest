# Feature Memo: Discord Notifications

作成日: 2026-06-01
Status: Draft
台帳: [docs/features.md](../features.md)

## 1. 背景

- Cloud Logging / Cloud Monitoring を一次監視基盤にする方針が固まりつつある。
- 運用中の異常検知は Cloud Monitoring から即時通知したい。
- 約定、決済、closeout、kill switch などの取引イベントも、Dashboard を開かなくても把握できる通知が必要。
- 監視イベントと取引イベントは payload、重要度、再送、通知先、冪等性の要件が違う。

## 2. 目的

- Cloud Monitoring の alert を Discord に通知できるようにする。
- 約定、決済、損益、closeout 結果などの取引イベントを Discord に通知できるようにする。
- 監視通知と取引通知を Pub/Sub topic / Function 単位で分け、障害時の影響範囲を小さくする。
- 将来の実装時に、Cloud Monitoring / Pub/Sub / Cloud Run Functions / Discord webhook の責務を迷わない状態にする。

## 3. 推奨アーキテクチャ

```text
Cloud Monitoring alert policies
  -> Pub/Sub topic: ops-alerts
  -> Cloud Run Function: notify-ops-discord
  -> Discord ops channel

OMS Live / OMS Paper / Gateway
  -> Pub/Sub topic: trading-events
  -> Cloud Run Function: notify-trading-discord
  -> Discord trading channel
```

Topic は必ず分ける。Function も分ける方針を基本とする。
実装量を抑えたい場合でも、同一コードベースから別 Function として deploy し、entrypoint と環境変数を分ける。

## 4. スコープ

### Ops Notifications

- Cloud Monitoring alert の open / close 通知。
- Cloud Run / production service の異常。
- Pub/Sub backlog / oldest unacked age の異常。
- kabu API error spike。
- OMS Live / Gateway heartbeat 欠落。
- closeout invariant failure など、運用介入が必要な alert。

### Trading Notifications

- live / paper 約定通知。
- 決済完了通知。
- 決済時の realized PnL。
- open position の closeout 結果。
- kill switch の ON / OFF。
- broker reject のうち資金・発注条件に関係するもの。

## 5. 非スコープ

- Discord から発注、停止、kill switch 操作を実行すること。
- Dashboard の代替 UI を Discord 上に作ること。
- Cloud Monitoring の alert policy 実装。
- trading event contract の実装。
- Slack / email / PagerDuty など、Discord 以外の通知先。

## 6. Topic / Function 分離方針

| 区分 | Topic | Function | 主な通知先 | 理由 |
|---|---|---|---|---|
| 監視通知 | `ops-alerts` | `notify-ops-discord` | ops channel | Cloud Monitoring payload を扱う。障害・遅延・基盤異常が中心。 |
| 取引通知 | `trading-events` | `notify-trading-discord` | trading channel | 約定・決済・損益を扱う。重複通知対策と表示品質を重視する。 |

監視通知が多発しても取引通知を巻き込まないことを優先する。
IAM、retry、DLQ、Discord webhook secret も分けられる設計にする。

## 7. Trading Event Payload Draft

取引通知はアプリケーションイベントとして publish する。
Pub/Sub は at-least-once delivery なので、`event_id` を必須にして通知側で冪等化する。

```json
{
  "event_id": "uuid",
  "event_type": "order_filled",
  "occurred_at": "2026-06-01T05:30:00Z",
  "environment": "production",
  "trade_mode": "live",
  "symbol": "7203",
  "side": "BUY",
  "quantity": 100,
  "price": 3012.0,
  "order_id": "broker-or-internal-order-id",
  "unified_signal_id": "optional-signal-id"
}
```

決済イベントでは損益を含める。

```json
{
  "event_id": "uuid",
  "event_type": "position_closed",
  "occurred_at": "2026-06-01T05:50:00Z",
  "environment": "production",
  "trade_mode": "live",
  "symbol": "7203",
  "side": "SELL",
  "quantity": 100,
  "entry_price": 3012.0,
  "exit_price": 3045.0,
  "realized_pnl_yen": 3300,
  "close_reason": "day_closeout",
  "order_id": "broker-or-internal-order-id",
  "unified_signal_id": null
}
```

損益は通知 Function で推測するより、OMS / Gateway 側で確定した値を payload に含める方針にする。
不足情報の補足が必要な場合のみ、通知 Function が Supabase を read する。

## 8. Discord Message Draft

Ops:

```text
[CRITICAL] OMS Live heartbeat missing
service: oms-live
environment: production
condition: heartbeat_missing
incident: https://console.cloud.google.com/monitoring/alerting/incidents/...
```

Trading fill:

```text
[LIVE] 約定
7203 BUY 100株 @ 3,012円
order_id: ...
signal_id: ...
```

Trading close:

```text
[LIVE] 決済完了
7203 SELL 100株 @ 3,045円
実現損益: +3,300円
理由: day_closeout
```

## 9. 運用要件

- Discord webhook URL は Secret Manager または 1Password 経由の環境変数で渡す。
- `ops` と `trading` の webhook secret は分ける。
- Pub/Sub subscription は retry と DLQ を設定する。
- Trading notification は `event_id` で冪等化する。
- Discord 投稿失敗時に元イベントを失わないよう、ack は投稿成功後に行う。
- Discord rate limit に備え、短時間に多発する ops alert は要約または抑制を検討する。
- Trading notification は原則として要約せず、約定・決済単位で通知する。

## 10. 依存

- Cloud Monitoring alert policy / notification channel。
- Pub/Sub topic / subscription。
- Cloud Run Functions または Cloud Run service。
- Discord webhook。
- Secret Manager または 1Password。
- Trading event contract。
- 必要に応じて通知済み event を記録する Supabase table。

## 11. 未決事項

- Function を完全に別サービスとして実装するか、同一コードベースの別 entrypoint とするか。
- 通知済み `event_id` の保存先を Supabase / Firestore / Redis のどれにするか。
- `trading-events` を約定専用にするか、kill switch / broker reject も含めるか。
- paper 通知を常時送るか、live のみ常時送って paper は必要時だけにするか。
- Discord channel の命名と severity ごとのメンションルール。
- closeout / broker reject の通知を Cloud Monitoring alert と trading event のどちらから送るか。

## 12. 段階的な進め方

1. Discord webhook と通知先 channel を ops / trading で分ける。
2. `ops-alerts` topic と `notify-ops-discord` を作り、Cloud Monitoring alert の通知だけを流す。
3. `trading-events` topic と `notify-trading-discord` を作り、paper 約定イベントで payload と表示を検証する。
4. Trading notification の `event_id` 冪等化を実装する。
5. live 約定、決済、realized PnL、closeout 結果へ対象を広げる。
6. DLQ と runbook を整備する。
