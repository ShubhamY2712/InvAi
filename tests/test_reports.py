"""Sales & Trends reports. today() is pinned to 2031-03-10 (conftest); sale timestamps are naive UTC.
India is UTC+05:30, so 18:30 UTC is midnight IST: 2031-03-04 18:30:00 UTC is 2031-03-05 00:00 IST."""
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlmodel import Session

import main
from main import Sale, StockUnit
from conftest import BUSINESS_ID, OTHER_BUSINESS_ID, asgi_request

REPORTS = ["/reports/sales-summary", "/reports/top-products", "/reports/dead-stock"]


@pytest.fixture
def make_sale(engine):
    """make_sale(product_id, quantity, total_price, utc_timestamp) inserts a sale directly."""
    def _make(product_id, quantity, total_price, utc_timestamp, business_id=BUSINESS_ID):
        with Session(engine) as session:
            session.add(Sale(product_id=product_id, user_id=1, business_id=business_id, quantity=Decimal(quantity),
                             total_price=Decimal(total_price), timestamp=utc_timestamp))
            session.commit()
    return _make


def utc(text):
    return datetime.fromisoformat(text)


def ist_noon(day):
    """06:30 UTC = 12:00 IST on the given date."""
    return datetime.combine(day, datetime.min.time()) + timedelta(hours=6, minutes=30)


# --- access and validation ---

@pytest.mark.parametrize("path", REPORTS)
def test_staff_cannot_view_reports(engine, path):
    staff_token = main.create_access_token({"sub": "1", "business_id": BUSINESS_ID, "role": "Staff"})
    status, body = asgi_request("GET", path, token=staff_token)
    assert status == 403
    assert body["detail"] == "Only the Owner or a Manager can view reports."


@pytest.mark.parametrize("path", ["/reports/sales-summary", "/reports/top-products"])
@pytest.mark.parametrize("query, expected", [
    ("from_date=2031-03-10&to_date=2031-03-09", 422),   # reversed
    ("from_date=2030-03-09&to_date=2031-03-09", 200),   # 365 days apart
    ("from_date=2030-03-09&to_date=2031-03-10", 200),   # 366 days apart: the limit
    ("from_date=2030-03-08&to_date=2031-03-10", 422),   # 367 days apart
    ("from_date=2031-03-10&to_date=2031-03-10", 200),   # single day
    ("from_date=not-a-date", 422),
])
def test_date_range_validation(api, path, query, expected):
    assert api("GET", f"{path}?{query}")[0] == expected


def test_reversed_range_message(api):
    status, body = api("GET", "/reports/sales-summary?from_date=2031-03-10&to_date=2031-03-01")
    assert status == 422 and body["detail"] == "from_date must be on or before to_date."


def test_span_limit_message(api):
    status, body = api("GET", "/reports/sales-summary?from_date=2030-01-01&to_date=2031-03-01")
    assert status == 422 and body["detail"] == "from_date and to_date can be at most 366 days apart."


# --- sales summary ---

def test_summary_defaults_to_last_30_days_including_today(api):
    status, body = api("GET", "/reports/sales-summary")
    assert status == 200
    assert (body["from_date"], body["to_date"], body["timezone"]) == ("2031-02-09", "2031-03-10", "Asia/Kolkata")
    assert len(body["daily"]) == 30
    assert body["daily"][0]["date"] == "2031-02-09" and body["daily"][-1]["date"] == "2031-03-10"


def test_summary_totals_and_zero_filled_days(api, make_product, make_sale):
    product, _ = make_product("Rice", StockUnit.KG, "1.00", [("100", None, -30)])
    make_sale(product, "1", "10.00", ist_noon(date(2031, 3, 2)))
    make_sale(product, "2", "20.00", ist_noon(date(2031, 3, 2)))
    make_sale(product, "1", "5.01", ist_noon(date(2031, 3, 4)))
    make_sale(product, "9", "999.00", ist_noon(date(2031, 2, 28)))                      # before the range
    make_sale(product, "9", "999.00", ist_noon(date(2031, 3, 2)), business_id=OTHER_BUSINESS_ID)  # another business

    status, body = api("GET", "/reports/sales-summary?from_date=2031-03-01&to_date=2031-03-05")
    assert status == 200
    assert body["total_revenue"] == 35.01
    assert body["sales_count"] == 3
    assert body["average_sale_value"] == 11.67  # 35.01 / 3 = 11.67
    assert body["daily"] == [
        {"date": "2031-03-01", "revenue": 0, "sales_count": 0},
        {"date": "2031-03-02", "revenue": 30, "sales_count": 2},
        {"date": "2031-03-03", "revenue": 0, "sales_count": 0},
        {"date": "2031-03-04", "revenue": 5.01, "sales_count": 1},
        {"date": "2031-03-05", "revenue": 0, "sales_count": 0},
    ]


def test_average_rounds_half_up(api, make_product, make_sale):
    product, _ = make_product("Rice", StockUnit.KG, "1.00", [("100", None, -30)])
    make_sale(product, "1", "0.01", ist_noon(date(2031, 3, 9)))
    make_sale(product, "1", "0.02", ist_noon(date(2031, 3, 9)))
    body = api("GET", "/reports/sales-summary?from_date=2031-03-09&to_date=2031-03-09")[1]
    assert body["average_sale_value"] == 0.02  # 0.015 -> 0.02


def test_summary_with_no_sales(api):
    body = api("GET", "/reports/sales-summary?from_date=2031-03-08&to_date=2031-03-10")[1]
    assert body["total_revenue"] == 0 and body["sales_count"] == 0 and body["average_sale_value"] is None
    assert [d["sales_count"] for d in body["daily"]] == [0, 0, 0]


def test_sale_just_after_midnight_ist_lands_on_the_new_day(api, make_product, make_sale):
    product, _ = make_product("Rice", StockUnit.KG, "1.00", [("100", None, -30)])
    make_sale(product, "1", "1.00", utc("2031-03-04T18:29:59"))  # 2031-03-04 23:59:59 IST
    make_sale(product, "1", "2.00", utc("2031-03-04T18:30:00"))  # 2031-03-05 00:00:00 IST
    make_sale(product, "1", "4.00", utc("2031-03-04T18:30:01"))  # 2031-03-05 00:00:01 IST

    body = api("GET", "/reports/sales-summary?from_date=2031-03-04&to_date=2031-03-05")[1]
    assert body["daily"] == [
        {"date": "2031-03-04", "revenue": 1, "sales_count": 1},
        {"date": "2031-03-05", "revenue": 6, "sales_count": 2},
    ]
    # range edges follow India days too
    assert api("GET", "/reports/sales-summary?from_date=2031-03-05&to_date=2031-03-05")[1]["total_revenue"] == 6
    assert api("GET", "/reports/sales-summary?from_date=2031-03-04&to_date=2031-03-04")[1]["total_revenue"] == 1


# --- top products ---

@pytest.fixture
def three_products(make_product, make_sale):
    rice, _ = make_product("Rice", StockUnit.KG, "1.00", [("100", None, -30)])
    eggs, _ = make_product("Eggs", StockUnit.PIECE, "0.50", [("100", None, -30)])
    milk, _ = make_product("Milk", StockUnit.LITRE, "2.00", [("100", None, -30)])
    flour, _ = make_product("Flour", StockUnit.KG, "0.80", [("100", None, -30)])
    day = date(2031, 3, 5)
    make_sale(rice, "2.5", "25.00", ist_noon(day))
    make_sale(rice, "1.25", "12.50", ist_noon(day))     # Rice: 3.75 kg, 37.50, 2 sales
    make_sale(eggs, "30", "15.00", ist_noon(day))       # Eggs: 30 piece, 15.00, 1 sale
    make_sale(milk, "4", "40.00", ist_noon(day))        # Milk: 4 litre, 40.00, 1 sale
    make_sale(flour, "5", "4.00", ist_noon(day))        # Flour: 5 kg, 4.00, 1 sale
    make_sale(milk, "50", "500.00", ist_noon(date(2031, 1, 1)))  # outside the range
    return rice, eggs, milk, flour


RANGE = "from_date=2031-03-01&to_date=2031-03-10"


def ranked(body):
    return [(p["rank"], p["product_id"], p["quantity_sold"], p["unit"]) for p in body["products"]]


def test_top_products_by_revenue(api, three_products):
    rice, eggs, milk, flour = three_products
    status, body = api("GET", f"/reports/top-products?{RANGE}")
    assert status == 200 and body["by"] == "revenue" and body["unit"] is None
    assert body["products"] == [
        {"rank": 1, "product_id": milk, "name": "Milk", "unit": "litre", "quantity_sold": 4, "revenue": 40, "sales_count": 1},
        {"rank": 2, "product_id": rice, "name": "Rice", "unit": "kg", "quantity_sold": 3.75, "revenue": 37.5, "sales_count": 2},
        {"rank": 3, "product_id": eggs, "name": "Eggs", "unit": "piece", "quantity_sold": 30, "revenue": 15, "sales_count": 1},
        {"rank": 4, "product_id": flour, "name": "Flour", "unit": "kg", "quantity_sold": 5, "revenue": 4, "sales_count": 1},
    ]


def test_unit_is_ignored_when_ranking_by_revenue(api, three_products):
    rice, eggs, milk, flour = three_products
    body = api("GET", f"/reports/top-products?{RANGE}&by=revenue&unit=kg")[1]
    assert body["unit"] is None
    assert [p["product_id"] for p in body["products"]] == [milk, rice, eggs, flour]


def test_quantity_ranking_defaults_to_piece(api, three_products):
    rice, eggs, milk, flour = three_products
    status, body = api("GET", f"/reports/top-products?{RANGE}&by=quantity")
    assert status == 200 and body["unit"] == "piece"
    assert ranked(body) == [(1, eggs, 30, "piece")]


def test_quantity_ranking_within_one_unit(api, three_products):
    rice, eggs, milk, flour = three_products
    body = api("GET", f"/reports/top-products?{RANGE}&by=quantity&unit=kg")[1]
    assert body["unit"] == "kg"
    assert ranked(body) == [(1, flour, 5, "kg"), (2, rice, 3.75, "kg")]  # 5 kg beats 3.75 kg despite less revenue
    assert ranked(api("GET", f"/reports/top-products?{RANGE}&by=quantity&unit=kg&limit=1")[1]) == [(1, flour, 5, "kg")]
    assert ranked(api("GET", f"/reports/top-products?{RANGE}&by=quantity&unit=litre")[1]) == [(1, milk, 4, "litre")]
    assert api("GET", f"/reports/top-products?{RANGE}&by=quantity&unit=g")[1]["products"] == []


@pytest.mark.parametrize("by", ["quantity", "revenue"])
def test_unknown_unit_rejected(api, by):
    assert api("GET", f"/reports/top-products?by={by}&unit=bushel")[0] == 422


def test_top_products_excludes_other_business(api, make_product, make_sale):
    theirs, _ = make_product("Theirs", StockUnit.KG, "1.00", [("5", None, 0)], business_id=OTHER_BUSINESS_ID)
    make_sale(theirs, "1", "100.00", ist_noon(date(2031, 3, 9)), business_id=OTHER_BUSINESS_ID)
    assert api("GET", "/reports/top-products")[1]["products"] == []


@pytest.mark.parametrize("query", ["by=price", "limit=0", "limit=101"])
def test_top_products_parameter_validation(api, query):
    assert api("GET", f"/reports/top-products?{query}")[0] == 422


# --- dead stock ---

def test_dead_stock(api, make_product, make_sale):
    never_sold, _ = make_product("Saffron", StockUnit.G, "12.345", [("10", None, -90)])   # value 123.45
    stale, _ = make_product("Flour", StockUnit.KG, "2.00", [("5", None, -90)])            # value 10.00
    fresh, _ = make_product("Rice", StockUnit.KG, "1.00", [("5", None, -90)])
    empty, _ = make_product("Eggs", StockUnit.PIECE, "0.50", [("0", None, -90)])
    make_sale(stale, "1", "2.00", ist_noon(date(2031, 1, 30)))     # 39 days before 2031-03-10
    make_sale(stale, "1", "2.00", ist_noon(date(2031, 2, 8)))      # last sale: 30 days ago -> outside the window
    make_sale(fresh, "1", "1.00", ist_noon(date(2031, 2, 9)))      # 29 days ago -> first day of the window
    make_sale(empty, "1", "0.50", ist_noon(date(2031, 1, 1)))      # stale, but no stock

    status, body = api("GET", "/reports/dead-stock")
    assert status == 200
    assert (body["days"], body["since"]) == (30, "2031-02-09")
    assert body["products"] == [
        {"product_id": never_sold, "name": "Saffron", "unit": "g", "stock": 10, "price": 12.35,
         "stock_value": 123.5, "last_sale_date": None, "days_since_last_sale": None},
        {"product_id": stale, "name": "Flour", "unit": "kg", "stock": 5, "price": 2,
         "stock_value": 10, "last_sale_date": "2031-02-08", "days_since_last_sale": 30},
    ]


def test_dead_stock_last_sale_uses_india_date(api, make_product, make_sale):
    product, _ = make_product("Rice", StockUnit.KG, "1.00", [("5", None, -90)])
    make_sale(product, "1", "1.00", utc("2031-03-08T18:30:00"))  # 2031-03-09 00:00 IST
    body = api("GET", "/reports/dead-stock?days=1")[1]           # window is just today, 2031-03-10
    assert body["products"][0]["last_sale_date"] == "2031-03-09"
    assert body["products"][0]["days_since_last_sale"] == 1
    assert api("GET", "/reports/dead-stock?days=2")[1]["products"] == []


def test_dead_stock_value_rounds_half_up(api, make_product):
    make_product("Tea", StockUnit.G, "0.05", [("0.5", None, -90)])  # 0.025 -> 0.03
    [row] = api("GET", "/reports/dead-stock")[1]["products"]
    assert row["stock_value"] == 0.03


def test_dead_stock_excludes_other_business(api, make_product):
    make_product("Theirs", StockUnit.KG, "1.00", [("5", None, 0)], business_id=OTHER_BUSINESS_ID)
    assert api("GET", "/reports/dead-stock")[1]["products"] == []


@pytest.mark.parametrize("days", [0, -1, 3651, "abc"])
def test_dead_stock_days_validation(api, days):
    assert api("GET", f"/reports/dead-stock?days={days}")[0] == 422


# --- schema ---

def test_sales_has_business_and_timestamp_index(engine):
    from sqlalchemy import inspect
    indexes = {ix["name"]: ix["column_names"] for ix in inspect(engine).get_indexes("sales")}
    assert indexes["ix_sales_business_id_timestamp"] == ["business_id", "timestamp"]
