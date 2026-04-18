# infra/

ローカル開発 / CI / ステージング環境のオーケストレーション定義。Supabase・Pub/Sub エミュレータ・全サービスの起動構成をここで管理する。

アーキテクチャ全体は [ルート CLAUDE.md](../CLAUDE.md)、スキーマは [contracts/](../contracts/) を参照。ここは `infra/` 配下で作業するときの運用ルール。

## ここに置くもの / 置かないもの

**置く**
- `docker-compose.*.yml`（dev / test / ci 等のオーケストレーション）
- Supabase ローカル設定（`supabase/config.toml`, `seed.sql`）
- Pub/Sub エミュレータ初期化（トピック定義 JSON）
- 全サービス共通の `.env.example`

**置かない**
- 各サービスの `Dockerfile` → `services/<name>/Dockerfile` に置く
- 個別サービスのビジネスロジック・設定
- 本番シークレット（Supabase サービスロールキー、API トークン等）→ Secret Manager 等の外部ストアへ

## 想定ファイル構成

```
infra/
├── docker-compose.dev.yml       # ローカル全サービス + Supabase + Pub/Sub emulator
├── docker-compose.test.yml      # CI 用（DB + Pub/Sub のみ、サービスは都度ビルド）
├── supabase/
│   ├── config.toml              # supabase init 成果物
│   ├── migrations/              # contracts/sql/*.sql への symlink または copy-on-bootstrap
│   └── seed.sql                 # system_status の初期行、開発用ダミー watchlist 等
├── pubsub/
│   └── topics.json              # raw-market-data, processed-features 等のトピック定義
└── .env.example                 # 全サービス共有の env テンプレ
```

## docker-compose の分割方針

- `dev`: 開発者が `docker compose -f infra/docker-compose.dev.yml up` で全てを一発起動できる
- `test`: CI で Supabase + Pub/Sub だけ立て、サービスは `pytest` 側で起動
- 本番は Docker Compose ではなく Kubernetes / Cloud Run 等の別定義を使う想定（範囲外）

## Supabase ローカル運用フロー

1. `supabase start` で Postgres + Studio を起動
2. `contracts/sql/*.sql` を番号順に適用
3. `infra/supabase/seed.sql` で `system_status (id=1)` の初期行等を投入
4. `scripts/gen-supabase-types.sh` を実行して `contracts/typescript/src/generated/database.types.ts` を再生成

マイグレーションの正本は `contracts/sql/`。`infra/supabase/migrations/` はそこを参照する形にし、二重管理しない。

## Pub/Sub エミュレータ運用

- Google Cloud Pub/Sub エミュレータ（`gcr.io/google.com/cloudsdktool/cloud-sdk`）を使用
- トピック一覧は `pubsub/topics.json` を Single Source of Truth とし、起動時にスクリプトで作成
- トピック名はルート CLAUDE.md の「Pub/Sub トピック一覧」と完全一致させる

## 環境変数・秘密情報

- `.env.example` は全キーをダミー値で列挙してコミット
- `.env` / `.env.local` は `.gitignore` 済み。絶対にコミットしない
- サービスごとの env は `services/<name>/.env.example` に置き、`infra/.env.example` は共通キーだけ（DB 接続情報、Pub/Sub エンドポイント等）

## ポート割当（ローカル）

衝突を避けるため以下を予約:

| ポート | 用途 |
|---|---|
| 54321 | Supabase API |
| 54322 | Supabase DB (Postgres) |
| 54323 | Supabase Studio |
| 8085 | Pub/Sub エミュレータ |
| 3000 | Dashboard (Next.js) |
| 8000 番台 | 各サービスの HTTP エンドポイント（ヘルスチェック等）|

新しいサービスを追加するときはこの表を更新する。
