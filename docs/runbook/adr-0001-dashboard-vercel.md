# ADR-0001 Dashboard Vercel Runbook

作成日: 2026-05-16

ADR-0001 の Dashboard を Vercel に deploy するための準備・確認手順。
Dashboard の root directory は `dashboard/` とし、Supabase service-role key は server-side env のみに置く。

## 1. Project Settings

Vercel project:

```text
Project name: trade-ai-dashboard
Framework preset: Next.js
Root directory: dashboard
Install command: npm ci
Build command: npm run build
Output directory: .next
Node.js: package.json の volta 指定に合わせる
```

GitHub repository と連携し、preview / production の両方で `dashboard/` を root directory にする。

## 2. Environment Variables

Vercel の Environment Variables に次を設定する。
Production / Preview の両方に同じ値を入れる。

Vercel の 1Password Integration を使っていない場合、Vercel には `op://...` 参照ではなく `op read` で解決した実値を入力する。
`NEXT_PUBLIC_SUPABASE_URL` は `https://<project-ref>.supabase.co` 形式になる。

| Vercel env | Source | Scope |
| --- | --- | --- |
| `NEXT_PUBLIC_SUPABASE_URL` | `op://roboinvest/production/SUPABASE_URL` を解決した実値 | Browser + Server |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | `op://roboinvest/production/SUPABASE_ANON_KEY` を解決した実値 | Browser + Server |
| `SUPABASE_SECRET_KEY` | `op://roboinvest/production/SUPABASE_SECRET_KEY` を解決した実値 | Server only |
| `NEXT_PUBLIC_APP_TIMEZONE` | `Asia/Tokyo` | Browser + Server |

`SUPABASE_SECRET_KEY` は絶対に `NEXT_PUBLIC_` prefix にしない。
Dashboard Auth/RLS 適用後、Dashboard の Server Components / Server Actions は cookie session 付きの authenticated user client を使う。
`SUPABASE_SECRET_KEY` は保守用 CLI / health check / migration 用に限定し、user-triggered path では使わない。
Client Components は anon key と authenticated session で Realtime を購読する。

## 3. Local Production Build Check

Vercel 設定前に local で本番相当 env を注入して確認する。

```bash
cd dashboard

NEXT_PUBLIC_SUPABASE_URL="$(op read "op://roboinvest/production/SUPABASE_URL")" \
NEXT_PUBLIC_SUPABASE_ANON_KEY="$(op read "op://roboinvest/production/SUPABASE_ANON_KEY")" \
SUPABASE_SECRET_KEY="$(op read "op://roboinvest/production/SUPABASE_SECRET_KEY")" \
NEXT_PUBLIC_APP_TIMEZONE=Asia/Tokyo \
  npm run lint

NEXT_PUBLIC_SUPABASE_URL="$(op read "op://roboinvest/production/SUPABASE_URL")" \
NEXT_PUBLIC_SUPABASE_ANON_KEY="$(op read "op://roboinvest/production/SUPABASE_ANON_KEY")" \
SUPABASE_SECRET_KEY="$(op read "op://roboinvest/production/SUPABASE_SECRET_KEY")" \
NEXT_PUBLIC_APP_TIMEZONE=Asia/Tokyo \
  npm test

NEXT_PUBLIC_SUPABASE_URL="$(op read "op://roboinvest/production/SUPABASE_URL")" \
NEXT_PUBLIC_SUPABASE_ANON_KEY="$(op read "op://roboinvest/production/SUPABASE_ANON_KEY")" \
SUPABASE_SECRET_KEY="$(op read "op://roboinvest/production/SUPABASE_SECRET_KEY")" \
NEXT_PUBLIC_APP_TIMEZONE=Asia/Tokyo \
  npm run build
```

2026-05-16 local check:

- `npm run lint`: OK
- `npm test`: 47 passed
- `npm run typecheck`: OK
- `npm run build`: OK

## 4. Service Key Leak Check

local build 後、service-role key 実値が build artifact に混入していないことを確認する。

```bash
cd dashboard
SUPABASE_SECRET_KEY="$(op read "op://roboinvest/production/SUPABASE_SECRET_KEY")"

if rg -F "$SUPABASE_SECRET_KEY" .next >/dev/null; then
  echo "SECRET_LEAK:yes"
  exit 1
else
  echo "SECRET_LEAK:no"
fi
```

2026-05-16 local check:

```text
SECRET_LEAK:no
```

注意: `.next/server/...` に `SUPABASE_SECRET_KEY` という env 名が含まれることはある。
問題にするのは secret 実値の混入。

## 5. Supabase Requirements

Dashboard Auth/RLS 適用前の browser Realtime には次が必要だった。
Auth/RLS 適用後は `contracts/sql/012_dashboard_auth_rls.sql` により anon read を削除し、authenticated dashboard admin の RLS で Realtime を確認する。

- `contracts/sql/011_dashboard_anon_read_policies.sql` が Supabase Cloud に適用済み。
- `supabase_realtime` publication に `system_status` / `positions` / `trades_live` / `trades_paper` / `strategy_logs` / `aggregator_logs` が含まれる。
- anon key で `system_status` UPDATE event を受信できる。

2026-05-16 check:

```text
realtime:subscribed
realtime:event:UPDATE:id=1
```

## 6. Preview / Production Verification

Vercel Preview URL と Production URL で次を確認する。

- `/` が 200。
- `/positions?type=paper` が 200、7203 paper position が見える。
- `/trades?type=paper` が 200、7203 paper trade が見える。
- `/signals` が 200、7203 signal log が見える。
- `/system` が 200、`is_trading_allowed=true` / `trade_mode=paper` が見える。
- Realtime indicator が接続状態になる。
- `/system` の kill switch / trade mode 操作が Cloud Supabase に反映される。

kill switch 操作を試す場合は必ず `true/paper` に戻す。

2026-05-16 Preview check:

- Preview URL: `https://roboinvest-git-adr-0001-production-compose-hiro88hyos-projects.vercel.app`
- `/api/env-check`: env 実値 materialize 後に `supabaseHost=cqexdwufmanuqccerdvo.supabase.co` を確認（一時 endpoint は確認後に削除）。
- `/`, `/positions?type=paper`, `/trades?type=paper`, `/signals`, `/system`: すべて 200。
- `/positions?type=paper`: 7203 paper position 表示 OK。
- `/trades?type=paper`: 7203 BUY paper trade 表示 OK。
- `/system`: `is_trading_allowed=true` / `trade_mode=paper` 表示 OK。

## 7. Rollback

Dashboard deploy に問題がある場合:

1. Vercel の Deployments から直前の成功 deployment に rollback する。
2. Cloud Supabase 側の `system_status` を確認する。
3. `is_trading_allowed` が意図せず変わっていないことを確認する。
4. service-role key を誤って public env に置いた疑いがある場合は、Supabase key rotation を検討する。

## 8. Checklist Mapping

`docs/adr/0001-implementation-checklist.md` の Dashboard / Vercel section に対応する。
Vercel project 作成と env 登録はブラウザ上の手作業として残す。
