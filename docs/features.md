# Feature Ledger

最終更新: 2026-06-01

この文書は、将来の実装候補・改善候補を feature として整理し、要件検討の入口をそろえるための台帳。
実装仕様書ではなく、何を独立した feature とみなすかを揃えることを目的とする。

## 1. 使い方

- 大項目: 継続的に管理したいテーマ群
- 中項目: 実装や要件整理の単位として扱う feature
- Status:
  - `Idea`: 方向性だけある
  - `Draft`: 要件整理を始めた
  - `Ready`: 実装着手できる
  - `Done`: 実装済み

必要に応じて各中項目ごとに、別 ADR / runbook / issue / 設計メモへ分離する。

## 2. 台帳

### A. Observability / Operations

| 中項目 | Status | 目的 | メモ |
|---|---|---|---|
| Cloud Logging へのログ集約 | Done | サービスログの収集先を GCP に統一する | [features/cloud-logging/index.md](features/cloud-logging/index.md)。pipeline は実装済み。イベント構造化の拡張は follow-up |
| アプリログの stdout/stderr 統一 | Draft | ファイル依存を減らし、実行基盤の標準収集に寄せる | `trade-ai-logs:/app/logs` volume はあるが、現状は実質未活用 |
| 構造化 JSON ログ対応 | Idea | 検索・相関分析・メトリクス化をしやすくする | 共通キー候補: `service`, `env`, `symbol`, `signal_id`, `order_id` |
| ログベースメトリクス / Alerting | Idea | 例外・注文失敗・Pub/Sub 停滞などを監視する | Cloud Logging 導入後に具体化する |
| Discord 通知 | Draft | 監視 alert と約定・決済イベントを Discord に通知する | [features/discord-notifications.md](features/discord-notifications.md)。監視系と取引系で topic / Function を分ける |
| 監査ログと運用ログの責務分離 | Draft | DB に残す業務記録と、運用観測用ログを分離する | `strategy_logs` / `aggregator_logs` は監査寄り、アプリ例外は運用ログ寄り |

### B. Production Platform

| 中項目 | Status | 目的 | メモ |
|---|---|---|---|
| ADR-0001 デプロイ構成の本実装 | Draft | 文書化済みの production 構成を実運用レベルで固める | `docs/adr/0001-deployment-architecture.md` と runbook が起点 |
| 本番 compose の安定運用 | Draft | 起動・再起動・日次運用・障害時手順を明確にする | 手順はあるが 24/7 運用としては未完成 |
| Secret / Credential 運用の整理 | Idea | GCP / Supabase / kabu / Gemini の取り扱いを標準化する | `op run` 前提の整理と host 上の一時ファイル管理が論点 |
| 永続 volume の棚卸し | Idea | 本当に必要な永続データだけ残す | `trade-ai-logs` の削除候補を含む |

### C. Trading Reliability

| 中項目 | Status | 目的 | メモ |
|---|---|---|---|
| live 注文フローの安定化 | Draft | live/paper 差分による事故を減らす | `gateway -> oms-live` 周りの reject 理由整理を含む |
| stale signal / backlog signal 対策 | Draft | 古いシグナルでの誤発注や無駄な reject を減らす | Pub/Sub backlog purge は運用済み、恒久対策は別途整理 |
| 引け後 / 市場外ガードの強化 | Draft | 営業時間外の publish / 発注を fail-close にする | 一部ガードは実装済み、仕様として整理余地あり |
| ポジション整合性の強化 | Idea | Supabase と kabu 実保有の不整合を抑える | reconcile 手順はあるが常設監視ではない |
| 異常系 runbook 整備 | Idea | kabu `Code 5` / `Code 8` などの復旧手順を定型化する | live 運用の再現性向上が目的 |

### D. Data Pipeline

| 中項目 | Status | 目的 | メモ |
|---|---|---|---|
| Universe Scanner 日次自動化の完成 | Draft | 毎営業日の watchlist 更新を安定運用にする | timer 導入済み、継続観測と異常時手順が残る |
| market data pipeline の健全性監視 | Idea | `feeder -> feature-engine` の詰まりを早期検知する | reconnect, publish 遅延, 欠損観測を含む |
| feature ストレージ運用の明確化 | Idea | warm / cold data の役割と保持方針を固める | `/data/warm`, `/data/cold` の運用方針整理 |
| daily_ohlcv 更新の堅牢化 | Draft | batch 更新失敗時の再実行性を高める | chunk upsert は実装済み、監視は未整理 |

### E. Strategy / Decisioning

| 中項目 | Status | 目的 | メモ |
|---|---|---|---|
| rule 戦略パラメータ管理 | Idea | RSI / SMA / Bollinger の設定変更を追跡可能にする | env 任せから一段整理したい |
| AI strategy gating の改善 | Draft | AI 呼び出し頻度と入力品質を安定化する | `strategy-ai-triggers` 経路の設計整理を含む |
| market regime / 地合いフィルタ | Draft | 急落・全面安相場で新規 BUY を抑制する | [features/market-regime-filter.md](features/market-regime-filter.md)。Universe Scanner の寄り前判定、AI 総合判定、Gateway fail-close を組み合わせる |
| consensus ルールの見直し | Idea | RULE / AI の重み付けと conflict policy を再評価する | 実運用ログを材料に要件化したい |
| feature と signal の説明可能性向上 | Idea | どの入力でどの判断になったかを追いやすくする | dashboard / logs / DB の責務分担が論点 |

### F. Dashboard / Operator UX

| 中項目 | Status | 目的 | メモ |
|---|---|---|---|
| 運用ダッシュボードの監視導線強化 | Idea | 異常検知から切り分けまでの時間を短縮する | Cloud Logging 導入後の導線設計と相性がよい |
| kill switch / GO 判定 UI の整備 | Idea | 運用判断を手順依存から減らす | 現状は runbook 依存が大きい |
| live / paper 状態比較ビュー | Idea | 2 系統の差分を見やすくする | ポジション、約定、reject、signal 流量が候補 |

## 3. 直近で要件整理したい項目

優先して要件を詰める候補:

1. [Cloud Logging へのログ集約](features/cloud-logging/index.md)
2. 監査ログと運用ログの責務分離
3. live 注文フローの安定化
4. stale signal / backlog signal 対策
5. market data pipeline の健全性監視

## 4. 要件メモのテンプレート

新しい中項目を掘るときは、最低限この観点を埋める。

| 観点 | 内容 |
|---|---|
| 背景 | 何が困っているか |
| 目的 | 何を改善したいか |
| スコープ | この feature に含めるもの |
| 非スコープ | 今回やらないもの |
| 依存 | 先に決めること、依存する基盤 |
| 運用影響 | 監視、手順、権限、コスト |
| 未決事項 | まだ決まっていないこと |
