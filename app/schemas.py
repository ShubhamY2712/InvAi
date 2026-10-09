"""Request bodies. Their class names are the schema names in the OpenAPI document.

Free-text fields are trimmed and length-capped; anything out of bounds is a 422 before any service runs."""
import re
from datetime import date
from decimal import Decimal
from typing import Annotated, Any

from pydantic import AfterValidator, StringConstraints
from sqlmodel import Field, SQLModel

from app.models import BusinessCategory, Money, StockUnit, UserRole

# --- Shared field types ---

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Username = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]
Sku = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
Description = Annotated[str, StringConstraints(max_length=1000)]
Phone = Annotated[str, StringConstraints(strip_whitespace=True, max_length=32)]

PASSWORD_MIN_CHARS = 8
PASSWORD_MAX_BYTES = 72  # bcrypt can't hash more


def _check_password(value: str) -> str:
    if len(value) < PASSWORD_MIN_CHARS or len(value.encode("utf-8")) > PASSWORD_MAX_BYTES:
        raise ValueError(f"Password must be at least {PASSWORD_MIN_CHARS} characters and at most {PASSWORD_MAX_BYTES} bytes "
                         "(bcrypt's limit; accented and non-Latin characters take 2 to 4 bytes each).")
    return value


Password = Annotated[str, AfterValidator(_check_password)]

# A practical check without a new dependency: one @, no spaces, a dot in the domain
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _check_email(value: str) -> str:
    if not _EMAIL_PATTERN.match(value):
        raise ValueError("Enter a valid email address, like name@example.com.")
    return value


Email = Annotated[str, StringConstraints(strip_whitespace=True, max_length=254), AfterValidator(_check_email)]


# --- Request bodies ---

class OnboardingRequest(SQLModel):
    business_name: Name
    category: BusinessCategory
    owner_username: Username
    email: Email
    password: Password


class ProductCreate(SQLModel):
    name: Name
    sku: Sku
    price: Money = Field(ge=0, max_digits=12, decimal_places=2)
    quantity: int = Field(default=0, ge=0)
    description: Description | None = None
    expiry_date: date | None = None  # expiry of the opening stock, if any
    unit: StockUnit = StockUnit.PIECE


class ProductUpdate(SQLModel):
    # Everything is optional because we only update what the frontend sends
    quantity: Any = None  # never accepted; declared so any attempt gets a clear 422 instead of being silently ignored
    name: Name | None = None
    price: Money | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    description: Description | None = None
    unit: StockUnit | None = None


class EmployeeCreate(SQLModel):
    username: Username
    full_name: Name
    email: Email
    password: Password # Plain text from the frontend, we will hash it below
    role: UserRole = UserRole.STAFF


class CheckoutRequest(SQLModel):
    product_id: int
    quantity: Decimal = Field(gt=0, max_digits=12, decimal_places=3)


class SupplierCreate(SQLModel):
    name: Name
    contact_email: Email | None = None
    phone: Phone | None = None


class PurchaseOrderCreate(SQLModel):
    supplier_id: int
    product_id: int
    quantity: Decimal = Field(gt=0, max_digits=12, decimal_places=3)  # whole numbers for piece products (checked in the service)
    unit_cost: Money = Field(ge=0, max_digits=12, decimal_places=2)
    expected_delivery_date: date | None = None  # must be after today()
