"""Database-enforced integrity, on throwaway Postgres databases built by the real migrations: the append-only
ledger triggers, the new foreign keys, the migration's orphan check, downgrade/upgrade, and reset_db.py.
Needs a local Postgres server (the one in .env); skipped otherwise."""
import os
import subprocess
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, IntegrityError

import main
from conftest import asgi_request, day, local_postgres_url

ROOT = Path(__file__).resolve().parent.parent
PREVIOUS, HEAD = "7086dfa92b45", "20ef20ef2e0a"
NEW_FKS = {"sales_product_id_fkey", "sales_user_id_fkey", "purchase_order_supplier_id_fkey", "purchase_order_product_id_fkey"}
TRIGGERS = {"stock_movement_no_update_or_delete", "stock_movement_no_truncate"}

SERVER = local_postgres_url()
pytestmark = pytest.mark.skipif(SERVER is None, reason="no local Postgres server in .env")


def run(args, url):
    env = {**os.environ, "DATABASE_URL": url.render_as_string(hide_password=False)}
    return subprocess.run([sys.executable, *args], cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)


def alembic(url, *args, check=True):
    result = run(["-m", "alembic", *args], url)
    if check:
        assert result.returncode == 0, result.stderr[-2000:]
    return result


@contextmanager
def scratch_database(revision="head"):
    base = make_url(SERVER)
    admin = create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")
    name = f"invai_test_{uuid.uuid4().hex[:8]}"
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = base.set(database=name)
    engine = create_engine(url)
    try:
        if revision:
            alembic(url, "upgrade", revision)
        yield url, engine
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def seed(engine):
    """A business, owner, supplier, product and batch, plus one ledger entry."""
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO businessprofile VALUES ('1111','Shop','RETAIL')"))
        conn.execute(text("INSERT INTO users (id, username, email, hashed_password, role, business_id) VALUES (1,'o','o@x','h','OWNER','1111')"))
        conn.execute(text("INSERT INTO suppliers (id, name, business_id) VALUES (1,'Vendor','1111')"))
        conn.execute(text("INSERT INTO product (id, name, sku, price, quantity, unit, business_id, min_stock_level, is_active) VALUES (1,'Rice','R',2,5,'KG','1111',10,true)"))
        conn.execute(text("INSERT INTO product_batch (id, product_id, business_id, quantity, received_date) VALUES (1,1,'1111',5,'2031-03-01')"))
        conn.execute(text("""INSERT INTO stock_movement (business_id, product_id, batch_id, quantity_change, reason, product_quantity_after, created_at)
                             VALUES ('1111',1,1,5,'OPENING',5,now())"""))


def catalog(engine):
    with engine.connect() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        fks = set(conn.execute(text("SELECT conname FROM pg_constraint WHERE contype = 'f'")).scalars())
        triggers = set(conn.execute(text("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal")).scalars())
        functions = conn.execute(text("SELECT count(*) FROM pg_proc WHERE proname = 'stock_movement_append_only'")).scalar()
    return version, fks, triggers, functions


@pytest.fixture(scope="module")
def head_db():
    with scratch_database() as (url, engine):
        seed(engine)
        yield url, engine


# --- append-only ledger ---

@pytest.mark.parametrize("statement, operation", [
    ("UPDATE stock_movement SET note = 'edited'", "UPDATE"),
    ("DELETE FROM stock_movement", "DELETE"),
    ("TRUNCATE stock_movement", "TRUNCATE"),
])
def test_ledger_rejects_changes(head_db, statement, operation):
    _, engine = head_db
    with pytest.raises(DBAPIError, match=f"stock_movement is an append-only ledger: {operation} is not allowed"):
        with engine.begin() as conn:
            conn.execute(text(statement))
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*), max(note) FROM stock_movement")).one() == (1, None)


def test_ledger_still_accepts_inserts(head_db):
    _, engine = head_db
    with engine.connect() as conn:
        transaction = conn.begin()
        conn.execute(text("""INSERT INTO stock_movement (business_id, product_id, batch_id, quantity_change, reason, product_quantity_after, created_at)
                             VALUES ('1111',1,1,-1,'SALE',4,now())"""))
        assert conn.execute(text("SELECT count(*) FROM stock_movement")).scalar() == 2
        transaction.rollback()  # keep the module's database as it was


# --- foreign keys ---

@pytest.mark.parametrize("statement, constraint", [
    ("INSERT INTO sales (product_id, user_id, business_id, quantity, total_price, timestamp) VALUES (999999,1,'1111',1,1,now())", "sales_product_id_fkey"),
    ("INSERT INTO sales (product_id, user_id, business_id, quantity, total_price, timestamp) VALUES (1,999999,'1111',1,1,now())", "sales_user_id_fkey"),
    ("INSERT INTO purchase_order (supplier_id, product_id, business_id, quantity, unit_cost, total_cost, status, timestamp) VALUES (999999,1,'1111',1,1,1,'PENDING',now())", "purchase_order_supplier_id_fkey"),
    ("INSERT INTO purchase_order (supplier_id, product_id, business_id, quantity, unit_cost, total_cost, status, timestamp) VALUES (1,999999,'1111',1,1,1,'PENDING',now())", "purchase_order_product_id_fkey"),
])
def test_new_foreign_keys_reject_orphans(head_db, statement, constraint):
    _, engine = head_db
    with pytest.raises(IntegrityError, match=constraint):
        with engine.begin() as conn:
            conn.execute(text(statement))


def test_head_has_every_new_constraint_and_trigger(head_db):
    version, fks, triggers, functions = catalog(head_db[1])
    assert version == HEAD and NEW_FKS <= fks and triggers == TRIGGERS and functions == 1


# --- the migration's orphan check ---

def test_upgrade_aborts_cleanly_when_rows_point_at_nothing():
    with scratch_database(PREVIOUS) as (url, engine):
        seed(engine)
        with engine.begin() as conn:
            for _ in range(2):
                conn.execute(text("INSERT INTO sales (product_id, user_id, business_id, quantity, total_price, timestamp) VALUES (999999,1,'1111',1,1,now())"))
            conn.execute(text("INSERT INTO purchase_order (supplier_id, product_id, business_id, quantity, unit_cost, total_cost, status, timestamp) VALUES (999999,1,'1111',1,1,1,'PENDING',now())"))
        result = alembic(url, "upgrade", "head", check=False)
        assert result.returncode != 0
        assert "Cannot add foreign keys; nothing was changed." in result.stderr
        assert "sales.product_id: 2 row(s) point at no product.id" in result.stderr
        assert "purchase_order.supplier_id: 1 row(s) point at no suppliers.id" in result.stderr
        version, fks, triggers, functions = catalog(engine)
        assert version == PREVIOUS and not (NEW_FKS & fks) and not triggers and functions == 0


# --- downgrade, upgrade, reset ---

def test_downgrade_then_upgrade():
    with scratch_database() as (url, engine):
        seed(engine)
        alembic(url, "downgrade", "-1")
        version, fks, triggers, functions = catalog(engine)
        assert version == PREVIOUS and not (NEW_FKS & fks) and not triggers and functions == 0
        with engine.begin() as conn:  # no triggers now: the ledger can be changed again
            conn.execute(text("UPDATE stock_movement SET note = 'while downgraded'"))
        alembic(url, "upgrade", "head")
        version, fks, triggers, functions = catalog(engine)
        assert version == HEAD and NEW_FKS <= fks and triggers == TRIGGERS and functions == 1
        with engine.connect() as conn:
            assert conn.execute(text("SELECT note FROM stock_movement")).scalar() == "while downgraded"  # data kept


def test_reset_db_works_with_the_triggers_in_place():
    with scratch_database() as (url, engine):
        seed(engine)
        result = run(["scripts/reset_db.py"], url)
        assert result.returncode == 0, result.stderr[-2000:]
        version, fks, triggers, functions = catalog(engine)
        assert version == HEAD and NEW_FKS <= fks and triggers == TRIGGERS and functions == 1
        with engine.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM stock_movement")).scalar() == 0


# --- the app's normal flows still work against the protected ledger ---

def test_app_flows_on_postgres(monkeypatch):
    with scratch_database() as (url, engine):
        monkeypatch.setattr(main, "engine", engine)
        status, onboard = asgi_request("POST", "/onboard-business/", {
            "business_name": "PG Shop", "category": main.BusinessCategory.RETAIL.value,
            "owner_username": "pgowner", "email": "pg@example.com", "password": "pw-123456"})
        assert status == 200
        token = main.create_access_token({"sub": str(onboard["owner_user_id"]), "business_id": onboard["business_id"], "role": "Owner"})
        api = lambda method, path, body=None: asgi_request(method, path, body, token)

        product = api("POST", "/products/", {"name": "Rice", "sku": "R", "price": 2, "quantity": 10, "unit": "kg",
                                             "expiry_date": str(day(5))})[1]["product"]["id"]
        supplier = api("POST", "/suppliers/", {"name": "Vendor"})[1]["supplier_id"]
        po = api("POST", "/purchase-orders/", {"supplier_id": supplier, "product_id": product, "quantity": 6, "unit_cost": 1})[1]["po_id"]
        steps = [
            api("PUT", f"/purchase-orders/{po}/deliver"),
            api("PUT", f"/purchase-orders/{po}/stock?received_quantity=6&rejected_quantity=1"),
            api("POST", "/checkout/", {"product_id": product, "quantity": 12.5}),
            api("PUT", f"/products/{product}/manual-audit?new_quantity=2&note=count"),
            api("PUT", f"/products/{product}/manual-audit?new_quantity=3"),
        ]
        cancelled_po = api("POST", "/purchase-orders/", {"supplier_id": supplier, "product_id": product, "quantity": 1, "unit_cost": 1})[1]["po_id"]
        steps.append(api("POST", f"/purchase-orders/{cancelled_po}/cancel?reason=no%20longer%20needed"))

        # expired stock for the daily check (INSERT is allowed on the ledger)
        with engine.begin() as conn:
            batch = conn.execute(text("""INSERT INTO product_batch (product_id, business_id, quantity, received_date, expiry_date)
                                         VALUES (:p, :b, 1.5, :r, :e) RETURNING id"""),
                                 {"p": product, "b": onboard["business_id"], "r": day(-9), "e": day(-1)}).scalar()
            quantity = conn.execute(text("UPDATE product SET quantity = quantity + 1.5 WHERE id = :p RETURNING quantity"), {"p": product}).scalar()
            conn.execute(text("""INSERT INTO stock_movement (business_id, product_id, batch_id, quantity_change, reason, product_quantity_after, created_at)
                                 VALUES (:b, :p, :batch, 1.5, 'OPENING', :q, now())"""),
                         {"b": onboard["business_id"], "p": product, "batch": batch, "q": quantity})
        steps.append(api("POST", "/system/daily-check"))
        steps.append(api("PUT", f"/products/{product}/manual-audit?new_quantity=0"))
        steps.append(api("DELETE", f"/products/{product}"))
        assert [s[0] for s in steps] == [200] * len(steps), [s for s in steps if s[0] != 200]

        with engine.connect() as conn:
            drift = conn.execute(text("""
                SELECT p.id FROM product p
                LEFT JOIN (SELECT product_id, SUM(quantity) t FROM product_batch GROUP BY 1) b ON b.product_id = p.id
                LEFT JOIN (SELECT product_id, SUM(quantity_change) t FROM stock_movement GROUP BY 1) m ON m.product_id = p.id
                WHERE p.quantity <> COALESCE(b.t, 0) OR p.quantity <> COALESCE(m.t, 0)""")).all()
            reasons = conn.execute(text("SELECT reason::text FROM stock_movement ORDER BY id")).scalars().all()
        assert drift == []
        # The final audit to 0 empties two batches (the rest of the receipt and the earlier adjustment): one entry each
        assert reasons == ["OPENING", "PURCHASE_RECEIPT", "SALE", "SALE", "AUDIT_DECREASE", "AUDIT_INCREASE",
                           "OPENING", "EXPIRY_DISPOSAL", "AUDIT_DECREASE", "AUDIT_DECREASE"]
