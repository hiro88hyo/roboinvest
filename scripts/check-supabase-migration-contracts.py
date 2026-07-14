#!/usr/bin/env python3
"""Verify deployable Supabase migrations match their contract SQL sources."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# The deployment directory preserves its historical sequence, while
# ``contracts/sql`` is the schema source of truth.  Keep this mapping explicit
# so inserting a second migration with the same contract prefix cannot make a
# source/deployment mismatch invisible in CI.
CONTRACT_MIGRATION_PAIRS = (
    ("001_system_status.sql", "001_system_status.sql"),
    ("002_positions.sql", "002_positions.sql"),
    ("003_strategy_logs.sql", "003_strategy_logs.sql"),
    ("004_aggregator_logs.sql", "004_aggregator_logs.sql"),
    ("005_trades_live.sql", "005_trades_live.sql"),
    ("006_trades_paper.sql", "006_trades_paper.sql"),
    ("007_watchlist.sql", "007_watchlist.sql"),
    ("008_master_stocks.sql", "008_master_stocks.sql"),
    ("009_daily_ohlcv.sql", "009_daily_ohlcv.sql"),
    ("010_trades_live_order_id.sql", "010_trades_live_order_id.sql"),
    ("010_trades_paper_signal_id_unique.sql", "011_trades_paper_signal_id_unique.sql"),
    ("011_dashboard_anon_read_policies.sql", "012_dashboard_anon_read_policies.sql"),
    ("012_dashboard_auth_rls.sql", "013_dashboard_auth_rls.sql"),
    ("013_market_regime.sql", "014_market_regime.sql"),
    ("014_service_role_table_grants.sql", "015_service_role_table_grants.sql"),
    ("015_gateway_kill_switch_rpc.sql", "016_gateway_kill_switch_rpc.sql"),
    ("016_gateway_risk_reservations.sql", "017_gateway_risk_reservations.sql"),
    ("017_positions_scheduled_exit_date.sql", "018_positions_scheduled_exit_date.sql"),
    ("018_oms_paper_apply_fill_rpc.sql", "019_oms_paper_apply_fill_rpc.sql"),
    ("019_event_paper_claim_cas_rpc.sql", "020_event_paper_claim_cas_rpc.sql"),
    ("020_event_paper_stage_dispatch_journal.sql", "021_event_paper_stage_dispatch_journal.sql"),
    ("022_positions_scheduled_exit_time.sql", "023_positions_scheduled_exit_time.sql"),
    (
        "021_oms_paper_position_generation_lineage.sql",
        "022_oms_paper_position_generation_lineage.sql",
    ),
    (
        "023_event_paper_frozen_execution_profile.sql",
        "024_event_paper_frozen_execution_profile.sql",
    ),
)


def validate_contract_migrations(root: Path = REPO_ROOT) -> list[str]:
    """Return every missing or byte-different contract/migration artifact."""

    contracts = root / "contracts" / "sql"
    migrations = root / "infra" / "supabase" / "migrations"
    errors: list[str] = []
    mapped_contracts = [contract_name for contract_name, _ in CONTRACT_MIGRATION_PAIRS]
    mapped_migrations = [migration_name for _, migration_name in CONTRACT_MIGRATION_PAIRS]
    if len(mapped_contracts) != len(set(mapped_contracts)):
        errors.append("duplicate contract SQL mapping")
    if len(mapped_migrations) != len(set(mapped_migrations)):
        errors.append("duplicate deployment migration mapping")

    contract_names = {path.name for path in contracts.glob("*.sql")}
    migration_names = {path.name for path in migrations.glob("*.sql")}
    for contract_name in sorted(contract_names - set(mapped_contracts)):
        errors.append(f"unmapped contract SQL: contracts/sql/{contract_name}")
    for migration_name in sorted(migration_names - set(mapped_migrations)):
        errors.append(f"unmapped deployment migration: infra/supabase/migrations/{migration_name}")

    for contract_name, migration_name in CONTRACT_MIGRATION_PAIRS:
        contract_path = contracts / contract_name
        migration_path = migrations / migration_name
        if not contract_path.is_file():
            errors.append(f"missing contract SQL: contracts/sql/{contract_name}")
            continue
        if not migration_path.is_file():
            errors.append(
                f"missing deployment migration: infra/supabase/migrations/{migration_name}"
            )
            continue
        if contract_path.read_bytes() != migration_path.read_bytes():
            errors.append(
                "contract/migration content differs: "
                f"contracts/sql/{contract_name} != infra/supabase/migrations/{migration_name}"
            )
    return errors


def main() -> int:
    errors = validate_contract_migrations()
    if errors:
        for error in errors:
            print(f"NG {error}")
        return 1
    print("OK Supabase contract SQL matches deployment migrations")
    return 0


if __name__ == "__main__":
    sys.exit(main())
