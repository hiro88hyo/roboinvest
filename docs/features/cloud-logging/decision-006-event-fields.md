# Decision Draft: 主要イベントごとの追加フィールド

作成日: 2026-05-24
対象: [index.md](index.md)
Status: Draft

## 前提

共通の必須キーは以下とする。

- `timestamp`
- `severity`
- `service`
- `environment`
- `event`
- `message`

本メモでは、主要イベントごとに追加で持たせるフィールドを定義する。

## 方針

- 追加フィールドは「検索軸」か「原因切り分け」に効くものだけに絞る
- 同じ意味の値は同じキー名に寄せる
- 未知の値を無理に埋めず、取れない場合は省略する

## 1. service_started

追加フィールド:

- `version`
- `runtime`
- `pid`
- `trade_mode`

目的:

- どのバージョン、どのモードで起動したかを確認する

## 2. service_stopped

追加フィールド:

- `reason`
- `exit_code`
- `trade_mode`

目的:

- 正常停止か異常停止かを追えるようにする

## 3. pubsub_publish_failed

追加フィールド:

- `topic`
- `message_id`
- `symbol`
- `signal_id`
- `order_id`
- `error_type`
- `reason`

目的:

- どの publish が、どの対象に対して失敗したかを追う

## 4. pubsub_pull_failed

追加フィールド:

- `subscription`
- `error_type`
- `reason`

目的:

- どの subscription の受信で失敗しているかを切り分ける

## 5. pubsub_ack_failed

追加フィールド:

- `subscription`
- `message_id`
- `error_type`
- `reason`

目的:

- ack 失敗による再配信やバックログ増加を追う

## 6. external_api_error

追加フィールド:

- `api_name`
- `endpoint`
- `http_status`
- `symbol`
- `error_type`
- `reason`

目的:

- 外部依存のどこで失敗しているかを切り分ける

## 7. signal_rejected

追加フィールド:

- `trade_mode`
- `symbol`
- `signal_id`
- `reason`
- `source`
- `holding_type`

目的:

- どのシグナルが、なぜ gateway などで reject されたかを追う

## 8. order_published

追加フィールド:

- `trade_mode`
- `symbol`
- `order_id`
- `signal_id`
- `side`
- `quantity`
- `destination_topic`

目的:

- どの注文がどこへ publish されたかを追う

補足:

- `order_published` は gateway が `live-orders` / `paper-orders` へ publish したことを表す
- OMS が broker API や paper execution へ進めたことは、必要になれば `order_submitted` や `paper_order_filled` など別イベントとして追加する
- Pub/Sub publish と broker submit を同じイベント名に混ぜない

## 9. order_execution_failed

追加フィールド:

- `trade_mode`
- `symbol`
- `order_id`
- `signal_id`
- `error_type`
- `reason`
- `broker_code`

目的:

- 実注文 / 擬似注文の失敗要因を追う

## 10. kill_switch_changed

追加フィールド:

- `trade_mode`
- `old_state`
- `new_state`
- `reason`
- `changed_by`

目的:

- kill switch の変化と原因を追う

## 11. market_close_guard_triggered

追加フィールド:

- `trade_mode`
- `symbol`
- `signal_id`
- `market_timezone`
- `guard_time`
- `reason`

目的:

- 市場外ガードがどの signal に対して発火したかを追う

## キー命名の補足

- `source` は signal source を表す用途に限定する
- `reason` は人間が読める短い分類語にする
- `error_type` は例外クラスや失敗種別の短い名前にする
- `broker_code` は kabu など外部 API が返した業者固有コードを入れる

## 今は入れないもの

- 巨大な request / response body
- stack trace 全文の構造化
- 個別サービス固有の詳細内部状態

これらは必要なら `message` や exception 出力で補い、共通スキーマには入れない。

## 次に必要なこと

1. 各サービスでどのイベントを実際に出すかを割り当てる
2. Python logging の JSON formatter 方針を決める
3. Collector 側で付ける `resource` / `service` 属性との重複整理をする
