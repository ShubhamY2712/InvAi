import pytest
from sqlmodel import Session, select

import main
from main import Product, ProductBatch, StockUnit
from conftest import day


def batches_for(engine, product_id):
    with Session(engine) as session:
        return session.exec(select(ProductBatch).where(ProductBatch.product_id == product_id)).all()


def test_initial_quantity_creates_opening_batch_with_expiry(api, engine, in_sync):
    status, body = api("POST", "/products/", {"name": "Milk", "sku": "M1", "price": 1.2, "quantity": 10,
                                              "expiry_date": str(day(30))})
    assert status == 200
    product_id = body["product"]["id"]
    [batch] = batches_for(engine, product_id)
    assert batch.quantity == 10
    assert batch.expiry_date == day(30)
    assert batch.po_id is None
    assert batch.received_date == day(0)  # today() in Asia/Kolkata, pinned by the fixed_today fixture
    assert in_sync(product_id) == 10


def test_opening_batch_without_expiry_never_expires(api, engine):
    status, body = api("POST", "/products/", {"name": "Salt", "sku": "S1", "price": 1, "quantity": 5})
    assert status == 200
    [batch] = batches_for(engine, body["product"]["id"])
    assert batch.expiry_date is None


def test_zero_quantity_creates_no_batch(api, engine):
    status, body = api("POST", "/products/", {"name": "Empty", "sku": "E1", "price": 1, "quantity": 0})
    assert status == 200
    assert batches_for(engine, body["product"]["id"]) == []


def test_unit_defaults_to_piece(api):
    status, body = api("POST", "/products/", {"name": "Soap", "sku": "S2", "price": 1})
    assert status == 200
    assert body["product"]["unit"] == "piece"


def test_unit_can_be_set(api, engine):
    status, body = api("POST", "/products/", {"name": "Rice", "sku": "R1", "price": 1, "unit": "kg"})
    assert status == 200
    assert body["product"]["unit"] == "kg"
    with Session(engine) as session:
        assert session.get(Product, body["product"]["id"]).unit == StockUnit.KG


def test_unknown_unit_rejected(api):
    status, _ = api("POST", "/products/", {"name": "Rice", "sku": "R1", "price": 1, "unit": "bushel"})
    assert status == 422


def test_price_with_three_decimals_rejected(api):
    status, _ = api("POST", "/products/", {"name": "Bad", "sku": "B1", "price": 2.555, "quantity": 1})
    assert status == 422


def test_price_and_quantity_are_json_numbers(api):
    api("POST", "/products/", {"name": "Milk", "sku": "M1", "price": 1.2, "quantity": 3})
    status, body = api("GET", "/products/")
    assert status == 200
    product = body["inventory"][0]
    assert isinstance(product["price"], float) and product["price"] == 1.2
    assert isinstance(product["quantity"], float) and product["quantity"] == 3


EXPIRY_MESSAGE = ("expiry_date must be after today (2031-03-10). "
                  "Stock expiring today or earlier is already expired and can't be sold.")


@pytest.mark.parametrize("offset", [0, -1, -365])
def test_expiry_today_or_earlier_rejected(api, engine, offset):
    status, body = api("POST", "/products/", {"name": "Milk", "sku": "M1", "price": 1, "quantity": 5,
                                              "expiry_date": str(day(offset))})
    assert status == 422
    assert body["detail"] == EXPIRY_MESSAGE
    with Session(engine) as session:
        assert session.exec(select(Product)).all() == []  # nothing was created


def test_expiry_rejected_even_without_quantity(api):
    status, body = api("POST", "/products/", {"name": "Milk", "sku": "M1", "price": 1, "expiry_date": str(day(0))})
    assert status == 422
    assert body["detail"] == EXPIRY_MESSAGE


def test_expiry_tomorrow_accepted(api, engine):
    status, body = api("POST", "/products/", {"name": "Milk", "sku": "M1", "price": 1, "quantity": 5,
                                              "expiry_date": str(day(1))})
    assert status == 200
    [batch] = batches_for(engine, body["product"]["id"])
    assert batch.expiry_date == day(1)
