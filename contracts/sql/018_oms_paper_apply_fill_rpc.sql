-- Atomically persist one OMS Paper fill and its resulting position mutation.
--
-- The application supplies the actual simulated fill and metadata for a new
-- BUY. Quantity/average-entry transitions are derived from the authoritative
-- positions row inside this transaction so concurrent workers cannot lose an
-- update. order_id is the primary idempotency key for every order, including
-- closeout/monitor orders whose unified_signal_id is NULL.

alter table public.trades_paper
    add column if not exists order_id uuid;

create unique index if not exists trades_paper_order_id_key
    on public.trades_paper (order_id)
    where order_id is not null;

-- Drop the pre-generation-check signature if this migration was exercised
-- locally while under development. Fresh environments simply emit a NOTICE.
drop function if exists public.oms_paper_apply_fill(
    uuid, uuid, text, text, integer, numeric, text, uuid, timestamptz,
    text, numeric, numeric, integer, date, numeric
);

create or replace function public.oms_paper_apply_fill(
    p_order_id uuid,
    p_trade_id uuid,
    p_symbol text,
    p_side text,
    p_filled_quantity integer,
    p_fill_price numeric,
    p_signal_source text,
    p_unified_signal_id uuid,
    p_executed_at timestamptz,
    p_expected_position_opened_at timestamptz,
    p_new_holding_type text,
    p_new_target_price numeric,
    p_new_stop_loss_price numeric,
    p_new_max_hold_days integer,
    p_new_scheduled_exit_date date,
    p_new_trailing_stop_pct numeric
)
returns table (
    outcome text,
    reason text,
    committed_trade_id uuid,
    position_action text,
    resulting_position jsonb
)
language plpgsql
security invoker
set search_path = ''
as $$
declare
    existing_trade public.trades_paper%rowtype;
    existing_position public.positions%rowtype;
    result_position public.positions%rowtype;
    position_exists boolean := false;
    next_quantity integer;
    next_entry_price numeric;
begin
    if p_order_id is null or p_trade_id is null then
        raise exception 'p_order_id and p_trade_id are required';
    end if;
    if p_symbol is null or pg_catalog.btrim(p_symbol) = '' then
        raise exception 'p_symbol is required';
    end if;
    if p_side not in ('BUY', 'SELL') then
        raise exception 'invalid p_side: %', p_side;
    end if;
    if p_signal_source not in ('RULE', 'AI', 'CONSENSUS') then
        raise exception 'invalid p_signal_source: %', p_signal_source;
    end if;
    if p_filled_quantity is null or p_filled_quantity <= 0 then
        raise exception 'p_filled_quantity must be positive';
    end if;
    if p_fill_price is null or p_fill_price <= 0 then
        raise exception 'p_fill_price must be positive';
    end if;
    if p_executed_at is null then
        raise exception 'p_executed_at is required';
    end if;
    if p_new_max_hold_days is not null and p_new_max_hold_days < 1 then
        raise exception 'p_new_max_hold_days must be positive';
    end if;

    -- A row lock cannot serialize two first BUYs when no position exists yet.
    -- The transaction-scoped advisory lock covers that gap and is released on
    -- commit/rollback. Every fill path uses the same symbol-derived key.
    perform pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended('oms-paper:' || p_symbol, 0)
    );

    select *
    into existing_trade
    from public.trades_paper
    where trades_paper.order_id = p_order_id;

    if found then
        -- Gateway may rebuild the same deterministic order_id after an
        -- at-least-once redelivery. created_at, the observed book, fill price,
        -- quantity, and attempt trade_id can therefore differ legitimately.
        -- Compare only stable routed-order identity fields here.
        if existing_trade.symbol <> p_symbol
            or existing_trade.side <> p_side
            or existing_trade.signal_source <> p_signal_source
            or existing_trade.unified_signal_id is distinct from p_unified_signal_id then
            raise exception 'order_id payload mismatch: %', p_order_id;
        end if;
        return query
        select
            'duplicate'::text,
            'order_id'::text,
            existing_trade.trade_id,
            'unchanged'::text,
            (
                select pg_catalog.to_jsonb(position_row)
                from public.positions as position_row
                where position_row.symbol = p_symbol
                  and position_row.trade_type = 'paper'
            );
        return;
    end if;

    if p_unified_signal_id is not null then
        select *
        into existing_trade
        from public.trades_paper
        where trades_paper.unified_signal_id = p_unified_signal_id;

        if found then
            if existing_trade.symbol <> p_symbol
                or existing_trade.side <> p_side
                or existing_trade.signal_source <> p_signal_source
                or (
                    existing_trade.order_id is not null
                    and existing_trade.order_id <> p_order_id
                ) then
                raise exception 'unified_signal_id payload mismatch: %', p_unified_signal_id;
            end if;
            return query
            select
                'duplicate'::text,
                'unified_signal_id'::text,
                existing_trade.trade_id,
                'unchanged'::text,
                (
                    select pg_catalog.to_jsonb(position_row)
                    from public.positions as position_row
                    where position_row.symbol = p_symbol
                      and position_row.trade_type = 'paper'
                );
            return;
        end if;
    end if;

    select *
    into existing_trade
    from public.trades_paper
    where trades_paper.trade_id = p_trade_id;

    if found then
        if existing_trade.symbol <> p_symbol
            or existing_trade.side <> p_side
            or existing_trade.signal_source <> p_signal_source
            or existing_trade.unified_signal_id is distinct from p_unified_signal_id
            or existing_trade.order_id is distinct from p_order_id
            or existing_trade.quantity <> p_filled_quantity
            or existing_trade.price <> p_fill_price
            or existing_trade.executed_at <> p_executed_at then
            raise exception 'trade_id payload mismatch: %', p_trade_id;
        end if;
        return query
        select
            'duplicate'::text,
            'trade_id'::text,
            existing_trade.trade_id,
            'unchanged'::text,
            (
                select pg_catalog.to_jsonb(position_row)
                from public.positions as position_row
                where position_row.symbol = p_symbol
                  and position_row.trade_type = 'paper'
            );
        return;
    end if;

    select *
    into existing_position
    from public.positions
    where positions.symbol = p_symbol
      and positions.trade_type = 'paper'
    for update;
    position_exists := found;

    if p_side = 'BUY' then
        if not position_exists then
            if p_new_holding_type not in ('day', 'swing') then
                raise exception 'invalid p_new_holding_type for new BUY: %', p_new_holding_type;
            end if;
            insert into public.positions (
                symbol,
                trade_type,
                side,
                quantity,
                entry_price,
                current_price,
                unrealized_pnl,
                holding_type,
                target_price,
                stop_loss_price,
                max_hold_days,
                scheduled_exit_date,
                trailing_stop_pct,
                opened_at
            )
            values (
                p_symbol,
                'paper',
                'LONG',
                p_filled_quantity,
                p_fill_price,
                p_fill_price,
                0,
                p_new_holding_type,
                p_new_target_price,
                p_new_stop_loss_price,
                p_new_max_hold_days,
                p_new_scheduled_exit_date,
                p_new_trailing_stop_pct,
                p_executed_at
            )
            returning * into result_position;
            position_action := 'inserted';
        else
            next_quantity := existing_position.quantity + p_filled_quantity;
            -- Keep the database transition identical to position_updater.py:
            -- Japanese equity average entry prices are rounded to one yen,
            -- half away from zero (Decimal ROUND_HALF_UP for positive prices).
            next_entry_price := pg_catalog.round(
                (
                    existing_position.entry_price * existing_position.quantity
                    + p_fill_price * p_filled_quantity
                ) / next_quantity,
                0
            );
            update public.positions
            set quantity = next_quantity,
                entry_price = next_entry_price
            where positions.symbol = p_symbol
              and positions.trade_type = 'paper'
            returning * into result_position;
            position_action := 'updated';
        end if;
    else
        if not position_exists then
            return query
            select
                'rejected'::text,
                'no_position_for_sell'::text,
                null::uuid,
                'unchanged'::text,
                null::jsonb;
            return;
        end if;
        if p_expected_position_opened_at is not null
            and existing_position.opened_at <> p_expected_position_opened_at then
            return query
            select
                'rejected'::text,
                'position_generation_mismatch'::text,
                null::uuid,
                'unchanged'::text,
                pg_catalog.to_jsonb(existing_position);
            return;
        end if;
        if p_filled_quantity > existing_position.quantity then
            return query
            select
                'rejected'::text,
                'oversell'::text,
                null::uuid,
                'unchanged'::text,
                pg_catalog.to_jsonb(existing_position);
            return;
        end if;
        if p_filled_quantity = existing_position.quantity then
            delete from public.positions
            where positions.symbol = p_symbol
              and positions.trade_type = 'paper';
            result_position := null;
            position_action := 'deleted';
        else
            update public.positions
            set quantity = existing_position.quantity - p_filled_quantity
            where positions.symbol = p_symbol
              and positions.trade_type = 'paper'
            returning * into result_position;
            position_action := 'updated';
        end if;
    end if;

    insert into public.trades_paper (
        trade_id,
        order_id,
        symbol,
        side,
        quantity,
        price,
        signal_source,
        unified_signal_id,
        executed_at
    )
    values (
        p_trade_id,
        p_order_id,
        p_symbol,
        p_side,
        p_filled_quantity,
        p_fill_price,
        p_signal_source,
        p_unified_signal_id,
        p_executed_at
    );

    return query
    select
        'applied'::text,
        null::text,
        p_trade_id,
        position_action,
        case
            when position_action = 'deleted' then null::jsonb
            else pg_catalog.to_jsonb(result_position)
        end;
end;
$$;

revoke all on function public.oms_paper_apply_fill(
    uuid, uuid, text, text, integer, numeric, text, uuid, timestamptz, timestamptz,
    text, numeric, numeric, integer, date, numeric
) from public;
revoke all on function public.oms_paper_apply_fill(
    uuid, uuid, text, text, integer, numeric, text, uuid, timestamptz, timestamptz,
    text, numeric, numeric, integer, date, numeric
) from anon;
revoke all on function public.oms_paper_apply_fill(
    uuid, uuid, text, text, integer, numeric, text, uuid, timestamptz, timestamptz,
    text, numeric, numeric, integer, date, numeric
) from authenticated;
grant execute on function public.oms_paper_apply_fill(
    uuid, uuid, text, text, integer, numeric, text, uuid, timestamptz, timestamptz,
    text, numeric, numeric, integer, date, numeric
) to service_role;

-- Trailing-stop updates do not create a trade, but they participate in the
-- same per-symbol lock and verify the position generation. This prevents a
-- stale monitor decision for position A from mutating a later position B.
create or replace function public.oms_paper_update_stop_loss(
    p_symbol text,
    p_expected_position_opened_at timestamptz,
    p_stop_loss_price numeric
)
returns table (
    outcome text,
    reason text,
    resulting_position jsonb
)
language plpgsql
security invoker
set search_path = ''
as $$
declare
    existing_position public.positions%rowtype;
    result_position public.positions%rowtype;
begin
    if p_symbol is null or pg_catalog.btrim(p_symbol) = '' then
        raise exception 'p_symbol is required';
    end if;
    if p_expected_position_opened_at is null then
        raise exception 'p_expected_position_opened_at is required';
    end if;
    if p_stop_loss_price is null or p_stop_loss_price <= 0 then
        raise exception 'p_stop_loss_price must be positive';
    end if;

    perform pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended('oms-paper:' || p_symbol, 0)
    );

    select *
    into existing_position
    from public.positions
    where positions.symbol = p_symbol
      and positions.trade_type = 'paper'
    for update;

    if not found then
        return query
        select
            'rejected'::text,
            'no_position_for_update'::text,
            null::jsonb;
        return;
    end if;

    if existing_position.opened_at <> p_expected_position_opened_at then
        return query
        select
            'rejected'::text,
            'position_generation_mismatch'::text,
            pg_catalog.to_jsonb(existing_position);
        return;
    end if;

    -- A trailing stop may only move in the protective direction. The symbol
    -- lock makes this check-and-update atomic even when monitors overlap.
    if existing_position.stop_loss_price is not null
       and p_stop_loss_price <= existing_position.stop_loss_price then
        return query
        select
            'rejected'::text,
            'stop_not_raised'::text,
            pg_catalog.to_jsonb(existing_position);
        return;
    end if;

    update public.positions
    set stop_loss_price = p_stop_loss_price
    where positions.symbol = p_symbol
      and positions.trade_type = 'paper'
    returning * into result_position;

    return query
    select
        'applied'::text,
        null::text,
        pg_catalog.to_jsonb(result_position);
end;
$$;

revoke all on function public.oms_paper_update_stop_loss(
    text, timestamptz, numeric
) from public;
revoke all on function public.oms_paper_update_stop_loss(
    text, timestamptz, numeric
) from anon;
revoke all on function public.oms_paper_update_stop_loss(
    text, timestamptz, numeric
) from authenticated;
grant execute on function public.oms_paper_update_stop_loss(
    text, timestamptz, numeric
) to service_role;
