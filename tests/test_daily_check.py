from decimal import Decimal

from app.models import StockUnit
from conftest import OTHER_BUSINESS_ID


def daily_check(api):
    status, body = api("POST", "/system/daily-check")
    assert status == 200
    return body


def test_removes_expired_stock_per_product_with_units(api, make_product, stock, in_sync):
    milk, [m_yesterday, m_today, m_fresh, m_none] = make_product(
        "Milk", StockUnit.LITRE, "1.00", [("1.5", -1, -9), ("0.5", 0, -9), ("2", 1, -9), ("3", None, -9)])
    eggs, [e_old, e_fresh] = make_product("Eggs", StockUnit.PIECE, "0.50", [("6", -3, -9), ("6", 4, -9)])
    body = daily_check(api)
    assert body["products"] == [
        {"product_id": milk, "product_name": "Milk", "removed": 2, "unit": "litre", "removed_display": "2 litre",
         "batches_cleared": [m_yesterday, m_today], "stock_remaining": 5},
        {"product_id": eggs, "product_name": "Eggs", "removed": 6, "unit": "piece", "removed_display": "6 piece",
         "batches_cleared": [e_old], "stock_remaining": 6},
    ]
    assert body["inconsistencies"] == []
    assert body["expired_batches_cleared"] == 3
    assert in_sync(milk) == 5 and in_sync(eggs) == 6
    assert stock(milk)[2] == {m_yesterday: 0, m_today: 0, m_fresh: 2, m_none: 3}


def test_fractional_amounts_display_without_trailing_zeros(api, make_product, in_sync):
    rice, _ = make_product("Rice", StockUnit.KG, "1.00", [("1.25", -1, -9), ("2", 3, -9)])
    [entry] = daily_check(api)["products"]
    assert (entry["removed"], entry["removed_display"]) == (1.25, "1.25 kg")
    assert in_sync(rice) == 2


def test_inconsistent_product_reported_and_left_unchanged(api, make_product, set_product_quantity, stock, in_sync):
    broken, _ = make_product("Milk", StockUnit.LITRE, "1.00", [("2", -1, -9), ("3", None, -9)])
    healthy, _ = make_product("Rice", StockUnit.KG, "1.00", [("1", -1, -9), ("4", None, -9)])
    set_product_quantity(broken, "1.5")  # less than the 2 litres sitting in its expired batch
    before = stock(broken)

    body = daily_check(api)

    assert body["inconsistencies"] == [{
        "product_id": broken, "product_name": "Milk", "unit": "litre", "stock": 1.5, "expired_in_batches": 2,
        "message": ("Milk: recorded stock is 1.5 litre but 2 litre is in expired batches. "
                    "Nothing was changed; run a manual audit to correct it."),
    }]
    assert stock(broken) == before  # no floor at 0, no partial change
    assert [p["product_id"] for p in body["products"]] == [healthy]  # the other product is still processed
    assert in_sync(healthy) == 4


def test_inconsistency_fixed_by_manual_audit(api, make_product, set_product_quantity, in_sync):
    broken, _ = make_product("Milk", StockUnit.LITRE, "1.00", [("2", -1, -9), ("3", None, -9)])
    set_product_quantity(broken, "1.5")
    assert daily_check(api)["inconsistencies"]
    assert api("PUT", f"/products/{broken}/manual-audit?new_quantity=3")[0] == 200  # expired batch is reduced first
    assert in_sync(broken) == 3
    body = daily_check(api)
    assert body["inconsistencies"] == [] and body["products"] == []


def test_second_run_removes_nothing(api, make_product, in_sync):
    milk, _ = make_product("Milk", StockUnit.LITRE, "1.00", [("2", -1, -9), ("3", None, -9)])
    daily_check(api)
    body = daily_check(api)
    assert body["products"] == [] and body["expired_batches_cleared"] == 0
    assert in_sync(milk) == 3


def test_other_business_untouched(api, make_product, stock, in_sync):
    theirs, _ = make_product("Theirs", StockUnit.KG, "1.00", [("5", -1, -9)], business_id=OTHER_BUSINESS_ID)
    assert daily_check(api)["products"] == []
    assert stock(theirs)[0] == Decimal("5")
    assert in_sync(theirs) == 5


def test_nothing_expired(api, make_product, in_sync):
    milk, _ = make_product("Milk", StockUnit.LITRE, "1.00", [("2", 1, -9), ("3", None, -9)])
    body = daily_check(api)
    assert body["products"] == [] and body["inconsistencies"] == [] and body["expired_batches_cleared"] == 0
    assert in_sync(milk) == 5
