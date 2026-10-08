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


def cors_origins() -> list[str]:
    """Origins allowed to call the API from a browser, from CORS_ORIGINS (comma-separated).
    Empty or unset allows no cross-origin requests. A "*" anywhere is refused: list origins explicitly."""
    raw = os.getenv("CORS_ORIGINS", "")
    if "*" in raw:
        raise RuntimeError('CORS_ORIGINS must not contain "*"; list the allowed origins explicitly.')
    # Browsers send Origin without a trailing slash, so "http://localhost:3000/" would never match as written
    return [origin.strip().rstrip("/") for origin in raw.split(",") if origin.strip()]
