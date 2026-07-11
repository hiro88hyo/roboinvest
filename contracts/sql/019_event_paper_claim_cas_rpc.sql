-- Atomic compare-and-swap for the event-paper claim journal.
--
-- The expected reasoning is sent in the POST body instead of a PostgREST URL
-- filter. This keeps arbitrarily encoded JSON out of request-target limits and
-- makes concurrent publication-attempt ownership explicit.

create or replace function public.event_paper_cas_strategy_reasoning(
    p_signal_id uuid,
    p_expected_reasoning text,
    p_updated_reasoning text
)
returns table (
    applied boolean,
    reasoning text
)
language plpgsql
security invoker
set search_path = ''
as $$
declare
    v_reasoning text;
begin
    if p_signal_id is null then
        raise exception 'p_signal_id is required';
    end if;
    if p_expected_reasoning is null then
        raise exception 'p_expected_reasoning is required';
    end if;
    if p_updated_reasoning is null then
        raise exception 'p_updated_reasoning is required';
    end if;

    update public.strategy_logs as logs
    set reasoning = p_updated_reasoning
    where logs.signal_id = p_signal_id
      and logs.reasoning is not distinct from p_expected_reasoning
    returning logs.reasoning into v_reasoning;

    if found then
        return query select true, v_reasoning;
        return;
    end if;

    select logs.reasoning
    into v_reasoning
    from public.strategy_logs as logs
    where logs.signal_id = p_signal_id;

    if not found then
        return query select false, null::text;
        return;
    end if;

    return query select false, v_reasoning;
end;
$$;

revoke all on function public.event_paper_cas_strategy_reasoning(
    uuid, text, text
) from public;
revoke all on function public.event_paper_cas_strategy_reasoning(
    uuid, text, text
) from anon;
revoke all on function public.event_paper_cas_strategy_reasoning(
    uuid, text, text
) from authenticated;
grant execute on function public.event_paper_cas_strategy_reasoning(
    uuid, text, text
) to service_role;
