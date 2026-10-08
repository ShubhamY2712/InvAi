-- 007_product_is_active.sql
-- Products are deactivated instead of deleted. Matches Product.is_active in main.py.
-- Run after 006_supplier_scorecard.sql. Safe to run more than once; every existing product starts active.

BEGIN;

ALTER TABLE product ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;

COMMIT;
