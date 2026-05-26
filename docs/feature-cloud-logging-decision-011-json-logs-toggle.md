# Decision Draft: `json_logs` の切り替え

作成日: 2026-05-24
対象: [docs/feature-cloud-logging.md](feature-cloud-logging.md)
Status: Draft

## 結論案

- JSON ログ出力は環境変数 `JSON_LOGS` で切り替え可能にする
- デフォルト値は `true` とする
- 必要時のみ `JSON_LOGS=false` でプレーンテキストへ戻せるようにする

## この案を採る理由

- Cloud Logging 導入後の通常運用では JSON を標準にしたい
- ただし初期移行時やローカルデバッグ時に、プレーンログへ一時退避できる逃げ道がある方が安全
- formatter の不具合や想定外の downstream 問題が出たときに、即時切り戻ししやすい

## 想定する挙動

- `JSON_LOGS=true` または未設定: 1 行 1 JSON
- `JSON_LOGS=false`: 従来に近いプレーンテキスト形式

`JSON_LOGS=false` は切り戻し用であり、この場合も Collector はログを捨てない。
アプリ JSON として parse できない行はプレーンテキストの `message` として Cloud Logging に送る。

## 適用方針

- 共通 logging 初期化関数が `JSON_LOGS` を読む
- 各サービスは原則その共通挙動に従う
- サービスごとに別々の toggle は持たない

## デフォルトを `true` にする理由

- 今回の feature の目的は Cloud Logging 向けの構造化ログ導入であり、JSON が本命だから
- デフォルトを `false` にすると、導入後も設定漏れでプレーンログが残りやすい
- 運用手順を単純に保てる

## 採らない案

### toggle を持たず、常に JSON 固定にする

見送る理由:

- 初期導入時の切り戻し余地がなくなる
- ローカル調査時の柔軟性が落ちる

### デフォルトを `false` にする

見送る理由:

- 導入しても実際には JSON 化されない環境が出やすい
- 本番 compose で設定漏れが起きると効果が薄い

### サービスごとに個別 toggle を持つ

見送る理由:

- 運用が複雑になる
- フォーマットの統一が崩れる

## 推奨する利用例

- ローカル開発: `JSON_LOGS=false` でもよい
- integration / CI: `JSON_LOGS=true`
- production compose: `JSON_LOGS=true`

## 実装メモ

- 共通 logging 初期化関数は `json_logs: bool | None = None` を受けられるようにし、未指定時は `JSON_LOGS` を読む
- bool 変換は `true/false`, `1/0`, `yes/no` 程度を受ける
- プレーンログ時も `service` 名と基本フォーマットはできるだけ維持する
