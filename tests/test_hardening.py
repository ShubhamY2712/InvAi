"""Phase 1 review fixes: validation, roles, accounts, SECRET_KEY, docs switch, and tidy-ups."""
import asyncio
import os
import subprocess
import sys
import urllib.parse
from decimal import Decimal
from pathlib import Path

import pytest
from dotenv import dotenv_values
from sqlmodel import Session, select

from app import security
from app.main import create_app
from app.models import BusinessCategory, Product, StockUnit, User
from conftest import BUSINESS_ID, asgi_request

ROOT = Path(__file__).resolve().parent.parent


def token_for(role, sub="1"):
    return security.create_access_token({"sub": sub, "business_id": BUSINESS_ID, "role": role})


def as_role(role):
    token = token_for(role)
    return lambda method, path, body=None: asgi_request(method, path, body, token)


def login(username, password):
    """POST /login/ as a form, like OAuth2 clients do. Returns (status, body)."""
    body = urllib.parse.urlencode({"username": username, "password": password}).encode()
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST", "scheme": "http",
             "path": "/login/", "raw_path": b"/login/", "query_string": b"", "client": ("t", 1), "server": ("t", 80),
             "root_path": "", "headers": [(b"host", b"t"), (b"content-type", b"application/x-www-form-urlencoded")]}
    out = {"body": b""}

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            out["status"] = message["status"]
        elif message["type"] == "http.response.body":
            out["body"] += message.get("body", b"")

    from app.main import app
    asyncio.run(app(scope, receive, send))
    return out["status"], out["body"].decode()


# --- 1. product validation ---

PRODUCT = {"name": "Rice", "sku": "R1", "price": 2}


@pytest.mark.parametrize("change", [{"quantity": -1}, {"price": -0.01}, {"name": ""}, {"name": "   "},
                                    {"name": "x" * 101}, {"sku": ""}, {"sku": "s" * 65}, {"description": "d" * 1001},
                                    {"price": 2.555}])
def test_product_create_rejects_bad_fields(api, engine, change):
    assert api("POST", "/products/", {**PRODUCT, **change})[0] == 422
    with Session(engine) as session:
        assert session.exec(select(Product)).all() == []


def test_product_create_accepts_limits_and_trims(api):
    status, body = api("POST", "/products/", {"name": "  " + "x" * 100 + "  ", "sku": " S " , "price": 0, "quantity": 0,
                                              "description": "d" * 1000})
    assert status == 200
    assert (len(body["product"]["name"]), body["product"]["sku"], body["product"]["price"]) == (100, "S", 0)


@pytest.mark.parametrize("change", [{"price": -1}, {"price": 2.555}, {"name": "  "}, {"name": "x" * 101},
                                    {"description": "d" * 1001}])
def test_product_patch_rejects_bad_fields(api, make_product, change):
    product_id, _ = make_product("Rice", StockUnit.KG, "1.00", [])
    assert api("PATCH", f"/products/{product_id}", change)[0] == 422


def test_product_patch_price_is_money(api, engine, make_product):
    product_id, _ = make_product("Rice", StockUnit.KG, "1.00", [])
    assert api("PATCH", f"/products/{product_id}", {"price": 2.5})[1]["product"]["price"] == 2.5
    with Session(engine) as session:
        assert session.get(Product, product_id).price == Decimal("2.50")


# --- 2. roles ---

def test_staff_cannot_add_suppliers_or_place_purchase_orders(make_product):
    product_id, _ = make_product("Rice", StockUnit.KG, "1.00", [])
    staff = as_role("Staff")
    status, body = staff("POST", "/suppliers/", {"name": "Vendor"})
    assert status == 403 and body["detail"] == "Only the Owner or a Manager can add suppliers."
    status, body = staff("POST", "/purchase-orders/", {"supplier_id": 1, "product_id": product_id, "quantity": 1, "unit_cost": 1})
    assert status == 403 and body["detail"] == "Only the Owner or a Manager can place purchase orders."


def test_manager_can_add_suppliers_and_place_purchase_orders(make_product):
    product_id, _ = make_product("Rice", StockUnit.KG, "1.00", [])
    manager = as_role("Manager")
    supplier_id = manager("POST", "/suppliers/", {"name": "Vendor"})[1]["supplier_id"]
    assert manager("POST", "/purchase-orders/", {"supplier_id": supplier_id, "product_id": product_id,
                                                 "quantity": 1, "unit_cost": 1})[0] == 200


def test_staff_can_receive_goods_but_not_stock_them(api, make_product):
    product_id, _ = make_product("Rice", StockUnit.KG, "1.00", [])
    supplier_id = api("POST", "/suppliers/", {"name": "Vendor"})[1]["supplier_id"]
    po_id = api("POST", "/purchase-orders/", {"supplier_id": supplier_id, "product_id": product_id,
                                              "quantity": 1, "unit_cost": 1})[1]["po_id"]
    staff = as_role("Staff")
    assert staff("PUT", f"/purchase-orders/{po_id}/deliver")[0] == 200
    assert staff("PUT", f"/purchase-orders/{po_id}/stock")[0] == 403


# --- 3. accounts ---

ONBOARD = {"business_name": "Shop", "category": BusinessCategory.RETAIL.value, "owner_username": "newowner",
           "email": "new@example.com", "password": "pw-123456"}


@pytest.mark.parametrize("password, accepted", [
    ("1234567", False),          # 7 characters
    ("12345678", True),
    ("x" * 72, True),            # 72 bytes: bcrypt's limit
    ("x" * 73, False),
    ("é" * 36, True),            # 36 characters, 72 bytes
    ("é" * 37, False),           # 37 characters, 74 bytes
])
def test_onboarding_password_length(engine, password, accepted):
    status, body = asgi_request("POST", "/onboard-business/", {**ONBOARD, "password": password})
    if accepted:
        assert status == 200
    else:
        assert status == 422
        assert "Password must be at least 8 characters and at most 72 bytes" in body["detail"][0]["msg"]


@pytest.mark.parametrize("change", [{"email": "not-an-email"}, {"email": "a b@example.com"}, {"email": "a@example"},
                                    {"owner_username": "   "}, {"owner_username": "u" * 51}, {"business_name": ""},
                                    {"business_name": "b" * 101}])
def test_onboarding_rejects_bad_fields(engine, change):
    assert asgi_request("POST", "/onboard-business/", {**ONBOARD, **change})[0] == 422


def test_onboarding_trims_username_and_email(engine):
    status, body = asgi_request("POST", "/onboard-business/", {**ONBOARD, "owner_username": "  trimmed  ", "email": " t@example.com "})
    assert status == 200
    with Session(engine) as session:
        user = session.get(User, body["owner_user_id"])
    assert (user.username, user.email) == ("trimmed", "t@example.com")


EMPLOYEE = {"username": "asha", "full_name": "Asha Rao", "email": "asha@example.com", "password": "pw-123456"}


def test_employee_full_name_is_stored(api, engine):
    status, body = api("POST", "/employees/", EMPLOYEE)
    assert status == 200
    with Session(engine) as session:
        assert session.get(User, body["employee_id"]).full_name == "Asha Rao"


@pytest.mark.parametrize("change, detail", [
    ({"email": "owner@example.com"}, "Email 'owner@example.com' is already registered."),
    ({"username": "owner"}, "Username 'owner' is already taken."),
])
def test_duplicate_employee_is_409(api, engine, change, detail):
    status, body = api("POST", "/employees/", {**EMPLOYEE, **change})
    assert status == 409 and body["detail"] == detail
    with Session(engine) as session:
        assert len(session.exec(select(User)).all()) == 1  # only conftest's owner


@pytest.mark.parametrize("change", [{"password": "short"}, {"password": "x" * 73}, {"email": "nope"}, {"full_name": " "},
                                    {"full_name": "f" * 101}, {"username": ""}, {"username": "u" * 51}])
def test_employee_rejects_bad_fields(api, change):
    assert api("POST", "/employees/", {**EMPLOYEE, **change})[0] == 422


def test_login_with_an_overlong_password_is_401_not_500(engine):
    asgi_request("POST", "/onboard-business/", ONBOARD)
    assert login("newowner", "pw-123456")[0] == 200
    assert login("newowner", "x" * 200) == (401, '{"detail":"Incorrect username or password"}')


@pytest.mark.parametrize("change", [{"name": ""}, {"name": "n" * 101}, {"contact_email": "nope"}, {"phone": "1" * 33}])
def test_supplier_rejects_bad_fields(api, change):
    assert api("POST", "/suppliers/", {"name": "Vendor", **change})[0] == 422


# --- 5. SECRET_KEY ---

@pytest.mark.parametrize("value, message", [
    (None, "SECRET_KEY is not set"),
    ("", "SECRET_KEY is not set"),
    ("replace-with-a-long-random-string", "still the .env.example placeholder"),
    ("k" * 31, "at least 32 characters"),
])
def test_secret_key_rules(value, message):
    with pytest.raises(RuntimeError, match=message):
        security.validate_secret_key(value)


def test_32_character_secret_key_is_accepted():
    assert security.validate_secret_key("k" * 32) == "k" * 32


def test_placeholder_matches_env_example():
    assert dotenv_values(ROOT / ".env.example")["SECRET_KEY"] == security.SECRET_KEY_PLACEHOLDER


def test_placeholder_secret_key_stops_the_app_starting():
    env = {**os.environ, "SECRET_KEY": "replace-with-a-long-random-string", "DATABASE_URL": "sqlite://", "CORS_ORIGINS": ""}
    result = subprocess.run([sys.executable, "-m", "uvicorn", "app.main:app", "--port", "0"], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode != 0 and "SECRET_KEY is still the .env.example placeholder" in result.stderr


# --- 6. docs switch and /dev/me/ ---

DOC_PATHS = ["/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"]


def status_of(app, path):
    out = {}
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET", "scheme": "http",
             "path": path, "raw_path": path.encode(), "query_string": b"", "headers": [(b"host", b"t")],
             "client": ("t", 1), "server": ("t", 80), "root_path": ""}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            out["status"] = message["status"]

    asyncio.run(app(scope, receive, send))
    return out["status"]


@pytest.mark.parametrize("value", ["false", "FALSE", "0", "no"])
def test_docs_can_be_turned_off(monkeypatch, value):
    monkeypatch.setenv("DOCS_ENABLED", value)
    app = create_app()
    assert [status_of(app, p) for p in DOC_PATHS] == [404, 404, 404, 404]


@pytest.mark.parametrize("value", ["true", "1", "yes", None])
def test_docs_on_by_default(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("DOCS_ENABLED", raising=False)
    else:
        monkeypatch.setenv("DOCS_ENABLED", value)
    app = create_app()
    assert [status_of(app, p) for p in DOC_PATHS] == [200, 200, 200, 200]


def test_unrecognised_docs_setting_stops_startup(monkeypatch):
    monkeypatch.setenv("DOCS_ENABLED", "maybe")
    with pytest.raises(RuntimeError, match='DOCS_ENABLED must be true or false'):
        create_app()


def test_dev_me_is_gone(api):
    assert api("GET", "/dev/me/")[0] == 404


# --- 7. tidy-ups ---

def test_checkout_401_does_not_include_the_user_id(make_product):
    product_id, _ = make_product("Rice", StockUnit.KG, "1.00", [("5", None, -1)])
    status, body = asgi_request("POST", "/checkout/", {"product_id": product_id, "quantity": 1}, token_for("Owner", sub="999"))
    assert status == 401 and body["detail"] == "User not found."


@pytest.mark.parametrize("method, path, body", [("POST", "/checkout/", {"product_id": 1, "quantity": 1}), ("GET", "/sales/", None)])
def test_non_numeric_token_subject_is_401_not_500(engine, method, path, body):
    status, response = asgi_request(method, path, body, token_for("Owner", sub="not-a-number"))
    assert status == 401 and response["detail"] == "Could not validate credentials"
