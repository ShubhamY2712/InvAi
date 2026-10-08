"""Stock movement ledger: one entry per batch touched, written in the same transaction as the stock change.
in_sync() (conftest) checks, after each operation, that every batch's entries sum to its quantity and every
product's entries sum to Product.quantity."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlmodel import Session

from app import main, security
from app.models import MovementReason, StockMovement, StockUnit
from conftest import BUSINESS_ID, OTHER_BUSINESS_ID, REAL_TODAY, asgi_request, day

R = MovementReason


def summary(entries):
    return [(e.reason, e.batch_id, e.quantity_change, e.product_quantity_after) for e in entries]


def new_since(movements, before, product_id=None):
    return movements(product_id)[len(before):]


# --- one entry per batch touched, per path ---

def test_product_creation_writes_opening_entry(api, movements, in_sync):
    status, body = api("POST", "/products/", {"name": "Milk", "sku": "M1", "price": 1, "quantity": 10})
    product_id = body["product"]["id"]
    [entry] = movements(product_id)
    assert (entry.reason, entry.quantity_change, entry.product_quantity_after) == (R.OPENING, 10, 10)
    assert entry.user_id == 1 and entry.business_id == BUSINESS_ID
    assert in_sync(product_id) == 10


def test_product_created_without_stock_has_no_entries(api, movements):
    product_id = api("POST", "/products/", {"name": "Milk", "sku": "M1", "price": 1})[1]["product"]["id"]
    assert movements(product_id) == []


def test_po_stocking_writes_purchase_receipt(api, make_product, movements, in_sync):
    product_id, _ = make_product("Rice", StockUnit.KG, "1.00", [("5", None, -9)])
    supplier_id = api("POST", "/suppliers/", {"name": "V"})[1]["supplier_id"]
    po_id = api("POST", "/purchase-orders/", {"supplier_id": supplier_id, "product_id": product_id,
                                              "quantity": 10, "unit_cost": 1})[1]["po_id"]
    api("PUT", f"/purchase-orders/{po_id}/deliver")
    before = movements(product_id)
    assert api("PUT", f"/purchase-orders/{po_id}/stock?expiry_date={day(20)}")[0] == 200
    [entry] = new_since(movements, before, product_id)
    assert (entry.reason, entry.quantity_change, entry.product_quantity_after) == (R.PURCHASE_RECEIPT, 10, 15)
    assert entry.po_id == po_id and entry.user_id == 1
    assert in_sync(product_id) == 15


def test_checkout_writes_one_sale_entry_per_batch(api, make_product, movements, in_sync):
    product_id, [a, b] = make_product("Rice", StockUnit.KG, "1.00", [("0.5", 5, -10), ("9", 10, -10)])
    before = movements(product_id)
    status, body = api("POST", "/checkout/", {"product_id": product_id, "quantity": 2.25})
    assert status == 200
    entries = new_since(movements, before, product_id)
    assert summary(entries) == [(R.SALE, a, Decimal("-0.5"), Decimal("9")), (R.SALE, b, Decimal("-1.75"), Decimal("7.25"))]
    assert {(e.sale_id, e.user_id) for e in entries} == {(body["sale_id"], 1)}
    assert in_sync(product_id) == Decimal("7.25")


def test_refused_checkout_writes_nothing(api, make_product, movements, in_sync):
    product_id, _ = make_product("Rice", StockUnit.KG, "1.00", [("1", None, -1)])
    before = movements(product_id)
    assert api("POST", "/checkout/", {"product_id": product_id, "quantity": 5})[0] == 400
    assert movements(product_id) == before
    in_sync(product_id)


def test_audit_decrease_one_entry_per_batch_with_note(api, make_product, movements, in_sync):
    product_id, [no_expiry, fresh, expired] = make_product(
        "Milk", StockUnit.LITRE, "1.00", [("4", None, -20), ("2", 5, -10), ("1.5", -1, -30)])
    before = movements(product_id)
    assert api("PUT", f"/products/{product_id}/manual-audit?new_quantity=5&note=Spilled%20crate")[0] == 200
    entries = new_since(movements, before, product_id)
    assert summary(entries) == [(R.AUDIT_DECREASE, expired, Decimal("-1.5"), Decimal("6")),
                                (R.AUDIT_DECREASE, fresh, Decimal("-1"), Decimal("5"))]
    assert {(e.note, e.user_id) for e in entries} == {("Spilled crate", 1)}
    assert in_sync(product_id) == 5


def test_audit_increase_writes_entry_for_adjustment_batch(api, make_product, movements, in_sync):
    product_id, _ = make_product("Milk", StockUnit.LITRE, "1.00", [("4", None, -20)])
    before = movements(product_id)
    body = api("PUT", f"/products/{product_id}/manual-audit?new_quantity=6.5")[1]
    [entry] = new_since(movements, before, product_id)
    assert (entry.reason, entry.batch_id, entry.quantity_change, entry.product_quantity_after, entry.note) == \
           (R.AUDIT_INCREASE, body["batch_created"]["batch_id"], Decimal("2.5"), Decimal("6.5"), None)
    assert in_sync(product_id) == Decimal("6.5")


def test_audit_with_unchanged_count_writes_nothing(api, make_product, movements, in_sync):
    product_id, _ = make_product("Milk", StockUnit.LITRE, "1.00", [("4", None, -20)])
    before = movements(product_id)
    api("PUT", f"/products/{product_id}/manual-audit?new_quantity=4")
    assert movements(product_id) == before
    in_sync(product_id)


def test_audit_note_is_limited_to_500_characters(api, make_product, movements):
    product_id, _ = make_product("Milk", StockUnit.LITRE, "1.00", [("4", None, -20)])
    before = movements(product_id)
    assert api("PUT", f"/products/{product_id}/manual-audit?new_quantity=1&note={'x' * 501}")[0] == 422
    assert movements(product_id) == before


def test_audit_repairing_drift_leaves_ledger_consistent(api, make_product, set_product_quantity, in_sync):
    product_id, _ = make_product("Milk", StockUnit.LITRE, "1.00", [("4", None, -20), ("2", 5, -10)])
    set_product_quantity(product_id, "1")  # batches and ledger still say 6
    api("PUT", f"/products/{product_id}/manual-audit?new_quantity=5")
    assert in_sync(product_id) == 5


def test_daily_check_writes_expiry_disposal_per_batch(api, make_product, movements, in_sync):
    product_id, [old, today_batch, fresh] = make_product(
        "Milk", StockUnit.LITRE, "1.00", [("1.5", -1, -9), ("0.5", 0, -9), ("2", 3, -9)])
    before = movements(product_id)
    api("POST", "/system/daily-check")
    entries = new_since(movements, before, product_id)
    assert summary(entries) == [(R.EXPIRY_DISPOSAL, old, Decimal("-1.5"), Decimal("2.5")),
                                (R.EXPIRY_DISPOSAL, today_batch, Decimal("-0.5"), Decimal("2"))]
    assert all(e.user_id == 1 for e in entries)
    assert in_sync(product_id) == 2


def test_daily_check_writes_nothing_for_inconsistent_product(api, make_product, set_product_quantity, movements):
    product_id, _ = make_product("Milk", StockUnit.LITRE, "1.00", [("2", -1, -9)])
    set_product_quantity(product_id, "1")
    before = movements(product_id)
    assert api("POST", "/system/daily-check")[1]["inconsistencies"]
    assert movements(product_id) == before


def test_ledger_stays_consistent_through_a_full_lifecycle(api, movements, in_sync):
    product_id = api("POST", "/products/", {"name": "Rice", "sku": "R", "price": 2, "quantity": 10, "unit": "kg",
                                            "expiry_date": str(day(3))})[1]["product"]["id"]
    in_sync(product_id)
    supplier_id = api("POST", "/suppliers/", {"name": "V"})[1]["supplier_id"]
    po_id = api("POST", "/purchase-orders/", {"supplier_id": supplier_id, "product_id": product_id,
                                              "quantity": 20, "unit_cost": 1})[1]["po_id"]
    api("PUT", f"/purchase-orders/{po_id}/deliver")
    api("PUT", f"/purchase-orders/{po_id}/stock?expiry_date={day(30)}"); in_sync(product_id)
    api("POST", "/checkout/", {"product_id": product_id, "quantity": 12.5}); in_sync(product_id)  # spans both batches
    api("PUT", f"/products/{product_id}/manual-audit?new_quantity=16"); in_sync(product_id)
    api("PUT", f"/products/{product_id}/manual-audit?new_quantity=18&expiry_date={day(60)}"); in_sync(product_id)
    assert in_sync(product_id) == 18
    assert [e.reason for e in movements(product_id)] == [
        R.OPENING, R.PURCHASE_RECEIPT, R.SALE, R.SALE, R.AUDIT_DECREASE, R.AUDIT_INCREASE]
    chain = movements(product_id)
    for previous, entry in zip(chain, chain[1:]):  # each entry continues from the one before
        assert previous.product_quantity_after + entry.quantity_change == entry.product_quantity_after


# --- append-only ---

def test_no_route_can_change_or_delete_ledger_entries():
    for route in main.app.routes:
        if "movement" in getattr(route, "path", ""):
            assert route.methods == {"GET"}, f"{route.path} allows {route.methods}"


# --- GET /inventory/movements ---

@pytest.fixture
def add_movement(engine):
    """Inserts a ledger entry directly with a chosen created_at (naive UTC); stock is not changed."""
    def _add(product_id, batch_id, change, reason, created_at, business_id=BUSINESS_ID, note=None):
        with Session(engine) as session:
            session.add(StockMovement(business_id=business_id, product_id=product_id, batch_id=batch_id,
                                      quantity_change=Decimal(change), reason=reason, product_quantity_after=Decimal("0"),
                                      created_at=created_at, note=note))
            session.commit()
    return _add


@pytest.fixture
def history(make_product, add_movement):
    rice, [rb] = make_product("Rice", StockUnit.KG, "1.00", [("0", None, -9)])
    milk, [mb] = make_product("Milk", StockUnit.LITRE, "1.00", [("0", None, -9)])
    add_movement(rice, rb, "5", R.PURCHASE_RECEIPT, datetime(2031, 3, 4, 18, 29, 59, tzinfo=timezone.utc))  # 2031-03-04 23:59:59 IST
    add_movement(rice, rb, "-1", R.SALE, datetime(2031, 3, 4, 18, 30, 0, tzinfo=timezone.utc))              # 2031-03-05 00:00:00 IST
    add_movement(milk, mb, "-2", R.EXPIRY_DISPOSAL, datetime(2031, 3, 9, 6, 30, tzinfo=timezone.utc), note="sour")
    add_movement(milk, mb, "-0.5", R.EXPIRY_DISPOSAL, datetime(2031, 3, 1, 6, 30, tzinfo=timezone.utc))
    add_movement(milk, mb, "-0.25", R.EXPIRY_DISPOSAL, datetime(2031, 2, 1, 6, 30, tzinfo=timezone.utc))  # before the range below
    return rice, milk


RANGE = "from_date=2031-03-01&to_date=2031-03-10"


def listed(body):
    return [(m["reason"], m["quantity_change"]) for m in body["movements"]]


def test_movements_newest_first_with_details(api, history):
    rice, milk = history
    status, body = api("GET", f"/inventory/movements?{RANGE}")
    assert status == 200 and body["count"] == 4
    assert listed(body) == [("expiry_disposal", -2), ("sale", -1), ("purchase_receipt", 5), ("expiry_disposal", -0.5)]
    newest = body["movements"][0]
    assert (newest["product_name"], newest["unit"], newest["note"]) == ("Milk", "litre", "sour")
    assert newest["created_at"] == "2031-03-09T06:30:00+00:00"  # naive UTC in the database, sent with its offset


def test_movements_filters(api, history):
    rice, milk = history
    assert listed(api("GET", f"/inventory/movements?{RANGE}&product_id={rice}")[1]) == [("sale", -1), ("purchase_receipt", 5)]
    assert listed(api("GET", f"/inventory/movements?{RANGE}&reason=expiry_disposal")[1]) == [("expiry_disposal", -2), ("expiry_disposal", -0.5)]
    assert listed(api("GET", f"/inventory/movements?{RANGE}&product_id={rice}&reason=sale")[1]) == [("sale", -1)]


def test_movements_date_range_uses_india_days(api, history):
    assert listed(api("GET", "/inventory/movements?from_date=2031-03-05&to_date=2031-03-05")[1]) == [("sale", -1)]
    assert listed(api("GET", "/inventory/movements?from_date=2031-03-04&to_date=2031-03-04")[1]) == [("purchase_receipt", 5)]


def test_movements_limit(api, history):
    assert listed(api("GET", f"/inventory/movements?{RANGE}&limit=2")[1]) == [("expiry_disposal", -2), ("sale", -1)]


@pytest.mark.parametrize("query", ["limit=0", "limit=501", "reason=theft", "product_id=abc",
                                   "from_date=2031-03-10&to_date=2031-03-01", "from_date=2030-01-01&to_date=2031-03-10"])
def test_movements_validation(api, query):
    assert api("GET", f"/inventory/movements?{query}")[0] == 422


def test_movements_max_limit_allowed(api):
    assert api("GET", "/inventory/movements?limit=500")[0] == 200


def test_movements_exclude_other_business(api, make_product, add_movement):
    theirs, [batch] = make_product("Theirs", StockUnit.KG, "1.00", [("0", None, -9)], business_id=OTHER_BUSINESS_ID)
    add_movement(theirs, batch, "-1", R.SALE, datetime(2031, 3, 9, 6, 30, tzinfo=timezone.utc), business_id=OTHER_BUSINESS_ID)
    assert api("GET", f"/inventory/movements?{RANGE}")[1]["movements"] == []
    assert api("GET", f"/inventory/movements?{RANGE}&product_id={theirs}")[1]["movements"] == []


@pytest.mark.parametrize("path", ["/inventory/movements", "/reports/waste"])
def test_staff_cannot_view(engine, path):
    staff_token = security.create_access_token({"sub": "1", "business_id": BUSINESS_ID, "role": "Staff"})
    assert asgi_request("GET", path, token=staff_token)[0] == 403


# --- GET /reports/waste ---

def test_waste_report_per_product_with_units(api, history, make_product, add_movement):
    rice, milk = history
    flour, [fb] = make_product("Flour", StockUnit.KG, "1.00", [("0", None, -9)])
    add_movement(flour, fb, "-1.25", R.EXPIRY_DISPOSAL, datetime(2031, 3, 2, 6, 30, tzinfo=timezone.utc))
    add_movement(flour, fb, "-3", R.AUDIT_DECREASE, datetime(2031, 3, 2, 6, 30, tzinfo=timezone.utc))  # not waste from expiry
    status, body = api("GET", f"/reports/waste?{RANGE}")
    assert status == 200
    assert body["total_entries"] == 3
    assert body["products"] == [
        {"product_id": flour, "name": "Flour", "unit": "kg", "quantity_disposed": 1.25, "entries": 1},
        {"product_id": milk, "name": "Milk", "unit": "litre", "quantity_disposed": 2.5, "entries": 2},
    ]


def test_waste_report_respects_range_and_business(api, history, make_product, add_movement):
    theirs, [batch] = make_product("Theirs", StockUnit.KG, "1.00", [("0", None, -9)], business_id=OTHER_BUSINESS_ID)
    add_movement(theirs, batch, "-9", R.EXPIRY_DISPOSAL, datetime(2031, 3, 9, 6, 30, tzinfo=timezone.utc), business_id=OTHER_BUSINESS_ID)
    body = api("GET", "/reports/waste?from_date=2031-02-01&to_date=2031-02-28")[1]
    assert body["total_entries"] == 1 and [p["quantity_disposed"] for p in body["products"]] == [0.25]


def test_waste_report_from_real_daily_check(api, make_product):
    milk, _ = make_product("Milk", StockUnit.LITRE, "1.00", [("1.5", -1, -9), ("0.5", 0, -9), ("2", 3, -9)])
    api("POST", "/system/daily-check")
    # Entries are stamped with the real clock (today() is pinned only for expiry decisions), so ask for
    # the real India date, plus the day before in case the run straddled midnight IST
    real_ist_date = REAL_TODAY()
    body = api("GET", f"/reports/waste?from_date={real_ist_date - timedelta(days=1)}&to_date={real_ist_date}")[1]
    assert body["total_entries"] == 2
    assert body["products"] == [{"product_id": milk, "name": "Milk", "unit": "litre", "quantity_disposed": 2, "entries": 2}]
