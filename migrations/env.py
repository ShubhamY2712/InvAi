"""Alembic environment. The database URL comes from DATABASE_URL (loaded from .env), never from alembic.ini."""
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import create_engine, pool

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")  # never overrides variables that are already set


def database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set. Add it to .env or the environment before running Alembic.")
    return url


database_url()  # fail clearly before importing main, which builds its engine from DATABASE_URL on import
sys.path.insert(0, str(PROJECT_ROOT))
import main  # noqa: E402,F401  registers every model on SQLModel.metadata (needs SECRET_KEY, like the app)
from sqlmodel import SQLModel  # noqa: E402

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata


def run_migrations_offline() -> None:
    """Emit SQL to stdout (alembic upgrade head --sql) without connecting."""
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_with(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # A caller that owns the transaction (scripts/reset_db.py) passes its connection in
    supplied = config.attributes.get("connection")
    if supplied is not None:
        _run_with(supplied)
        return
    connectable = create_engine(database_url(), poolclass=pool.NullPool)
    with connectable.connect() as connection:
        _run_with(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
