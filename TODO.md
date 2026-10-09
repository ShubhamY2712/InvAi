# Known gaps

These are the gaps found in the Phase 1 review that are still open. The review's must-fix items (input validation,
role checks, account rules, `SECRET_KEY` rules, removing `/dev/me/`, the `DOCS_ENABLED` switch, Postgres test skips)
are done and aren't listed here.

## Before going public

- [ ] **Rate limiting on `/login/` and `/onboard-business/`.** Nothing slows down password guessing or mass sign-ups,
      and there is no lockout after repeated failed logins.
- [ ] **Pagination on `GET /sales/` and `GET /products/`.** Both return every row, which gets slow as a business grows.
- [ ] **Unique SKU per business.** `sku` is indexed but not unique, so a business can create two products with the same SKU.
- [ ] **Bring the hosted (Neon) database onto Alembic.** Apply whichever of `migrations/legacy/001`–`008` it hasn't
      had, `alembic stamp 13385b7b4ede`, then `alembic upgrade head` (steps in [migrations/legacy/README.md](migrations/legacy/README.md)).
      Sales that point to deleted products or users will stop `20ef20ef2e0a`'s orphan check and must be dealt with
      first. Drop the leftover `inventoryitem` table.
- [ ] **Schedule the daily expiry check** on the server ([docs/DEVELOPMENT.md](docs/DEVELOPMENT.md#scheduling-the-daily-expiry-check)).
      Until then, expired stock is only cleared when someone calls `POST /system/daily-check`.
- [ ] **CI** that runs `python -m pytest` and `python scripts/check_migrations.py` on every push, with a Postgres
      service and `TEST_DATABASE_URL` set so the Postgres tests run instead of skipping.
- [ ] **Confirm the old secrets were rotated.** The original JWT secret is still in the git history (it was
      hardcoded in `main.py` until commit `5e767d2`), so production must use a newly generated `SECRET_KEY`.
      Change the Neon database password too if it has ever been shared outside `.env`.
- [ ] **Production settings:** `DOCS_ENABLED=false`, and `CORS_ORIGINS` set to the real frontend origin.

## Later

### Features

- [ ] Carts with several items in one sale (checkout sells one product per request).
- [ ] Returns, refunds and voided sales.
- [ ] Backorders: a short delivery closes the purchase order; the missing quantity isn't kept on order.
- [ ] User management: list users, change a role, deactivate a user.
- [ ] Editing suppliers and the business profile.
- [ ] Setting `min_stock_level` through the API (the low-stock alert reads it, but nothing writes it).
- [ ] Opening stock on `POST /products/` must be a whole number, even for kg and litre products; loose goods
      start at 0 and arrive through a purchase order or an audit.
- [ ] Price history. A price change overwrites the old price, and the waste report gives quantities only:
      the price of stock at the time it was disposed of isn't recorded, so waste has no money value.
- [ ] More than one currency or time zone: everything assumes INR and Asia/Kolkata.

### Security

- [ ] Token revocation and refresh tokens. A token stays valid for its full hour after logout, and a role change
      only takes effect once the old token expires.
- [ ] Row-level security in Postgres as a second line of defence; tenant isolation is enforced only by the app's
      `business_id` filters.
- [ ] Usernames and emails are unique across all businesses, and email uniqueness is case-sensitive
      (`Owner@x.com` and `owner@x.com` are different accounts).

### Operations

- [ ] Monitoring and structured logging.
- [ ] Deployment: Docker image, HTTPS, database backups and a restore test.
- [ ] `uvicorn --reload` also watches `venv/`, so installing packages restarts the dev server
      (use `--reload-dir app` if that gets in the way).

### Database and tests

- [ ] Indexes on unindexed foreign-key columns: `product_batch.po_id` and five `stock_movement` columns.
- [ ] Database length limits on existing text columns. The API caps lengths, but the columns are unbounded, so
      direct SQL or a script can still store anything.
- [ ] The SQLite test database doesn't enforce foreign keys, so a missing FK check only fails in the Postgres tests.
- [ ] Offline `alembic upgrade --sql` stops at `20ef20ef2e0a`, because its orphan check has to query the database.
