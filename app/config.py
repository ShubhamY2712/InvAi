"""Paths and settings. Loads .env once (never overriding variables already set). Importing this module never
requires SECRET_KEY or a database: the modules that need them check for themselves."""
import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def database_url() -> str | None:
    return os.getenv("DATABASE_URL")


def secret_key() -> str | None:
    return os.getenv("SECRET_KEY")


_TRUE, _FALSE = {"true", "1", "yes"}, {"false", "0", "no"}


def docs_enabled() -> bool:
    """DOCS_ENABLED: serve /docs, /redoc and /openapi.json. Unset means true; anything unrecognised stops startup."""
    raw = os.getenv("DOCS_ENABLED", "true").strip().lower()
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    raise RuntimeError(f'DOCS_ENABLED must be true or false (also accepted: 1/0, yes/no), not "{raw}".')


def cors_origins() -> list[str]:
    """Origins allowed to call the API from a browser, from CORS_ORIGINS (comma-separated).
    Empty or unset allows no cross-origin requests. A "*" anywhere is refused: list origins explicitly."""
    raw = os.getenv("CORS_ORIGINS", "")
    if "*" in raw:
        raise RuntimeError('CORS_ORIGINS must not contain "*"; list the allowed origins explicitly.')
    # Browsers send Origin without a trailing slash, so "http://localhost:3000/" would never match as written
    return [origin.strip().rstrip("/") for origin in raw.split(",") if origin.strip()]
