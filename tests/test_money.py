from decimal import Decimal

import pytest

import main
from main import StockUnit


@pytest.mark.parametrize("value, expected", [
    ("0.125", "0.13"), ("0.135", "0.14"), ("2.675", "2.68"), ("1.005", "1.01"), ("0.124", "0.12"), ("7", "7.00"),
])
def test_round_money_is_half_up(value, expected):
    assert main.round_money(Decimal(value)) == Decimal(expected)


@pytest.fixture
def supplier_id(api):
    return api("POST", "/suppliers/", {"name": "Vendor"})[1]["supplier_id"]


def test_purchase_order_total(api, make_product, supplier_id):
    product_id, _ = make_product("Eggs", StockUnit.PIECE, "0.50", [("12", 7, -1)])
    status, body = api("POST", "/purchase-orders/", {"supplier_id": supplier_id, "product_id": product_id,
                                                     "quantity": 7, "unit_cost": 0.15})
    assert status == 200
    assert body["expense"] == 1.05


def test_unit_cost_with_three_decimals_rejected(api, make_product, supplier_id):
    product_id, _ = make_product("Eggs", StockUnit.PIECE, "0.50", [("12", 7, -1)])
    status, _ = api("POST", "/purchase-orders/", {"supplier_id": supplier_id, "product_id": product_id,
                                                  "quantity": 1, "unit_cost": 0.155})
    assert status == 422


def test_sales_totals_and_quantities_are_json_numbers(api, make_product):
    product_id, _ = make_product("Rice", StockUnit.KG, "0.15", [("5", None, 0)])
    api("POST", "/checkout/", {"product_id": product_id, "quantity": 1.5})
    status, body = api("GET", "/sales/")
    assert status == 200
    assert body["total_revenue"] == 0.23
    [sale] = body["sales_data"]
    assert isinstance(sale["total_price"], float) and sale["total_price"] == 0.23
    assert isinstance(sale["quantity"], float) and sale["quantity"] == 1.5
