# Decision Draft: Python logging の JSON formatter 方針

作成日: 2026-05-24
対象: [index.md](index.md)
Status: Draft

## 結論案

- 当面は Python 標準 `logging` を維持し、薄い共通 JSON formatter をリポジトリ内で自前実装する
- `structlog` や `python-json-logger` のような外部 logging ライブラリは初期導入では使わない
- 全サービスの `logging.basicConfig(...)` を、共通の logging 初期化関数に寄せる

## この案を第一候補にする理由

- 現在の全サービスがすでに標準 `logging` を使っている
- 変更対象を formatter / setup に閉じ込めやすい
- 依存追加を避けられる
- JSON 1 行出力と共通フィールド付与だけなら標準 `logging` で十分対応できる

## 出力方針

- 1 行 1 JSON
- `stdout` へ出力
- `message` は人間可読を維持する
- 構造化したい値は `extra` 経由で formatter に渡す

## formatter が出す想定キー

共通の必須キー:

- `timestamp`
- `severity`
- `service`
- `environment`
- `event`
- `message`

条件付きキー:

- `trade_mode`
- `symbol`
- `signal_id`
- `order_id`
- `topic`
- `subscription`
- `reason`
- `error_type`

## 実装イメージ

- 共有モジュールに `JsonFormatter`
- 共有モジュールに `configure_logging(service_name: str, environment: str, level: str, json_logs: bool = True)` のような初期化関数
- 各サービスの `__main__.py` では `basicConfig(...)` 直書きをやめて共有初期化関数を呼ぶ
- 通常ログは `logger.info("...")`
- 構造化イベントは `logger.info("...", extra={...})`

## event の扱い

- `event` は構造化ログでは原則必須
- 既存のプレーンログ文言をすぐに全部置き換えず、重要イベントから `extra={"event": ...}` を付ける
- `event` が未指定の通常ログは、導入初期は formatter 側で `event="log"` のようなデフォルトを補う案を許容する

## 例外の扱い

- `logger.exception(...)` は引き続き使う
- formatter は `exc_info` があれば `exception` フィールドにまとめる
- stack trace は JSON の一部として文字列で保持し、過度に細分化しない

## 採らない案

### `structlog` を初手で導入する

見送る理由:

- 学習コストと移行コストが増える
- 今回の目的に対して初期投資が大きい

### `python-json-logger` のような専用 formatter ライブラリを入れる

見送る理由:

- 追加依存を増やすほどの複雑さはまだない
- 欲しい出力形式が比較的単純

### 各サービスが独自 formatter を持つ

見送る理由:

- スキーマの揺れが起きやすい
- 保守コストが上がる

## 導入ステップ案

1. 共通 logging モジュールを 1 つ作る
2. `service_started` / `service_stopped` だけ先に JSON 化する
3. `gateway`, `oms-live`, `strategy-ai` など重要サービスからイベント付きログを増やす
4. その後に全サービスへ共通初期化を広げる

## 残課題

- 共通 logging モジュールをどこに置くか
- `environment` の値をどの設定から取るか
- `json_logs` の ON/OFF を環境変数化するか
- Collector 側で自動付与する属性との重複をどこまで許すか
