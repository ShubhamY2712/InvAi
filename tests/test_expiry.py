from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.models import StockUnit
from conftest import REAL_TODAY, day


def test_today_is_the_date_in_kolkata():
    assert REAL_TODAY() == datetime.now(ZoneInfo("Asia/Kolkata")).date()
    assert isinstance(REAL_TODAY(), date)


def test_expiring_soon_window_is_after_today_up_to_days(api, make_product):
    product_id, [expired, today_batch, tomorrow, edge, beyond, empty, no_expiry] = make_product(
        "Milk", StockUnit.LITRE, "1.00",
        [("1", -1, -9), ("1", 0, -9), ("2", 1, -9), ("3", 7, -9), ("4", 8, -9), ("0", 2, -9), ("5", None, -9)])
    status, body = api("GET", "/alerts/expiring-soon/")
    assert status == 200
    assert [(b["batch_id"], b["days_left"]) for b in body["batches"]] == [(tomorrow, 1), (edge, 7)]
    assert body["window_end"] == str(day(7))


def test_expiring_soon_days_parameter(api, make_product):
    product_id, [today_batch, tomorrow, later] = make_product(
        "Milk", StockUnit.LITRE, "1.00", [("1", 0, -9), ("1", 1, -9), ("1", 30, -9)])
    assert [b["batch_id"] for b in api("GET", "/alerts/expiring-soon/?days=1")[1]["batches"]] == [tomorrow]
    assert [b["batch_id"] for b in api("GET", "/alerts/expiring-soon/?days=30")[1]["batches"]] == [tomorrow, later]


@pytest.mark.parametrize("days, expected_status", [(0, 422), (-1, 422), (1, 200), (3650, 200), (3651, 422)])
def test_expiring_soon_days_range(api, days, expected_status):
    assert api("GET", f"/alerts/expiring-soon/?days={days}")[0] == expected_status


def test_daily_check_clears_batches_expiring_today_and_earlier(api, make_product, stock, in_sync):
    product_id, [yesterday, today_batch, tomorrow, no_expiry] = make_product(
        "Milk", StockUnit.LITRE, "1.00", [("1.5", -1, -9), ("0.5", 0, -9), ("2", 1, -9), ("3", None, -9)])
    status, body = api("POST", "/system/daily-check")
    assert status == 200
    assert body["expired_batches_cleared"] == 2
    assert "total_items_removed_from_shelf" not in body  # removed: it added up different units
    product_qty, batch_total, by_id = stock(product_id)
    assert by_id == {yesterday: 0, today_batch: 0, tomorrow: 2, no_expiry: 3}
    assert product_qty == batch_total == 5
    in_sync(product_id)


def test_checkout_alerts_and_daily_check_agree_on_a_batch_expiring_today(api, make_product, stock):
    product_id, [today_batch] = make_product("Milk", StockUnit.LITRE, "1.00", [("1", 0, -9)])
    assert api("POST", "/checkout/", {"product_id": product_id, "quantity": 1})[0] == 400
    assert api("GET", "/alerts/expiring-soon/")[1]["batches"] == []
    api("POST", "/system/daily-check")
    assert stock(product_id)[2] == {today_batch: Decimal("0")}
