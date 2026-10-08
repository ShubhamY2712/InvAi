-- 003_business_ids_and_fks.sql
-- Business IDs and user IDs: foreign keys from every business_id column to businessprofile.id,
-- an index on users.business_id, and the users id sequence moved past old {business_id}001 owner IDs.
-- Matches main.py. Run after 002_money_decimal.sql. Existing 4-digit business IDs stay valid;
-- new businesses get 8-character IDs from the app.

BEGIN;

-- 1. Guard: abort everything if any row points at a business that doesn't exist
DO $$
DECLARE
    bad TEXT;
BEGIN
    SELECT string_agg(format('%s: %s row(s) with unknown business_id %L', t, n, business_id), '; ')
      INTO bad
      FROM (
          SELECT 'sales' AS t, business_id, count(*) AS n FROM sales x
           WHERE NOT EXISTS (SELECT 1 FROM businessprofile b WHERE b.id = x.business_id) GROUP BY business_id
          UNION ALL
          SELECT 'suppliers', business_id, count(*) FROM suppliers x
           WHERE NOT EXISTS (SELECT 1 FROM businessprofile b WHERE b.id = x.business_id) GROUP BY business_id
          UNION ALL
          SELECT 'purchase_order', business_id, count(*) FROM purchase_order x
           WHERE NOT EXISTS (SELECT 1 FROM businessprofile b WHERE b.id = x.business_id) GROUP BY business_id
          UNION ALL
          SELECT 'product_batch', business_id, count(*) FROM product_batch x
           WHERE NOT EXISTS (SELECT 1 FROM businessprofile b WHERE b.id = x.business_id) GROUP BY business_id
          UNION ALL
          SELECT 'sale_batch_allocation', business_id, count(*) FROM sale_batch_allocation x
           WHERE NOT EXISTS (SELECT 1 FROM businessprofile b WHERE b.id = x.business_id) GROUP BY business_id
      ) orphans;

    IF bad IS NOT NULL THEN
        RAISE EXCEPTION 'Aborted, nothing changed: %', bad;
    END IF;
END $$;

-- 2. Foreign keys, named as Postgres names them when create_all builds a fresh database.
-- Each is skipped if it already exists, so this is safe on a database created by the new code.
DO $$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['sales', 'suppliers', 'purchase_order', 'product_batch', 'sale_batch_allocation'] LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = t || '_business_id_fkey') THEN
            EXECUTE format('ALTER TABLE %I ADD CONSTRAINT %I FOREIGN KEY (business_id) REFERENCES businessprofile (id)',
                           t, t || '_business_id_fkey');
        END IF;
    END LOOP;
END $$;

-- 3. users.business_id is filtered on (employees per business) but had no index
CREATE INDEX IF NOT EXISTS ix_users_business_id ON users (business_id);

-- 4. User IDs now always come from the sequence. Old owners were inserted with explicit
-- {business_id}001 IDs (e.g. 7931001) that never advanced it, so move it past the highest ID in use.
SELECT setval(pg_get_serial_sequence('users', 'id'),
              COALESCE((SELECT max(id) FROM users), 1),
              (SELECT max(id) FROM users) IS NOT NULL);

COMMIT;
