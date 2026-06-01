# Decision Note: ログ取り込み経路

作成日: 2026-05-23
対象: [index.md](index.md)

## 決定

- 当面のログ取り込み経路は `stdout JSON -> OpenTelemetry Collector` とする
- 各コンテナ内アプリから OTLP で Collector へ直接送る方式は、将来の拡張候補として扱う

## 採用構成

```text
application
  -> stdout / stderr
  -> Docker container logs
  -> OpenTelemetry Collector
  -> Google Cloud
```

## 理由

- 現在のサービスはすでに標準 logging ベースで `stdout/stderr` 出力に寄っている
- 全サービスへ OTel logging/exporter を導入するより変更量が小さい
- 認証、再送、バッファリング、送信失敗時の扱いを Collector 側へ集約できる
- 今回は logs が対象であり、traces/metrics まで含めた完全な OTel 化は別段でよい

## 今回採らない案

### 各アプリから OTLP で Collector へ直接送る

見送る理由:

- 全サービスへ SDK または logging bridge の導入が必要
- logs だけ先に整える目的に対して導入コストが高い
- アプリ側の責務が増える

## この決定で必要になること

1. アプリログを構造化 JSON に寄せる
2. Collector が Docker コンテナログを読む具体方式を決める
3. Collector 側で付与する共通属性を決める
4. `trade-ai-logs` volume の撤去可否を判断する

特に 2 は実装上の主要リスクであり、Docker の json-file log envelope、アプリ JSON の二段 parse、Cloud Logging の severity/timestamp mapping、log rotation、Collector 再起動時の読み取り位置を検証対象に含める。

## 将来の見直し条件

- traces と logs をより厳密に相関したくなったとき
- アプリから OTLP log record を直接出す方が運用上有利になったとき
- Docker ログ経由の制約が問題になったとき
