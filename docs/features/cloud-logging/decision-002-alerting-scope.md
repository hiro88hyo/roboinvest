# Decision Note: Cloud Logging と Alerting の分離

作成日: 2026-05-23
対象: [index.md](index.md)

## 決定

- Alerting は `Cloud Logging へのログ集約` と同一 feature に含めない
- Alerting は別 feature として扱う

## 理由

- まずはログの集約先、出力方針、責務分離を固める方が先
- ログ集約とアラート設計を同時に進めると、要件が広がって整理しにくい
- Alerting は監視対象、閾値、通知先、運用当番など別の論点を持つ

## この決定で Cloud Logging feature に残る範囲

- `stdout/stderr` ベースのログ集約
- Cloud Logging への収集方式
- 構造化フィールドの最小方針
- DB 上の監査ログと運用ログの責務分離
- runbook 上の確認導線の整理

## この決定で Cloud Logging feature から外れる範囲

- ログベースメトリクス設計
- アラート条件の定義
- 通知チャネル設計
- オンコールや運用フローへの組み込み

## 次の主要論点

1. Collector が Docker container logs を読む具体方式を何にするか
2. Cloud Logging 上の `timestamp` / `severity` / `jsonPayload` / `resource` への mapping をどうするか
3. どのイベントを DB とログの両方に残すか
4. PII / secret / 注文関連のマスキングルールをどうするか

JSON 構造化の粒度は、後続 decision で別途整理済み。
