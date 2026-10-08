"""CORS: allowed origins come from CORS_ORIGINS. Each test builds its own app with create_app()."""
import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app import config
from app.main import create_app

ROOT = Path(__file__).resolve().parent.parent
ALLOWED = "http://localhost:3000"


def request(app, method, path, headers):
    """Sends one request straight to the ASGI app. Returns (status, {lower-case header: value})."""
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method, "scheme": "http",
             "path": path, "raw_path": path.encode(), "query_string": b"", "client": ("test", 1), "server": ("test", 80),
             "root_path": "", "headers": [(b"host", b"test")] + [(k.lower().encode(), v.encode()) for k, v in headers.items()]}
    response = {"status": None, "headers": {}}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            response["status"] = message["status"]
            response["headers"] = {k.decode().lower(): v.decode() for k, v in message["headers"]}

    asyncio.run(app(scope, receive, send))
    return response["status"], response["headers"]


def preflight(app, origin, method="POST", headers="authorization,content-type"):
    return request(app, "OPTIONS", "/products/", {"Origin": origin, "Access-Control-Request-Method": method,
                                                  "Access-Control-Request-Headers": headers})


def cors_headers(headers):
    return {k: v for k, v in headers.items() if k.startswith("access-control-")}


@pytest.fixture
def app_with(monkeypatch):
    """app_with(value): an app built with CORS_ORIGINS set to value (None: unset)."""
    def build(value):
        if value is None:
            monkeypatch.delenv("CORS_ORIGINS", raising=False)
        else:
            monkeypatch.setenv("CORS_ORIGINS", value)
        return create_app()
    return build


def test_preflight_from_an_allowed_origin(app_with):
    status, headers = preflight(app_with(ALLOWED), ALLOWED)
    assert status == 200
    assert headers["access-control-allow-origin"] == ALLOWED
    assert {m.strip() for m in headers["access-control-allow-methods"].split(",")} == {"GET", "POST", "PUT", "PATCH", "DELETE"}
    assert {h.strip().lower() for h in headers["access-control-allow-headers"].split(",")} >= {"authorization", "content-type"}
    assert "access-control-allow-credentials" not in headers
    assert "Origin" in headers.get("vary", "")


def test_preflight_asking_for_another_header_is_refused(app_with):
    status, _ = preflight(app_with(ALLOWED), ALLOWED, headers="x-custom")
    assert status == 400


def test_preflight_from_a_disallowed_origin_gets_no_cors_headers(app_with):
    status, headers = preflight(app_with(ALLOWED), "https://evil.example")
    assert status == 400
    assert cors_headers(headers) == {}


def test_simple_request_from_a_disallowed_origin_gets_no_allow_origin(app_with):
    app = app_with(ALLOWED)
    _, allowed = request(app, "GET", "/openapi.json", {"Origin": ALLOWED})
    _, disallowed = request(app, "GET", "/openapi.json", {"Origin": "https://evil.example"})
    assert allowed["access-control-allow-origin"] == ALLOWED
    assert "access-control-allow-origin" not in disallowed


@pytest.mark.parametrize("value", ["", "  ,  ", None])
def test_empty_setting_allows_nothing(app_with, value):
    app = app_with(value)
    status, headers = preflight(app, ALLOWED)
    assert status == 400 and cors_headers(headers) == {}
    assert "access-control-allow-origin" not in request(app, "GET", "/openapi.json", {"Origin": ALLOWED})[1]


def test_several_origins_with_spaces_and_trailing_slashes(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", " http://localhost:3000/ , https://app.example.com ,, ")
    assert config.cors_origins() == ["http://localhost:3000", "https://app.example.com"]


@pytest.mark.parametrize("value", ["*", "https://app.example.com,*", "https://*.example.com"])
def test_star_is_rejected(app_with, value):
    with pytest.raises(RuntimeError, match='CORS_ORIGINS must not contain "\\*"'):
        app_with(value)


def test_star_stops_uvicorn_from_starting():
    env = {**os.environ, "CORS_ORIGINS": "https://app.example.com,*", "DATABASE_URL": "sqlite://", "SECRET_KEY": "test-secret"}
    result = subprocess.run([sys.executable, "-m", "uvicorn", "app.main:app", "--port", "0"], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode != 0
    assert 'CORS_ORIGINS must not contain "*"' in result.stderr
