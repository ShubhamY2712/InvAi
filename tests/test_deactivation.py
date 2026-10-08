"""Products are deactivated instead of deleted."""
from datetime import timedelta

import pytest
from sqlmodel import Session, select

import main
from main import Product, ProductBatch, PurchaseOrder, StockUnit
from conftest import BUSINESS_ID, OTHER_BUSINESS_ID, REAL_TODAY, asgi_request


def deactivate(api, product_id):
    return api("DELETE", f"/products/{product_id}")


def reactivate(api, product_id):
    return api("POST", f"/products/{product_id}/reactivate")


def is_active(engine, product_id):
    with Session(engine) as session:
        return session.get(Product, product_id).is_active


def set_active(engine, product_id, active):
    with Session(engine) as session:
        product = session.get(Product, product_id)
        product.is_active = active
        session.add(product)
        session.commit()


@pytest.fixture
def empty_product(make_product):
    """A product whose stock was sold or audited away: 0 stock, but batch and ledger history remain."""
    return make_product("Rice", StockUnit.KG, "1.00", [("0", None, -9)])[0]


def make_po(api, product_id, deliver=False, stock=False):
    supplier_id = api("POST", "/suppliers/", {"name": "Vendor"})[1]["supplier_id"]
    po_id = api("POST", "/purchase-orders/", {"supplier_id": supplier_id, "product_id": product_id,
                                              "quantity": 4, "unit_cost": 1})[1]["po_id"]
    if deliver or stock:
        api("PUT", f"/purchase-orders/{po_id}/deliver")
    if stock:
        api("PUT", f"/purchase-orders/{po_id}/stock")
    return po_id


def as_role(role):
    token = main.create_access_token({"sub": "1", "business_id": BUSINESS_ID, "role": role})
    return lambda method, path, body=None: asgi_request(method, path, body, token)


# --- deactivation ---

def test_deactivate_keeps_the_product_and_its_history(api, engine, empty_product):
    status, body = deactivate(api, empty_product)
    assert status == 200
    assert body == {"success": True, "message": "Rice has been deactivated.", "product_id": empty_product, "is_active": False}
    assert is_active(engine, empty_product) is False
    with Session(engine) as session:
        assert session.exec(select(ProductBatch).where(ProductBatch.product_id == empty_product)).all()


def test_deactivating_twice_is_a_no_op(api, engine, empty_product):
    deactivate(api, empty_product)
    status, body = deactivate(api, empty_product)
    assert status == 200 and body["message"] == "Rice is already inactive."
    assert is_active(engine, empty_product) is False


def test_cannot_deactivate_with_stock(api, engine, make_product):
    product_id, _ = make_product("Rice", StockUnit.KG, "1.00", [("2.5", None, -9)])
    status, body = deactivate(api, product_id)
    assert status == 409
    assert body["detail"] == "Rice still has 2.5 kg in stock. Run a manual audit to bring it to 0 before deactivating."
    assert is_active(engine, product_id) is True


def test_audit_to_zero_then_deactivate(api, engine, make_product, in_sync):
    product_id, _ = make_product("Rice", StockUnit.KG, "1.00", [("2.5", None, -9)])
    assert api("PUT", f"/products/{product_id}/manual-audit?new_quantity=0")[0] == 200
    assert deactivate(api, product_id)[0] == 200
    assert in_sync(product_id) == 0


@pytest.mark.parametrize("deliver", [False, True])
def test_cannot_deactivate_with_unstocked_purchase_orders(api, engine, empty_product, deliver):
    first, second = make_po(api, empty_product, deliver=deliver), make_po(api, empty_product, deliver=deliver)
    status, body = deactivate(api, empty_product)
    assert status == 409
    assert body["detail"] == (f"Rice has purchase orders that aren't stocked yet (#{first}, #{second}). "
                              "Stock them before deactivating.")
    assert is_active(engine, empty_product) is True


def test_stocked_purchase_orders_do_not_block(api, engine, empty_product):
    make_po(api, empty_product, stock=True)
    api("PUT", f"/products/{empty_product}/manual-audit?new_quantity=0")
    assert deactivate(api, empty_product)[0] == 200


@pytest.mark.parametrize("role", ["Manager", "Staff"])
def test_only_the_owner_can_deactivate_or_reactivate(engine, empty_product, role):
    other = as_role(role)
    assert other("DELETE", f"/products/{empty_product}")[0] == 403
    set_active(engine, empty_product, False)
    assert other("POST", f"/products/{empty_product}/reactivate")[0] == 403
    assert is_active(engine, empty_product) is False


def test_other_business_product_404(api, make_product):
    theirs = make_product("Theirs", StockUnit.KG, "1.00", [], business_id=OTHER_BUSINESS_ID)[0]
    assert deactivate(api, theirs)[0] == 404
    assert reactivate(api, theirs)[0] == 404


# --- reactivation ---

def test_reactivate(api, engine, empty_product):
    deactivate(api, empty_product)
    status, body = reactivate(api, empty_product)
    assert status == 200
    assert body == {"success": True, "message": "Rice has been reactivated.", "product_id": empty_product, "is_active": True}
    assert is_active(engine, empty_product) is True


def test_reactivating_an_active_product_is_a_no_op(api, empty_product):
    status, body = reactivate(api, empty_product)
    assert status == 200 and body["message"] == "Rice is already active."


# --- inactive products can't change stock ---

INACTIVE = "Rice is inactive. Reactivate it first."


@pytest.fixture
def inactive_with_stock(engine, make_product):
    """Forced into a state the API wouldn't allow (inactive with stock), so each blocked path can be shown to change nothing."""
    product_id, _ = make_product("Rice", StockUnit.KG, "1.00", [("5", None, -9)])
    set_active(engine, product_id, False)
    return product_id


def test_checkout_blocked(api, inactive_with_stock, movements, in_sync):
    before = movements(inactive_with_stock)
    status, body = api("POST", "/checkout/", {"product_id": inactive_with_stock, "quantity": 1})
    assert status == 409 and body["detail"] == INACTIVE
    assert movements(inactive_with_stock) == before and in_sync(inactive_with_stock) == 5


def test_purchase_order_creation_blocked(api, engine, inactive_with_stock):
    supplier_id = api("POST", "/suppliers/", {"name": "Vendor"})[1]["supplier_id"]
    status, body = api("POST", "/purchase-orders/", {"supplier_id": supplier_id, "product_id": inactive_with_stock,
                                                     "quantity": 4, "unit_cost": 1})
    assert status == 409 and body["detail"] == INACTIVE
    with Session(engine) as session:
        assert session.exec(select(PurchaseOrder)).all() == []


def test_purchase_order_stocking_blocked(api, engine, make_product, in_sync):
    product_id, _ = make_product("Rice", StockUnit.KG, "1.00", [("5", None, -9)])
    po_id = make_po(api, product_id, deliver=True)
    set_active(engine, product_id, False)  # only reachable by bypassing the API, which refuses with an open PO
    status, body = api("PUT", f"/purchase-orders/{po_id}/stock")
    assert status == 409 and body["detail"] == INACTIVE
    with Session(engine) as session:
        assert session.get(PurchaseOrder, po_id).status == "DELIVERED"
    assert in_sync(product_id) == 5


def test_manual_audit_blocked(api, inactive_with_stock, movements, in_sync):
    before = movements(inactive_with_stock)
    status, body = api("PUT", f"/products/{inactive_with_stock}/manual-audit?new_quantity=1")
    assert status == 409 and body["detail"] == INACTIVE
    assert movements(inactive_with_stock) == before and in_sync(inactive_with_stock) == 5


def test_everything_works_again_after_reactivation(api, engine, inactive_with_stock, in_sync):
    reactivate(api, inactive_with_stock)
    assert api("POST", "/checkout/", {"product_id": inactive_with_stock, "quantity": 1})[0] == 200
    assert api("PUT", f"/products/{inactive_with_stock}/manual-audit?new_quantity=2")[0] == 200
    make_po(api, inactive_with_stock, stock=True)
    assert in_sync(inactive_with_stock) == 6


def test_patch_still_allowed_on_inactive_product(api, engine, empty_product):
    deactivate(api, empty_product)
    status, body = api("PATCH", f"/products/{empty_product}", {"description": "Discontinued"})
    assert status == 200 and body["product"]["description"] == "Discontinued"


# --- lists ---

def test_product_list_hides_inactive_by_default(api, empty_product, make_product):
    active, _ = make_product("Milk", StockUnit.LITRE, "1.00", [("1", None, -9)])
    deactivate(api, empty_product)
    default = api("GET", "/products/")[1]
    assert [p["id"] for p in default["inventory"]] == [active] and default["total_items"] == 1
    everything = api("GET", "/products/?include_inactive=true")[1]
    assert {(p["id"], p["is_active"]) for p in everything["inventory"]} == {(active, True), (empty_product, False)}


def test_low_stock_alert_ignores_inactive_products(api, empty_product, make_product):
    low, _ = make_product("Milk", StockUnit.LITRE, "1.00", [("1", None, -9)])  # below the default minimum of 10
    deactivate(api, empty_product)                                            # 0 stock, but retired
    assert [p["id"] for p in api("GET", "/inventory/alerts/low-stock")[1]["items_to_reorder"]] == [low]


# --- history-based reports keep inactive products ---

def test_reports_and_ledger_keep_inactive_products_history(api, make_product):
    milk, _ = make_product("Milk", StockUnit.LITRE, "2.00", [("3", None, -9), ("2", -1, -9)])
    assert api("POST", "/checkout/", {"product_id": milk, "quantity": 3})[0] == 200  # sells the fresh batch
    assert api("POST", "/system/daily-check")[1]["products"][0]["removed"] == 2      # disposes of the expired one
    assert deactivate(api, milk)[0] == 200

    # Sales and ledger entries are stamped with the real clock, so ask about the real India date (and the day before)
    real = f"from_date={REAL_TODAY() - timedelta(days=1)}&to_date={REAL_TODAY()}"
    assert api("GET", f"/reports/sales-summary?{real}")[1]["total_revenue"] == 6
    assert [(p["product_id"], p["quantity_sold"]) for p in api("GET", f"/reports/top-products?{real}")[1]["products"]] == [(milk, 3)]
    reasons = [m["reason"] for m in api("GET", f"/inventory/movements?{real}&product_id={milk}")[1]["movements"]]
    assert sorted(reasons) == ["expiry_disposal", "opening", "opening", "sale"]
    assert api("GET", f"/reports/waste?{real}")[1]["products"] == [
        {"product_id": milk, "name": "Milk", "unit": "litre", "quantity_disposed": 2, "entries": 1}]
    assert api("GET", "/products/")[1]["inventory"] == []
