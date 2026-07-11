# Dev Setup

ローカル開発環境の立ち上げ手順と、現時点で残っている作業の一覧。

## 前提ツール

| ツール | 用途 | 確認 |
|---|---|---|
| Docker / Docker Compose v2 | Pub/Sub エミュレータ、テスト用 Postgres | `docker compose version` |
| uv | Python パッケージ管理 | `uv --version` |
| volta | Node.js バージョン固定（Dashboard 作業時） | `volta --version` |
| Supabase CLI | ローカル Supabase 起動 | `supabase --version` |

`gcloud` は host に入れない方針（Pub/Sub 関連は docker compose 内の cloud-sdk イメージで完結）。アドホックに使いたい場合のワンライナーは「動作確認」節参照。

Supabase CLI 未インストールの場合（`npm -g` は非対応。Homebrew か release バイナリをユーザー領域に入れる）:

```bash
# macOS / Linux (Homebrew)
brew install supabase/tap/supabase

# Linux (Homebrew を入れたくない場合、GitHub release の静的バイナリを ~/.local/bin へ)
curl -L https://github.com/supabase/cli/releases/latest/download/supabase_linux_amd64.tar.gz \
  | tar -xz -C /tmp
mkdir -p ~/.local/bin
install /tmp/supabase ~/.local/bin/supabase
# PATH に ~/.local/bin が未登録なら shell rc に追記:
#   echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
supabase --version

# 一時利用のみなら npx（グローバル install 不要、毎回起動は遅め）
npx supabase start
```

## 初回セットアップ

1. **リポジトリクローン & Python 依存同期**
   ```bash
   git clone git@github.com:hiro88hyo/roboinvest.git
   cd roboinvest
   uv sync
   ```

2. **環境変数テンプレをコピー**
   ```bash
   cp infra/.env.example .env
   ```
   `SUPABASE_PUBLISHABLE_KEY` / `SUPABASE_SECRET_KEY` は次ステップの出力で埋める。
   各サービスを直接起動する場合は `services/<name>/.env.example` も `.env` にコピーして、同じ Supabase / Pub/Sub 値を転記する。

3. **Supabase ローカル起動（初回はイメージ pull で数分かかる）**
   ```bash
   cd infra && supabase start
   ```
   コンソールに `Publishable key` (`sb_publishable_...`) と `Secret key` (`sb_secret_...`) が表示されるので `.env` に転記。
   旧 `anon` / `service_role` (JWT) は deprecated のため使わない。
   Studio は http://localhost:54323 で開ける。

4. **マイグレーション & シード適用**
   `supabase start` が `infra/supabase/migrations/` を自動適用する（`contracts/sql/` への symlink）。
   シードは `supabase db reset` 時に `seed.sql` が実行される。
   `ls -la infra/supabase/migrations/` で symlink 切れがないこと、
   `cd infra && supabase migration list --local` で末尾の migration まで揃うことを確認する。

5. **Pub/Sub エミュレータ起動**
   ```bash
   docker compose -f infra/docker-compose.dev.yml up -d
   ```
   `pubsub-init` が `infra/pubsub/topics.json` / `infra/pubsub/subscriptions.json`
   を SSOT として、現行 9 トピック + 13 サブスクリプションを作成して終了する。

## 日常運用コマンド

```bash
# Supabase の停止 / 再起動
cd infra && supabase stop
cd infra && supabase start

# DB を初期化してマイグレーション＋シード再適用
cd infra && supabase db reset

# Pub/Sub の停止
docker compose -f infra/docker-compose.dev.yml down

# CI テスト用スタック
docker compose -f infra/docker-compose.test.yml up -d
docker compose -f infra/docker-compose.test.yml down -v

# 全サービスの lint / test（実装が揃ってから）
make lint-all
make test-all

# Supabase 型再生成
./scripts/gen-supabase-types.sh

# 開発スタックのヘルスチェック
uv run scripts/health-check.py

# Paper trading 起動前チェック + サービス起動
bash scripts/start-paper-trading.sh
```

## 動作確認（スモークテスト）

```bash
# Supabase への接続
psql postgresql://postgres:postgres@127.0.0.1:54322/postgres -c '\dt'
# → system_status, positions, strategy_logs 等が並ぶはず

# Pub/Sub のトピック一覧（REST API を直接叩く、gcloud 不要）
curl -s http://localhost:8085/v1/projects/trade-ai-dev/topics | python3 -m json.tool
# → topics.json と同じ現行 9 件

# Pub/Sub のサブスクリプション一覧
curl -s http://localhost:8085/v1/projects/trade-ai-dev/subscriptions | python3 -m json.tool
# → subscriptions.json と同じ現行 13 件

# アドホックに gcloud コマンドを使いたい場合（host にインストール不要、docker 経由）
docker run --rm --network host \
  -e PUBSUB_EMULATOR_HOST=localhost:8085 \
  gcr.io/google.com/cloudsdktool/cloud-sdk:slim \
  gcloud pubsub topics list --project=trade-ai-dev

# system_status シード確認
psql postgresql://postgres:postgres@127.0.0.1:54322/postgres \
  -c 'select id, trade_mode, trading_style from system_status;'
# → id=1, paper, day
```

## 既知の未完了作業（TODO）

実装を進める上でまだ手が回っていない箇所。次フェーズで順次解決する。

- [ ] `infra/docker-compose.dev.yml` は Pub/Sub emulator 用で、アプリケーションサービスは `bash scripts/start-paper-trading.sh` または各サービスの `uv run python -m <pkg> stream` で起動する運用
- [ ] 本番向け `infra/docker-compose.prod.yml` は未作成。詳細は `docs/adr/0001-implementation-checklist.md`
- [ ] `.env.example` の `SUPABASE_*` キーはプレースホルダ。`supabase start` 実行後に各開発者が手動で埋める運用
- [ ] サービス別 `.env` は共通 `.env` から自動生成されない。必要なサービスの `.env.example` をコピーして手動で同期する
- [ ] Supabase CLI 無しで動かす代替フロー（生 Postgres + psql でマイグレーション適用）は未整備。インストール不要の軽量ルートが欲しければ別途 compose 定義を追加する
- [ ] Pub/Sub エミュレータは永続化していない。再起動でトピックは再作成されるがメッセージは消える（開発用途なので許容）
- [ ] Paper 起動スクリプトは `/tmp/feeder-prod.env` がある場合だけ feeder を自動起動する。kabu 接続情報は手元の private env で管理する

## トラブルシューティング

| 症状 | 対処 |
|---|---|
| `supabase start` がポート競合で失敗 | 他プロセスの 54321-54324 を停止、または `infra/supabase/config.toml` のポートを変更 |
| `docker compose up` で `pubsub-init` が繰り返し再起動 | `restart: "no"` 指定済み。1 回成功して exit 0 で終わるのが正常 |
| マイグレーションが適用されない | symlink 切れを `ls -la infra/supabase/migrations/` で確認し、`cd infra && supabase migration list --local` と照合。切れていたら再作成 |
| Studio で DB スキーマが空 | `supabase db reset` でマイグレーション＋シードを再適用 |
