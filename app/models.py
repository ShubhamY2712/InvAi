"""Database models: enums, field types and tables.

Imports nothing that needs SECRET_KEY or a database connection, so Alembic (migrations/env.py) and scripts
can import it on their own."""
import secrets
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Annotated

from pydantic import PlainSerializer
from sqlalchemy import DateTime, Index
from sqlalchemy.types import TypeDecorator
from sqlmodel import Field, SQLModel

from app.timeutils import utc_now

# Explicit constraint names, identical to Postgres's own defaults, so Alembic migrations can refer to them
# (an unnamed constraint can't be dropped or altered by a migration). Must be set before the models below.
SQLModel.metadata.naming_convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "%(table_name)s_%(column_0_name)s_key",
    "ck": "%(table_name)s_%(constraint_name)s_check",
    "fk": "%(table_name)s_%(column_0_name)s_fkey",
    "pk": "%(table_name)s_pkey",
}


# --- 1. THE SAAS DEFINITIONS (Fixed Categories & Roles) ---

class BusinessCategory(str, Enum):
    RETAIL = "General & Daily Retail"
    HEALTHCARE = "Healthcare & Wellness"
    FNB = "Food & Beverage (F&B)"
    FASHION = "Fashion & Apparel"
    TECH = "Tech & Electronics"
    INDUSTRIAL = "Industrial & Hardware"
    SERVICES = "Service-Based Inventory"


class StockUnit(str, Enum):
    PIECE = "piece"
    KG = "kg"
    G = "g"
    LITRE = "litre"
    ML = "ml"


class UserRole(str, Enum):
    OWNER = "Owner"
    STAFF = "Staff"
    MANAGER = "Manager"


class MovementReason(str, Enum):
    OPENING = "opening"
    PURCHASE_RECEIPT = "purchase_receipt"
    SALE = "sale"
    AUDIT_INCREASE = "audit_increase"
    AUDIT_DECREASE = "audit_decrease"
    EXPIRY_DISPOSAL = "expiry_disposal"


# --- FIELD TYPES ---

# Stock quantities stay Decimal in Python but go out as JSON numbers (Pydantic would otherwise emit strings)
Quantity = Annotated[Decimal, PlainSerializer(float, return_type=float, when_used="json")]

# Money: Decimal in Python, NUMERIC(12,2) in the database, a JSON number in responses
Money = Annotated[Decimal, PlainSerializer(float, return_type=float, when_used="json")]


class UTCDateTime(TypeDecorator):
    """timestamptz column that always hands back aware UTC datetimes.
    Postgres returns timestamptz values in the session's time zone (e.g. +05:30 on a server set to India),
    and SQLite returns them without any time zone; both come back as UTC here. Naive datetimes are refused
    on the way in, because Postgres would read them in the session's time zone."""
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError(f"naive datetime {value!r} for a timestamptz column; use an aware UTC value (utc_now())")
        return value.astimezone(timezone.utc)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


# Pydantic would write UTC datetimes as "...Z"; send "+00:00", like every other timestamp in the API
UTCTimestamp = Annotated[datetime, PlainSerializer(lambda value: value.isoformat(), return_type=str, when_used="json")]


# --- ID GENERATOR LOGIC ---
# No 0/O or 1/I, so IDs can be read out and typed without mix-ups
BUSINESS_ID_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
BUSINESS_ID_LENGTH = 8  # 32^8 ≈ 1.1 trillion IDs


def generate_business_id() -> str:
    """Generates a random 8-character business ID (e.g. "K7QM2XRA") using a cryptographically secure source."""
    return "".join(secrets.choice(BUSINESS_ID_ALPHABET) for _ in range(BUSINESS_ID_LENGTH))


# --- MULTI-TENANT DATABASE TABLES ---

class BusinessProfile(SQLModel, table=True):
    # 8-character random string ID; onboarding retries if one is ever already taken
    id: str = Field(default_factory=generate_business_id, primary_key=True)
    business_name: str
    category: BusinessCategory


class User(SQLModel, table=True):
    __tablename__ = "users"
    id: int | None = Field(default=None, primary_key=True) # <--- The DB handles this!
    username: str = Field(unique=True, index=True)
    email: str = Field(unique=True, index=True)
    hashed_password: str
    full_name: str | None = Field(default=None, max_length=100)  # set for employees; owners and older users have none
    role: UserRole = Field(default=UserRole.STAFF)
    business_id: str = Field(foreign_key="businessprofile.id", index=True)


class Product(SQLModel, table=True):
    __tablename__ = "product"
    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    sku: str = Field(index=True) # Stock Keeping Unit (Barcode equivalent)
    description: str | None = None
    price: Money = Field(max_digits=12, decimal_places=2)
    quantity: Quantity = Field(default=Decimal("0"), max_digits=12, decimal_places=3)
    unit: StockUnit = Field(default=StockUnit.PIECE)

    # The crucial multi-tenant lock: This ties the product to a specific business
    business_id: str = Field(foreign_key="businessprofile.id", index=True)

    min_stock_level: Quantity = Field(default=Decimal("10"), max_digits=12, decimal_places=3)

    # Deactivated products keep their history but can't be sold, ordered, stocked or audited
    is_active: bool = Field(default=True)


class Sale(SQLModel, table=True):
    __tablename__ = "sales"
    # Reports filter one business's sales by time range
    __table_args__ = (Index("ix_sales_business_id_timestamp", "business_id", "timestamp"),)

    id: int | None = Field(default=None, primary_key=True)
    product_id: int = Field(foreign_key="product.id", index=True) # What was sold
    user_id: int = Field(foreign_key="users.id", index=True)       # Who sold it (Ankit or Rahul)
    business_id: str = Field(foreign_key="businessprofile.id") # Multi-tenant lock; indexed by ix_sales_business_id_timestamp
    quantity: Quantity = Field(max_digits=12, decimal_places=3)
    total_price: Money = Field(max_digits=12, decimal_places=2)

    # Automatically stamps the exact millisecond the sale happens
    timestamp: UTCTimestamp = Field(default_factory=utc_now, sa_type=UTCDateTime)


class Supplier(SQLModel, table=True):
    __tablename__ = "suppliers"

    id: int | None = Field(default=None, primary_key=True)
    name: str
    contact_email: str | None = None
    phone: str | None = None
    business_id: str = Field(foreign_key="businessprofile.id", index=True) # Locks this supplier to FreshMart only


class PurchaseOrder(SQLModel, table=True):
    __tablename__ = "purchase_order"

    id: int | None = Field(default=None, primary_key=True)
    supplier_id: int = Field(foreign_key="suppliers.id", index=True)
    product_id: int = Field(foreign_key="product.id", index=True)
    business_id: str = Field(foreign_key="businessprofile.id", index=True)
    quantity: Quantity = Field(max_digits=12, decimal_places=3)
    unit_cost: Money = Field(max_digits=12, decimal_places=2)
    total_cost: Money = Field(max_digits=12, decimal_places=2)
    status: str = Field(default="PENDING")
    expected_delivery_date: date | None = None
    # Set at stocking: what arrived, and how much of it was rejected (accepted = received - rejected)
    received_quantity: Quantity | None = Field(default=None, max_digits=12, decimal_places=3)
    rejected_quantity: Quantity | None = Field(default=None, max_digits=12, decimal_places=3)

    # --- The 3-Step AI Analytics Timestamps ---
    timestamp: UTCTimestamp = Field(default_factory=utc_now, sa_type=UTCDateTime)  # Step 1: Placed Order
    delivered_at: UTCTimestamp | None = Field(default=None, sa_type=UTCDateTime)     # Step 2: Reached Loading Dock
    stocked_at: UTCTimestamp | None = Field(default=None, sa_type=UTCDateTime)       # Step 3: Scanned to Shelf
    # Only pending POs can be cancelled; status becomes CANCELLED
    cancelled_at: UTCTimestamp | None = Field(default=None, sa_type=UTCDateTime)
    cancellation_reason: str | None = None


class ProductBatch(SQLModel, table=True):
    __tablename__ = "product_batch"  # <--- FORCE THE TABLE NAME
    id: int | None = Field(default=None, primary_key=True)

    # Notice how we use the exact strings we defined above
    product_id: int = Field(foreign_key="product.id", index=True)
    po_id: int | None = Field(default=None, foreign_key="purchase_order.id")

    business_id: str = Field(foreign_key="businessprofile.id", index=True)
    quantity: Quantity = Field(default=Decimal("0"), max_digits=12, decimal_places=3)
    received_date: date
    expiry_date: date | None = None  # NULL = never expires (e.g. opening stock)


class SaleBatchAllocation(SQLModel, table=True):
    __tablename__ = "sale_batch_allocation"
    id: int | None = Field(default=None, primary_key=True)
    sale_id: int = Field(foreign_key="sales.id", index=True)
    batch_id: int = Field(foreign_key="product_batch.id", index=True)
    quantity: Quantity = Field(max_digits=12, decimal_places=3)
    business_id: str = Field(foreign_key="businessprofile.id", index=True)


# --- STOCK MOVEMENT LEDGER (append-only: nothing updates or deletes entries; enforced by database triggers) ---

class StockMovement(SQLModel, table=True):
    """One entry per batch touched by a stock change. For every batch the entries sum to its quantity,
    and for every product they sum to Product.quantity."""
    __tablename__ = "stock_movement"
    __table_args__ = (Index("ix_stock_movement_business_product_created", "business_id", "product_id", "created_at"),)
    id: int | None = Field(default=None, primary_key=True)
    business_id: str = Field(foreign_key="businessprofile.id")
    product_id: int = Field(foreign_key="product.id")
    batch_id: int = Field(foreign_key="product_batch.id")
    quantity_change: Quantity = Field(max_digits=12, decimal_places=3)  # signed: + into stock, - out of stock
    reason: MovementReason
    sale_id: int | None = Field(default=None, foreign_key="sales.id")
    po_id: int | None = Field(default=None, foreign_key="purchase_order.id")
    user_id: int | None = Field(default=None, foreign_key="users.id")
    note: str | None = None
    product_quantity_after: Quantity = Field(max_digits=12, decimal_places=3)
    created_at: UTCTimestamp = Field(default_factory=utc_now, sa_type=UTCDateTime)
