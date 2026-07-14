-- Extend the event-paper dispatch journal's exact strategy allowlist without
-- weakening its PAPER_ONLY or destination guards.  The replacement is
-- deliberately fail-closed if the installed function differs from migration
-- 021's reviewed definition.
do $$
declare
    v_function_def text;
    v_updated_def text;
    v_old_guard constant text := $guard$
        if (p_input_payload ->> 'routing_intent') is distinct from 'PAPER_ONLY'
            or (p_input_payload ->> 'strategy_key') is distinct from
                'event_cluster_earnings_dividend_value_guard_fixed20_stop_v1_research__opening_transport_stress_v1'
            or (p_output_payload ->> 'routing_intent') is distinct from 'PAPER_ONLY'
            or (p_output_payload ->> 'strategy_key') is distinct from
                'event_cluster_earnings_dividend_value_guard_fixed20_stop_v1_research__opening_transport_stress_v1' then
    $guard$;
    v_new_guard constant text := $guard$
        if (p_input_payload ->> 'routing_intent') is distinct from 'PAPER_ONLY'
            or (p_input_payload ->> 'strategy_key') not in (
                'event_cluster_earnings_dividend_value_guard_fixed20_stop_v1_research__opening_transport_stress_v1',
                'event_cluster_earnings_dividend_value_guard_fixed20_stop_v1_research__frozen_opening_close_v1'
            )
            or (p_output_payload ->> 'routing_intent') is distinct from 'PAPER_ONLY'
            or (p_output_payload ->> 'strategy_key') not in (
                'event_cluster_earnings_dividend_value_guard_fixed20_stop_v1_research__opening_transport_stress_v1',
                'event_cluster_earnings_dividend_value_guard_fixed20_stop_v1_research__frozen_opening_close_v1'
            ) then
    $guard$;
begin
    select pg_catalog.pg_get_functiondef(p.oid)
    into strict v_function_def
    from pg_catalog.pg_proc as p
    join pg_catalog.pg_namespace as n on n.oid = p.pronamespace
    where n.nspname = 'public'
      and p.proname = 'event_paper_stage_dispatch';

    v_updated_def := pg_catalog.replace(v_function_def, v_old_guard, v_new_guard);
    if v_updated_def = v_function_def then
        raise exception 'event_paper_stage_dispatch guard differs from the reviewed source';
    end if;

    execute v_updated_def;
end;
$$;
