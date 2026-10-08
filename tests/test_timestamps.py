"""Timestamps are timestamptz: aware UTC in Python, sent with +00:00, never naive."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import StatementError
from sqlmodel import Session, select

from app import timeutils
from app.models import Sale, StockUnit, UTCDateTime
from app.timeutils import ist_date, ist_day_start_utc
from conftest import BUSINESS_ID

IST = timezone(timedelta(hours=5, minutes=30))


def test_utc_now_is_aware_utc():
    assert timeutils.utc_now().utcoffset() == timedelta(0)


def test_naive_datetimes_are_refused(engine, make_product):
    product_id, _ = make_product("Rice", StockUnit.KG, "1.00", [])
    with Session(engine) as session:
        session.add(Sale(product_id=product_id, user_id=1, business_id=BUSINESS_ID, quantity=Decimal("1"),
                         total_price=Decimal("1"), timestamp=datetime(2031, 3, 4, 18, 30)))
        with pytest.raises(StatementError, match="naive datetime"):
            session.commit()


def test_other_offsets_are_stored_and_read_back_as_the_same_moment_in_utc(engine, make_product):
    product_id, _ = make_product("Rice", StockUnit.KG, "1.00", [])
    moment_in_ist = datetime(2031, 3, 5, 0, 0, tzinfo=IST)  # = 2031-03-04 18:30 UTC
    with Session(engine) as session:
        session.add(Sale(product_id=product_id, user_id=1, business_id=BUSINESS_ID, quantity=Decimal("1"),
                         total_price=Decimal("1"), timestamp=moment_in_ist))
        session.commit()
    with Session(engine) as session:
        stored = session.exec(select(Sale)).one().timestamp
    assert stored == moment_in_ist
    assert (stored.isoformat()) == "2031-03-04T18:30:00+00:00"


def test_result_processing_normalises_to_utc():
    column_type = UTCDateTime()
    assert column_type.process_result_value(datetime(2031, 3, 4, 18, 30), None) == datetime(2031, 3, 4, 18, 30, tzinfo=timezone.utc)
    from_ist_session = datetime(2031, 3, 5, 0, 0, tzinfo=IST)  # what psycopg2 returns on a server set to India
    assert column_type.process_result_value(from_ist_session, None).isoformat() == "2031-03-04T18:30:00+00:00"


def test_ist_day_starts_at_1830_utc_the_day_before():
    assert ist_day_start_utc(date(2031, 3, 5)) == datetime(2031, 3, 4, 18, 30, tzinfo=timezone.utc)


def test_postgres_ist_date_is_a_single_conversion():
    sql = str(select(ist_date(Sale.timestamp)).compile(dialect=postgresql.dialect()))
    assert "CAST(timezone('Asia/Kolkata', sales.timestamp) AS DATE)" in sql
    assert "timezone('UTC'" not in sql


def test_api_timestamps_carry_the_utc_offset(api, make_product):
    product_id, _ = make_product("Rice", StockUnit.KG, "1.00", [("5", None, -9)])
    assert api("POST", "/checkout/", {"product_id": product_id, "quantity": 1})[0] == 200
    sale_stamp = api("GET", "/sales/")[1]["sales_data"][0]["timestamp"]

    supplier_id = api("POST", "/suppliers/", {"name": "V"})[1]["supplier_id"]
    po_id = api("POST", "/purchase-orders/", {"supplier_id": supplier_id, "product_id": product_id,
                                              "quantity": 2, "unit_cost": 1})[1]["po_id"]
    cancelled_at = api("POST", f"/purchase-orders/{po_id}/cancel")[1]["cancelled_at"]

    today = timeutils.utc_now().astimezone(timeutils.BUSINESS_TZ).date()
    movement_stamp = api("GET", f"/inventory/movements?from_date={today - timedelta(days=1)}&to_date={today}")[1]["movements"][0]["created_at"]

    for stamp in (sale_stamp, cancelled_at, movement_stamp):
        assert stamp.endswith("+00:00"), stamp
        assert abs(datetime.fromisoformat(stamp) - datetime.now(timezone.utc)) < timedelta(minutes=1)
