-- 005_stock_movement_ledger.sql
-- Append-only stock movement ledger. Matches StockMovement / MovementReason in main.py.
-- Run after 004_sales_business_timestamp_index.sql.
--
-- Safe to run more than once, and in either order relative to starting the new code: the app's startup
-- create_all may already have created the table and written entries. The backfill only adds what is missing.

BEGIN;

-- 1. Enum (labels are the member NAMES, like the other enums) and table
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'movementreason') THEN
        CREATE TYPE movementreason AS ENUM
            ('OPENING', 'PURCHASE_RECEIPT', 'SALE', 'AUDIT_INCREASE', 'AUDIT_DECREASE', 'EXPIRY_DISPOSAL');
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS stock_movement (
    id                     SERIAL NOT NULL,
    business_id            VARCHAR NOT NULL,
    product_id             INTEGER NOT NULL,
    batch_id               INTEGER NOT NULL,
    quantity_change        NUMERIC(12,3) NOT NULL,
    reason                 movementreason NOT NULL,
    sale_id                INTEGER,
    po_id                  INTEGER,
    user_id                INTEGER,
    note                   VARCHAR,
    product_quantity_after NUMERIC(12,3) NOT NULL,
    created_at             TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY (business_id) REFERENCES businessprofile (id),
    FOREIGN KEY (product_id)  REFERENCES product (id),
    FOREIGN KEY (batch_id)    REFERENCES product_batch (id),
    FOREIGN KEY (sale_id)     REFERENCES sales (id),
    FOREIGN KEY (po_id)       REFERENCES purchase_order (id),
    FOREIGN KEY (user_id)     REFERENCES users (id)
);
CREATE INDEX IF NOT EXISTS ix_stock_movement_business_product_created
    ON stock_movement (business_id, product_id, created_at);

-- 2. Guard: the ledger must end up summing to Product.quantity, so stock and batches must already agree
DO $$
DECLARE
    bad TEXT;
BEGIN
    SELECT string_agg(format('product %s: quantity %s, batch total %s', p.id, p.quantity, COALESCE(t.total, 0)), '; ' ORDER BY p.id)
      INTO bad
      FROM product p
      LEFT JOIN (SELECT product_id, SUM(quantity) AS total FROM product_batch GROUP BY product_id) t ON t.product_id = p.id
     WHERE p.quantity <> COALESCE(t.total, 0);

    IF bad IS NOT NULL THEN
        RAISE EXCEPTION 'Aborted, nothing changed: stock differs from batches (run a manual audit first) -> %', bad;
    END IF;
END $$;

-- 3. Backfill: for each batch, an opening entry for whatever its existing entries don't explain yet.
-- With no ledger yet, that is the stock on hand. product_quantity_after runs per product and ends at Product.quantity.
INSERT INTO stock_movement (business_id, product_id, batch_id, quantity_change, reason, product_quantity_after, note, created_at)
SELECT gap.business_id,
       gap.product_id,
       gap.batch_id,
       gap.diff,
       'OPENING',
       p.quantity - SUM(gap.diff) OVER (PARTITION BY gap.product_id)
                  + SUM(gap.diff) OVER (PARTITION BY gap.product_id ORDER BY gap.batch_id),
       'Ledger backfill: stock on hand before the ledger existed',
       (now() AT TIME ZONE 'UTC')
  FROM (
      SELECT b.business_id, b.product_id, b.id AS batch_id,
             b.quantity - COALESCE((SELECT SUM(m.quantity_change) FROM stock_movement m WHERE m.batch_id = b.id), 0) AS diff
        FROM product_batch b
  ) gap
  JOIN product p ON p.id = gap.product_id
 WHERE gap.diff <> 0
 ORDER BY gap.product_id, gap.batch_id;

COMMIT;
