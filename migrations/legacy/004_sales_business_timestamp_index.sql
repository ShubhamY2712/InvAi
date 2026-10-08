-- 004_sales_business_timestamp_index.sql
-- Composite index for the report queries, which filter one business's sales by time range.
-- It starts with business_id, so it also serves plain business_id lookups and the foreign key check,
-- which makes the old single-column ix_sales_business_id redundant.
-- Matches Sale in main.py. Run after 003_business_ids_and_fks.sql.
--
-- On a large, busy sales table, run these instead, outside a transaction, so writes aren't blocked:
--   CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_sales_business_id_timestamp ON sales (business_id, "timestamp");
--   DROP INDEX CONCURRENTLY IF EXISTS ix_sales_business_id;

BEGIN;

-- Create the new index first, so business_id is never left without one
CREATE INDEX IF NOT EXISTS ix_sales_business_id_timestamp ON sales (business_id, "timestamp");
DROP INDEX IF EXISTS ix_sales_business_id;

COMMIT;
