-- 008_po_cancellation.sql
-- Purchase order cancellation. Matches PurchaseOrder.cancelled_at / cancellation_reason in main.py.
-- status is a plain text column, so 'CANCELLED' needs no type change.
-- Run after 007_product_is_active.sql. Safe to run more than once.

BEGIN;

ALTER TABLE purchase_order ADD COLUMN IF NOT EXISTS cancelled_at TIMESTAMP WITHOUT TIME ZONE;
ALTER TABLE purchase_order ADD COLUMN IF NOT EXISTS cancellation_reason VARCHAR;

COMMIT;
