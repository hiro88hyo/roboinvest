# Decision Draft: `environment` フィールドの取得元

作成日: 2026-05-24
対象: [index.md](index.md)
Status: Draft

## 結論案

- ログの `environment` は専用の環境変数 `APP_ENV` から取る
- `TRADE_MODE` は `environment` の代用に使わない
- `APP_ENV` が未設定のときは `dev` をデフォルト値とする

## この案を採る理由

- `environment` と `trade_mode` は意味が違う
- `TRADE_MODE=paper/live` は売買経路の意味であり、`production/dev/test` とは別軸
- 今後 `production + paper` や `production + live` の両方があり得るため、混ぜると検索軸が壊れる
- ログ検索や運用切り分けでは `environment` を独立キーにしておいた方が扱いやすい

## 想定する値

- `dev`
- `test`
- `production`

必要になれば将来 `staging` を追加できる。

## 例

- ローカル開発: `APP_ENV=dev`
- integration / CI: `APP_ENV=test`
- LAN host の本番 compose: `APP_ENV=production`

## 採らない案

### `TRADE_MODE` を `environment` に流用する

見送る理由:

- `paper/live` と `dev/production` が混ざる
- `production + paper` のような実運用状態を正しく表せない

### `PUBSUB_EMULATOR_HOST` の有無などから推測する

見送る理由:

- 暗黙的で分かりにくい
- 将来の実行形態が変わったときに壊れやすい

### ホスト名や compose ファイル名から推測する

見送る理由:

- 実装が不安定
- ログの意味が環境依存になる

## 導入方針

- 各サービス settings に `app_env: str = "dev"` 相当を追加するか、共通 logging 初期化関数が `os.environ["APP_ENV"]` を直接読む
- production compose には `APP_ENV=production` を追加する
- dev/test 用 compose やテスト実行環境では必要に応じて `APP_ENV` を設定する

本番 compose では `APP_ENV` を明示する。ライブラリ側の未設定時デフォルト `dev` はローカル開発のための逃げ道であり、本番設定漏れを許容する意図ではない。

## `trade_mode` との関係

- `environment`: `dev/test/production`
- `trade_mode`: `paper/live`

両方をログに持たせることで、例えば `production` 上の `paper` 運用と `live` 運用を区別できる。
