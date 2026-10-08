import pytest
from sqlmodel import Session

from main import Product, StockUnit
from conftest import OTHER_BUSINESS_ID

STOCK_MESSAGE = "Stock can't be changed here. Use purchase-order stocking, checkout, or a manual audit."


def patch(api, product_id, body):
    return api("PATCH", f"/products/{product_id}", body)


def unit_of(engine, product_id):
    with Session(engine) as session:
        return session.get(Product, product_id).unit


@pytest.mark.parametrize("quantity", [5, 0, None, 1.5, "abc"])
def test_quantity_change_rejected(api, make_product, stock, in_sync, quantity):
    product_id, _ = make_product("Rice", StockUnit.KG, "1.00", [("5", None, 0)])
    status, body = patch(api, product_id, {"quantity": quantity, "price": 9})
    assert status == 422
    assert body["detail"] == STOCK_MESSAGE
    assert in_sync(product_id) == 5


def test_price_and_description_still_update(api, make_product, in_sync):
    product_id, _ = make_product("Rice", StockUnit.KG, "1.00", [("5", None, 0)])
    status, body = patch(api, product_id, {"price": 2.5, "description": "Basmati"})
    assert status == 200
    assert body["product"]["price"] == 2.5
    assert body["product"]["description"] == "Basmati"
    assert body["product"]["quantity"] == 5
    assert in_sync(product_id) == 5


def test_unit_change_allowed_without_stock_or_batches(api, engine, make_product, in_sync):
    product_id, _ = make_product("Rice", StockUnit.PIECE, "1.00", [])
    status, body = patch(api, product_id, {"unit": "kg"})
    assert status == 200
    assert body["product"]["unit"] == "kg"
    assert unit_of(engine, product_id) == StockUnit.KG
    assert in_sync(product_id) == 0


def test_unit_change_rejected_with_stock(api, engine, make_product, in_sync):
    product_id, _ = make_product("Rice", StockUnit.PIECE, "1.00", [("5", None, 0)])
    status, body = patch(api, product_id, {"unit": "kg", "price": 9})
    assert status == 422
    assert body["detail"] == ("Can't change the unit of Rice from piece to kg: it has stock or batch history, "
                              "and those quantities would change meaning.")
    assert unit_of(engine, product_id) == StockUnit.PIECE
    assert in_sync(product_id) == 5


def test_unit_change_rejected_with_only_empty_batches(api, engine, make_product, in_sync):
    product_id, _ = make_product("Rice", StockUnit.PIECE, "1.00", [("0", 3, -1)])
    status, _ = patch(api, product_id, {"unit": "kg"})
    assert status == 422
    assert unit_of(engine, product_id) == StockUnit.PIECE
    assert in_sync(product_id) == 0


def test_same_unit_allowed_with_stock(api, make_product, in_sync):
    product_id, _ = make_product("Rice", StockUnit.KG, "1.00", [("5", None, 0)])
    status, body = patch(api, product_id, {"unit": "kg", "description": "Same unit"})
    assert status == 200
    assert body["product"]["description"] == "Same unit"
    assert in_sync(product_id) == 5


def test_other_business_product_404(api, make_product):
    product_id, _ = make_product("Theirs", StockUnit.KG, "1.00", [("5", None, 0)], business_id=OTHER_BUSINESS_ID)
    assert patch(api, product_id, {"price": 2})[0] == 404


def test_name_can_be_changed(api, engine, make_product, in_sync):
    product_id, _ = make_product("Rice", StockUnit.KG, "1.00", [("5", None, 0)])
    status, body = patch(api, product_id, {"name": "Basmati Rice"})
    assert status == 200
    assert body["product"]["name"] == "Basmati Rice"
    with Session(engine) as session:
        assert session.get(Product, product_id).name == "Basmati Rice"
    assert in_sync(product_id) == 5


def test_name_left_alone_when_not_sent(api, make_product):
    product_id, _ = make_product("Rice", StockUnit.KG, "1.00", [("5", None, 0)])
    status, body = patch(api, product_id, {"price": 3})
    assert status == 200
    assert body["product"]["name"] == "Rice"
