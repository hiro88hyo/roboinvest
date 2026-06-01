# Decision Note: JSON 構造化の粒度

作成日: 2026-05-24
対象: [index.md](index.md)

## 決定

- アプリログは `薄い共通スキーマ + イベント別追加フィールド` で JSON 構造化する
- 最初から巨大な共通スキーマを全ログに強制しない
- 重要イベントから優先して JSON 化し、段階的に対象を広げる

## 基本方針

- 1 行 1 JSON を基本とする
- 検索軸になる最小限のキーだけを共通化する
- イベントごとに必要な追加フィールドを持たせる
- `message` に情報を埋め込みすぎず、検索したい値はトップレベルキーとして出す

## 共通の必須キー

- `timestamp`
- `severity`
- `service`
- `environment`
- `event`
- `message`

移行初期の通常ログや、外部ライブラリ由来のログでは `event` を明示できない場合がある。
その場合は formatter または Collector 側で `event="log"` 相当のデフォルトを補い、ログを捨てない。

## 条件付きで持たせるキー

- `trade_mode`
- `symbol`
- `signal_id`
- `order_id`
- `topic`
- `subscription`
- `reason`
- `error_type`

これらは全ログで必須にはせず、イベントに応じて付与する。

## 最初に JSON 化する対象

- サービス起動 / 停止
- Pub/Sub publish / pull / ack の失敗
- 外部 API エラー
- signal reject
- order publish
- order execution failure
- kill switch 変化
- market close guard 発火

## 採らない方針

### 全ログに巨大な共通スキーマを強制する

見送る理由:

- 実装コストが高い
- サービスごとの差分を吸収しにくい
- 初期導入の速度を落とす

### `message` にだけ情報を詰め込む

見送る理由:

- Cloud Logging 上での検索性が落ちる
- 後からメトリクス化や集計をするときに扱いにくい

## イベント命名の初期方針

- `snake_case` を使う
- 動詞を含む完了/発生ベースの名前に寄せる
- 例:
  - `service_started`
  - `service_stopped`
  - `pubsub_publish_failed`
  - `pubsub_pull_failed`
  - `signal_rejected`
  - `order_published`
  - `order_execution_failed`
  - `kill_switch_changed`
  - `market_close_guard_triggered`

## この決定で次に必要になること

1. 主要イベントごとのフィールド一覧を決める
2. Python logging から 1 行 1 JSON を出す実装方針を決める
3. Collector 側で追加する共通属性と、アプリ側で出す属性の境界を決める
