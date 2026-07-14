# Feature Memo: Cloud Logging へのログ集約

作成日: 2026-05-23
Status: Implemented with Follow-ups
台帳: [docs/features.md](../../features.md)

## 1. 背景

- 現在の各 Python サービスは `logging.basicConfig(...)` により、実質的に `stdout/stderr` へログを出している
- `infra/docker-compose.prod.yml` には `trade-ai-logs:/app/logs` volume があるが、アプリ側ではファイルハンドラを使っておらず、実態としては未活用に近い
- 運用時の確認は `docker compose logs` 依存が強く、検索性・保持・集計・アラート連携が弱い
- 一方で `strategy_logs` / `aggregator_logs` / `trades_live` / `trades_paper` は業務記録であり、アプリ運用ログとは役割が違う

## 2. 目的

- サービス運用ログの一次集約先を Google Cloud Logging に統一する
- コンテナ内ファイルではなく、標準出力ベースでログ収集できる構成に寄せる
- 障害調査、日次監視、アラート設定、後日の追跡をやりやすくする
- 監査用途の DB 記録と、運用観測のためのアプリログを分離する

## 3. スコープ

- 全 Python サービスのアプリログを `stdout/stderr` ベースで収集する
- 実行基盤から Cloud Logging へログを送る
- 検索しやすい最小限の構造化フィールド方針を決める
- 重要イベントのログレベル方針を決める
- 導入後の運用確認手順を runbook に落とす

## 4. 非スコープ

- `strategy_logs` / `aggregator_logs` / `trades_*` を Cloud Logging に置き換えること
- すべての業務イベントをログだけで監査できるようにすること
- いきなり全ログを高粒度 JSON に完全統一すること
- dashboard 側の監視 UI を同時に作り込むこと

## 5. 期待する到達点

- 本番運用時に `docker compose logs` を主たる観測手段にしなくてよい
- サービス単位、銘柄単位、signal/order 単位で最低限の検索ができる
- 例外、注文失敗、Pub/Sub 停滞などの監視対象を Cloud Logging 起点で定義できる
- `trade-ai-logs` volume の要否を判断できる

## 6. 対象ログの分類

| 種別 | 主な保存先 | 用途 |
|---|---|---|
| アプリ運用ログ | Cloud Logging | 例外、警告、処理件数、状態遷移、外部 API エラー |
| 業務監査ログ | Supabase | 戦略判断、統合シグナル、約定、ポジション |
| 一時デバッグログ | 原則 stdout/stderr | 開発・一時調査。常設ファイルは持たない方針を基本とする |

## 7. 最小構造化フィールド案

- `service`
- `environment`
- `trade_mode`
- `symbol`
- `signal_id`
- `order_id`
- `topic` または `subscription`
- `event`
- `reason`

最初から全ログに必須とはせず、主要イベントから段階的にそろえる。

## 8. 重要イベント候補

- サービス起動 / 停止
- Pub/Sub publish / pull / ack の失敗
- 外部 API エラー
- signal reject
- order publish
- order execution failure
- kill switch 変化
- market close guard 発火

## 9. ログレベルの初期方針

- `INFO`: 通常の状態遷移、件数サマリ、主要な業務イベント
- `WARNING`: リトライ可能エラー、想定内だが注意が必要な reject、外部依存の一時失敗
- `ERROR`: 処理失敗、継続不能ではないが運用介入が要るもの
- `EXCEPTION` / traceback: 想定外エラー

## 10. 依存

- 実行基盤: 当面は `LAN host + docker compose` とする
- Cloud Logging 収集方式: 当面は `stdout JSON -> Docker container logs -> OpenTelemetry Collector` とする
- GCP project / IAM / 保持期間 / コスト方針
- どのサービスが本番観測対象かの明確化

## 11. 運用影響

- ログ閲覧権限を GCP IAM ベースで整理する必要がある
- 保持期間とログ量次第でコストが増える
- 監視導線が `docker compose logs` から Cloud Logging へ移る
- runbook の更新が必要になる

## 12. 未決事項

### 解消済み

- Collector が Docker container logs を読む具体方式。
- Collector 側での Docker JSON envelope / アプリ JSON の parse 方針。
- Cloud Logging 上の `timestamp` / `severity` / `jsonPayload` / `resource` への mapping。
- Cloud Logging への認証を Collector 側へ集約する方針。
- `JSON_LOGS=false` による一時切り戻し方針。

### 残課題

- 全サービス・全重要イベントの構造化は段階実装中。
- OMS Live fill / closeout など、一部ログはまだ `event="log"` が多く、`order_filled` / `closeout_completed` / `broker_order_failed` などへ分ける余地がある。
- どのイベントを DB とログの両方に残すかは、イベントごとに継続判断する。
- PII / secret / 注文関連のマスキングルールは、必要に応じて具体化する。
- ログ量、保持期間、除外フィルタ、コスト上限は別途運用判断する。
- Alerting / Monitoring / 通知は別 feature として扱う。
- 2026-07-11: ERROR、closeout invariant、market data stale、broker reject の
  log-based counter metric 定義を `infra/monitoring/log-based-metrics.json` に分離した。
  適用手順は `docs/runbook/log-based-metrics.md`。Alert policy はまだ作成しない。

## 13. 段階的な進め方

1. Done: 現状のログ出力点を棚卸しする。
2. Done: Cloud Logging に載せる対象サービスと基盤を決める。
3. Done: `stdout/stderr` 統一と `trade-ai-logs` volume の扱いを決める。
4. Done: Collector の入力、parse、Cloud Logging mapping を小さく検証する。
5. Partial: 主要イベントだけ構造化フィールドを付与する。
6. Done: runbook に確認手順と障害時の見る場所を書く。
7. Done: ログベースメトリクス / Alerting を別 feature として分離する。

## 14. 実装メモ

- 2026-05-31: `trade_contracts.logging` に `configure_logging` / `JsonFormatter` / `event_extra` を追加した
- 各 Python サービスの entrypoint はデフォルトで stdout/stderr に 1 行 JSON を出力する
- 手元で従来のテキスト形式に戻したい場合は `JSON_LOGS=false` を指定する
- `environment` は `APP_ENV`, `ENVIRONMENT`, `NODE_ENV` の順に読み、未指定時は `dev` とする
- 最初の構造化イベントとして Gateway の `signal_rejected` / `order_published` に検索用フィールドを付与した
- production compose に OpenTelemetry Collector を `observability` profile で追加した
- Collector は `service` と `event` を持つアプリ JSON だけを Cloud Logging へ送る
- `roles/logging.logWriter` 付与後、SA の `entries:write` probe が HTTP 200 になることを確認した
- `observability` profile で Collector だけを起動し、Docker log 経由の probe が Google Cloud Console に流れることを確認した
