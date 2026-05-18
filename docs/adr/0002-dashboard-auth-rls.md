# ADR-0002 Dashboard Auth And RLS

作成日: 2026-05-17

Production Dashboard は本番テスト完了までは現行構成で検証を継続する。
ただし、一般公開 URL + Supabase anon read policy + Server Action service-role 更新の組み合わせは live readiness 前に閉じる。
この ADR は実装前の設計メモであり、コード変更や Supabase policy 変更は含めない。

## 1. Problem

現行 Dashboard は Vercel 公開 URL から到達でき、browser client は `NEXT_PUBLIC_SUPABASE_ANON_KEY` で Supabase Realtime を購読する。
`contracts/sql/011_dashboard_anon_read_policies.sql` は次の table に `to anon using (true)` の SELECT policy を付与している。

- `system_status`
- `positions`
- `trades_live`
- `trades_paper`
- `strategy_logs`
- `aggregator_logs`

そのため、Dashboard が一般公開の場合、取引状態、position、trade、strategy log が anon key 経由で読める。
さらに `/system` は Server Action から `SUPABASE_SECRET_KEY` を使って `system_status` を更新するため、アプリ側のアクセス制限がない状態では kill switch / trade mode の変更面が公開される。

## 2. Decision

Dashboard は OAuth2 login を必須にし、DB 側は RLS で Dashboard user の read / write 権限を強制する。
OAuth2 は入口の本人確認、RLS は Supabase Data API / Realtime / Server Action 経由の最終防衛線として扱う。

初期実装は Supabase Auth を採用する。

- OAuth provider は Google または GitHub から開始する。
- 許可ユーザーは `dashboard_admins` table で管理する。
- RLS 判定には `auth.uid()` と `dashboard_admins` を使う。
- `user_metadata` は認可判定に使わない。
- `service_role` は Dashboard の通常 read / write path から外す。
- `SUPABASE_SECRET_KEY` は保守用または migration / batch 用に限定し、Client Components と user-triggered Server Actions では使わない。

## 3. Access Model

Dashboard user は2段階で扱う。

| Role | 判定 | 権限 |
| --- | --- | --- |
| authenticated | Supabase Auth session が有効 | Dashboard route へ入れる候補。RLS では原則 read 不可 |
| dashboard admin | `dashboard_admins.user_id = auth.uid()` | Dashboard data read と `system_status` 操作が可能 |

将来、read-only operator が必要になった場合は `dashboard_users(role text)` へ拡張する。
初期段階では admin のみで十分とする。

## 4. Database Design

追加する table:

```sql
create table dashboard_admins (
  user_id uuid primary key references auth.users(id) on delete cascade,
  created_at timestamptz not null default now()
);

alter table dashboard_admins enable row level security;
```

`dashboard_admins` 自体は client から列挙させない。
admin 判定は RLS policy 内の `exists` で行う。

read policy の方針:

```sql
create policy dashboard_admin_select_positions
  on positions
  for select
  to authenticated
  using (
    exists (
      select 1
      from dashboard_admins
      where user_id = auth.uid()
    )
  );
```

`system_status` の update policy 方針:

```sql
create policy dashboard_admin_update_system_status
  on system_status
  for update
  to authenticated
  using (
    exists (
      select 1
      from dashboard_admins
      where user_id = auth.uid()
    )
  )
  with check (
    exists (
      select 1
      from dashboard_admins
      where user_id = auth.uid()
    )
  );
```

Postgres RLS では UPDATE に SELECT policy も必要なので、`system_status` には admin SELECT policy も付与する。

## 5. Dashboard App Design

Next.js app は `@supabase/ssr` の cookie session を使う。

- `/login` を追加し、OAuth2 sign-in を開始する。
- `/auth/callback` を追加し、Supabase Auth callback を処理する。
- middleware または route-level guard で未ログイン user を `/login` へ redirect する。
- Server Components は session user を確認してから data fetch する。
- Client Components は browser client の authenticated session で Realtime を購読する。
- `/system` Server Actions は `SUPABASE_SECRET_KEY` ではなく authenticated user client で更新する。

Vercel project 側では恒久対策が入るまで Deployment Protection または Password Protection を有効化する。
これは RLS 実装までの暫定防御であり、RLS の代替にはしない。

## 6. Realtime

Realtime publication は引き続き対象 table を含める。
ただし、event delivery は authenticated user の RLS に従わせる。
anon key での Realtime read 成功を完了条件にしない。

検証は次で行う。

- 未ログイン browser では Dashboard に入れない。
- 未ログイン / anon key では対象 table が読めない。
- 非 admin authenticated user では対象 table が読めない。
- admin authenticated user では Dashboard 表示と Realtime 更新が動く。

## 7. Migration Order

本番テスト完了後、次の順で進める。

1. Vercel の Deployment Protection を有効化する。
2. Supabase Auth provider を設定する。
3. Dashboard auth route / cookie server client / route guard を実装する。
4. `dashboard_admins` table と admin RLS policies を追加する。
5. `/system` Server Actions を authenticated user client 更新へ変更する。
6. anon SELECT policies を削除する。
7. admin user で read / Realtime / system update を検証する。
8. non-admin user と anon で拒否されることを検証する。
9. 問題がなければ Deployment Protection の扱いを再判断する。

## 8. Rollback

auth/RLS 移行で Dashboard が使えなくなった場合は、まず Vercel Deployment Protection を維持したまま直前 deployment に rollback する。
DB 側 policy を戻す必要がある場合も、一般公開状態で anon read を復活させない。
緊急運用は Supabase SQL editor または service-side scripts から `system_status` を確認・更新する。

## 9. Done Criteria

- Dashboard production URL が未ログインで data を返さない。
- `anon` role で対象 table を SELECT できない。
- non-admin authenticated user が対象 table を SELECT / UPDATE できない。
- admin authenticated user が主要 route と Realtime を利用できる。
- `/system` 操作は admin user の権限で RLS を通って更新される。
- `SUPABASE_SECRET_KEY` は Dashboard user-triggered path から外れている。
- runbook に provider 設定、admin 追加、検証、rollback 手順がある。
