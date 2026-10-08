"""Purchase order cancellation: only pending orders, and cancelled orders are inert everywhere else."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlmodel import Session, select

import main
from main import ProductBatch, PurchaseOrder, StockUnit
from conftest import BUSINESS_ID, OTHER_BUSINESS_ID, asgi_request


@pytest.fixture
def rice(make_product):
    return make_product("Rice", StockUnit.KG, "1.00", [("5", None, -9)])[0]


@pytest.fixture
def new_po(api):
    """new_po(product_id, deliver=False, stock=False) -> po_id, ordered through the API."""
    def _make(product_id, deliver=False, stock=False):
        supplier_id = api("POST", "/suppliers/", {"name": "Vendor"})[1]["supplier_id"]
        po_id = api("POST", "/purchase-orders/", {"supplier_id": supplier_id, "product_id": product_id,
                                                  "quantity": 4, "unit_cost": 1})[1]["po_id"]
        if deliver or stock:
            assert api("PUT", f"/purchase-orders/{po_id}/deliver")[0] == 200
        if stock:
            assert api("PUT", f"/purchase-orders/{po_id}/stock")[0] == 200
        return po_id
    return _make


def cancel(api, po_id, reason=None):
    return api("POST", f"/purchase-orders/{po_id}/cancel" + (f"?reason={reason}" if reason is not None else ""))


def po_row(engine, po_id):
    with Session(engine) as session:
        return session.get(PurchaseOrder, po_id)


# --- cancelling ---

def test_cancel_pending_po(api, engine, rice, new_po, movements, in_sync):
    po_id = new_po(rice)
    before = movements(rice)
    status, body = cancel(api, po_id, "Supplier%20out%20of%20stock")
    assert status == 200
    assert (body["message"], body["status"], body["reason"]) == (f"PO #{po_id} has been cancelled.", "CANCELLED", "Supplier out of stock")
    po = po_row(engine, po_id)
    assert (po.status, po.cancellation_reason) == ("CANCELLED", "Supplier out of stock")
    assert po.cancelled_at.tzinfo is None
    assert abs(datetime.now(timezone.utc).replace(tzinfo=None) - po.cancelled_at) < timedelta(seconds=30)
    assert movements(rice) == before and in_sync(rice) == 5  # no stock change, no ledger entry


def test_reason_is_optional(api, engine, rice, new_po):
    po_id = new_po(rice)
    assert cancel(api, po_id)[1]["reason"] is None
    assert po_row(engine, po_id).cancellation_reason is None


def test_reason_limited_to_500_characters(api, engine, rice, new_po):
    po_id = new_po(rice)
    assert cancel(api, po_id, "x" * 501)[0] == 422
    assert po_row(engine, po_id).status == "PENDING"
    assert cancel(api, po_id, "x" * 500)[0] == 200


def test_cancelling_twice_is_a_no_op(api, engine, rice, new_po):
    po_id = new_po(rice)
    cancel(api, po_id, "first")
    first = po_row(engine, po_id)
    status, body = cancel(api, po_id, "second")
    assert status == 200 and body["message"] == f"PO #{po_id} is already cancelled."
    again = po_row(engine, po_id)
    assert (again.cancelled_at, again.cancellation_reason) == (first.cancelled_at, "first")


def test_delivered_po_cannot_be_cancelled(api, engine, rice, new_po):
    po_id = new_po(rice, deliver=True)
    status, body = cancel(api, po_id)
    assert status == 409
    assert body["detail"] == (f"PO #{po_id} has already been delivered and can't be cancelled. To refuse the goods, "
                              "stock it with rejected_quantity equal to received_quantity.")
    assert po_row(engine, po_id).status == "DELIVERED"


def test_stocked_po_cannot_be_cancelled(api, engine, rice, new_po, in_sync):
    po_id = new_po(rice, stock=True)
    status, body = cancel(api, po_id)
    assert status == 409 and body["detail"] == f"PO #{po_id} has already been stocked and can't be cancelled."
    assert po_row(engine, po_id).status == "STOCKED"
    assert in_sync(rice) == 9


# --- cancelled orders are inert ---

def test_cancelled_po_cannot_be_delivered(api, engine, rice, new_po):
    po_id = new_po(rice)
    cancel(api, po_id)
    status, body = api("PUT", f"/purchase-orders/{po_id}/deliver")
    assert status == 409 and body["detail"] == f"PO #{po_id} was cancelled and can't be delivered."
    po = po_row(engine, po_id)
    assert po.status == "CANCELLED" and po.delivered_at is None


def test_cancelled_po_cannot_be_stocked(api, engine, rice, new_po, movements, in_sync):
    po_id = new_po(rice)
    cancel(api, po_id)
    before = movements(rice)
    status, body = api("PUT", f"/purchase-orders/{po_id}/stock")
    assert status == 409 and body["detail"] == f"PO #{po_id} was cancelled and can't be stocked."
    with Session(engine) as session:
        assert session.exec(select(ProductBatch).where(ProductBatch.po_id == po_id)).all() == []
    assert movements(rice) == before and in_sync(rice) == 5


def test_cancelling_unblocks_deactivation(api, engine, make_product, new_po):
    product_id = make_product("Rice", StockUnit.KG, "1.00", [("0", None, -9)])[0]
    po_id = new_po(product_id)
    status, body = api("DELETE", f"/products/{product_id}")
    assert status == 409 and f"#{po_id}" in body["detail"]
    cancel(api, po_id)
    assert api("DELETE", f"/products/{product_id}")[0] == 200


def test_cancelled_pos_excluded_from_scorecards(api, engine, rice):
    supplier_id = api("POST", "/suppliers/", {"name": "Vendor"})[1]["supplier_id"]
    with Session(engine) as session:
        for status in ("STOCKED", "CANCELLED"):  # the cancelled one even carries timestamps inside the range
            session.add(PurchaseOrder(supplier_id=supplier_id, product_id=rice, business_id=BUSINESS_ID, quantity=Decimal("10"),
                                      unit_cost=Decimal("1"), total_cost=Decimal("10"), status=status,
                                      timestamp=datetime(2031, 3, 1, 6, 30), delivered_at=datetime(2031, 3, 3, 6, 30),
                                      stocked_at=datetime(2031, 3, 3, 8, 0), received_quantity=Decimal("10"),
                                      rejected_quantity=Decimal("0")))
        session.commit()
    body = api("GET", f"/suppliers/{supplier_id}/scorecard?from_date=2031-03-01&to_date=2031-03-10")[1]
    assert body["po_count"] == 1


# --- access ---

def test_staff_cannot_cancel(engine, rice, new_po):
    po_id = new_po(rice)
    staff = main.create_access_token({"sub": "1", "business_id": BUSINESS_ID, "role": "Staff"})
    status, body = asgi_request("POST", f"/purchase-orders/{po_id}/cancel", token=staff)
    assert status == 403 and body["detail"] == "Only the Owner or a Manager can cancel purchase orders."
    assert po_row(engine, po_id).status == "PENDING"


def test_manager_can_cancel(engine, rice, new_po):
    po_id = new_po(rice)
    manager = main.create_access_token({"sub": "1", "business_id": BUSINESS_ID, "role": "Manager"})
    assert asgi_request("POST", f"/purchase-orders/{po_id}/cancel", token=manager)[0] == 200


def test_unknown_or_other_business_po_404(api, engine, make_product):
    their_product = make_product("Theirs", StockUnit.KG, "1.00", [], business_id=OTHER_BUSINESS_ID)[0]
    with Session(engine) as session:
        po = PurchaseOrder(supplier_id=1, product_id=their_product, business_id=OTHER_BUSINESS_ID, quantity=Decimal("1"),
                           unit_cost=Decimal("1"), total_cost=Decimal("1"))
        session.add(po); session.commit(); their_po = po.id
    assert cancel(api, their_po)[0] == 404
    assert po_row(engine, their_po).status == "PENDING"
    assert cancel(api, 99999)[0] == 404
