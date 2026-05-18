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

## 7. Rollback

Dashboard が使えなくなった場合:

1. Vercel Deployment Protection を維持する。
2. Vercel Deployments から直前の成功 deployment に rollback する。
3. Supabase SQL editor で `system_status` を確認する。
4. 必要なら SQL editor または service-side script で `is_trading_allowed=true` / `trade_mode=paper` に戻す。
5. 一般公開状態で anon read policies を復活させない。

## 8. Checklist Mapping

`docs/adr/0001-implementation-checklist.md` の Live Readiness Gate / Dashboard security hardening に対応する。
