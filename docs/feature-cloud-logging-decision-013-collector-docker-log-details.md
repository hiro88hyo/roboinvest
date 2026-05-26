# Decision Draft: Collector による Docker ログ収集の詳細

作成日: 2026-05-24
対象: [docs/feature-cloud-logging.md](feature-cloud-logging.md)
Status: Draft

## 結論案

- Collector は Docker が保持する container log file を読む方式を第一候補とする
- 入力は OpenTelemetry Collector の `filelog` receiver を第一候補とする
- Docker の JSON envelope を parse した後、`log` フィールド内のアプリ JSON をさらに parse する
- parse できないログは捨てず、プレーンテキストの `message` として Cloud Logging へ送る

## 収集対象

```text
/var/lib/docker/containers/*/*-json.log
```

Collector コンテナには host 側の Docker log directory を read-only mount する。

## parse 方針

Docker の標準 json-file log driver では、各行はおおむね以下の形になる。

```json
{"log":"{\"event\":\"service_started\",...}\n","stream":"stdout","time":"2026-05-24T00:00:00.000000000Z"}
```

処理方針:

1. Docker envelope の `time`, `stream`, `log` を読む
2. `log` の末尾改行を取り除く
3. `log` が JSON object として parse できる場合はアプリ JSON として扱う
4. parse できない場合は `message` に格納し、`event="log"` 相当の通常ログとして扱う

## Cloud Logging mapping

- アプリ JSON の `timestamp` がある場合は Cloud Logging の log timestamp に使う
- アプリ JSON の `severity` は Cloud Logging の severity に map する
- アプリ JSON のその他フィールドは原則 `jsonPayload` に残す
- Docker / Collector 由来の host, container, source path は resource または collector attributes として残す

`severity` を `jsonPayload.severity` に残すだけにしない。Cloud Logging の severity filter で検索できる状態を到達条件にする。

## 送信失敗時の扱い

- Collector 側で batch と retry を有効化する
- 短時間の Cloud Logging API 障害では再送を試みる
- 長時間送信できない場合に host disk を圧迫しないよう、queue 上限と drop 方針を明示する
- drop が発生した場合は Collector 自身のログで検知できるようにする

## log rotation / 再起動

- Docker log rotation の設定を確認する
- Collector 再起動時に同じログを重複送信しないため、読み取り位置の保存方式を決める
- rotation で未送信ログが消えないよう、保持サイズと送信遅延の関係を確認する

## 権限

- Collector コンテナは Docker log directory を read-only で読む
- Cloud Logging 書き込み用の Google 認証情報は Collector コンテナだけに渡す
- アプリコンテナには Cloud Logging 用の認証情報を渡さない

## 検証条件

実装前に以下を確認する。

- `JSON_LOGS=true` のログが Cloud Logging で `jsonPayload.event` などとして検索できる
- `JSON_LOGS=false` のプレーンログが捨てられず Cloud Logging に届く
- `severity=ERROR` のログが Cloud Logging の severity filter で検索できる
- Collector 再起動後にログの大きな重複や欠落がない
- Cloud Logging API への一時送信失敗時に retry される

## 残課題

- 実際の Collector config
- Docker log rotation の具体値
- Collector 自身の health check と運用確認手順
- ログ量が増えた場合の除外フィルタと保持期間
