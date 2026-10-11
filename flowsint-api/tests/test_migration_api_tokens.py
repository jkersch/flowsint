"""The api_tokens migration builds the table the ApiToken model expects."""

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from flowsint_core.core.models import ApiToken, Base

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "3b7e9a1c4d2f_add_api_tokens_table.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("api_tokens_migration", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(engine, step):
    migration = _load_migration()
    with engine.begin() as conn:
        ops = Operations(MigrationContext.configure(conn))
        migration.op = ops  # the module imported alembic.op; bind it here
        getattr(migration, step)()


def test_upgrade_matches_model_and_downgrade_removes_it():
    engine = sa.create_engine("sqlite://")
    Base.metadata.tables["profiles"].create(engine)

    _run(engine, "upgrade")
    inspector = sa.inspect(engine)
    columns = {c["name"]: c for c in inspector.get_columns("api_tokens")}

    assert set(columns) == {c.name for c in ApiToken.__table__.columns}
    for column in ApiToken.__table__.columns:
        assert columns[column.name]["nullable"] == column.nullable, column.name
    assert {i["name"] for i in inspector.get_indexes("api_tokens")} == {
        "idx_api_tokens_owner_id"
    }
    assert [
        u["column_names"] for u in inspector.get_unique_constraints("api_tokens")
    ] == [["token_hash"]]
    fk = inspector.get_foreign_keys("api_tokens")[0]
    assert (fk["referred_table"], fk["constrained_columns"]) == (
        "profiles",
        ["owner_id"],
    )
    assert fk["options"]["ondelete"] == "CASCADE"

    _run(engine, "downgrade")
    assert "api_tokens" not in sa.inspect(engine).get_table_names()


def test_migration_follows_the_previous_head():
    migration = _load_migration()
    assert migration.down_revision == "c7d1f0a92b3e"
