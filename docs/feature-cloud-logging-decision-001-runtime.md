# Decision Note: Cloud Logging Runtime Assumption

作成日: 2026-05-23
対象: [docs/feature-cloud-logging.md](feature-cloud-logging.md)

## 決定

- 当面の実行基盤は `LAN host` 上の `docker compose` とする
- Cloud Logging は、そのローカル実行中の本番構成からログを集約するために導入を検討する
- GKE / Cloud Run への移行判断は本 feature のスコープ外とする

## 理由

- 現時点ではコストと複雑性を増やさずに運用を継続したい
- 既存の runbook や production compose は `LAN host + docker compose` 前提で整っている
- 実行基盤の移行とログ集約を同時に進めると、切り分け対象が増えて要件整理がぶれやすい

## これにより解消した未決事項

- 「本番の主要実行基盤を何に寄せるか」は、当面は解消済みとする

## この決定で残る主要論点

1. `LAN host + docker compose` から Cloud Logging へ送る具体方式を何にするか
2. Collector が Docker container logs を読む具体方式を何にするか
3. どのイベントを DB とログの両方に残すか
4. PII / secret / 注文関連のマスキングルールをどうするか

JSON 構造化の粒度と Alerting の分離は、後続 decision で別途整理済み。

## 次の整理対象

- まずは `LAN host + docker compose` からの Cloud Logging 収集方式を比較する
- その後に、DB と運用ログの責務分離を決める
