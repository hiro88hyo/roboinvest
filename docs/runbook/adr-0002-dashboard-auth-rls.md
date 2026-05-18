# ADR-0002 Dashboard Auth And RLS Runbook

作成日: 2026-05-17

Production Dashboard の OAuth2 + Supabase RLS 移行手順。
本番テストが一通り完了するまでは実施しない。
この runbook は設計の固定を目的とし、現時点で本番設定を変更しない。

## 1. Pre-change Guard

RLS 移行前に Vercel 側で一時的な入口制限を有効化する。

- Deployment Protection、Password Protection、SSO、Vercel Firewall のいずれかを有効にする。
- Production URL と Preview URL の両方を対象にする。
- protection を有効化した状態で `/`, `/positions`, `/trades`, `/signals`, `/system` が保護されることを確認する。

## 2. Supabase Auth Provider

Supabase Dashboard で OAuth provider を有効化する。

初期候補:

- Google: 個人 Gmail / Google Workspace で運用する場合。
- GitHub: repo access と運用者が一致する場合。

設定する redirect URL:

```text
https://<production-dashboard-domain>/auth/callback
https://<preview-dashboard-domain>/auth/callback
http://localhost:3000/auth/callback
```

許可 user は OAuth provider 側だけに依存せず、`dashboard_admins` table でも制御する。

### Supabase Dashboard Setup

Supabase project の Dashboard で次を設定する。

1. `Authentication` -> `Providers` を開く。
2. 使う provider を有効化する。
3. provider の client ID / client secret を登録する。
4. `Authentication` -> `URL Configuration` を開く。
5. Site URL に production Dashboard URL を設定する。
6. Redirect URLs に production / stable preview / local callback URL を追加する。

登録する Redirect URLs:

```text
https://<production-dashboard-domain>/auth/callback
https://<stable-preview-dashboard-domain>/auth/callback
http://localhost:3000/auth/callback
```

Vercel の branch preview URL は commit ごとに変わるため、OAuth callback URL としては常用しない。
Preview で OAuth を検証する場合は、Vercel の stable branch domain または検証用 custom domain を使い、その URL を Supabase と provider 側の両方に登録する。

### GitHub OAuth App Setup

GitHub を使う場合は GitHub の OAuth App を作成する。

1. GitHub `Settings` -> `Developer settings` -> `OAuth Apps` を開く。
2. `New OAuth App` を作成する。
3. `Application name` は `Trade AI Dashboard` など識別できる名前にする。
4. `Homepage URL` は production Dashboard URL にする。
5. `Authorization callback URL` は Supabase callback URL にする。

GitHub OAuth App の callback は Dashboard の `/auth/callback` ではなく、Supabase Auth の callback URL を使う。
Supabase Dashboard の GitHub provider 設定画面に表示される callback URL をそのまま登録する。

```text
https://<project-ref>.supabase.co/auth/v1/callback
```

GitHub から発行された client ID / client secret を Supabase GitHub provider に登録する。
client secret は repo や Vercel env には置かず、Supabase provider 設定と 1Password にだけ保存する。

### Google OAuth Client Setup

Google を使う場合は Google Cloud Console で OAuth client を作成する。

1. Google Cloud Console で対象 project を開く。
2. `APIs & Services` -> `OAuth consent screen` を設定する。
3. `APIs & Services` -> `Credentials` -> `Create Credentials` -> `OAuth client ID` を選ぶ。
4. Application type は `Web application` にする。
5. Authorized JavaScript origins に production / stable preview / local origin を追加する。
6. Authorized redirect URIs に Supabase Auth callback URL を追加する。

Authorized JavaScript origins:

```text
https://<production-dashboard-domain>
https://<stable-preview-dashboard-domain>
http://localhost:3000
```

Authorized redirect URI:

```text
https://<project-ref>.supabase.co/auth/v1/callback
```

Google から発行された client ID / client secret を Supabase Google provider に登録する。

### Vercel Environment Variables

Dashboard 側は Supabase Auth provider の選択に次の env を使う。

```text
NEXT_PUBLIC_SUPABASE_AUTH_PROVIDER=github
```

値は `github` または `google`。
未設定または不正値の場合、Dashboard は `github` を使う。

Vercel の Production / Preview の両方に設定する。
provider を切り替えた場合は Vercel redeploy が必要。

### Initial Admin User Registration

初回 admin 登録は OAuth login 後に行う。
最初の login 直後は `auth.users` に user は作られるが、`dashboard_admins` に入るまでは Dashboard data を読めない。

Supabase SQL editor で user を確認する。

```sql
select id, email, created_at
from auth.users
order by created_at desc
limit 10;
```

admin user を追加する。

```sql
insert into public.dashboard_admins (user_id)
select id
from auth.users
where email = '<operator-email>'
on conflict (user_id) do nothing;
```

登録後、対象 user で再読み込みする。
JWT / Realtime session の反映が遅い場合は一度 sign out して sign in し直す。

## 3. Schema Migration Draft

実装時は migration file として追加する。
直接本番 SQL editor で試す場合も、最終的に repo の SQL と一致させる。

```sql
create table if not exists dashboard_admins (
  user_id uuid primary key references auth.users(id) on delete cascade,
  created_at timestamptz not null default now()
);

alter table dashboard_admins enable row level security;
```

admin user 追加:

```sql
insert into dashboard_admins (user_id)
select id
from auth.users
where email = '<operator-email>'
on conflict (user_id) do nothing;
```

## 4. Policy Migration Draft

既存の anon policies を削除する。

```sql
drop policy if exists dashboard_anon_select_system_status on system_status;
drop policy if exists dashboard_anon_select_positions on positions;
drop policy if exists dashboard_anon_select_trades_live on trades_live;
drop policy if exists dashboard_anon_select_trades_paper on trades_paper;
drop policy if exists dashboard_anon_select_strategy_logs on strategy_logs;
drop policy if exists dashboard_anon_select_aggregator_logs on aggregator_logs;
```

admin read policies を追加する。

```sql
create policy dashboard_admin_select_system_status
  on system_status
  for select
  to authenticated
  using (exists (select 1 from dashboard_admins where user_id = auth.uid()));

create policy dashboard_admin_select_positions
  on positions
  for select
  to authenticated
  using (exists (select 1 from dashboard_admins where user_id = auth.uid()));

create policy dashboard_admin_select_trades_live
  on trades_live
  for select
  to authenticated
  using (exists (select 1 from dashboard_admins where user_id = auth.uid()));

create policy dashboard_admin_select_trades_paper
  on trades_paper
  for select
  to authenticated
  using (exists (select 1 from dashboard_admins where user_id = auth.uid()));

create policy dashboard_admin_select_strategy_logs
  on strategy_logs
  for select
  to authenticated
  using (exists (select 1 from dashboard_admins where user_id = auth.uid()));

create policy dashboard_admin_select_aggregator_logs
  on aggregator_logs
  for select
  to authenticated
  using (exists (select 1 from dashboard_admins where user_id = auth.uid()));
```

`system_status` update policy を追加する。

```sql
create policy dashboard_admin_update_system_status
  on system_status
  for update
  to authenticated
  using (exists (select 1 from dashboard_admins where user_id = auth.uid()))
  with check (exists (select 1 from dashboard_admins where user_id = auth.uid()));
```

## 5. App Changes

実装時の変更範囲:

- `dashboard/src/lib/supabase/server.ts`
  - cookie based `createServerClient` を追加する。
  - user-triggered path では service-role client を使わない。
- `dashboard/src/lib/supabase/client.ts`
  - browser client は Supabase Auth session を保持する。
- `dashboard/src/app/login/page.tsx`
  - OAuth sign-in UI を追加する。
- `dashboard/src/app/auth/callback/route.ts`
  - OAuth callback を処理する。
- `dashboard/src/middleware.ts` または route-level guard
  - 未ログイン user を `/login` へ redirect する。
- `dashboard/src/app/system/actions.ts`
  - `getServiceClient()` ではなく authenticated server client で `system_status` を更新する。

## 6. Verification

移行後に確認する。

- 未ログインで production URL にアクセスすると `/login` に redirect される。
- admin OAuth login 後に `/`, `/positions`, `/trades`, `/signals`, `/system` が表示できる。
- Realtime indicator が接続状態になる。
- `/system` で kill switch を `false` に変更し、すぐ `true` に戻せる。
- `/system` で `trade_mode` を `paper` に維持できる。
- anon key だけでは対象 table を SELECT できない。
- admin ではない authenticated user では対象 table を SELECT / UPDATE できない。
- `SUPABASE_SECRET_KEY` 実値が client bundle に含まれない。

kill switch 操作を試す場合は必ず `is_trading_allowed=true` / `trade_mode=paper` に戻す。

### Machine Verification For Coding Agents

本番 Dashboard はログイン必須にするため、コーディングエージェントや CI は Dashboard UI への直接アクセスを前提にしない。
機械検証は次の経路で行う。

- Supabase schema / table 疎通: `op run --env-file infra/env.production -- uv run python scripts/health-check.py --check supabase --timeout 30`
- Pub/Sub 疎通: `scripts/gcp-pubsub-admin.py --check` または runbook `docs/runbook/adr-0001-gcp-pubsub.md` の check-only 手順
- Vercel deploy 状態: GitHub checks、Vercel deployment status、または `curl -I` で `/login` redirect / protected response を確認
- 緊急の `system_status` 確認・復旧: Supabase SQL editor または service-side script を service role で実行する

エージェントに一時的な UI 確認が必要な場合だけ、人間が Preview URL、テスト user、または protection bypass を短時間渡す。
通常の運用確認・障害調査では Dashboard login を必須手順にしない。

## 7. Rollback

Dashboard が使えなくなった場合:

1. Vercel Deployment Protection を維持する。
2. Vercel Deployments から直前の成功 deployment に rollback する。
3. Supabase SQL editor で `system_status` を確認する。
4. 必要なら SQL editor または service-side script で `is_trading_allowed=true` / `trade_mode=paper` に戻す。
5. 一般公開状態で anon read policies を復活させない。

## 8. Checklist Mapping

`docs/adr/0001-implementation-checklist.md` の Live Readiness Gate / Dashboard security hardening に対応する。
