"""Test settings. Database tests run against a fresh `suraksha_test` database, migrated with
Alembic; they are skipped when Postgres is not reachable (start it with `uv run poe infra`)."""

import os
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[1]
ADMIN_URL = os.environ.get(
    "TEST_ADMIN_DATABASE_URL", "postgresql://suraksha:suraksha@localhost:5433/postgres"
)
TEST_DB = "suraksha_test"
TEST_URL = ADMIN_URL.rsplit("/", 1)[0].replace("postgresql://", "postgresql+psycopg://")
TEST_URL = f"{TEST_URL}/{TEST_DB}"

os.environ.update(
    DATABASE_URL=TEST_URL,
    VAULT_KEY=Fernet.generate_key().decode(),
    LLM_MODE="replay",
)


def _recreate_database() -> bool:
    import psycopg

    try:
        with psycopg.connect(ADMIN_URL, autocommit=True, connect_timeout=3) as conn:
            conn.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
            conn.execute(f"CREATE DATABASE {TEST_DB}")
        return True
    except psycopg.OperationalError:
        return False


@pytest.fixture(scope="session")
def migrated_db() -> str:
    if not _recreate_database():
        pytest.skip("Postgres not reachable; run `uv run poe infra`")
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", TEST_URL)
    command.upgrade(cfg, "head")
    return TEST_URL


@pytest.fixture
def docstore_dir(tmp_path, monkeypatch) -> Path:
    from suraksha.config import get_settings

    monkeypatch.setattr(get_settings(), "docstore_dir", tmp_path)
    return tmp_path
