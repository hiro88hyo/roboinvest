from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "check-supabase-migration-contracts.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_supabase_migration_contracts", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_deployment_migrations_match_contract_sql() -> None:
    module = _load_script()
    assert module.validate_contract_migrations() == []


def test_mapping_must_cover_every_contract_and_migration(monkeypatch) -> None:
    module = _load_script()
    missing_pair = module.CONTRACT_MIGRATION_PAIRS[-1]
    monkeypatch.setattr(module, "CONTRACT_MIGRATION_PAIRS", module.CONTRACT_MIGRATION_PAIRS[:-1])

    assert module.validate_contract_migrations() == [
        f"unmapped contract SQL: contracts/sql/{missing_pair[0]}",
        f"unmapped deployment migration: infra/supabase/migrations/{missing_pair[1]}",
    ]
