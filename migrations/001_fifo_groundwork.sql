-- 001_fifo_groundwork.sql
-- FIFO step 1: schema and data groundwork. Matches the models in main.py.
--
-- Run this BEFORE starting the updated app: the new code selects product.unit,
-- which does not exist until this migration has run.
-- Everything runs in one transaction; any error rolls the whole file back.

BEGIN;

-- 1. Stock quantities: integer -> NUMERIC(12,3)
ALTER TABLE product        ALTER COLUMN quantity        TYPE NUMERIC(12,3) USING quantity::NUMERIC(12,3);
ALTER TABLE product        ALTER COLUMN min_stock_level TYPE NUMERIC(12,3) USING min_stock_level::NUMERIC(12,3);
ALTER TABLE product_batch  ALTER COLUMN quantity        TYPE NUMERIC(12,3) USING quantity::NUMERIC(12,3);
ALTER TABLE sales          ALTER COLUMN quantity        TYPE NUMERIC(12,3) USING quantity::NUMERIC(12,3);
ALTER TABLE purchase_order ALTER COLUMN quantity        TYPE NUMERIC(12,3) USING quantity::NUMERIC(12,3);

-- min_stock_level was added by a raw ALTER and allowed NULL; the model requires a value
UPDATE product SET min_stock_level = 10 WHERE min_stock_level IS NULL;
ALTER TABLE product ALTER COLUMN min_stock_level SET DEFAULT 10;
ALTER TABLE product ALTER COLUMN min_stock_level SET NOT NULL;

-- 2. Batches without an expiry date (opening stock) or without a purchase order
ALTER TABLE product_batch ALTER COLUMN expiry_date DROP NOT NULL;
ALTER TABLE product_batch ALTER COLUMN po_id       DROP NOT NULL;  -- already nullable; kept for clarity

-- 3. Product unit. Labels are the enum member NAMES, like the existing userrole/businesscategory types.
CREATE TYPE stockunit AS ENUM ('PIECE', 'KG', 'G', 'LITRE', 'ML');
ALTER TABLE product ADD COLUMN unit stockunit NOT NULL DEFAULT 'PIECE';

-- 4. Which batches each sale drew from.
-- IF NOT EXISTS: the app's startup create_all may already have created this table.
CREATE TABLE IF NOT EXISTS sale_batch_allocation (
    id          SERIAL NOT NULL,
    sale_id     INTEGER NOT NULL,
    batch_id    INTEGER NOT NULL,
    quantity    NUMERIC(12,3) NOT NULL,
    business_id VARCHAR NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY (sale_id)  REFERENCES sales (id),
    FOREIGN KEY (batch_id) REFERENCES product_batch (id)
);
CREATE INDEX IF NOT EXISTS ix_sale_batch_allocation_sale_id     ON sale_batch_allocation (sale_id);
CREATE INDEX IF NOT EXISTS ix_sale_batch_allocation_batch_id    ON sale_batch_allocation (batch_id);
CREATE INDEX IF NOT EXISTS ix_sale_batch_allocation_business_id ON sale_batch_allocation (business_id);

-- 5. Guard: abort everything if any product has more stock in batches than on the product itself.
DO $$
DECLARE
    bad TEXT;
BEGIN
    SELECT string_agg(format('product %s (business %s): quantity %s, batch total %s',
                             p.id, p.business_id, p.quantity, t.total), '; ' ORDER BY p.id)
      INTO bad
      FROM product p
      JOIN (SELECT product_id, SUM(quantity) AS total FROM product_batch GROUP BY product_id) t
        ON t.product_id = p.id
     WHERE t.total > p.quantity;

    IF bad IS NOT NULL THEN
        RAISE EXCEPTION 'Backfill aborted: batch totals exceed product quantity -> %', bad;
    END IF;
END $$;

-- 6. Backfill: one opening batch per product for stock not covered by any batch.
-- No expiry, no purchase order; received_date is the day the migration runs.
INSERT INTO product_batch (product_id, po_id, business_id, quantity, received_date, expiry_date)
SELECT p.id, NULL, p.business_id, p.quantity - COALESCE(t.total, 0), CURRENT_DATE, NULL
  FROM product p
  LEFT JOIN (SELECT product_id, SUM(quantity) AS total FROM product_batch GROUP BY product_id) t
    ON t.product_id = p.id
 WHERE p.quantity > COALESCE(t.total, 0);

COMMIT;
