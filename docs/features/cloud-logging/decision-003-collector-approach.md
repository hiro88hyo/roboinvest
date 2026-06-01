# Decision Note: Cloud Logging の収集方式

作成日: 2026-05-23
対象: [index.md](index.md)
Status: Accepted

## 結論案

- 当面のログ収集方式は `stdout JSON + OpenTelemetry Collector` を第一候補とする
- Collector は `LAN host` 上で `docker compose` の一員として動かす
- Cloud Logging への送信認証は Collector 側に集約する
- 本 feature では logs のみを対象とし、metrics は別 feature とする

## 構成イメージ

```text
Python services
  -> stdout / stderr
  -> Docker container logs
  -> OpenTelemetry Collector
  -> Google Cloud Telemetry API / Cloud Logging
```

## この案を第一候補にする理由

- アプリが Google Cloud 固有ライブラリに直接依存しない
- 認証情報を各サービスに配らず、Collector 側に集約できる
- ログ収集の設定を host 側で一元管理できる
- 将来 traces を追加するときに forwarder を入れ替えずに済む
- Google は vendor-neutral な OpenTelemetry を推奨している

## 前提

- 各サービスは引き続き `stdout/stderr` を基本の出力先とする
- できるだけ早い段階でプレーンテキストから構造化 JSON に寄せる
- `trade-ai-logs:/app/logs` のようなログ専用 volume は廃止候補とする

## 役割分担

### アプリケーション側

- 標準 logging を維持する
- 重要イベントに共通キーを載せる
- 可能であれば 1 行 1 JSON の形式に寄せる

### Collector 側

- コンテナログの収集
- 必要最小限の変換
- Google Cloud への認証
- バッチ送信とリトライ

## 収集入力の第一候補

- Docker が保持するコンテナログを Collector が読む

この案を第一候補にする理由:

- 既存サービスの変更が最小で済む
- アプリごとに OTLP exporter を組み込まなくてよい
- まず logs だけを対象にしやすい

## 収集入力の代替案

### 代替案 A: 各アプリから OTLP で Collector へ直接送る

利点:

- アプリから Collector へ構造化済みの log record を送れる
- trace 相関を取りやすい

弱点:

- 全サービスに OTel SDK または logging bridge の導入が必要
- 今回の「まず logs だけ」には少し重い

### 代替案 B: 各アプリから Google Cloud Logging ライブラリで直接送る

利点:

- Cloud Logging へ直行できる

弱点:

- GCP 依存が各サービスへ分散する
- 認証や再送方針の制御が分散する
- 将来の送り先変更に弱い

### 代替案 C: Fluent Bit / Vector などの別 forwarder を使う

利点:

- ログ専用として軽量
- 実績が多い

弱点:

- 今回の文脈では OpenTelemetry より拡張の一貫性が弱い
- Google 純正の推奨導線は OTel の方が明確

## 認証方針の案

- Collector コンテナにのみ Google 認証を持たせる
- 認証は ADC ベースとし、サービスアカウントに `roles/logging.logWriter` 相当の書き込み権限を付与する
- サービス本体コンテナには Cloud Logging 用の認証情報を配らない

## この案で先に決めるべきこと

1. Collector が Docker ログをどう読むか
2. JSON 構造化の最低ラインをどこに置くか
3. `resource` / `service` / `environment` などの共通属性をどこで付けるか
4. `trade-ai-logs` volume をいつ撤去するか

Collector が Docker ログを読む具体方式は、別メモ
[decision-013-collector-docker-log-details.md](decision-013-collector-docker-log-details.md)
で詰める。

## 残課題

- 解消済み: `LAN host + docker compose` 上での Collector 入力方式の詳細は [decision-013-collector-docker-log-details.md](decision-013-collector-docker-log-details.md) で整理し、production compose に実装した。
- 解消済み: JSON 構造化の粒度は [decision-005-json-granularity.md](decision-005-json-granularity.md) で整理した。
- 残り: DB 監査ログと運用ログの責務分離はイベントごとに継続判断する。
- 残り: マスキングルールは必要に応じて具体化する。

## 現時点の推奨順位

1. `stdout JSON + OpenTelemetry Collector`
2. `stdout JSON + Fluent Bit`
3. 各サービスから Cloud Logging ライブラリで直接送信
