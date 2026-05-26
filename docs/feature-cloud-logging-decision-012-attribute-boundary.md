# Decision Draft: アプリ側属性と Collector 側属性の境界

作成日: 2026-05-24
対象: [docs/feature-cloud-logging.md](feature-cloud-logging.md)
Status: Draft

## 結論案

- 業務・アプリ文脈の属性はアプリ側で出す
- 実行基盤・収集経路に関わる属性は Collector 側で付与する
- 同じ意味の値を両側で重複して持たせない

## アプリ側で持つもの

- `timestamp`
- `severity`
- `service`
- `environment`
- `event`
- `message`
- `trade_mode`
- `symbol`
- `signal_id`
- `order_id`
- `topic`
- `subscription`
- `reason`
- `error_type`

理由:

- これらは業務処理やアプリ挙動の文脈で決まる
- アプリが一番正確に意味を知っている

## Collector 側で持つもの

- ホスト名
- コンテナ名
- コンテナ ID
- compose project 名
- 収集元 path / source 種別
- Google Cloud 側の resource 属性

理由:

- 実行基盤や収集パイプラインに紐づく情報だから
- アプリに持たせると環境依存の責務が増える

## `service` の扱い

- `service` はアプリ側が明示的に出す
- Collector 側で付くコンテナ名や resource 名を `service` の代用にしない

理由:

- `gateway` などの論理サービス名を検索軸として固定したい
- コンテナ名は deploy ごとに揺れる可能性がある

Cloud Logging / OTel の `service.name` にも同じ論理サービス名を入れる場合は許容する。
ただし、アプリログ内の `service` と異なる値を入れない。

## `environment` の扱い

- `environment` はアプリ側が `APP_ENV` から出す
- Collector 側の resource 属性とは別物として扱う

## 重複を避ける方針

- たとえば `service.name` のような Collector / OTel の属性と、アプリ JSON の `service` が両方あること自体は許容する
- ただしアプリ独自キーとして何を正とするかは明確にする
- アプリ検索の主軸は、まずアプリ JSON のキーを使う

## 許容する重複

- Cloud Logging / OTel resource に入るインフラ属性
- アプリ JSON に入る論理属性

これは「完全な重複」ではなく、責務の異なる並存として扱う。

## 避けるべき重複

- アプリで `container_name` を出す
- Collector 側で `trade_mode` や `signal_id` を無理に補完する
- 同じ意味の `service` を別キー名で複数持つ

## 検索の基本ルール

- 運用者が一次的に見るのはアプリ JSON の `service`, `environment`, `event`, `symbol`, `signal_id`
- インフラ切り分けが必要なときだけ、Collector / resource 属性で host や container を見る

## 実装メモ

- アプリは業務キーだけを JSON に出す
- Collector はできるだけ薄くし、過剰な変換を持たせない
- 収集パイプラインでアプリ文脈を再構成しようとしない
- `severity` と `timestamp` は Cloud Logging の専用フィールドへ map し、単に `jsonPayload` 内へ残すだけにしない
