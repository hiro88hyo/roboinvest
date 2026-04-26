# dashboard/

Trade AI Agent の運用 UI。Supabase に蓄積された `system_status` / `positions` / `trades_live` / `trades_paper` / `aggregator_logs` / `strategy_logs` を可視化し、Realtime で更新する。書き込みはキルスイッチ（`is_trading_allowed`）と `trade_mode` 切替に限定する。

アーキテクチャ全体・スキーマはルート [CLAUDE.md](../CLAUDE.md) と [contracts/](../contracts/) を参照。ここは `dashboard/` 配下で作業するときの運用ルール。

## 責務 / 非責務

**責務**
- Supabase への読取（PostgREST + Realtime）と画面表示
  - 現在ポジション（`positions`、`trade_type` で live/paper 切替）
  - 約定履歴（`trades_live` / `trades_paper`）
  - Strategy A/B シグナルログ（`strategy_logs`、`source` で RULE/AI 切替）
  - Aggregator 統合シグナル（`aggregator_logs`、`signal_source`）
  - システムステータス（`system_status` シングルトン）
- キルスイッチ ON/OFF（`system_status.is_trading_allowed` の更新）
- `trade_mode` の `live` ⇄ `paper` 切替
- `contracts/typescript/src/generated/database.types.ts` のインポートで型安全な Supabase クライアント

**非責務**
- シグナル生成・売買判断 → Strategy A/B、Aggregator
- リスク制御の実行 → Gateway（Dashboard はキルスイッチを「フラグを倒すだけ」、判定は Gateway 側）
- 約定ロジック → OMS Live / Paper
- バックテスト UI（必要になったら別サービス or 別ページ群として後付け、Phase 3 までのスコープ外）
- 認証・ユーザー管理 → 当面は社内利用前提でスキップ。Supabase Auth 統合は別 PR

## 技術スタック（採用判断）

- **Next.js 15 (App Router) + React 19 + TypeScript 5**
  - RSC で初期描画 → Client Component で Realtime 購読のハイブリッド
  - `app/` ディレクトリ構成、`page.tsx` / `layout.tsx` を最小限に
- **Tailwind CSS 4**
  - CSS-first 設定（`@theme` ブロック）。`tailwind.config.*` は最小限
- **Biome**: lint + format（ESLint / Prettier は使わない）
- **vitest + @testing-library/react**: ユニット / コンポーネントテスト
- **volta** で Node / npm のバージョン固定（グローバルインストール禁止）
- **Supabase クライアント**:
  - Server Component / Server Action: `@supabase/ssr` の `createServerClient`（cookie 経由）
  - Client Component: `@supabase/ssr` の `createBrowserClient`
- **状態管理**: 当面は React の組み込み（`useState` / `useReducer` / Context）。Zustand 等は需要が出てから

## 接続経路の前提

- ローカル開発時の Supabase URL: `http://127.0.0.1:54321`、anon key は `infra/supabase/config.toml` 由来
- Realtime は WebSocket。`NEXT_PUBLIC_SUPABASE_URL` / `NEXT_PUBLIC_SUPABASE_ANON_KEY` に集約
- 書き込み（キルスイッチ等）は **Server Action 経由**。クライアントから直接 service-role キーを使わない
- Supabase service-role キーが必要な操作は `SUPABASE_SECRET_KEY` を server 側でのみ参照（`NEXT_PUBLIC_*` には絶対に置かない）
- ポート 3000 は `infra/CLAUDE.md` で予約済み

## 実装フェーズ

oms-paper / gateway / feeder と同じ 3 フェーズ + scaffold パターン。フェーズごとにコミット → `--no-ff` マージ。

### Phase 0: scaffold + ツールチェイン整備

- `dashboard/` を新規作成（Next.js 15 + Tailwind 4 + Biome + vitest）
- `volta` で Node/npm を固定（`package.json` の `volta` フィールド）
- `contracts/typescript/` を npm パッケージとして整備（`package.json` / `tsconfig.json`）
- `dashboard/tsconfig.json` の `paths` で `@contracts/*` → `../contracts/typescript/src/*` を解決
- `scripts/gen-supabase-types.sh`: Supabase CLI から `database.types.ts` を生成し `contracts/typescript/src/generated/` に書き出す
- ルート `Makefile` の `lint-all` / `test-all` に dashboard を組み込む
- 最小ページ（`/` のプレースホルダ）と vitest の sanity test を 1 本

### Phase 1: 静的読取ページ（RSC）

- `lib/supabase/server.ts`: `createServerClient` ラッパー
- ルート構成:
  - `/` overview（system_status 1 件 + 当日 P/L サマリ）
  - `/positions` 現在ポジション一覧（live / paper タブ）
  - `/trades` 約定履歴（live / paper タブ、無限スクロールは Phase 2 以降）
  - `/signals` Strategy A/B + Aggregator のログ
  - `/system` システムステータス詳細（読取のみ、操作は Phase 3）
- 各ページは RSC で初期データを fetch し、テーブル / カードで表示
- 共通 UI（テーブル / バッジ / カード）は `components/ui/` に最小限
- Tailwind で読みやすい暗背景・等幅数値表示を整える

### Phase 2: Realtime 購読

- `lib/supabase/client.ts`: `createBrowserClient` ラッパー
- Phase 1 の各ページに対応する Client Component を追加
  - `positions` / `trades_live` / `trades_paper` / `strategy_logs` / `aggregator_logs` / `system_status` の `postgres_changes` を購読
  - 初期データは RSC からの hydration、以降は Realtime で差分適用
- 接続状態（connected / reconnecting / disconnected）をヘッダに小さく表示
- コンポーネント単位の vitest テスト（モックチャネルで挿入確認）

### Phase 3: 書き込み（キルスイッチ + trade_mode）

- `/system` に操作 UI を追加
  - キルスイッチ ON/OFF トグル（確認ダイアログ必須）
  - `trade_mode` の `live` ⇄ `paper` セグメント
- Server Action で `system_status` を更新（`SUPABASE_SECRET_KEY` で UPDATE 権限を持たせる）
- 操作ログは `system_status.updated_at` で十分（専用 audit テーブルは作らない、需要が出てから）
- 誤操作防止: live 切替時は確認ダイアログに「実取引が再開されます」を明示

## ディレクトリ構成（想定）

```
dashboard/
├── CLAUDE.md                    # 本ファイル
├── package.json                 # volta + Next.js + Tailwind + Biome + vitest
├── tsconfig.json                # paths で @contracts/* を解決
├── next.config.ts
├── biome.json
├── vitest.config.ts
├── postcss.config.mjs
├── .env.example
├── .gitignore                   # ルート .gitignore で node_modules / .next は既にカバー済
├── public/
└── src/
    ├── app/
    │   ├── layout.tsx           # 共通レイアウト（ヘッダ・接続状態）
    │   ├── page.tsx             # / overview
    │   ├── globals.css          # Tailwind エントリ
    │   ├── positions/page.tsx
    │   ├── trades/page.tsx
    │   ├── signals/page.tsx
    │   └── system/
    │       ├── page.tsx         # 読取（Phase 1）
    │       └── actions.ts       # Server Actions（Phase 3）
    ├── components/
    │   ├── ui/                  # テーブル / バッジ / カード
    │   ├── positions/           # PositionsTable, RealtimePositions
    │   ├── trades/
    │   ├── signals/
    │   └── system/              # KillSwitchToggle, TradeModeSelector
    ├── lib/
    │   ├── supabase/
    │   │   ├── server.ts        # Phase 1: createServerClient
    │   │   └── client.ts        # Phase 2: createBrowserClient
    │   └── format.ts            # 数値・日時フォーマット（JST）
    └── __tests__/               # vitest（コンポーネント単位）
```

## 設定（env）

`.env.example` に列挙するキー:
- `NEXT_PUBLIC_SUPABASE_URL`: 既定 `http://127.0.0.1:54321`
- `NEXT_PUBLIC_SUPABASE_ANON_KEY`: ローカル Supabase の anon キー
- `SUPABASE_SECRET_KEY`: Server Action での書き込み専用（**`NEXT_PUBLIC_*` には絶対に置かない**）
- `NEXT_PUBLIC_APP_TIMEZONE`: 既定 `Asia/Tokyo`

`.env.local` は `.gitignore` 済み。コミット禁止。

## 開発コマンド

```bash
cd dashboard
npm install
npm run dev         # http://localhost:3000
npm run build
npm run lint        # Biome check
npm run format      # Biome format
npm test            # vitest
```

ルートからは `make lint-all` / `make test-all` で Python 側と一括実行。

## 開発時の注意

- **volta 経由必須**: `npm i -g` で Node / npm をグローバル汚染しない。`package.json` の `volta` フィールドが Single Source of Truth
- **Server / Client の境界**: Supabase client は server 用と browser 用で別ファイル。混在させない（`"use client"` でないファイルから browser client を import するとビルド時に検出されない事故が起きやすい）
- **service-role キーは server only**: `NEXT_PUBLIC_*` プレフィックスを付けない。間違えるとブラウザに露出する
- **型は手動更新しない**: `database.types.ts` は `scripts/gen-supabase-types.sh` でのみ更新。スキーマ変更時は contracts/sql → 型再生成 → dashboard ビルドの順
- **Realtime の filter は最小限に**: 全テーブル全件購読すると無駄に重い。表示中の銘柄・直近 N 件などにフィルタ
- **Realtime の重複に備える**: at-least-once。`signal_id` / `trade_id` を key にしてクライアント側で dedupe する
- **時刻は JST 表示**: Supabase は timestamptz（UTC 保持）。表示時に `Asia/Tokyo` に変換
- **キルスイッチ操作は冪等**: 連打されてもサーバ側で同一値書き込みを許容する。トグル UI は楽観更新せず、Server Action 完了を待ってから状態反映
- **テストは I/O モック前提**: vitest ではネットワーク叩かない。Supabase client はテスト時に注入する設計に倒す（DI もしくはモジュールモック）
