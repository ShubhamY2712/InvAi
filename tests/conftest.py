"""Shared fixtures. Every test gets a fresh in-memory SQLite database; no test can reach a real database."""
import asyncio
import json
import os
from datetime import date, timedelta
from decimal import Decimal

# main reads these at import time, and its load_dotenv() never overrides variables that are already set
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["SECRET_KEY"] = "test-secret"

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import main
from main import BusinessCategory, BusinessProfile, Product, ProductBatch, User, UserRole

BUSINESS_ID = "1111"
OTHER_BUSINESS_ID = "2222"

# Far from the real date, so any code path still using date.today() for expiry would fail the tests
FIXED_TODAY = date(2031, 3, 10)
REAL_TODAY = main.today


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(main, "today", lambda: FIXED_TODAY)


def day(offset: int = 0) -> date:
    """FIXED_TODAY shifted by offset days."""
    return FIXED_TODAY + timedelta(days=offset)


@pytest.fixture
def engine(monkeypatch):
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(eng)
    monkeypatch.setattr(main, "engine", eng)
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
    token = main.create_access_token({"sub": "1", "business_id": BUSINESS_ID, "role": "Owner"})
    return lambda method, path, body=None: asgi_request(method, path, body, token)


@pytest.fixture
def make_product(engine):
    """make_product(name, unit, price, [(quantity, expiry_offset_or_None, received_offset), ...]) -> (product_id, [batch_ids]).
    Product.quantity is set to the sum of the batches, as it would be after the groundwork migration."""
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
