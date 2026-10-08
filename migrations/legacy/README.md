# Legacy SQL migrations (not run any more)

These eight files were written by hand, before the project used Alembic, to bring the old Neon
database up to date with the models one step at a time:

| File | What it did |
|---|---|
| `001_fifo_groundwork.sql` | Decimal quantities, units, nullable batch fields, opening batches |
| `002_money_decimal.sql` | Money columns to `NUMERIC(12,2)` |
| `003_business_ids_and_fks.sql` | Foreign keys to `businessprofile`, user id sequence fix |
| `004_sales_business_timestamp_index.sql` | Composite sales index |
| `005_stock_movement_ledger.sql` | Stock movement ledger, with backfill |
| `006_supplier_scorecard.sql` | Received / rejected quantities, expected delivery date |
| `007_product_is_active.sql` | Product deactivation |
| `008_po_cancellation.sql` | Purchase order cancellation |

They are kept for history and for one job: bringing a database that predates Alembic up to the
schema the Alembic baseline describes. **Nothing runs them automatically, and new changes must not
be added here.** Schema changes now go through Alembic; see [docs/DATABASE.md](../../docs/DATABASE.md).

## Adopting a pre-Alembic database

1. Apply whichever of 001–008 the database hasn't had yet, in order (each is wrapped in
   `BEGIN`/`COMMIT` and most are safe to re-run).
2. Mark it as being at the Alembic baseline, without running any DDL:
   `alembic stamp 13385b7b4ede`
3. Upgrade to the latest revision and confirm the schema matches the models:
   `alembic upgrade head` then `alembic check`

If `alembic check` reports differences, fix them with a new reviewed migration rather than by
editing these files. A local development database is simpler to reset: `python scripts/reset_db.py`.
