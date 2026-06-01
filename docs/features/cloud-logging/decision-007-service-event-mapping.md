# Decision Draft: サービスごとのイベント割り当て

作成日: 2026-05-24
対象: [index.md](index.md)
Status: Partially Implemented

## 方針

- すべてのサービスに同じイベントを強制しない
- 横断的に意味が揃うイベントだけ共通化する
- サービス固有のイベントは、その責務に直結するものだけ追加する

## 全サービス共通で出すイベント

以下は全 Python サービスで原則出す。

- `service_started`
- `service_stopped`
- `external_api_error`  
  外部 API を持たないサービスは不要

## サービス別マッピング

### feeder

出すイベント:

- `service_started`
- `service_stopped`
- `external_api_error`
- `pubsub_publish_failed`

補足:

- kabu API / WebSocket 系エラーは `external_api_error`
- `raw-market-data` publish 失敗は `pubsub_publish_failed`

### feature-engine

出すイベント:

- `service_started`
- `service_stopped`
- `pubsub_pull_failed`
- `pubsub_ack_failed`
- `pubsub_publish_failed`

補足:

- `raw-market-data` pull / ack / `processed-features` publish が対象

### strategy-rule

出すイベント:

- `service_started`
- `service_stopped`
- `pubsub_pull_failed`
- `pubsub_ack_failed`
- `pubsub_publish_failed`

補足:

- `processed-features` pull / ack / `strategy-signals-a` publish が対象

### strategy-ai

出すイベント:

- `service_started`
- `service_stopped`
- `pubsub_pull_failed`
- `pubsub_ack_failed`
- `pubsub_publish_failed`
- `external_api_error`
- `ai_decision_skipped`
- `ai_trigger_parse_failed`

補足:

- LLM API エラーは `external_api_error`
- `processed-features` または AI trigger 系の pull / ack、`strategy-signals-b` publish が対象
- AI が signal を出さない場合は `ai_decision_skipped` で理由を残す
- trigger payload の JSON / schema failure は `ai_trigger_parse_failed` で poison ack を追う

### aggregator

出すイベント:

- `service_started`
- `service_stopped`
- `pubsub_pull_failed`
- `pubsub_ack_failed`
- `pubsub_publish_failed`

補足:

- `strategy-signals-a/b` pull / ack、`trade-signals` publish が対象

### gateway

出すイベント:

- `service_started`
- `service_stopped`
- `pubsub_pull_failed`
- `pubsub_ack_failed`
- `pubsub_publish_failed`
- `signal_rejected`
- `kill_switch_changed`
- `market_close_guard_triggered`

補足:

- `trade-signals` pull / ack、`live-orders` または `paper-orders` publish が対象
- `signal_rejected` は最重要イベントの一つとして必須寄りで扱う

### oms-paper

出すイベント:

- `service_started`
- `service_stopped`
- `pubsub_pull_failed`
- `pubsub_ack_failed`
- `order_published`
- `order_execution_failed`

補足:

- `paper-orders` pull / ack が対象
- 紙上約定の失敗や closeout 処理失敗は `order_execution_failed`

### oms-live

出すイベント:

- `service_started`
- `service_stopped`
- `pubsub_pull_failed`
- `pubsub_ack_failed`
- `order_published`
- `order_execution_failed`
- `external_api_error`

補足:

- `live-orders` pull / ack が対象
- kabu 注文 API エラーは `external_api_error`
- 実発注失敗は `order_execution_failed`

### universe-scanner

出すイベント:

- `service_started`
- `service_stopped`
- `external_api_error`

補足:

- J-Quants / Supabase など batch 実行時の外部依存エラーが対象

## 今回は共通化しないイベント

- feature 計算完了
- strategy 評価完了
- consensus 成立
- backtest 完了

理由:

- まずは障害調査と運用監視に効くイベントを先に揃える
- 件数サマリや詳細な業務イベントは次段で必要性を見て追加する

## 実装優先度

### 優先度 A

- `service_started`
- `service_stopped`
- `pubsub_publish_failed`
- `pubsub_pull_failed`
- `pubsub_ack_failed`
- `external_api_error`
- `signal_rejected`
- `order_execution_failed`

### 優先度 B

- `order_published`
- `kill_switch_changed`
- `market_close_guard_triggered`

## 次に必要なこと

1. Python logging の JSON formatter 方針を決める
2. 既存ログ文言をどこまでイベント化して置き換えるか決める
3. Collector 側で自動付与する属性との境界を決める
