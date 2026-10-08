"""Migrations at head must match the models. Needs a local Postgres server (the one in .env), so it is skipped
when there isn't one; scripts/check_migrations.py does the work on throwaway databases it creates and drops."""
import os
import subprocess
import sys
from pathlib import Path

import pytest
from dotenv import dotenv_values
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parent.parent


def local_postgres_url() -> str | None:
    """The DATABASE_URL from .env if it is a reachable Postgres server on this machine (conftest replaces the
    environment's DATABASE_URL with SQLite, so .env is read directly)."""
    url = dotenv_values(ROOT / ".env").get("DATABASE_URL")
    if not url:
        return None
    parsed = make_url(url)
    if parsed.get_backend_name() != "postgresql" or parsed.host not in ("localhost", "127.0.0.1"):
        return None
    try:
        engine = create_engine(parsed.set(database="postgres"), connect_args={"connect_timeout": 3})
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
    except Exception:
        return None
    return url


def test_migrations_at_head_match_the_models():
    url = local_postgres_url()
    if url is None:
        pytest.skip("no local Postgres server in .env")
    env = {**os.environ, "DATABASE_URL": url}
    env.pop("SECRET_KEY", None)  # use the real one from .env
    result = subprocess.run([sys.executable, "scripts/check_migrations.py"], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=600)
    output = result.stdout + result.stderr
    assert result.returncode == 0, output[-3000:]
    assert "OK   alembic check: the migrations at head match the models" in output
    assert "OK   upgrade head == create_all" in output
    assert "OK   downgrade base removes every table and enum type" in output
