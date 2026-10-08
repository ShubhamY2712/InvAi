"""The database engine and sessions, the startup schema check, and reading database errors.
Code uses db.new_session() (not a copied engine reference) so tests can swap the engine."""
from alembic.config import Config as AlembicConfig
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, create_engine

from app.config import PROJECT_ROOT, database_url

engine = create_engine(database_url(), pool_pre_ping=True)


def new_session() -> Session:
    return Session(engine)


SCHEMA_OUT_OF_DATE = "Database schema is out of date. Run: alembic upgrade head"


def check_schema_is_current(db_engine) -> None:
    """Raises RuntimeError unless the database is at the Alembic head revision(s) of this code."""
    heads = set(ScriptDirectory.from_config(AlembicConfig(str(PROJECT_ROOT / "alembic.ini"))).get_heads())
    with db_engine.connect() as connection:
        current = set(MigrationContext.configure(connection).get_current_heads())
    if current != heads:
        raise RuntimeError(f"{SCHEMA_OUT_OF_DATE} "
                           f"(database: {', '.join(sorted(current)) or 'none'}; code: {', '.join(sorted(heads))})")


# Which unique rule an IntegrityError broke, matched on the Postgres constraint name or the SQLite "table.column"
UNIQUE_VIOLATION_MARKERS = {
    "business_id": ("businessprofile_pkey", "businessprofile.id"),
    "username": ("ix_users_username", "users.username"),
    "email": ("ix_users_email", "users.email"),
}


def duplicate_field(exc: IntegrityError) -> str | None:
    """Returns "business_id", "username" or "email" if exc is a duplicate on that field, else None."""
    message = str(exc.orig)
    return next((field for field, markers in UNIQUE_VIOLATION_MARKERS.items()
                 if any(marker in message for marker in markers)), None)
