# Development

## Running

```
python -m venv venv && venv\Scripts\activate      # or source venv/bin/activate
pip install -r requirements.txt
copy .env.example .env                             # cp on macOS/Linux; then fill in real values
alembic upgrade head                               # the app refuses to start on an out-of-date schema
uvicorn app.main:app --reload
```

For a fresh local database: `python scripts/reset_db.py` (local only; drops everything and migrates to head).
Schema changes are covered in [DATABASE.md](DATABASE.md).

## Settings (`.env`)

Every setting the app reads is listed, with a placeholder and a comment, in the committed `.env.example`.
`.env` itself is git-ignored and must never be committed.

| Setting | Used for |
|---|---|
| `DATABASE_URL` | Postgres connection for the app, Alembic and the scripts |
| `SECRET_KEY` | Signs login tokens; the app won't start without it |
| `CORS_ORIGINS` | Browser origins allowed to call the API (see below) |

## CORS

`CORS_ORIGINS` is a comma-separated list of exact origins, e.g. `http://localhost:3000,https://app.example.com`
(scheme, host and port; no path, and a trailing `/` is ignored).

- **Empty or unset:** no cross-origin requests are allowed. Same-origin requests and non-browser clients are unaffected.
- **`*` is refused:** the app won't start if the value contains `*` anywhere. List every origin explicitly.
- Allowed methods: `GET, POST, PUT, PATCH, DELETE`. Allowed request headers: `Authorization, Content-Type`.
- `allow_credentials` is off: the frontend sends the token in the `Authorization` header, not a cookie.
- A refused preflight gets a 400 with no `Access-Control-*` headers at all.

Remember to add the frontend's production origin to `CORS_ORIGINS` when deploying.

## Layout

| Path | What lives there |
|---|---|
| `app/main.py` | The FastAPI app: startup schema check, the `ServiceError` handler, the routers |
| `app/config.py` | Project paths; loads `.env` |
| `app/models.py` | Enums, field types (`Quantity`, `Money`, `UTCDateTime`, `UTCTimestamp`) and all tables. Imports without `SECRET_KEY` or a database, so Alembic and scripts can use it alone |
| `app/schemas.py` | Request bodies (their class names appear in the OpenAPI document) |
| `app/db.py` | The engine, `new_session()`, the startup schema check, reading database errors |
| `app/security.py` | Passwords, JWT tokens, `get_current_user`, role checks. Checks `SECRET_KEY` on import |
| `app/timeutils.py` | The business time zone, `today()`, `utc_now()`, India dates in SQL |
| `app/errors.py` | Errors services raise (`NotFound`, `Conflict`, `InvalidInput`, ...) |
| `app/services/` | Business logic, one module per area (accounts, products, inventory, audit, checkout, purchase orders, suppliers, daily check, reports, scorecards) plus shared `common`, `stock` (row locking) and `ledger` |
| `app/routers/` | HTTP endpoints: auth, products, inventory, sales, purchase_orders, suppliers, reports |
| `scripts/` | `reset_db.py`, `run_daily_check.py`, `check_migrations.py` |

## Conventions

- **Endpoints are thin:** check the caller's role, call one service, return its result.
- **Services don't import FastAPI.** They take plain values and schema objects and raise `app.errors`
  exceptions; `app/main.py` turns those into the same `{"detail": ...}` responses as `HTTPException`. That
  keeps them callable from scripts.
- **Services open their own session** with `db.new_session()` and commit where the operation ends. The one
  exception is `daily_check.run_daily_check(session, ...)`, whose caller owns the transaction.
- **Look things up through the module** (`timeutils.today()`, `db.new_session()`, `models.generate_business_id()`),
  so tests can swap them with `monkeypatch.setattr(timeutils, "today", ...)` and so on.
- **Dependencies point one way:** routers → services → models/db/timeutils. Services never import routers or `app.main`.

## Tests

`python -m pytest`. Most tests use an in-memory SQLite database built from the models. Tests that need Postgres
(`test_migrations.py`, `test_postgres_integrity.py`) create and drop scratch databases on the local server named in
`.env`, and are skipped when there isn't one.
