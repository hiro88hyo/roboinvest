# ADR-0001 Supabase Cloud Runbook

ADR-0001 の paper production trial 用 Supabase Cloud project に schema / seed / initial data を入れ、health check で確認する手順。

secret 実値は repo に書かない。`SUPABASE_URL` / `SUPABASE_SECRET_KEY` / dashboard 用 anon key は 1Password の `roboinvest` vault に登録し、`op run --env-file infra/env.production -- ...` 経由で使う。

## 1. Preconditions

- Supabase Cloud project を作成済みであること。
- paper production trial は Free plan でも進めてよい。live readiness gate 前に Pro plan / PITR を有効化すること。
- `infra/env.production` は `infra/env.production.tpl` から作成し、secret 実値ではなく `op://...` 参照を残していること。
- 初回 trial は `TRADE_MODE=paper` / `OMS_LIVE_DRY_RUN=true` のまま進めること。

1Password fields:

| item | field |
| --- | --- |
| `production` | `SUPABASE_URL` |
| `production` | `SUPABASE_SECRET_KEY` |
| `production` | `SUPABASE_ANON_KEY` |

Dashboard / Vercel では次の env 名に対応させる。

| Vercel env | 1Password source |
| --- | --- |
| `NEXT_PUBLIC_SUPABASE_URL` | `op://roboinvest/production/SUPABASE_URL` |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | `op://roboinvest/production/SUPABASE_ANON_KEY` |
| `SUPABASE_SECRET_KEY` | `op://roboinvest/production/SUPABASE_SECRET_KEY` |

## 2. Apply Schema

Cloud project の SQL Editor で `contracts/sql/` を番号順に実行する。

現行の適用順:

1. `contracts/sql/001_system_status.sql`
2. `contracts/sql/002_positions.sql`
3. `contracts/sql/003_strategy_logs.sql`
4. `contracts/sql/004_aggregator_logs.sql`
5. `contracts/sql/005_trades_live.sql`
6. `contracts/sql/006_trades_paper.sql`
7. `contracts/sql/007_watchlist.sql`
8. `contracts/sql/008_master_stocks.sql`
9. `contracts/sql/009_daily_ohlcv.sql`
10. `contracts/sql/010_trades_live_order_id.sql`
11. `contracts/sql/010_trades_paper_signal_id_unique.sql`
12. `contracts/sql/011_dashboard_anon_read_policies.sql`
13. `contracts/sql/012_dashboard_auth_rls.sql`
14. `contracts/sql/013_market_regime.sql`
15. `contracts/sql/014_service_role_table_grants.sql`
16. `contracts/sql/015_gateway_kill_switch_rpc.sql`
17. `contracts/sql/016_gateway_risk_reservations.sql`
18. `contracts/sql/017_positions_scheduled_exit_date.sql`

各 SQL は `create table if not exists` / `create index if not exists` 形式を基本にしている。途中で失敗した場合は、失敗箇所を直して同じ順番で再実行する。

適用後、SQL Editor で最低限確認する。

```sql
select table_name
from information_schema.tables
where table_schema = 'public'
  and table_name in (
    'system_status',
    'positions',
    'strategy_logs',
    'aggregator_logs',
    'trades_live',
    'trades_paper',
    'watchlist',
    'master_stocks',
    'daily_ohlcv'
  )
order by table_name;
```

## 3. Seed `system_status`

`infra/supabase/seed.sql` の `system_status` singleton を Cloud project にも投入する。

初回 production trial は paper mode のため、seed 後に次の状態を確認する。

```sql
select
  id,
  is_trading_allowed,
  trade_mode,
  trading_style,
  daily_loss_limit,
  weekly_loss_limit,
  monthly_loss_limit
from system_status
where id = 1;
```

期待値:

- `id = 1`
- `is_trading_allowed = true`
- `trade_mode = 'paper'`
- `trading_style = 'day'`

loss limit は trial 用の仮値として `infra/supabase/seed.sql` の値から開始し、live readiness gate 前に運用値へ見直す。

## 4. Initial Market Data Policy

`master_stocks` / `daily_ohlcv` / `watchlist` は production trial の目的ごとに扱いを分ける。

Paper compose 起動・Pub/Sub 疎通・Dashboard 表示確認:

- `system_status` だけ必須。
- `master_stocks` / `daily_ohlcv` は空でも schema health check は通る。
- `watchlist` が空だと feeder の登録対象がないため、market data flow の確認前に最小 seed を入れる。

最小 `watchlist` seed:

```sql
insert into watchlist (symbol, valid_date, symbol_name, score, selected_reasons)
values ('7203', current_date, 'Toyota Motor', 1.0, '{"source":"manual-production-trial"}'::jsonb)
on conflict (symbol, valid_date) do update
set
  symbol_name = excluded.symbol_name,
  score = excluded.score,
  selected_reasons = excluded.selected_reasons;
```

Universe Scanner / feature generation まで確認する場合:

- J-Quants 有料 plan / refresh token が整ってから `universe-scanner` batch を実行し、`master_stocks` / `daily_ohlcv` / `watchlist` を生成する。
- 手動 CSV import は、J-Quants ingestion の代替ではなく一時検証用途に限定する。
- `daily_ohlcv` は `(symbol, date)` primary key、`watchlist` は `(symbol, valid_date)` primary key を正とする。

## 5. Realtime Publication

Dashboard が realtime 表示で購読する table を Cloud project の Realtime publication に追加する。

初回確認対象:

- `system_status`
- `positions`
- `trades_paper`
- `trades_live`
- `strategy_logs`
- `aggregator_logs`
- `watchlist`

Supabase Dashboard の Database > Publications で `supabase_realtime` に対象 table が含まれることを確認する。

SQL で設定する場合は、Cloud project の状態を見て未追加 table だけ追加する。

```sql
alter publication supabase_realtime add table system_status;
alter publication supabase_realtime add table positions;
alter publication supabase_realtime add table trades_paper;
alter publication supabase_realtime add table trades_live;
alter publication supabase_realtime add table strategy_logs;
alter publication supabase_realtime add table aggregator_logs;
alter publication supabase_realtime add table watchlist;
```

## 6. Health Check

`scripts/health-check.py` の Supabase section は Cloud Supabase に対しても同じ env で動く。

```bash
op run --env-file infra/env.production -- \
  uv run scripts/health-check.py --check supabase --timeout 30
```

期待結果:

- `system_status`
- `positions`
- `strategy_logs`
- `aggregator_logs`
- `trades_live`
- `trades_paper`
- `watchlist`
- `master_stocks`
- `daily_ohlcv`
- `market_regime`

上記 tables が `OK` になること。

`positions.scheduled_exit_date` も `OK` になること。`column positions.scheduled_exit_date
does not exist` が出る場合は `contracts/sql/017_positions_scheduled_exit_date.sql` が
Cloud project に未適用であり、swing fixed-hold の operational gate は実行しない。

`401` / `403` の場合は `SUPABASE_SECRET_KEY` が service role key か、1Password field と `infra/env.production` の参照が一致しているかを確認する。

`404` の場合は schema 未適用または table 名の不一致を疑う。

## 7. RLS Policy

ADR-0001 の paper production trial では、server-side services と Vercel server action に service role key を注入して進める。
browser client には `NEXT_PUBLIC_SUPABASE_ANON_KEY` だけを渡し、service role key は絶対に渡さない。

RLS 本番化は live readiness gate 前の後続タスクに分離する。paper trial では RLS policy の細分化を blocker にしない。

Dashboard の browser Realtime 購読には anon role の SELECT policy と `supabase_realtime` publication 登録が必要。`contracts/sql/011_dashboard_anon_read_policies.sql` 適用後、anon key で `system_status` UPDATE event 受信を確認済み。

live readiness gate 前に少なくとも次を決める。

- Dashboard browser client が anon key で読める table / column。
- kill switch / trade mode 更新を server action のみ許可するか。
- service role key を client-side bundle に絶対に出さない確認手順。

## 8. Pro / PITR Decision

ADR-0001 の段階では、paper production trial を優先する。

- Paper trial: Free plan でも可。schema / seed / Realtime / health check が通ることを重視する。
- Live readiness gate 前: Pro plan と PITR を有効化する。
- 実損益が発生する live 運用: positions / trades / system_status の復旧要件を満たすため、PITR を有効にした状態だけを許可する。
