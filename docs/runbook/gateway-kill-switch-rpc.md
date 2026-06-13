# Gateway Kill-Switch RPC Runbook

`services/gateway` が `public.gateway_check_kill_switch()` を呼ぶ版を本番反映する手順。
Gateway code は RPC が存在しない DB では注文処理に失敗するため、SQL を先に適用する。

## Preconditions

- market hours / pre-open 中に実施しない。実施する場合はユーザーが明示承認する。
- `make lint-all` と `make test-all` が成功している。
- 対象 commit に `contracts/sql/015_gateway_kill_switch_rpc.sql` が含まれている。
- Supabase project の SQL Editor または DB 接続で SQL を実行できる。

## Apply

1. Supabase SQL Editor で `contracts/sql/015_gateway_kill_switch_rpc.sql` を実行する。
2. 権限が `service_role` 限定になっていることを確認する。

```sql
select
  p.proname,
  has_function_privilege('service_role', p.oid, 'execute') as service_role_execute,
  has_function_privilege('anon', p.oid, 'execute') as anon_execute,
  has_function_privilege('authenticated', p.oid, 'execute') as authenticated_execute
from pg_proc p
join pg_namespace n on n.oid = p.pronamespace
where n.nspname = 'public'
  and p.proname = 'gateway_check_kill_switch';
```

期待値:

- `service_role_execute = true`
- `anon_execute = false`
- `authenticated_execute = false`

3. RPC が `system_status` を読めることを確認する。副作用を残さないため transaction 内で実行して rollback する。

```sql
begin;
select
  id,
  is_trading_allowed,
  trade_mode,
  passed,
  reason,
  disabled
from public.gateway_check_kill_switch();
rollback;
```

期待値:

- 1 row が返る。
- 通常状態では `passed = true`, `reason is null`, `disabled = false`。
- すでに kill switch OFF の場合は `passed = false`, `reason = 'kill_switch_off'`。

4. SQL 適用後に app を deploy する。

```bash
bash scripts/deploy-production.sh --apply --kabu-offline
```

kabu station / Windows proxy 起動済みの寄り前確認では `--kabu-offline` を外す。

## Post-Deploy Check

deploy 後に Supabase と service health を確認する。

```bash
set -a && . infra/.op.service-account.env && set +a
op run --env-file infra/env.production -- \
  uv run python scripts/production-preopen-check.py --timeout 30 --kabu-offline
```

成功条件:

- post-check が `NG 0`。
- `gateway` が起動している。
- Gateway log に `rpc=gateway_check_kill_switch` の 4xx/5xx が出ていない。

ログ確認例:

```bash
op run --env-file infra/env.production -- \
  docker compose --env-file infra/env.production -f infra/docker-compose.prod.yml \
  logs --tail=100 gateway
```

## Rollback

DB だけ先に rollback しない。新 Gateway code は RPC に依存しているため、関数を消すと
注文処理が fail-closed する。

1. 先に Gateway を RPC 依存前の commit/image に戻す。
2. Gateway が旧 code で起動し、注文処理が `read_system_status()` path に戻ったことを確認する。
3. SQL Editor で関数を削除する。

```sql
drop function if exists public.gateway_check_kill_switch();
```

rollback 後も通常の Supabase health check を実行する。

```bash
op run --env-file infra/env.production -- \
  uv run python scripts/health-check.py --check supabase --timeout 30
```
