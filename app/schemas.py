"""Request bodies. Their class names are the schema names in the OpenAPI document."""
from datetime import date
from decimal import Decimal
from typing import Any

from sqlmodel import Field, SQLModel

from app.models import BusinessCategory, Money, StockUnit, UserRole


class OnboardingRequest(SQLModel):
    business_name: str
    category: BusinessCategory
    owner_username: str
    email: str
    password: str


class ProductCreate(SQLModel):
    name: str
    sku: str
    price: Money = Field(max_digits=12, decimal_places=2)
    quantity: int = 0
    description: str | None = None
    expiry_date: date | None = None  # expiry of the opening stock, if any
    unit: StockUnit = StockUnit.PIECE


class ProductUpdate(SQLModel):
    # Everything is optional because we only update what the frontend sends
    quantity: Any = None  # never accepted; declared so any attempt gets a clear 422 instead of being silently ignored
    name: str | None = None
    price: float | None = None
    description: str | None = None
    unit: StockUnit | None = None


class EmployeeCreate(SQLModel):
    username: str
    full_name: str
    email: str
    password: str # Plain text from the frontend, we will hash it below
    role: UserRole = UserRole.STAFF


class CheckoutRequest(SQLModel):
    product_id: int
    quantity: Decimal = Field(gt=0, max_digits=12, decimal_places=3)


class SupplierCreate(SQLModel):
    name: str
    contact_email: str | None = None
    phone: str | None = None


class PurchaseOrderCreate(SQLModel):
    supplier_id: int
    product_id: int
    quantity: Decimal = Field(gt=0, max_digits=12, decimal_places=3)  # whole numbers for piece products (checked in the service)
    unit_cost: Money = Field(ge=0, max_digits=12, decimal_places=2)
    expected_delivery_date: date | None = None  # must be after today()
