# Decision Note: 共通 logging モジュールの置き場所

作成日: 2026-05-24
対象: [index.md](index.md)
Status: Accepted

## 結論案

- 共通 logging モジュールは当面 `contracts/python/trade_contracts/` 配下に置く
- 具体的には `trade_contracts.logging` のようなモジュール名を第一候補とする
- 新しい shared workspace package はこの段階では作らない

## この案を採る理由

- 既存の全サービスはすでに `trade-contracts` に依存している
- import path を増やさず、全サービスからすぐに使える
- 今回必要なのは小さな formatter / setup 関数であり、新規 package を切るほどの規模ではない
- shared code の追加コストを最小化できる

## 置き場所の候補

### 第一候補

- `contracts/python/trade_contracts/logging.py`

入れるもの:

- `JsonFormatter`
- `configure_logging(...)`
- event 付き logging を補助する小さな helper

### 第二候補

- `contracts/python/trade_contracts/observability.py`

この候補を第二にする理由:

- 将来 traces / metrics まで含めたくなった場合は名前として自然
- ただし現時点では scope が logs に限定されており、少し広すぎる

## 新しい shared package を作らない理由

- `services-common` のような package を切ると workspace 設定、依存、import 整理が増える
- 現時点の要件は logging 初期化と formatter が中心で、責務がまだ小さい
- 共有基盤を増やしすぎるより、まずは最小構成で導入した方が進めやすい

## 注意点

- `trade_contracts` は本来 contracts / schema の色が強いので、運用系ユーティリティを増やしすぎない
- logging モジュールは薄く保ち、サービス固有ロジックを持ち込まない
- observability 関連が今後増えすぎる場合は、後で shared package に分離できる前提で設計する

## 推奨する実装境界

- `trade_contracts.logging`:
  - formatter
  - logging setup
  - 共通キーの整形
- 各サービス側:
  - `service` 名の指定
  - event ごとの `extra` 内容
  - サービス固有のメッセージ文言

## 将来の分離条件

以下のどれかを満たしたら、専用 shared package への切り出しを再検討する。

- logging 以外の observability 共通機能が増える
- traces / metrics の共通初期化まで持ち始める
- `trade_contracts` の責務が明らかに広がりすぎる
