from decimal import Decimal

import pytest
from sqlmodel import Session

from app.models import ProductBatch, StockUnit
from conftest import OTHER_BUSINESS_ID, day


def audit(api, product_id, new_quantity, expiry_date=None):
    path = f"/products/{product_id}/manual-audit?new_quantity={new_quantity}"
    if expiry_date is not None:
        path += f"&expiry_date={expiry_date}"
    return api("PUT", path)


def reduced(body):
    return [(b["batch_id"], b["quantity_removed"], b["remaining"]) for b in body["batches_reduced"]]


@pytest.fixture
def milk(make_product):
    """8 litres: 1.5 expired yesterday, 0.5 expiring today, 2 expiring in 5 days, 4 with no expiry."""
    return make_product("Milk", StockUnit.LITRE, "1.00", [("4", None, -20), ("2", 5, -10), ("1.5", -1, -30), ("0.5", 0, -30)])


# --- validation ---

@pytest.mark.parametrize("value", ["-1", "0.0001", "abc", "1000000000"])
def test_invalid_count_rejected(api, milk, in_sync, value):
    product_id, _ = milk
    assert audit(api, product_id, value)[0] == 422
    assert in_sync(product_id) == 8


def test_missing_count_rejected(api, milk):
    product_id, _ = milk
    assert api("PUT", f"/products/{product_id}/manual-audit")[0] == 422


def test_fractional_count_rejected_for_piece_products(api, make_product, in_sync):
    product_id, _ = make_product("Eggs", StockUnit.PIECE, "0.50", [("12", 7, -1)])
    status, body = audit(api, product_id, "10.5")
    assert status == 422
    assert body["detail"] == "Eggs is counted by the piece, so new_quantity must be a whole number."
    assert in_sync(product_id) == 12


def test_whole_count_accepted_for_piece_products(api, make_product, in_sync):
    product_id, _ = make_product("Eggs", StockUnit.PIECE, "0.50", [("12", 7, -1)])
    assert audit(api, product_id, "10.000")[0] == 200
    assert in_sync(product_id) == 10


def test_other_business_product_404(api, make_product):
    product_id, _ = make_product("Theirs", StockUnit.KG, "1.00", [("5", None, 0)], business_id=OTHER_BUSINESS_ID)
    assert audit(api, product_id, 1)[0] == 404


# --- lower count ---

def test_lower_count_removes_expired_first_then_fifo(api, milk, in_sync):
    product_id, [no_expiry, fresh, expired, today_batch] = milk
    status, body = audit(api, product_id, "5")
    assert status == 200
    assert reduced(body) == [(expired, 1.5, 0), (today_batch, 0.5, 0), (fresh, 1, 1)]
    assert body["batch_created"] is None
    assert (body["previous_qty"], body["new_qty"], body["unit"]) == (8, 5, "litre")
    assert in_sync(product_id) == 5


def test_count_of_zero_empties_every_batch(api, milk, stock, in_sync):
    product_id, [no_expiry, fresh, expired, today_batch] = milk
    status, body = audit(api, product_id, "0")
    assert status == 200
    assert [b[0] for b in reduced(body)] == [expired, today_batch, fresh, no_expiry]
    assert in_sync(product_id) == 0
    assert set(stock(product_id)[2].values()) == {Decimal("0")}


def test_fractional_count_for_kg_product(api, make_product, in_sync):
    product_id, [batch] = make_product("Rice", StockUnit.KG, "1.00", [("5", 9, -1)])
    status, body = audit(api, product_id, "3.25")
    assert status == 200
    assert reduced(body) == [(batch, 1.75, 3.25)]
    assert in_sync(product_id) == Decimal("3.25")


# --- higher count ---

def test_higher_count_creates_adjustment_batch(api, engine, milk, in_sync):
    product_id, _ = milk
    status, body = audit(api, product_id, "10.5", expiry_date=day(30))
    assert status == 200
    assert body["batches_reduced"] == []
    created = body["batch_created"]
    assert created["quantity"] == 2.5 and created["expiry_date"] == str(day(30))
    with Session(engine) as session:
        batch = session.get(ProductBatch, created["batch_id"])
    assert batch.po_id is None and batch.received_date == day(0) and batch.product_id == product_id
    assert in_sync(product_id) == Decimal("10.5")


def test_adjustment_batch_without_expiry_never_expires(api, milk, in_sync):
    product_id, _ = milk
    status, body = audit(api, product_id, "9")
    assert status == 200
    assert body["batch_created"]["expiry_date"] is None
    assert in_sync(product_id) == 9


@pytest.mark.parametrize("offset", [0, -1])
def test_adjustment_expiry_must_be_after_today(api, milk, stock, in_sync, offset):
    product_id, _ = milk
    before = stock(product_id)
    status, body = audit(api, product_id, "10", expiry_date=day(offset))
    assert status == 422
    assert body["detail"].startswith("expiry_date must be after today")
    assert stock(product_id) == before
    assert in_sync(product_id) == 8


def test_equal_count_changes_nothing(api, milk, stock, in_sync):
    product_id, _ = milk
    before = stock(product_id)
    status, body = audit(api, product_id, "8")
    assert status == 200
    assert body["batches_reduced"] == [] and body["batch_created"] is None
    assert stock(product_id) == before
    assert in_sync(product_id) == 8


# --- repairing drift ---

def test_audit_repairs_stock_lower_than_batches(api, milk, set_product_quantity, in_sync):
    product_id, [no_expiry, fresh, expired, today_batch] = milk
    set_product_quantity(product_id, "3")  # batches still hold 8
    status, body = audit(api, product_id, "6")
    assert status == 200
    assert body["previous_qty"] == 3
    assert reduced(body) == [(expired, 1.5, 0), (today_batch, 0.5, 0)]
    assert in_sync(product_id) == 6


def test_audit_repairs_stock_higher_than_batches(api, milk, set_product_quantity, in_sync):
    product_id, _ = milk
    set_product_quantity(product_id, "10")  # batches hold 8
    status, body = audit(api, product_id, "9")
    assert status == 200
    assert body["previous_qty"] == 10
    assert body["batch_created"]["quantity"] == 1
    assert in_sync(product_id) == 9
