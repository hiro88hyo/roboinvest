-- Durable, fail-closed delivery journal for the isolated event-paper path.
--
-- Pub/Sub acknowledgement and a PostgreSQL write cannot be committed
-- atomically.  Aggregator and Gateway therefore record one immutable business
-- payload, durably mark an external publication attempt, and only then mark
-- its successful Pub/Sub response confirmed.  Any uncheckpointed attempt is
-- deliberately treated as ambiguous rather than automatically re-published.

create table if not exists public.event_paper_stage_dispatches (
    stage text not null check (stage in ('aggregator', 'gateway')),
    input_signal_id uuid not null,
    input_payload jsonb not null check (pg_catalog.jsonb_typeof(input_payload) = 'object'),
    input_payload_sha256 text not null check (input_payload_sha256 ~ '^[0-9a-f]{64}$'),
    output_payload jsonb not null check (pg_catalog.jsonb_typeof(output_payload) = 'object'),
    output_payload_sha256 text not null check (output_payload_sha256 ~ '^[0-9a-f]{64}$'),
    destination_topic text not null check (pg_catalog.btrim(destination_topic) <> ''),
    status text not null default 'prepared'
        check (status in ('prepared', 'attempting', 'confirmed', 'ambiguous')),
    attempt_id text,
    attempted_at timestamptz,
    pubsub_message_id text,
    confirmed_at timestamptz,
    last_error text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    primary key (stage, input_signal_id),
    check (
        (status = 'prepared'
            and attempt_id is null
            and attempted_at is null
            and pubsub_message_id is null
            and confirmed_at is null)
        or
        (status in ('attempting', 'ambiguous')
            and attempt_id is not null
            and attempted_at is not null
            and pubsub_message_id is null
            and confirmed_at is null)
        or
        (status = 'confirmed'
            and attempt_id is not null
            and attempted_at is not null
            and pubsub_message_id is not null
            and confirmed_at is not null)
    )
);

create index if not exists event_paper_stage_dispatches_status_updated_at_idx
    on public.event_paper_stage_dispatches (status, updated_at);

alter table public.event_paper_stage_dispatches enable row level security;
revoke all on public.event_paper_stage_dispatches from anon;
revoke all on public.event_paper_stage_dispatches from authenticated;
grant select, insert, update, delete on public.event_paper_stage_dispatches to service_role;

-- One RPC covers a journal's prepare/read/begin/confirm/ambiguous transitions.
-- The output payload and both hashes are immutable once prepared.  This makes
-- a redelivery with changed consensus or sizing inputs a hard failure instead
-- of silently publishing a different business command under the same ID.
create or replace function public.event_paper_stage_dispatch(
    p_action text,
    p_stage text,
    p_input_signal_id uuid,
    p_input_payload jsonb default null,
    p_input_payload_sha256 text default null,
    p_output_payload jsonb default null,
    p_output_payload_sha256 text default null,
    p_destination_topic text default null,
    p_attempt_id text default null,
    p_pubsub_message_id text default null,
    p_occurred_at timestamptz default null,
    p_error text default null
)
returns table (
    outcome text,
    stage text,
    input_signal_id uuid,
    input_payload jsonb,
    input_payload_sha256 text,
    output_payload jsonb,
    output_payload_sha256 text,
    destination_topic text,
    status text,
    attempt_id text,
    attempted_at timestamptz,
    pubsub_message_id text,
    confirmed_at timestamptz,
    last_error text
)
language plpgsql
security invoker
set search_path = ''
as $$
declare
    v_row public.event_paper_stage_dispatches%rowtype;
    v_outcome text;
begin
    if p_action is null or p_action not in ('prepare', 'read', 'begin', 'confirm', 'ambiguous') then
        raise exception 'invalid p_action: %', p_action;
    end if;
    if p_stage is null or p_stage not in ('aggregator', 'gateway') then
        raise exception 'invalid p_stage: %', p_stage;
    end if;
    if p_input_signal_id is null then
        raise exception 'p_input_signal_id is required';
    end if;

    if p_action = 'prepare' then
        if p_input_payload is null
            or p_output_payload is null
            or p_input_payload_sha256 is null
            or p_output_payload_sha256 is null
            or p_destination_topic is null then
            raise exception 'prepare requires immutable payloads, hashes, and destination topic';
        end if;
        if pg_catalog.jsonb_typeof(p_input_payload) <> 'object'
            or pg_catalog.jsonb_typeof(p_output_payload) <> 'object' then
            raise exception 'prepare payloads must be JSON objects';
        end if;
        if p_input_payload_sha256 !~ '^[0-9a-f]{64}$'
            or p_output_payload_sha256 !~ '^[0-9a-f]{64}$' then
            raise exception 'prepare payload hashes must be lowercase SHA-256 hex';
        end if;
        if pg_catalog.btrim(p_destination_topic) = '' then
            raise exception 'p_destination_topic is required';
        end if;

        -- This journal is not a generic delivery mechanism.  It is only for
        -- the frozen PAPER_ONLY opening transport stress profile.
        if (p_input_payload ->> 'routing_intent') is distinct from 'PAPER_ONLY'
            or (p_input_payload ->> 'strategy_key') is distinct from
                'event_cluster_earnings_dividend_value_guard_fixed20_stop_v1_research__opening_transport_stress_v1'
            or (p_output_payload ->> 'routing_intent') is distinct from 'PAPER_ONLY'
            or (p_output_payload ->> 'strategy_key') is distinct from
                'event_cluster_earnings_dividend_value_guard_fixed20_stop_v1_research__opening_transport_stress_v1' then
            raise exception 'event-paper dispatch journal only accepts the frozen PAPER_ONLY strategy';
        end if;
        if (p_stage = 'aggregator' and p_destination_topic <> 'trade-signals')
            or (p_stage = 'gateway' and p_destination_topic <> 'paper-orders') then
            raise exception 'event-paper dispatch destination does not match stage';
        end if;
        if p_stage = 'gateway'
            and (p_output_payload ->> 'trade_mode') is distinct from 'paper' then
            raise exception 'event-paper gateway output must be a paper order';
        end if;

        insert into public.event_paper_stage_dispatches (
            stage,
            input_signal_id,
            input_payload,
            input_payload_sha256,
            output_payload,
            output_payload_sha256,
            destination_topic
        )
        values (
            p_stage,
            p_input_signal_id,
            p_input_payload,
            p_input_payload_sha256,
            p_output_payload,
            p_output_payload_sha256,
            p_destination_topic
        )
        on conflict on constraint event_paper_stage_dispatches_pkey do nothing;

        select *
        into v_row
        from public.event_paper_stage_dispatches as dispatches
        where dispatches.stage = p_stage
          and dispatches.input_signal_id = p_input_signal_id
        for update;

        if v_row.input_payload is distinct from p_input_payload
            or v_row.input_payload_sha256 is distinct from p_input_payload_sha256
            or v_row.output_payload is distinct from p_output_payload
            or v_row.output_payload_sha256 is distinct from p_output_payload_sha256
            or v_row.destination_topic is distinct from p_destination_topic then
            v_outcome := 'payload_mismatch';
        elsif v_row.status = 'confirmed' then
            v_outcome := 'confirmed';
        elsif v_row.status in ('attempting', 'ambiguous') then
            v_outcome := 'ambiguous';
        else
            v_outcome := 'prepared';
        end if;
    else
        select *
        into v_row
        from public.event_paper_stage_dispatches as dispatches
        where dispatches.stage = p_stage
          and dispatches.input_signal_id = p_input_signal_id
        for update;

        if not found then
            if p_action = 'read' then
                return;
            end if;
            raise exception 'event-paper dispatch is not prepared: %, %', p_stage, p_input_signal_id;
        end if;

        if p_action = 'read' then
            if v_row.status = 'confirmed' then
                v_outcome := 'confirmed';
            elsif v_row.status in ('attempting', 'ambiguous') then
                v_outcome := 'ambiguous';
            else
                v_outcome := 'prepared';
            end if;
        elsif p_action = 'begin' then
            if p_attempt_id is null or pg_catalog.btrim(p_attempt_id) = '' or p_occurred_at is null then
                raise exception 'begin requires p_attempt_id and p_occurred_at';
            end if;
            if v_row.status = 'prepared' then
                update public.event_paper_stage_dispatches
                set status = 'attempting',
                    attempt_id = p_attempt_id,
                    attempted_at = p_occurred_at,
                    last_error = null,
                    updated_at = now()
                where event_paper_stage_dispatches.stage = p_stage
                  and event_paper_stage_dispatches.input_signal_id = p_input_signal_id
                returning * into v_row;
                v_outcome := 'attempt_started';
            -- The HTTP response can be lost after this transaction commits.
            -- Retrying the same client attempt is still before the external
            -- publish boundary, so it must resume normally rather than turn a
            -- never-published command into an ambiguous terminal state.
            elsif v_row.status = 'attempting' and v_row.attempt_id = p_attempt_id then
                v_outcome := 'attempt_started';
            elsif v_row.status = 'confirmed' then
                v_outcome := 'confirmed';
            else
                v_outcome := 'ambiguous';
            end if;
        elsif p_action = 'confirm' then
            if p_attempt_id is null
                or pg_catalog.btrim(p_attempt_id) = ''
                or p_pubsub_message_id is null
                or pg_catalog.btrim(p_pubsub_message_id) = ''
                or p_occurred_at is null then
                raise exception 'confirm requires attempt, Pub/Sub message, and timestamp';
            end if;
            if v_row.status = 'attempting' and v_row.attempt_id = p_attempt_id then
                update public.event_paper_stage_dispatches
                set status = 'confirmed',
                    pubsub_message_id = p_pubsub_message_id,
                    confirmed_at = p_occurred_at,
                    last_error = null,
                    updated_at = now()
                where event_paper_stage_dispatches.stage = p_stage
                  and event_paper_stage_dispatches.input_signal_id = p_input_signal_id
                returning * into v_row;
                v_outcome := 'confirmed';
            elsif v_row.status = 'confirmed'
                and v_row.attempt_id = p_attempt_id
                and v_row.pubsub_message_id = p_pubsub_message_id then
                v_outcome := 'confirmed';
            elsif v_row.status in ('attempting', 'ambiguous') then
                v_outcome := 'ambiguous';
            else
                v_outcome := 'attempt_mismatch';
            end if;
        else
            -- ``ambiguous`` is terminal for automatic workers.  A later
            -- receipt/reconciliation process may inspect it, but no worker
            -- may publish the same business payload again.
            if p_attempt_id is null or pg_catalog.btrim(p_attempt_id) = '' or p_occurred_at is null then
                raise exception 'ambiguous requires p_attempt_id and p_occurred_at';
            end if;
            if v_row.status = 'attempting' and v_row.attempt_id = p_attempt_id then
                update public.event_paper_stage_dispatches
                set status = 'ambiguous',
                    last_error = nullif(pg_catalog.btrim(coalesce(p_error, '')), ''),
                    updated_at = now()
                where event_paper_stage_dispatches.stage = p_stage
                  and event_paper_stage_dispatches.input_signal_id = p_input_signal_id
                returning * into v_row;
                v_outcome := 'ambiguous';
            elsif v_row.status = 'ambiguous' and v_row.attempt_id = p_attempt_id then
                v_outcome := 'ambiguous';
            elsif v_row.status = 'confirmed' then
                v_outcome := 'confirmed';
            else
                v_outcome := 'attempt_mismatch';
            end if;
        end if;
    end if;

    return query
    select
        v_outcome,
        v_row.stage,
        v_row.input_signal_id,
        v_row.input_payload,
        v_row.input_payload_sha256,
        v_row.output_payload,
        v_row.output_payload_sha256,
        v_row.destination_topic,
        v_row.status,
        v_row.attempt_id,
        v_row.attempted_at,
        v_row.pubsub_message_id,
        v_row.confirmed_at,
        v_row.last_error;
end;
$$;

revoke all on function public.event_paper_stage_dispatch(
    text, text, uuid, jsonb, text, jsonb, text, text, text, text, timestamptz, text
) from public;
revoke all on function public.event_paper_stage_dispatch(
    text, text, uuid, jsonb, text, jsonb, text, text, text, text, timestamptz, text
) from anon;
revoke all on function public.event_paper_stage_dispatch(
    text, text, uuid, jsonb, text, jsonb, text, text, text, text, timestamptz, text
) from authenticated;
grant execute on function public.event_paper_stage_dispatch(
    text, text, uuid, jsonb, text, jsonb, text, text, text, text, timestamptz, text
) to service_role;
