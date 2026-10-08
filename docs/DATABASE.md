# Database schema and migrations

The schema is defined by the SQLModel classes in `main.py` and changed **only** through Alembic
migrations in `migrations/versions/`. The app never creates or alters tables itself: at startup it
compares the database's Alembic revision with the code's, and refuses to start if they differ:

```
RuntimeError: Database schema is out of date. Run: alembic upgrade head (database: none; code: <head revision>)
```

Alembic reads the database from `DATABASE_URL` (loaded from `.env`, like the app). The URL is never
written into `alembic.ini`. Importing the models also needs `SECRET_KEY`, so both must be set.

## Everyday workflow

1. **Change a model** in `main.py`.
2. **Generate a draft migration** against a database that is at the current head:
   ```
   alembic revision --autogenerate -m "add barcode to product"
   ```
3. **Review the draft** in `migrations/versions/`. Autogenerate is a starting point, not a finished
   migration: check every operation against the gotchas below, add data migrations it can't know
   about, and make sure `downgrade()` really undoes `upgrade()`.
4. **Apply it:** `alembic upgrade head`
5. **Check models and migrations agree:** `python scripts/check_migrations.py`
   (creates scratch databases on your local Postgres, runs the migrations, `alembic check`, compares
   with `create_all`, downgrades, and drops them). `tests/test_migrations.py` runs it too.
6. **Commit the model change and the migration together.**

Useful commands: `alembic current` (the database's revision), `alembic history` (all revisions),
`alembic downgrade -1` (undo the last one), `alembic upgrade head --sql` (print the SQL without running it).

## Local development

`python scripts/reset_db.py` drops everything in your local database and migrates it to head, in one
transaction (if a migration fails, nothing changes). It refuses to run unless `DATABASE_URL` points at
`localhost` or `127.0.0.1`.

The test suite builds its SQLite schema directly from the models (`create_all`) for speed, so **tests
do not exercise the migrations**. That is what `scripts/check_migrations.py` is for.

## Adopting an existing database

A database built before Alembic (with `create_all` or the hand-written SQL in `migrations/legacy/`)
already has the schema but no revision. Bring it to the legacy 008 state, then:

```
alembic stamp 13385b7b4ede   # record the baseline without running any DDL
alembic upgrade head         # apply anything newer
alembic check                # confirm the schema matches the models
```

## Gotchas

- **New values for a Postgres enum are not detected.** Autogenerate doesn't compare enum labels, so
  adding a member to `StockUnit`, `UserRole`, `BusinessCategory` or `MovementReason` produces an empty
  draft. Write it by hand, and use the member **name**, which is what SQLAlchemy stores
  (`DOZEN`, not `dozen`):
  ```python
  def upgrade():
      op.execute("ALTER TYPE stockunit ADD VALUE IF NOT EXISTS 'DOZEN'")
  ```
  The new value can't be used later in the same transaction, so don't backfill rows with it in the
  same migration. Removing or renaming an enum value has no `ALTER TYPE` equivalent: create a new type,
  convert the column, drop the old type.
- **Enum types outlive their tables.** `op.drop_table()` doesn't drop the type, so `downgrade()` must
  (see the end of the baseline). Creating a table with an enum that already exists needs
  `postgresql.ENUM(..., create_type=False)`.
- **Renames look like drop + add.** Renaming a column or table autogenerates a drop and an add, which
  loses the data. Replace them with `op.alter_column(..., new_column_name=...)` or `op.rename_table(...)`.
- **New `NOT NULL` columns on tables with data** fail unless you add a `server_default`, or add the
  column as nullable, backfill it, then set `nullable=False` in the same migration.
- **Server defaults and some type details aren't compared** (`compare_server_default` is off), so
  `alembic check` won't catch a missing `DEFAULT`. Review those by eye.
- **Constraint names come from the naming convention** in `main.py`, which matches Postgres's own
  defaults (`<table>_pkey`, `<table>_<column>_fkey`, `ix_<table>_<column>`). Keep it: migrations refer
  to constraints by name, and unnamed ones can't be dropped or altered later.
- **Big indexes on busy tables:** `CREATE INDEX CONCURRENTLY` can't run inside a transaction. Wrap it:
  ```python
  with op.get_context().autocommit_block():
      op.create_index("ix_...", "sales", ["..."], postgresql_concurrently=True)
  ```
- **Never edit a migration that has run anywhere else.** Write a new one. If two branches both add
  migrations, `alembic heads` shows two heads and the app refuses to start; join them with
  `alembic merge heads -m "merge"`.
- **Timestamps are `timestamptz` and always UTC.** Declare datetime columns with
  `sa_type=UTCDateTime` (and annotate them `UTCTimestamp`): the column type refuses naive datetimes and
  hands values back as aware UTC whatever the session's time zone, and `UTCTimestamp` sends them with
  `+00:00`. Use `utc_now()`, never `datetime.utcnow()`. Autogenerate writes `UTCDateTime` columns as
  `sa.DateTime(timezone=True)` (a hook in `env.py`), so migrations never import `main`.
- **Converting a column between `timestamp` and `timestamptz` needs `USING`.** A bare type change casts with
  the session's `TimeZone`, which silently shifts every value (by 5h30m on a server set to India). Say how
  to read the old value, as in `7086dfa92b45`:
  `postgresql_using='"col" AT TIME ZONE 'UTC''` in both `upgrade()` and `downgrade()`.
- **Autogenerate doesn't detect triggers or functions.** The ledger's append-only protection lives only in
  migration `20ef20ef2e0a`: a `stock_movement_append_only()` function plus two triggers that reject `UPDATE`
  and `DELETE` (per row) and `TRUNCATE` (per statement) on `stock_movement`. `alembic check` can't see them,
  so `scripts/check_migrations.py` checks for them explicitly. If a future migration recreates or renames
  `stock_movement`, it must recreate the triggers too.
- **The ledger is append-only, in the database too.** Never fix a ledger entry in place: insert a correcting
  entry. Even manual SQL gets *"stock_movement is an append-only ledger: UPDATE is not allowed"*. `DROP TABLE`
  (as `scripts/reset_db.py` does) doesn't fire the triggers.
- **Check for orphans before adding a foreign key.** Count the rows that point at nothing first, and stop
  with a clear message if there are any (see `20ef20ef2e0a`), rather than letting `ALTER TABLE` fail on the
  first bad row. On the old Neon data, sales whose product was hard-deleted (before products were
  deactivated instead) will stop that migration: repoint or remove them, then run it again.
- **SQLModel string columns** are rendered as `sqlmodel.sql.sqltypes.AutoString()`; the migration
  template already imports `sqlmodel` for this.
