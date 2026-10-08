"""GET /sales/: quantities sold are reported per unit, never summed across units."""
from decimal import Decimal

from sqlmodel import Session

import main
from main import Sale, StockUnit, User, UserRole
from conftest import BUSINESS_ID, OTHER_BUSINESS_ID, asgi_request


def add_sale(engine, product_id, quantity, user_id=1, business_id=BUSINESS_ID):
    with Session(engine) as session:
        session.add(Sale(product_id=product_id, user_id=user_id, business_id=business_id,
                         quantity=Decimal(quantity), total_price=Decimal("1.00")))
        session.commit()


def test_quantities_are_broken_down_by_unit(api, make_product):
    eggs, _ = make_product("Eggs", StockUnit.PIECE, "0.50", [("20", None, -9)])
    rice, _ = make_product("Rice", StockUnit.KG, "2.00", [("5", None, -9)])
    make_product("Milk", StockUnit.LITRE, "1.00", [("5", None, -9)])  # nothing sold: not listed
    for product_id, quantity in [(eggs, 5), (eggs, 7), (rice, 1.5)]:
        assert api("POST", "/checkout/", {"product_id": product_id, "quantity": quantity})[0] == 200
    status, body = api("GET", "/sales/")
    assert status == 200
    assert body["items_sold_by_unit"] == {"piece": 12, "kg": 1.5}
    assert "total_items_sold" not in body
    assert body["total_records"] == 3 and body["total_revenue"] == 9  # 12 x 0.50 + 1.5 x 2.00


def test_no_sales_gives_an_empty_breakdown(api):
    status, body = api("GET", "/sales/")
    assert status == 200 and body["items_sold_by_unit"] == {} and body["total_records"] == 0


def test_staff_breakdown_covers_only_their_own_sales(engine, make_product):
    with Session(engine) as session:
        session.add(User(id=2, username="staff", email="staff@example.com", hashed_password="x",
                         role=UserRole.STAFF, business_id=BUSINESS_ID))
        session.commit()
    eggs, _ = make_product("Eggs", StockUnit.PIECE, "0.50", [])
    rice, _ = make_product("Rice", StockUnit.KG, "2.00", [])
    add_sale(engine, eggs, "3", user_id=2)
    add_sale(engine, rice, "0.75", user_id=1)  # the owner's sale
    staff = main.create_access_token({"sub": "2", "business_id": BUSINESS_ID, "role": "Staff"})
    body = asgi_request("GET", "/sales/", token=staff)[1]
    assert body["items_sold_by_unit"] == {"piece": 3} and body["total_records"] == 1


def test_other_businesses_are_excluded(api, engine, make_product):
    theirs, _ = make_product("Theirs", StockUnit.KG, "1.00", [], business_id=OTHER_BUSINESS_ID)
    add_sale(engine, theirs, "9", business_id=OTHER_BUSINESS_ID)
    assert api("GET", "/sales/")[1]["items_sold_by_unit"] == {}


def test_sales_of_missing_products_are_reported_separately(api, engine, make_product):
    rice, _ = make_product("Rice", StockUnit.KG, "2.00", [])
    add_sale(engine, rice, "2")
    add_sale(engine, 99999, "4")  # product hard-deleted before deactivation existed
    assert api("GET", "/sales/")[1]["items_sold_by_unit"] == {"kg": 2, "unknown": 4}
