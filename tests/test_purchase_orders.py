from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import Session, select

from main import ProductBatch, PurchaseOrder, StockUnit
from conftest import day


def test_stocking_creates_batch_received_today(api, engine, make_product, stock):
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
    product_qty, batch_total, _ = stock(product_id)
    assert product_qty == batch_total == 15


def test_purchase_order_timestamp_is_naive_utc(api, engine, make_product):
    product_id, _ = make_product("Rice", StockUnit.KG, "1.00", [("5", None, -9)])
    supplier_id = api("POST", "/suppliers/", {"name": "Vendor"})[1]["supplier_id"]
    po_id = api("POST", "/purchase-orders/", {"supplier_id": supplier_id, "product_id": product_id,
                                              "quantity": 1, "unit_cost": 1})[1]["po_id"]
    with Session(engine) as session:
        stamp = session.get(PurchaseOrder, po_id).timestamp
    assert stamp.tzinfo is None
    assert abs(datetime.now(timezone.utc).replace(tzinfo=None) - stamp) < timedelta(seconds=30)


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
