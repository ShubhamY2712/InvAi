"""scripts/run_daily_check.py: the daily check for every business, each in its own transaction."""
import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine

import main
from main import BusinessCategory, BusinessProfile, MovementReason, StockUnit
from conftest import BUSINESS_ID, OTHER_BUSINESS_ID
from scripts import run_daily_check as job

THIRD_ID = "3333"
SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "run_daily_check.py"


@pytest.fixture
def three_businesses(engine, make_product):
    """One product per business: 2 litres expired yesterday, 3 litres that never expire."""
    with Session(engine) as session:
        session.add(BusinessProfile(id=THIRD_ID, business_name="Third", category=BusinessCategory.RETAIL))
        session.commit()
    return {biz: make_product(f"Milk-{biz}", StockUnit.LITRE, "1.00", [("2", -1, -9), ("3", None, -9)], business_id=biz)[0]
            for biz in (BUSINESS_ID, OTHER_BUSINESS_ID, THIRD_ID)}


@pytest.fixture
def log_lines(caplog):
    caplog.set_level(logging.INFO, logger="daily_check")
    return lambda: [r.getMessage() for r in caplog.records if r.name == "daily_check"]


def disposals(movements, product_id):
    return movements(product_id, MovementReason.EXPIRY_DISPOSAL)


def test_clears_every_business_and_marks_entries_as_scheduled(engine, three_businesses, movements, in_sync, log_lines):
    assert job.run_all(engine) == 0
    for biz, product_id in three_businesses.items():
        assert in_sync(product_id) == 3
        [entry] = disposals(movements, product_id)
        assert (entry.quantity_change, entry.user_id, entry.note, entry.business_id) == (-2, None, "scheduled daily check", biz)
    assert log_lines() == [
        "business 1111: 1 batch cleared, 1 product affected, 0 inconsistencies",
        "business 2222: 1 batch cleared, 1 product affected, 0 inconsistencies",
        "business 3333: 1 batch cleared, 1 product affected, 0 inconsistencies",
        "daily check finished: 3 businesses, 3 ok, 0 failed; 3 batches cleared, 3 products affected, 0 inconsistencies",
    ]


def test_one_failing_business_is_rolled_back_and_the_others_complete(engine, three_businesses, movements, in_sync,
                                                                     log_lines, monkeypatch):
    real = main.run_daily_check

    def fails_after_changes(session, business_id, **kwargs):
        result = real(session, business_id, **kwargs)  # changes are made in the session...
        if business_id == OTHER_BUSINESS_ID:
            raise RuntimeError("boom")                  # ...then the business fails before commit
        return result

    monkeypatch.setattr(main, "run_daily_check", fails_after_changes)
    assert job.run_all(engine) == 1

    failed = three_businesses[OTHER_BUSINESS_ID]
    assert in_sync(failed) == 5 and disposals(movements, failed) == []  # fully rolled back
    for biz in (BUSINESS_ID, THIRD_ID):
        assert in_sync(three_businesses[biz]) == 3
    assert "business 2222: FAILED (RuntimeError: boom)" in log_lines()
    assert log_lines()[-1] == ("daily check finished: 3 businesses, 2 ok, 1 failed; "
                               "2 batches cleared, 2 products affected, 0 inconsistencies")


def test_second_run_the_same_day_changes_nothing(engine, three_businesses, stock, movements, in_sync, log_lines):
    assert job.run_all(engine) == 0
    snapshot = {pid: (stock(pid), [m.id for m in movements(pid)]) for pid in three_businesses.values()}
    assert job.run_all(engine) == 0
    assert {pid: (stock(pid), [m.id for m in movements(pid)]) for pid in three_businesses.values()} == snapshot
    assert log_lines()[-1] == ("daily check finished: 3 businesses, 3 ok, 0 failed; "
                               "0 batches cleared, 0 products affected, 0 inconsistencies")


def test_inconsistency_is_reported_but_not_a_failure(engine, three_businesses, set_product_quantity, stock, caplog, log_lines):
    broken = three_businesses[BUSINESS_ID]
    set_product_quantity(broken, "1")  # less than the 2 litres in its expired batch
    before = stock(broken)
    assert job.run_all(engine) == 0
    assert stock(broken) == before
    warning = [r for r in caplog.records if r.name == "daily_check" and r.levelno == logging.WARNING]
    assert [r.getMessage() for r in warning] == [
        "business 1111: 0 batches cleared, 0 products affected, 1 inconsistency (Milk-1111)"]
    assert log_lines()[-1].endswith("2 batches cleared, 2 products affected, 1 inconsistency")


def test_business_with_nothing_expired(engine, log_lines):
    assert job.run_all(engine) == 0
    assert log_lines()[0] == "business 1111: 0 batches cleared, 0 products affected, 0 inconsistencies"


def test_endpoint_still_records_the_caller(api, make_product, movements):
    product_id, _ = make_product("Milk", StockUnit.LITRE, "1.00", [("2", -1, -9)])
    status, body = api("POST", "/system/daily-check")
    assert status == 200 and body["message"] == "Daily health check complete."
    assert list(body) == ["message", "expired_batches_cleared", "products", "inconsistencies"]
    [entry] = disposals(movements, product_id)
    assert (entry.user_id, entry.note) == (1, None)


# --- the real process and its exit codes ---

def run_script(db_path):
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{db_path.as_posix()}", "SECRET_KEY": "test-secret"}
    result = subprocess.run([sys.executable, str(SCRIPT)], env=env, capture_output=True, text=True, timeout=120)
    return result.returncode, result.stdout + result.stderr


@pytest.fixture
def sqlite_file(tmp_path):
    path = tmp_path / "daily.db"
    eng = create_engine(f"sqlite:///{path.as_posix()}")
    SQLModel.metadata.create_all(eng)
    with Session(eng) as session:
        session.add(BusinessProfile(id="AAAA", business_name="A", category=BusinessCategory.RETAIL))
        session.add(BusinessProfile(id="BBBB", business_name="B", category=BusinessCategory.RETAIL))
        session.commit()
    yield path, eng
    eng.dispose()


def test_exit_code_0_when_every_business_succeeds(sqlite_file):
    code, output = run_script(sqlite_file[0])
    assert code == 0, output
    assert "daily check finished: 2 businesses, 2 ok, 0 failed" in output


def test_exit_code_1_when_any_business_fails(sqlite_file):
    path, eng = sqlite_file
    with eng.begin() as conn:
        conn.execute(text("DROP TABLE stock_movement"))
        conn.execute(text("DROP TABLE sale_batch_allocation"))
        conn.execute(text("DROP TABLE product_batch"))  # every business's check now fails
    code, output = run_script(path)
    assert code == 1, output
    assert "business AAAA: FAILED" in output and "business BBBB: FAILED" in output
    assert "daily check finished: 2 businesses, 0 ok, 2 failed" in output


def test_exit_code_1_when_businesses_cannot_be_listed(tmp_path):
    code, output = run_script(tmp_path / "empty.db")  # no tables at all
    assert code == 1 and "could not list businesses" in output
