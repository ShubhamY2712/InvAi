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
