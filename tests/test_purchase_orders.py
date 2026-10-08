from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import Session, select

from main import ProductBatch, PurchaseOrder, StockUnit
from conftest import day


def test_stocking_creates_batch_received_today(api, engine, make_product, in_sync):
    product_id, _ = make_product("Rice", StockUnit.KG, "1.00", [("5", None, -9)])
    supplier_id = api("POST", "/suppliers/", {"name": "Vendor"})[1]["supplier_id"]
    po_id = api("POST", "/purchase-orders/", {"supplier_id": supplier_id, "product_id": product_id,
                                              "quantity": 10, "unit_cost": 1.25})[1]["po_id"]
    assert api("PUT", f"/purchase-orders/{po_id}/deliver")[0] == 200
    status, body = api("PUT", f"/purchase-orders/{po_id}/stock?expiry_date={day(20)}")
    assert status == 200
    with Session(engine) as session:
        [batch] = session.exec(select(ProductBatch).where(ProductBatch.po_id == po_id)).all()
    assert batch.received_date == day(0)  # today() in Asia/Kolkata, pinned by the fixed_today fixture
    assert batch.expiry_date == day(20)
    assert batch.quantity == 10
    assert in_sync(product_id) == 15


def test_purchase_order_timestamp_is_aware_utc(api, engine, make_product):
    product_id, _ = make_product("Rice", StockUnit.KG, "1.00", [("5", None, -9)])
    supplier_id = api("POST", "/suppliers/", {"name": "Vendor"})[1]["supplier_id"]
    po_id = api("POST", "/purchase-orders/", {"supplier_id": supplier_id, "product_id": product_id,
                                              "quantity": 1, "unit_cost": 1})[1]["po_id"]
    with Session(engine) as session:
        stamp = session.get(PurchaseOrder, po_id).timestamp
    assert stamp.utcoffset() == timedelta(0)  # aware UTC
    assert abs(datetime.now(timezone.utc) - stamp) < timedelta(seconds=30)


def delivered_po(api, product_id):
    supplier_id = api("POST", "/suppliers/", {"name": "Vendor"})[1]["supplier_id"]
    po_id = api("POST", "/purchase-orders/", {"supplier_id": supplier_id, "product_id": product_id,
                                              "quantity": 10, "unit_cost": 1})[1]["po_id"]
    api("PUT", f"/purchase-orders/{po_id}/deliver")
    return po_id


@pytest.mark.parametrize("offset", [0, -1, -365])
def test_stocking_rejects_expiry_today_or_earlier(api, engine, make_product, stock, offset):
    product_id, _ = make_product("Rice", StockUnit.KG, "1.00", [("5", None, -9)])
    po_id = delivered_po(api, product_id)
    status, body = api("PUT", f"/purchase-orders/{po_id}/stock?expiry_date={day(offset)}")
    assert status == 422
    assert body["detail"] == ("expiry_date must be after today (2031-03-10). "
                              "Stock expiring today or earlier is already expired and can't be sold.")
    with Session(engine) as session:
        assert session.get(PurchaseOrder, po_id).status == "DELIVERED"  # can still be stocked with a valid date
        assert session.exec(select(ProductBatch).where(ProductBatch.po_id == po_id)).all() == []
    assert stock(product_id)[0] == 5


def test_stocking_accepts_expiry_tomorrow(api, engine, make_product):
    product_id, _ = make_product("Rice", StockUnit.KG, "1.00", [("5", None, -9)])
    po_id = delivered_po(api, product_id)
    assert api("PUT", f"/purchase-orders/{po_id}/stock?expiry_date={day(1)}")[0] == 200


def test_delivered_at_is_aware_utc(api, engine, make_product):
    product_id, _ = make_product("Rice", StockUnit.KG, "1.00", [("5", None, -9)])
    po_id = delivered_po(api, product_id)
    with Session(engine) as session:
        po = session.get(PurchaseOrder, po_id)
    assert po.delivered_at.utcoffset() == timedelta(0)  # aware UTC
    assert abs(datetime.now(timezone.utc) - po.delivered_at) < timedelta(seconds=30)
    assert po.delivered_at >= po.timestamp  # same clock as the order timestamp


def test_stocking_without_expiry_never_expires(api, engine, make_product, in_sync):
    product_id, _ = make_product("Rice", StockUnit.KG, "1.00", [("5", None, -9)])
    po_id = delivered_po(api, product_id)
    status, body = api("PUT", f"/purchase-orders/{po_id}/stock")
    assert status == 200 and body["batch_expiry"] is None
    with Session(engine) as session:
        [batch] = session.exec(select(ProductBatch).where(ProductBatch.po_id == po_id)).all()
    assert batch.expiry_date is None and batch.quantity == 10
    assert in_sync(product_id) == 15
    # never treated as expired: it can be sold and the daily check leaves it alone
    assert api("POST", "/checkout/", {"product_id": product_id, "quantity": 15})[0] == 200
    assert api("POST", "/system/daily-check")[1]["products"] == []


# --- PO creation validation ---

def create_po(api, product_id, quantity, unit_cost=1):
    supplier_id = api("POST", "/suppliers/", {"name": "Vendor"})[1]["supplier_id"]
    return api("POST", "/purchase-orders/", {"supplier_id": supplier_id, "product_id": product_id,
                                             "quantity": quantity, "unit_cost": unit_cost})


@pytest.mark.parametrize("quantity", [0, -1, "0.0001", "abc", None])
def test_po_quantity_must_be_positive_with_three_decimals(api, make_product, quantity):
    product_id, _ = make_product("Rice", StockUnit.KG, "1.00", [])
    assert create_po(api, product_id, quantity)[0] == 422


def test_fractional_po_for_kg_product(api, engine, make_product, in_sync):
    product_id, _ = make_product("Rice", StockUnit.KG, "1.00", [])
    status, body = create_po(api, product_id, "2.5", unit_cost="1.25")
    assert status == 200 and body["expense"] == 3.13  # 3.125 rounded half-up
    assert body["message"] == "Order placed for 2.5 kg of Rice. Awaiting delivery."
    po_id = body["po_id"]
    api("PUT", f"/purchase-orders/{po_id}/deliver")
    assert api("PUT", f"/purchase-orders/{po_id}/stock")[1]["accepted_quantity"] == 2.5
    assert in_sync(product_id) == 2.5


def test_fractional_po_rejected_for_piece_product(api, engine, make_product):
    product_id, _ = make_product("Eggs", StockUnit.PIECE, "0.50", [])
    status, body = create_po(api, product_id, "1.5")
    assert status == 422
    assert body["detail"] == "Eggs is counted by the piece, so quantity must be a whole number."
    with Session(engine) as session:
        assert session.exec(select(PurchaseOrder)).all() == []


def test_whole_po_quantity_with_trailing_zeros_for_piece_product(api, make_product):
    product_id, _ = make_product("Eggs", StockUnit.PIECE, "0.50", [])
    assert create_po(api, product_id, "12.000")[0] == 200


def test_negative_unit_cost_rejected(api, make_product):
    product_id, _ = make_product("Rice", StockUnit.KG, "1.00", [])
    assert create_po(api, product_id, 5, unit_cost=-0.01)[0] == 422


def test_free_goods_allowed(api, make_product):
    product_id, _ = make_product("Rice", StockUnit.KG, "1.00", [])
    status, body = create_po(api, product_id, 5, unit_cost=0)
    assert status == 200 and body["expense"] == 0
