"""Shared fixtures. Every test gets a fresh in-memory SQLite database; no test can reach a real database."""
import asyncio
import json
import os
from datetime import date, timedelta
from decimal import Decimal

# main reads these at import time, and its load_dotenv() never overrides variables that are already set
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["SECRET_KEY"] = "test-secret"
os.environ["CORS_ORIGINS"] = ""  # tests that need CORS build their own app (tests/test_cors.py)

import pytest
from dotenv import dotenv_values
from sqlalchemy import create_engine as sa_create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app import db, main, models, security, timeutils
from app.services import ledger
from app.models import BusinessCategory, BusinessProfile, Product, ProductBatch, StockMovement, User, UserRole

BUSINESS_ID = "1111"
OTHER_BUSINESS_ID = "2222"

# Far from the real date, so any code path still using date.today() for expiry would fail the tests
FIXED_TODAY = date(2031, 3, 10)
REAL_TODAY = timeutils.today


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(timeutils, "today", lambda: FIXED_TODAY)


def day(offset: int = 0) -> date:
    """FIXED_TODAY shifted by offset days."""
    return FIXED_TODAY + timedelta(days=offset)


@pytest.fixture
def engine(monkeypatch):
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(eng)
    monkeypatch.setattr(db, "engine", eng)
    with Session(eng) as session:
        session.add(BusinessProfile(id=BUSINESS_ID, business_name="Shop", category=BusinessCategory.RETAIL))
        session.add(BusinessProfile(id=OTHER_BUSINESS_ID, business_name="Other", category=BusinessCategory.RETAIL))
        session.add(User(id=1, username="owner", email="owner@example.com", hashed_password="x",
                         role=UserRole.OWNER, business_id=BUSINESS_ID))
        session.commit()
    yield eng
    eng.dispose()


def asgi_request(method: str, path: str, body=None, token: str | None = None):
    """Calls the app directly over ASGI (no HTTP client or lifespan). Returns (status, parsed JSON)."""
    path_only, _, query = path.partition("?")
    raw_body = json.dumps(body).encode() if body is not None else b""
    headers = [(b"host", b"test"), (b"content-type", b"application/json")]
    if token:
        headers.append((b"authorization", f"Bearer {token}".encode()))
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method, "scheme": "http",
        "path": path_only, "raw_path": path_only.encode(), "query_string": query.encode(), "headers": headers,
        "client": ("test", 1), "server": ("test", 80), "root_path": "",
    }
    response = {"status": None, "body": b""}

    async def receive():
        return {"type": "http.request", "body": raw_body, "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            response["status"] = message["status"]
        elif message["type"] == "http.response.body":
            response["body"] += message.get("body", b"")

    asyncio.run(main.app(scope, receive, send))
    return response["status"], json.loads(response["body"])


@pytest.fixture
def api(engine):
    """api(method, path, body=None) as the business owner."""
    token = security.create_access_token({"sub": "1", "business_id": BUSINESS_ID, "role": "Owner"})
    return lambda method, path, body=None: asgi_request(method, path, body, token)


@pytest.fixture
def make_product(engine):
    """make_product(name, unit, price, [(quantity, expiry_offset_or_None, received_offset), ...]) -> (product_id, [batch_ids]).
    Product.quantity is set to the sum of the batches, and each non-empty batch gets an opening ledger entry,
    as they would after the groundwork and ledger migrations."""
    def _make(name, unit, price, batches, business_id=BUSINESS_ID):
        with Session(engine) as session:
            product = Product(name=name, sku=name, price=Decimal(price), unit=unit, business_id=business_id,
                              quantity=sum((Decimal(q) for q, _, _ in batches), Decimal("0")))
            session.add(product)
            session.flush()
            batch_ids = []
            for quantity, expiry_offset, received_offset in batches:
                batch = ProductBatch(product_id=product.id, business_id=business_id, quantity=Decimal(quantity),
                                     received_date=day(received_offset),
                                     expiry_date=None if expiry_offset is None else day(expiry_offset))
                session.add(batch)
                session.flush()
                batch_ids.append(batch.id)
            ledger.record_movements(session, product, models.MovementReason.OPENING,
                                  [(batch_id, Decimal(q)) for batch_id, (q, _, _) in zip(batch_ids, batches) if Decimal(q) != 0])
            session.commit()
            return product.id, batch_ids
    return _make


@pytest.fixture
def stock(engine):
    """stock(product_id) -> (Product.quantity, sum of its batch quantities, {batch_id: quantity})."""
    def _stock(product_id):
        with Session(engine) as session:
            product_qty = session.get(Product, product_id).quantity
            batches = session.exec(select(ProductBatch).where(ProductBatch.product_id == product_id)).all()
            by_id = {b.id: b.quantity for b in batches}
            return product_qty, sum(by_id.values(), Decimal("0")), by_id
    return _stock


@pytest.fixture
def movements(engine):
    """movements(product_id=None, reason=None) -> that business-wide list of StockMovement rows, oldest first."""
    def _movements(product_id=None, reason=None):
        with Session(engine) as session:
            query = select(StockMovement).order_by(StockMovement.id)
            if product_id is not None:
                query = query.where(StockMovement.product_id == product_id)
            if reason is not None:
                query = query.where(StockMovement.reason == reason)
            return session.exec(query).all()
    return _movements


@pytest.fixture
def in_sync(stock, movements):
    """in_sync(product_id): asserts that Product.quantity equals the sum of its batches, that the ledger agrees
    (per batch: its entries sum to its quantity; per product: they sum to Product.quantity, and the latest
    product_quantity_after equals it), and returns Product.quantity."""
    def _check(product_id):
        product_qty, batch_total, by_batch = stock(product_id)
        assert product_qty == batch_total, f"Product.quantity {product_qty} != batch total {batch_total}"

        entries = movements(product_id)
        ledger_by_batch = {}
        for entry in entries:
            ledger_by_batch[entry.batch_id] = ledger_by_batch.get(entry.batch_id, Decimal("0")) + entry.quantity_change
        for batch_id, quantity in by_batch.items():
            assert ledger_by_batch.get(batch_id, Decimal("0")) == quantity, \
                f"batch {batch_id}: ledger sums to {ledger_by_batch.get(batch_id, 0)}, batch holds {quantity}"
        assert set(ledger_by_batch) <= set(by_batch), "ledger entries for batches this product doesn't have"
        assert sum(ledger_by_batch.values(), Decimal("0")) == product_qty, "ledger doesn't sum to Product.quantity"
        if entries:
            assert entries[-1].product_quantity_after == product_qty, \
                f"latest product_quantity_after {entries[-1].product_quantity_after} != Product.quantity {product_qty}"
        return product_qty
    return _check


@pytest.fixture
def set_product_quantity(engine):
    """Forces Product.quantity out of line with its batches, to simulate drift from before batch tracking."""
    def _set(product_id, quantity):
        with Session(engine) as session:
            product = session.get(Product, product_id)
            product.quantity = Decimal(quantity)
            session.add(product)
            session.commit()
    return _set


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def local_postgres_url() -> str | None:
    """The DATABASE_URL from .env if it is a reachable Postgres server on this machine (conftest replaces the
    environment's DATABASE_URL with SQLite, so .env is read directly)."""
    url = dotenv_values(os.path.join(PROJECT_ROOT, ".env")).get("DATABASE_URL")
    if not url:
        return None
    parsed = make_url(url)
    if parsed.get_backend_name() != "postgresql" or parsed.host not in ("localhost", "127.0.0.1"):
        return None
    try:
        engine = sa_create_engine(parsed.set(database="postgres"), connect_args={"connect_timeout": 3})
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
    except Exception:
        return None
    return url
