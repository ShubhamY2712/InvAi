-- 002_money_decimal.sql
-- Money columns: double precision -> NUMERIC(12,2). Matches the Money fields in main.py.
-- Run after 001_fifo_groundwork.sql. Values are rounded to 2 places, half away from zero.

BEGIN;

ALTER TABLE product        ALTER COLUMN price       TYPE NUMERIC(12,2) USING round(price::NUMERIC, 2);
ALTER TABLE sales          ALTER COLUMN total_price TYPE NUMERIC(12,2) USING round(total_price::NUMERIC, 2);
ALTER TABLE purchase_order ALTER COLUMN unit_cost   TYPE NUMERIC(12,2) USING round(unit_cost::NUMERIC, 2);
ALTER TABLE purchase_order ALTER COLUMN total_cost  TYPE NUMERIC(12,2) USING round(total_cost::NUMERIC, 2);

COMMIT;
