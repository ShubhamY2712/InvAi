"""reset_db.py must refuse anything that isn't localhost/127.0.0.1. These URLs are never connected to."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "reset_db.py"


@pytest.mark.parametrize("url", [
    "postgresql://user:secretpw@ep-example.neon.tech/db",
    "postgresql://user:secretpw@localhost.evil.com/db",
    "postgresql://user:secretpw@127.0.0.2/db",
    "postgresql://user:secretpw@localhost/db?host=ep-example.neon.tech",
    "postgresql://user:secretpw@localhost/db?hostaddr=10.0.0.1",
    "sqlite:///local.db",
    "",
])
def test_refuses_non_local_database(url):
    env = {**os.environ, "DATABASE_URL": url}
    result = subprocess.run([sys.executable, str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 1
    assert "Refusing to run" in result.stderr
    assert "secretpw" not in result.stderr + result.stdout
