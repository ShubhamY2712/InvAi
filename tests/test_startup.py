"""App startup: the schema comes from Alembic, and the app refuses to run against an out-of-date database."""
import asyncio
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import StaticPool

from app import db, main

ROOT = Path(__file__).resolve().parent.parent
HEAD = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini"))).get_current_head()


def start_app():
    """Runs the ASGI lifespan startup (and shutdown). Returns (succeeded, failure message)."""
    sent, step = [], {"n": 0}
    started = asyncio.Event()

    async def receive():
        step["n"] += 1
        if step["n"] == 1:
            return {"type": "lifespan.startup"}
        await started.wait()
        return {"type": "lifespan.shutdown"}

    async def send(message):
        sent.append(message)
        if message["type"] in ("lifespan.startup.complete", "lifespan.startup.failed"):
            started.set()

    async def run():
        try:
            await main.app({"type": "lifespan", "asgi": {"version": "3.0"}, "state": {}}, receive, send)
        except Exception:
            pass  # Starlette re-raises the startup error after reporting it
    asyncio.run(run())
    types = [m["type"] for m in sent]
    failed = next((m for m in sent if m["type"] == "lifespan.startup.failed"), None)
    return "lifespan.startup.complete" in types, (failed or {}).get("message", "")


@pytest.fixture
def empty_db(monkeypatch):
    """A brand-new, empty SQLite database used as the app's database."""
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    monkeypatch.setattr(db, "engine", eng)
    yield eng
    eng.dispose()


def stamp(eng, revision):
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)"))
        conn.execute(text("INSERT INTO alembic_version VALUES (:r)"), {"r": revision})


def test_refuses_to_start_on_an_unmigrated_database(empty_db):
    ok, message = start_app()
    assert not ok
    assert f"Database schema is out of date. Run: alembic upgrade head (database: none; code: {HEAD})" in message


def test_refuses_to_start_on_an_older_revision(empty_db):
    stamp(empty_db, "0123456789ab")
    ok, message = start_app()
    assert not ok and "Database schema is out of date. Run: alembic upgrade head (database: 0123456789ab;" in message


def test_starts_at_head_and_creates_nothing(empty_db):
    stamp(empty_db, HEAD)
    ok, message = start_app()
    assert ok, message
    assert inspect(empty_db).get_table_names() == ["alembic_version"]  # no create_all any more


def test_alembic_ini_has_no_database_url():
    assert not re.search(r"^\s*sqlalchemy\.url\s*=", (ROOT / "alembic.ini").read_text(), flags=re.M)


def test_alembic_needs_database_url():
    env = {**os.environ, "DATABASE_URL": "", "SECRET_KEY": "test-secret"}  # set (empty), so .env can't fill it in
    result = subprocess.run([sys.executable, "-m", "alembic", "current"], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode != 0
    assert "DATABASE_URL is not set. Add it to .env or the environment before running Alembic." in result.stderr


def test_one_head_only():
    assert len(ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini"))).get_heads()) == 1
