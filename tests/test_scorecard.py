"""Partial PO receipts and supplier scorecards. today() is pinned to 2031-03-10; timestamps are naive UTC and
India is UTC+05:30, so 18:30 UTC is midnight IST."""
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlmodel import Session, select

import main
from main import ProductBatch, PurchaseOrder, StockUnit
from conftest import BUSINESS_ID, OTHER_BUSINESS_ID, REAL_TODAY, asgi_request, day


def at(text):
    return datetime.fromisoformat(text)


@pytest.fixture
def supplier(api):
    """supplier(name) -> id, created through the API."""
    return lambda name="Vendor": api("POST", "/suppliers/", {"name": name})[1]["supplier_id"]


@pytest.fixture
def make_po(engine):
    """Inserts a PO directly, stocked by default, so every timestamp and quantity can be chosen."""
    def _make(supplier_id, product_id, quantity="10", unit_cost="1.00", ordered=at("2031-03-01T06:30"),
              delivered=at("2031-03-03T06:30"), stocked=at("2031-03-03T08:00"), expected=None,
              received=None, rejected="0", status="STOCKED", business_id=BUSINESS_ID):
        with Session(engine) as session:
            po = PurchaseOrder(supplier_id=supplier_id, product_id=product_id, business_id=business_id,
                               quantity=Decimal(quantity), unit_cost=Decimal(unit_cost),
                               total_cost=main.round_money(Decimal(quantity) * Decimal(unit_cost)), status=status,
                               timestamp=ordered, delivered_at=delivered, stocked_at=stocked, expected_delivery_date=expected,
                               received_quantity=Decimal(received if received is not None else quantity),
                               rejected_quantity=Decimal(rejected))
            session.add(po)
            session.commit()
            return po.id
    return _make


@pytest.fixture
def rice(make_product):
    return make_product("Rice", StockUnit.KG, "1.00", [("5", None, -9)])[0]


def scorecard(api, supplier_id, query="from_date=2031-03-01&to_date=2031-03-10"):
    status, body = api("GET", f"/suppliers/{supplier_id}/scorecard?{query}")
    assert status == 200, body
    return body


# --- PO creation: expected delivery date ---

@pytest.mark.parametrize("offset", [0, -1])
def test_expected_delivery_date_must_be_after_today(api, supplier, rice, offset):
    status, body = api("POST", "/purchase-orders/", {"supplier_id": supplier(), "product_id": rice, "quantity": 10,
                                                     "unit_cost": 1, "expected_delivery_date": str(day(offset))})
    assert status == 422
    assert body["detail"] == "expected_delivery_date must be after today (2031-03-10)."


def test_expected_delivery_date_stored(api, engine, supplier, rice):
    status, body = api("POST", "/purchase-orders/", {"supplier_id": supplier(), "product_id": rice, "quantity": 10,
                                                     "unit_cost": 1, "expected_delivery_date": str(day(3))})
    assert status == 200 and body["expected_delivery_date"] == str(day(3))
    with Session(engine) as session:
        assert session.get(PurchaseOrder, body["po_id"]).expected_delivery_date == day(3)


def test_expected_delivery_date_optional(api, supplier, rice):
    body = api("POST", "/purchase-orders/", {"supplier_id": supplier(), "product_id": rice, "quantity": 10, "unit_cost": 1})[1]
    assert body["expected_delivery_date"] is None


# --- PO stocking with received / rejected quantities ---

@pytest.fixture
def delivered_po(api, supplier):
    """delivered_po(product_id, quantity) -> po_id of a PO that has been ordered and delivered through the API."""
    def _make(product_id, quantity=10):
        po_id = api("POST", "/purchase-orders/", {"supplier_id": supplier(), "product_id": product_id,
                                                  "quantity": quantity, "unit_cost": 1})[1]["po_id"]
        assert api("PUT", f"/purchase-orders/{po_id}/deliver")[0] == 200
        return po_id
    return _make


def stock_po(api, po_id, **params):
    query = "&".join([f"expiry_date={day(30)}"] + [f"{k}={v}" for k, v in params.items()])
    return api("PUT", f"/purchase-orders/{po_id}/stock?{query}")


def po_row(engine, po_id):
    with Session(engine) as session:
        return session.get(PurchaseOrder, po_id)


def batches_from(engine, po_id):
    with Session(engine) as session:
        return session.exec(select(ProductBatch).where(ProductBatch.po_id == po_id)).all()


def test_defaults_receive_the_full_order(api, engine, rice, delivered_po, in_sync):
    po_id = delivered_po(rice)
    status, body = stock_po(api, po_id)
    assert status == 200
    assert (body["received_quantity"], body["rejected_quantity"], body["accepted_quantity"]) == (10, 0, 10)
    assert body["message"] == "PO Stocked. 10 kg added to main inventory (10 kg received, 0 kg rejected)."
    po = po_row(engine, po_id)
    assert (po.status, po.received_quantity, po.rejected_quantity) == ("STOCKED", 10, 0)
    assert po.stocked_at is not None and po.stocked_at >= po.delivered_at
    assert [b.quantity for b in batches_from(engine, po_id)] == [10]
    assert in_sync(rice) == 15


def test_short_delivery_with_rejections_closes_the_po(api, engine, rice, delivered_po, movements, in_sync):
    po_id = delivered_po(rice)
    status, body = stock_po(api, po_id, received_quantity="8", rejected_quantity="2")
    assert status == 200
    assert body["message"] == "PO Stocked. 6 kg added to main inventory (8 kg received, 2 kg rejected)."
    assert body["accepted_quantity"] == 6 and body["batch_id"] is not None
    assert po_row(engine, po_id).status == "STOCKED"
    assert [b.quantity for b in batches_from(engine, po_id)] == [6]
    [receipt] = [m for m in movements(rice) if m.po_id == po_id]
    assert (receipt.reason, receipt.quantity_change, receipt.batch_id) == (main.MovementReason.PURCHASE_RECEIPT, 6, body["batch_id"])
    assert in_sync(rice) == 11


def test_fractional_receipt_for_kg_product(api, engine, rice, delivered_po, in_sync):
    po_id = delivered_po(rice)
    assert stock_po(api, po_id, received_quantity="7.5", rejected_quantity="0.25")[1]["accepted_quantity"] == 7.25
    assert in_sync(rice) == Decimal("12.25")


def test_over_delivery_is_accepted(api, engine, rice, delivered_po, in_sync):
    po_id = delivered_po(rice)
    assert stock_po(api, po_id, received_quantity="12")[1]["accepted_quantity"] == 12
    assert in_sync(rice) == 17


@pytest.mark.parametrize("received, rejected", [("5", "5"), ("0", "0")])
def test_nothing_accepted_closes_po_without_batch_or_ledger_entry(api, engine, rice, delivered_po, movements, in_sync,
                                                                  received, rejected):
    po_id = delivered_po(rice)
    before = movements(rice)
    status, body = stock_po(api, po_id, received_quantity=received, rejected_quantity=rejected)
    assert status == 200
    assert body["message"] == f"PO closed. Nothing added to inventory ({received} kg received, {rejected} kg rejected)."
    assert (body["accepted_quantity"], body["batch_id"], body["batch_expiry"]) == (0, None, None)
    assert po_row(engine, po_id).status == "STOCKED"
    assert batches_from(engine, po_id) == []
    assert movements(rice) == before
    assert in_sync(rice) == 5


@pytest.mark.parametrize("params, message", [
    ({"received_quantity": "8", "rejected_quantity": "9"}, "rejected_quantity can't be more than received_quantity."),
    ({"rejected_quantity": "11"}, "rejected_quantity can't be more than received_quantity."),  # default received = 10
    ({"received_quantity": "-1"}, None),
    ({"rejected_quantity": "-1"}, None),
    ({"received_quantity": "8.0001"}, None),
    ({"received_quantity": "abc"}, None),
])
def test_invalid_receipts_rejected_and_nothing_changes(api, engine, rice, delivered_po, in_sync, params, message):
    po_id = delivered_po(rice)
    status, body = stock_po(api, po_id, **params)
    assert status == 422
    if message:
        assert body["detail"] == message
    assert po_row(engine, po_id).status == "DELIVERED"
    assert batches_from(engine, po_id) == []
    assert in_sync(rice) == 5


@pytest.mark.parametrize("params", [{"received_quantity": "9.5"}, {"rejected_quantity": "0.5"}])
def test_piece_products_need_whole_numbers(api, engine, make_product, delivered_po, in_sync, params):
    eggs, _ = make_product("Eggs", StockUnit.PIECE, "0.50", [("12", None, -1)])
    po_id = delivered_po(eggs)
    status, body = stock_po(api, po_id, **params)
    assert status == 422
    assert body["detail"] == "Eggs is counted by the piece, so received_quantity and rejected_quantity must be whole numbers."
    assert po_row(engine, po_id).status == "DELIVERED"
    assert in_sync(eggs) == 12


def test_po_cannot_be_stocked_twice(api, rice, delivered_po, in_sync):
    po_id = delivered_po(rice)
    assert stock_po(api, po_id)[0] == 200
    assert stock_po(api, po_id)[0] == 400
    assert in_sync(rice) == 15


# --- scorecard metrics ---

def test_lead_time(api, supplier, rice, make_po):
    s = supplier()
    make_po(s, rice, ordered=at("2031-03-01T06:30"), delivered=at("2031-03-03T06:30"))   # 2 days
    make_po(s, rice, ordered=at("2031-03-01T06:30"), delivered=at("2031-03-04T18:30"))   # 3.5 days
    body = scorecard(api, s)
    assert body["po_count"] == 2 and body["avg_lead_time_days"] == 2.75


def test_on_time_rate_uses_india_dates(api, supplier, rice, make_po):
    s = supplier()
    expected = date(2031, 3, 5)
    make_po(s, rice, expected=expected, delivered=at("2031-03-05T18:29:59"))  # 23:59:59 IST on the 5th: on time
    make_po(s, rice, expected=expected, delivered=at("2031-03-05T18:30:00"))  # 00:00 IST on the 6th: late
    make_po(s, rice, expected=None, delivered=at("2031-03-09T06:30"))         # no promise: not counted
    body = scorecard(api, s)
    assert body["po_count"] == 3 and body["on_time_rate"] == 0.5


def test_on_time_rate_null_without_expected_dates(api, supplier, rice, make_po):
    s = supplier()
    make_po(s, rice)
    assert scorecard(api, s)["on_time_rate"] is None


def test_fill_rate_caps_over_delivery(api, supplier, rice, make_po):
    s = supplier()
    make_po(s, rice, quantity="10", received="8")    # 0.8
    make_po(s, rice, quantity="10", received="12")   # capped at 1
    make_po(s, rice, quantity="10", received="10")   # 1
    assert scorecard(api, s)["fill_rate"] == 0.9333  # 2.8 / 3


def test_defect_rate_skips_nothing_received(api, supplier, rice, make_po):
    s = supplier()
    make_po(s, rice, received="8", rejected="2")     # 0.25
    make_po(s, rice, received="12", rejected="0")    # 0
    make_po(s, rice, received="0", rejected="0")     # skipped
    body = scorecard(api, s)
    assert body["defect_rate"] == 0.125
    assert body["fill_rate"] == 0.6                  # (0.8 + 1 + 0) / 3: the empty delivery still counts for fill


def test_defect_rate_null_when_nothing_was_received(api, supplier, rice, make_po):
    s = supplier()
    make_po(s, rice, received="0")
    body = scorecard(api, s)
    assert body["defect_rate"] is None and body["fill_rate"] == 0


def test_price_volatility_per_product_then_averaged(api, supplier, make_product, make_po):
    s = supplier()
    a = make_product("A", StockUnit.KG, "1.00", [])[0]
    b = make_product("B", StockUnit.KG, "1.00", [])[0]
    c = make_product("C", StockUnit.KG, "1.00", [])[0]
    make_po(s, a, unit_cost="10.00"); make_po(s, a, unit_cost="12.00")                            # sd 1 / mean 11
    make_po(s, b, unit_cost="7.00")                                                              # one PO: skipped
    make_po(s, c, unit_cost="5.00"); make_po(s, c, unit_cost="5.00"); make_po(s, c, unit_cost="5.00")  # 0
    assert scorecard(api, s)["price_volatility"] == 0.0455  # (0.090909 + 0) / 2


def test_price_volatility_null_without_repeat_purchases(api, supplier, make_product, make_po):
    s = supplier()
    for name in ("A", "B"):
        make_po(s, make_product(name, StockUnit.KG, "1.00", [])[0], unit_cost="3.00")
    assert scorecard(api, s)["price_volatility"] is None


def test_all_metrics_null_without_stocked_pos_in_range(api, supplier, rice, make_po):
    s = supplier()
    make_po(s, rice, status="PENDING", delivered=None, stocked=None)
    make_po(s, rice, status="DELIVERED", stocked=None)
    make_po(s, rice, stocked=at("2031-02-01T06:30"))  # stocked before the range
    assert scorecard(api, s) == {
        "supplier_id": s, "name": "Vendor", "from_date": "2031-03-01", "to_date": "2031-03-10", "po_count": 0,
        "avg_lead_time_days": None, "on_time_rate": None, "fill_rate": None, "defect_rate": None, "price_volatility": None}


def test_range_is_by_india_stocking_date(api, supplier, rice, make_po):
    s = supplier()
    make_po(s, rice, stocked=at("2031-03-04T18:29:59"))  # 2031-03-04 IST
    make_po(s, rice, stocked=at("2031-03-04T18:30:00"))  # 2031-03-05 IST
    assert scorecard(api, s, "from_date=2031-03-05&to_date=2031-03-05")["po_count"] == 1
    assert scorecard(api, s, "from_date=2031-03-04&to_date=2031-03-04")["po_count"] == 1


def test_default_range_is_last_90_days(api, supplier, rice, make_po):
    s = supplier()
    make_po(s, rice, stocked=at("2030-12-11T06:30"))  # first day of the window
    make_po(s, rice, stocked=at("2030-12-10T06:30"))  # just outside
    status, body = api("GET", f"/suppliers/{s}/scorecard")
    assert (body["from_date"], body["to_date"], body["po_count"]) == ("2030-12-11", "2031-03-10", 1)


@pytest.mark.parametrize("path", ["/suppliers/scorecards", "/suppliers/{s}/scorecard"])
@pytest.mark.parametrize("query", ["from_date=2031-03-10&to_date=2031-03-01", "from_date=2030-01-01&to_date=2031-03-10"])
def test_date_range_validation(api, supplier, path, query):
    assert api("GET", f"{path.format(s=supplier())}?{query}")[0] == 422


# --- all suppliers, access, other businesses ---

def test_scorecards_for_all_suppliers(api, supplier, rice, make_po):
    zeta, alpha = supplier("Zeta Farms"), supplier("Alpha Foods")
    make_po(zeta, rice, received="8")
    body = api("GET", "/suppliers/scorecards?from_date=2031-03-01&to_date=2031-03-10")[1]
    assert [(s["name"], s["po_count"], s["fill_rate"]) for s in body["suppliers"]] == [
        ("Alpha Foods", 0, None), ("Zeta Farms", 1, 0.8)]


@pytest.mark.parametrize("path", ["/suppliers/scorecards", "/suppliers/1/scorecard"])
def test_staff_cannot_view_scorecards(engine, path):
    staff = main.create_access_token({"sub": "1", "business_id": BUSINESS_ID, "role": "Staff"})
    status, body = asgi_request("GET", path, token=staff)
    assert status == 403 and body["detail"] == "Only the Owner or a Manager can view supplier scorecards."


def test_other_business_suppliers_and_pos_are_invisible(api, engine, supplier, rice, make_po, make_product):
    mine = supplier("Mine")
    with Session(engine) as session:
        theirs = main.Supplier(name="Theirs", business_id=OTHER_BUSINESS_ID)
        session.add(theirs); session.commit(); theirs_id = theirs.id
    their_product = make_product("Theirs", StockUnit.KG, "1.00", [], business_id=OTHER_BUSINESS_ID)[0]
    make_po(theirs_id, their_product, business_id=OTHER_BUSINESS_ID)
    make_po(mine, their_product, business_id=OTHER_BUSINESS_ID)  # another business's PO naming my supplier
    assert api("GET", f"/suppliers/{theirs_id}/scorecard")[0] == 404
    body = api("GET", "/suppliers/scorecards?from_date=2031-03-01&to_date=2031-03-10")[1]
    assert [(s["name"], s["po_count"]) for s in body["suppliers"]] == [("Mine", 0)]


def test_scorecard_from_real_api_flow(api, supplier, rice, delivered_po, in_sync):
    po_id = delivered_po(rice)
    stock_po(api, po_id, received_quantity="9", rejected_quantity="3")
    in_sync(rice)
    body = api("GET", f"/suppliers/scorecards?from_date={REAL_TODAY() - timedelta(days=1)}&to_date={REAL_TODAY()}")[1]
    [card] = [c for c in body["suppliers"] if c["po_count"]]
    assert (card["po_count"], card["fill_rate"], card["defect_rate"], card["on_time_rate"]) == (1, 0.9, 0.3333, None)
    assert card["avg_lead_time_days"] == 0  # ordered and delivered moments apart
