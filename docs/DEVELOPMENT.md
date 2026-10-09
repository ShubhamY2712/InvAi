# Development

## Setup

You need **Python 3.10+** and **PostgreSQL 17+** installed. Run the commands below from the project folder.

### 1. Create the Postgres user and database

The app connects as its own user (here `invai`) to its own database (also `invai`). Give that user
`CREATEDB`. The app itself doesn't need it, but the Postgres tests and `scripts/check_migrations.py` create and
drop scratch databases (see [Tests](#tests)).

**With psql**, connect as the `postgres` superuser (`psql -U postgres`; on Windows psql is in
`C:\Program Files\PostgreSQL\17\bin` if it isn't on your `PATH`) and run:

```sql
CREATE ROLE invai LOGIN PASSWORD 'choose-a-password' CREATEDB;
CREATE DATABASE invai OWNER invai;
```

**With pgAdmin 4**, connect to your local server and then:

1. Right-click **Login/Group Roles** → **Create** → **Login/Group Role...**
   - **General** tab: set Name to `invai`.
   - **Definition** tab: enter a password.
   - **Privileges** tab: turn on **Can login?** and **Create databases?**, then Save.
2. Right-click **Databases** → **Create** → **Database...**
   - Set Database to `invai` and Owner to `invai`, then Save.

To give the permission to a user that already exists: `ALTER ROLE invai CREATEDB;`

### 2. Create and activate a virtual environment

```
python -m venv venv
```

Then activate it. Do this in every new terminal before you run anything else:

| Shell | Command |
|---|---|
| PowerShell | `venv\Scripts\Activate.ps1` |
| cmd | `venv\Scripts\activate.bat` |
| Git Bash | `source venv/Scripts/activate` |
| macOS / Linux | `source venv/bin/activate` |

If PowerShell refuses with "running scripts is disabled on this system", run
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once to allow local scripts for your account, then try again.

### 3. Install, configure, migrate, run

```
pip install -r requirements.txt
copy .env.example .env                             # cp in Git Bash, macOS and Linux
```

Edit `.env` (every setting is described under [Settings](#settings-env)):

- `DATABASE_URL`: the user, password and database from step 1, e.g. `postgresql://invai:choose-a-password@localhost:5432/invai`.
- `SECRET_KEY`: generate one with `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
  The app won't start with the placeholder.

Then create the tables and start the API:

```
alembic upgrade head                               # the app refuses to start on an out-of-date schema
uvicorn app.main:app --reload
```

The API is now at <http://127.0.0.1:8000>. `--reload` restarts it whenever you edit the code.

To start over with a fresh local database: `python scripts/reset_db.py` (local only; drops everything and migrates to head).
Schema changes are covered in [DATABASE.md](DATABASE.md).

## First walkthrough

With the API running and `DOCS_ENABLED` left at `true`, open <http://127.0.0.1:8000/docs>. Each endpoint has
a **Try it out** button that sends a real request.

1. **Create a business and its owner.** `POST /onboard-business/` needs no login:
   ```json
   {
     "business_name": "Corner Shop",
     "category": "General & Daily Retail",
     "owner_username": "owner",
     "email": "owner@example.com",
     "password": "a-long-password"
   }
   ```
   `category` must be one of the values listed in the request schema. Passwords need at least 8 characters.
2. **Log in.** Click **Authorize** (top right), enter the owner's username and password, leave `client_id` and
   `client_secret` empty, and click **Authorize**. Every request from the page now carries the login token.
   The token lasts an hour. After that, protected endpoints answer 401 and you log in again the same way.
   Outside `/docs`, log in with `POST /login/`, sending `username` and `password` as form fields. It returns
   an `access_token`, which you send as `Authorization: Bearer <token>`.
3. **Use the API as that owner**, for example:
   - `POST /products/` with `{"name": "Milk 1L", "sku": "MILK-1L", "price": 60, "quantity": 20, "unit": "piece", "expiry_date": "<a date after today>"}`
   - `GET /products/` to see it, and `GET /products/{product_id}/batches` for its stock batch.
   - `POST /checkout/` with `{"product_id": <its id>, "quantity": 2}` to record a sale.
   - `GET /sales/` and `GET /inventory/movements` to see the sale and its ledger entry.
   - `POST /employees/` to add Manager or Staff logins for the same business.

## Settings (`.env`)

Every setting the app reads is listed, with a placeholder and a comment, in the committed `.env.example`.
`.env` itself is git-ignored and must never be committed.

| Setting | Used for |
|---|---|
| `DATABASE_URL` | Postgres connection for the app, Alembic and the scripts |
| `SECRET_KEY` | Signs login tokens. The app won't start if it's missing, shorter than 32 characters, or still the `.env.example` placeholder |
| `CORS_ORIGINS` | Browser origins allowed to call the API (see below) |
| `DOCS_ENABLED` | `true` (default) serves `/docs`, `/redoc` and `/openapi.json`; `false` turns all three off. Set it to `false` in production. Values: `true/false`, `1/0`, `yes/no` |

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

```
python -m pytest
```

The full suite takes **several minutes**: the Postgres tests build and migrate real databases, and some tests start
the app in a subprocess. While you work, run a single file or a subset, e.g. `python -m pytest tests/test_cors.py` or
`python -m pytest -k expiry`. Add `-rs` to see why any tests were skipped.

Most tests use an in-memory SQLite database built from the models, so they need no Postgres server.

The Postgres tests (`test_migrations.py`, `test_postgres_integrity.py`) create and drop scratch databases on the server
in `.env`'s `DATABASE_URL`, or in `TEST_DATABASE_URL` if you set that environment variable (useful for CI). They skip,
with the reason, when that server isn't on `localhost`, can't be reached, or its user can't create databases. To let
them run, give the user that permission: `ALTER ROLE <user> CREATEDB;` (or tick **Create databases?** on the role's
**Privileges** tab in pgAdmin).

## Scheduling the daily expiry check

`scripts/run_daily_check.py` disposes of expired stock for every business: it empties each batch whose expiry date
has arrived and records the disposal in the stock ledger. Each business runs in its own transaction, and running the
script twice on the same day changes nothing. It exits with `0` if every business succeeded and `1` if any failed.
An Owner or Manager can run the same check for their own business with `POST /system/daily-check`.

Run it once a day, **just after midnight India time**. "Today" is always the Asia/Kolkata date, whatever the
machine's time zone, and stock counts as expired from its expiry date onward. The script reads `.env` from the
project folder and logs to stderr, so the examples below append its output to `logs/daily_check.log` (`logs/` is
git-ignored). Create that folder first with `mkdir logs`.

**Windows (Task Scheduler)**: in PowerShell, with your project path in `-WorkingDirectory`, run:

```powershell
$action = New-ScheduledTaskAction -Execute "cmd.exe" `
    -Argument '/c venv\Scripts\python.exe scripts\run_daily_check.py >> logs\daily_check.log 2>&1' `
    -WorkingDirectory "C:\path\to\InvAi_Project"
$trigger = New-ScheduledTaskTrigger -Daily -At 00:10
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable    # runs at the next start-up if the PC was off at 00:10
Register-ScheduledTask -TaskName "InvAi daily check" -Action $action -Trigger $trigger -Settings $settings
```

Or use the Task Scheduler app: **Create Basic Task...** → trigger **Daily** at 00:10 → action **Start a program**,
with Program `cmd.exe`, Arguments `/c venv\Scripts\python.exe scripts\run_daily_check.py >> logs\daily_check.log 2>&1`
and **Start in** set to the project folder. The time is the PC's local time; 00:10 assumes the PC is set to India time.

Test it with `Start-ScheduledTask -TaskName "InvAi daily check"`, then read `logs\daily_check.log`. The task's
**Last Run Result** is `0x0` on success and `0x1` if a business failed. By default the task only runs while you are
logged in. To change that, open the task's **Properties** and choose **Run whether user is logged on or not**.

**macOS / Linux (cron)**: run `crontab -e` and add one line. cron uses the machine's time zone, so pick the line
that matches it:

```
# machine on India time: 00:10 IST
10 0 * * * cd /path/to/InvAi_Project && venv/bin/python scripts/run_daily_check.py >> logs/daily_check.log 2>&1
# machine on UTC: 18:40 UTC is 00:10 IST the next day
40 18 * * * cd /path/to/InvAi_Project && venv/bin/python scripts/run_daily_check.py >> logs/daily_check.log 2>&1
```
