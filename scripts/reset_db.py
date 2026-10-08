"""Dev only: drop every table in the database and recreate them from the models in main.py.

Refuses to run unless DATABASE_URL points at localhost or 127.0.0.1.

Usage (from the project root):  python scripts/reset_db.py
"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import MetaData, create_engine, text
from sqlalchemy.engine import make_url

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ALLOWED_HOSTS = {"localhost", "127.0.0.1"}


def reset() -> None:
    load_dotenv(PROJECT_ROOT / ".env")
    raw_url = os.getenv("DATABASE_URL")
    if not raw_url:
        sys.exit("Refusing to run: DATABASE_URL is not set.")

    url = make_url(raw_url)
    # libpq lets ?host= / ?hostaddr= override the host in the URL, so those are refused too
    if url.host not in ALLOWED_HOSTS or {"host", "hostaddr"} & set(url.query):
        sys.exit(f"Refusing to run: DATABASE_URL host is {url.host!r}; only localhost or 127.0.0.1 is allowed.")

    # Importing main registers every model on SQLModel.metadata
    sys.path.insert(0, str(PROJECT_ROOT))
    from sqlmodel import SQLModel
    import main  # noqa: F401

    engine = create_engine(url)
    with engine.begin() as conn:  # one transaction: any failure leaves the database as it was
        # Drop every table that exists, including ones no longer in the models
        existing = MetaData()
        existing.reflect(conn)
        existing.drop_all(conn)

        # Postgres enum types outlive their tables; drop them so they are rebuilt from the models
        if conn.dialect.name == "postgresql":
            enum_types = conn.execute(text(
                "SELECT t.typname FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace "
                "WHERE t.typtype = 'e' AND n.nspname = current_schema()"
            )).scalars().all()
            for name in enum_types:
                conn.execute(text(f'DROP TYPE "{name}"'))

        SQLModel.metadata.create_all(conn)

    print(f"Reset {url.host}/{url.database}: dropped {len(existing.tables)} tables, "
          f"created {len(SQLModel.metadata.tables)} from the models.")


if __name__ == "__main__":
    reset()
