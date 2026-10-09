"""Migrations at head must match the models. Needs a local Postgres server (the one in .env), so it is skipped
when there isn't one; scripts/check_migrations.py does the work on throwaway databases it creates and drops."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import postgres_test_server

ROOT = Path(__file__).resolve().parent.parent


def test_migrations_at_head_match_the_models():
    url, skip_reason = postgres_test_server()
    if url is None:
        pytest.skip(skip_reason)
    env = {**os.environ, "DATABASE_URL": url}
    env.pop("SECRET_KEY", None)  # use the real one from .env
    result = subprocess.run([sys.executable, "scripts/check_migrations.py"], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=600)
    output = result.stdout + result.stderr
    assert result.returncode == 0, output[-3000:]
    assert "OK   alembic check: the migrations at head match the models" in output
    assert "OK   upgrade head == create_all" in output
    assert "OK   append-only triggers present on stock_movement" in output
    assert "OK   downgrade base removes every table, enum type and function" in output
