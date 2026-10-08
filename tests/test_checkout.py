from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlmodel import Session, select

from main import Sale, SaleBatchAllocation, StockUnit
from conftest import OTHER_BUSINESS_ID, day


def checkout(api, product_id, quantity):
    return api("POST", "/checkout/", {"product_id": product_id, "quantity": quantity})


def allocations(body):
    return [(a["batch_id"], a["quantity"]) for a in body["allocations"]]


def assert_in_sync(stock, product_id):
    product_qty, batch_total, _ = stock(product_id)
    assert product_qty == batch_total


# --- validation ---

@pytest.mark.parametrize("quantity", [0, -1, "0.0001", "abc", None])
def test_invalid_quantity_rejected(api, make_product, quantity):
    product_id, _ = make_product("Rice", StockUnit.KG, "1.00", [("5", None, 0)])
    status, _ = checkout(api, product_id, quantity)
    assert status == 422


def test_fractional_piece_sale_rejected(api, make_product, stock):
    product_id, _ = make_product("Eggs", StockUnit.PIECE, "0.50", [("12", 7, -1)])
    status, body = checkout(api, product_id, 1.5)
    assert status == 422
    assert body["detail"] == "Eggs is sold by the piece, so quantity must be a whole number."
    assert stock(product_id)[0] == 12


def test_whole_piece_sale_with_trailing_zeros_accepted(api, make_product, stock):
    product_id, [batch] = make_product("Eggs", StockUnit.PIECE, "0.50", [("12", 7, -1)])
    status, body = checkout(api, product_id, "2.000")
    assert status == 200
    assert allocations(body) == [(batch, 2)]
    assert_in_sync(stock, product_id)


def test_unknown_product_404(api, make_product):
    product_id, _ = make_product("Rice", StockUnit.KG, "1.00", [("5", None, 0)])
    assert checkout(api, product_id + 1000, 1)[0] == 404


def test_other_business_product_404(api, make_product):
    product_id, _ = make_product("Theirs", StockUnit.KG, "1.00", [("5", None, 0)], business_id=OTHER_BUSINESS_ID)
    assert checkout(api, product_id, 1)[0] == 404


# --- FIFO allocation ---

def test_sells_from_soonest_expiring_batch_first(api, make_product, stock):
    product_id, [later, sooner] = make_product("Rice", StockUnit.KG, "0.15", [("3", 10, -10), ("2", 5, -10)])
    status, body = checkout(api, product_id, 1.5)
    assert status == 200
    assert body["allocations"] == [{"batch_id": sooner, "quantity": 1.5, "expiry_date": str(day(5))}]
    assert body["message"] == "Successfully sold 1.5 kg of Rice"
    assert body["revenue"] == 0.23  # 0.15 x 1.5 = 0.225, rounded half-up
    assert_in_sync(stock, product_id)


def test_sale_spans_two_batches_and_records_allocations(api, engine, make_product, stock):
    product_id, [a, b] = make_product("Rice", StockUnit.KG, "0.15", [("0.5", 5, -10), ("3", 10, -10)])
    status, body = checkout(api, product_id, 2.25)
    assert status == 200
    assert allocations(body) == [(a, 0.5), (b, 1.75)]
    with Session(engine) as session:
        rows = session.exec(select(SaleBatchAllocation).where(SaleBatchAllocation.sale_id == body["sale_id"])
                            .order_by(SaleBatchAllocation.id)).all()
        sale = session.get(Sale, body["sale_id"])
    assert [(r.batch_id, r.quantity, r.business_id) for r in rows] == [(a, Decimal("0.5"), "1111"), (b, Decimal("1.75"), "1111")]
    assert sum(r.quantity for r in rows) == sale.quantity
    assert sale.total_price == Decimal("0.34")  # 0.3375 rounded half-up
    _, _, by_id = stock(product_id)
    assert by_id == {a: 0, b: Decimal("1.25")}
    assert_in_sync(stock, product_id)


def test_null_expiry_batches_used_last(api, make_product, stock):
    # The no-expiry batch was received first, but dated batches still go before it
    product_id, [no_expiry, dated] = make_product("Rice", StockUnit.KG, "1.00", [("4", None, -20), ("1.25", 10, -1)])
    status, body = checkout(api, product_id, 3)
    assert status == 200
    assert body["allocations"] == [
        {"batch_id": dated, "quantity": 1.25, "expiry_date": str(day(10))},
        {"batch_id": no_expiry, "quantity": 1.75, "expiry_date": None},
    ]
    assert_in_sync(stock, product_id)


def test_expired_batches_skipped_including_ones_expiring_today(api, make_product, stock):
    product_id, [yesterday, today_batch, fresh] = make_product(
        "Rice", StockUnit.KG, "1.00", [("1.5", -1, -30), ("0.5", 0, -30), ("2", 3, -1)])
    status, body = checkout(api, product_id, 2)
    assert status == 200
    assert allocations(body) == [(fresh, 2)]
    _, _, by_id = stock(product_id)
    assert by_id[yesterday] == Decimal("1.5") and by_id[today_batch] == Decimal("0.5")
    assert body["stock_remaining"] == 2  # expired stock is still on hand until the daily check
    assert_in_sync(stock, product_id)


def test_insufficient_stock_message_shows_sellable_and_expired(api, make_product, stock):
    product_id, _ = make_product("Rice", StockUnit.KG, "1.00", [("1.5", -1, -30), ("0.5", 0, -30), ("2.25", None, -1)])
    status, body = checkout(api, product_id, 3)
    assert status == 400
    assert body["detail"] == "Not enough stock! Only 2.25 kg of Rice can be sold; 2 kg has expired and is awaiting disposal."
    assert stock(product_id)[0] == Decimal("4.25")  # nothing changed
    assert_in_sync(stock, product_id)


def test_nothing_sellable_message(api, make_product):
    product_id, _ = make_product("Rice", StockUnit.KG, "1.00", [("2", -1, -30)])
    status, body = checkout(api, product_id, 0.001)
    assert status == 400
    assert body["detail"] == "Not enough stock! Only 0 kg of Rice can be sold; 2 kg has expired and is awaiting disposal."


def test_same_expiry_ordered_by_received_date_then_id(api, make_product):
    product_id, [newest, older_1, older_2] = make_product(
        "Oil", StockUnit.LITRE, "3.00", [("1", 9, -1), ("1", 9, -3), ("1", 9, -3)])
    status, body = checkout(api, product_id, 3)
    assert status == 200
    assert [a["batch_id"] for a in body["allocations"]] == [older_1, older_2, newest]


def test_stock_matches_batches_after_every_sale(api, make_product, stock):
    product_id, _ = make_product("Rice", StockUnit.KG, "0.15", [
        ("2", 5, -10), ("3", 10, -10), ("4", None, -20), ("1.5", -1, -30), ("0.5", 0, -30), ("0", 1, -5)])
    for quantity, expected_status in [(1.5, 200), (2.25, 200), (3, 200), (3, 400), (2.25, 200), (0.001, 400)]:
        status, _ = checkout(api, product_id, quantity)
        assert status == expected_status
        assert_in_sync(stock, product_id)
    assert stock(product_id)[0] == 2  # only the expired 1.5 + 0.5 remain


def test_sale_timestamp_is_naive_utc(api, engine, make_product):
    product_id, _ = make_product("Rice", StockUnit.KG, "1.00", [("5", None, 0)])
    status, body = checkout(api, product_id, 1)
    assert status == 200
    with Session(engine) as session:
        stamp = session.get(Sale, body["sale_id"]).timestamp
    assert stamp.tzinfo is None
    assert abs(datetime.now(timezone.utc).replace(tzinfo=None) - stamp) < timedelta(seconds=30)
