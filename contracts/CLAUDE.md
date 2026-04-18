# contracts/

全サービスの Single Source of Truth。Pydantic モデル・SQL マイグレーション・TypeScript 型の3層を一貫させる。

アーキテクチャ全体・テーブル詳細はルート [CLAUDE.md](../CLAUDE.md) を参照。ここでは `contracts/` 配下で作業するときの運用ルールのみを定義する。

## 変更の伝播順序

スキーマを変えるときは必ずこの順で実施する:

1. `python/trade_contracts/*.py` の Pydantic モデルを更新
2. `sql/NNN_*.sql` に新規マイグレーションを追加（既存ファイルは編集しない）
3. `./scripts/gen-supabase-types.sh` を実行して `typescript/src/generated/database.types.ts` を再生成
4. ルートで `make test-all` を実行し、全サービスの CI が通ることを確認

Python だけ、SQL だけの変更は禁止。3層が同期していない PR はマージしない。

## 後方互換ルール

- **フィールド追加**: 新規フィールドは必ず `Optional` / `NULL 許容` で追加し、既存サービスが壊れないようにする
- **フィールド削除**: 即削除せず、非推奨期間を設けて段階的に外す
- **enum 値**: 追加は OK、削除・リネームは破壊的変更として扱う
- **型変更**: `int → Decimal` など互換のない変更は新フィールド導入 + 旧フィールド非推奨で移行

## 命名・型の規約

- DB カラム・Pydantic フィールドは共に `snake_case`
- 価格・金額・損益は必ず `Decimal`（`float` は丸め誤差のため禁止）
- 株数・ボリュームは `int`
- UUID 主キーは Pydantic 側で `default_factory=uuid4`、SQL 側ではアプリ生成値を INSERT する前提で DEFAULT を付けない
- `typescript/src/generated/` は自動生成物。手で編集しない

## マイグレーションのルール

- ファイル名: `NNN_description.sql`（3桁連番、スネークケース）
- 既存マイグレーションは絶対に編集しない。修正が必要なら新ファイルで ALTER/UPDATE する
- CHECK 制約で enum 値を列挙し、Pydantic の enum と完全一致させる
- FK 参照順序: `strategy_logs → aggregator_logs → trades_live/paper`
