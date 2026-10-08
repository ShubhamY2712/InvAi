-- 006_supplier_scorecard.sql
-- Supplier scorecard columns on purchase_order. Matches PurchaseOrder in main.py.
-- Run after 005_stock_movement_ledger.sql. Safe to run more than once.

BEGIN;

ALTER TABLE purchase_order ADD COLUMN IF NOT EXISTS expected_delivery_date DATE;
ALTER TABLE purchase_order ADD COLUMN IF NOT EXISTS received_quantity NUMERIC(12,3);
ALTER TABLE purchase_order ADD COLUMN IF NOT EXISTS rejected_quantity NUMERIC(12,3);

-- POs stocked before this change always took the full order with nothing rejected,
-- and stocking never recorded stocked_at: use the delivery time, the closest thing on record.
-- Without it, old POs would never fall inside any scorecard date range.
UPDATE purchase_order
   SET received_quantity = COALESCE(received_quantity, quantity),
       rejected_quantity = COALESCE(rejected_quantity, 0),
       stocked_at        = COALESCE(stocked_at, delivered_at)
 WHERE status = 'STOCKED'
   AND (received_quantity IS NULL OR rejected_quantity IS NULL OR stocked_at IS NULL);

COMMIT;
