from fastapi import FastAPI, Depends, HTTPException, Query, status
from fastapi.security import OAuth2PasswordRequestForm, OAuth2PasswordBearer
from sqlmodel import Field, Session, SQLModel, create_engine, select
from typing import Annotated, Any, List, Literal
from pydantic import PlainSerializer
import os
from dotenv import load_dotenv
from contextlib import asynccontextmanager
from enum import Enum
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo
import secrets
import statistics
from sqlalchemy import Date, Index, func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.expression import FunctionElement
from alembic.config import Config as AlembicConfig
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from pathlib import Path
import bcrypt
from datetime import datetime, timedelta, timezone
from jose import jwt, JWTError
import enum


load_dotenv()

# --- JWT CONFIGURATION ---
# --- THE DOOR ---
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")
SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY is not set. Add it to your .env file.")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 # The user will stay logged in for 1 hour

# --- MODERN SECURITY ENGINE ---
def get_password_hash(password: str) -> str:
    """Hashes a password directly using the official bcrypt library."""
    # 1. Convert the string password to raw bytes
    pwd_bytes = password.encode('utf-8')
    # 2. Generate a cryptographic salt and hash the password
    salt = bcrypt.gensalt()
    hashed_password = bcrypt.hashpw(pwd_bytes, salt)
    # 3. Return as a standard string for the database
    return hashed_password.decode('utf-8')

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifies a typed password against the database hash."""
    password_byte_enc = plain_password.encode('utf-8')
    hashed_password_bytes = hashed_password.encode('utf-8')
    return bcrypt.checkpw(password_byte_enc, hashed_password_bytes)

def create_access_token(data: dict):
    """Creates a digitally signed JWT token containing user data."""
    to_encode = data.copy()
    
    # Calculate the exact time the token should expire
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    
    # Cryptographically sign the token using our SECRET_KEY
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

# --- THE BOUNCER & IDENTITY VERIFIER ---
def get_current_user(token: str = Depends(oauth2_scheme)):
    """Verifies the JWT and extracts the user data. Rejects invalid tokens."""
    
    # The standard error we throw if the token is fake, expired, or missing
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    try:
        # 1. Identity Verification (The Logic): Decode the cryptographic signature
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        
        # 2. Extract the vital information
        user_id: str = payload.get("sub")
        business_id: str = payload.get("business_id")
        
        if user_id is None or business_id is None:
            raise credentials_exception
            
        # 3. The Bouncer Opens the Door: Return the verified identity back to the route
        return {
            "user_id": user_id, 
            "business_id": business_id, 
            "role": payload.get("role")
        }
        
    except JWTError:
        # If the signature fails or token is expired, kick them out
        raise credentials_exception

DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_engine(DATABASE_URL, pool_pre_ping=True)

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

# Stock quantities stay Decimal in Python but go out as JSON numbers (Pydantic would otherwise emit strings)
Quantity = Annotated[Decimal, PlainSerializer(float, return_type=float, when_used="json")]

def format_qty(quantity, unit: StockUnit | None = None) -> str:
    """Formats a quantity for messages: no trailing zeros, plus the unit (e.g. "1.5 kg")."""
    text = format(Decimal(str(quantity)).normalize(), "f")
    return f"{text} {unit.value}" if unit else text

# Money: Decimal in Python, NUMERIC(12,2) in the database, a JSON number in responses
Money = Annotated[Decimal, PlainSerializer(float, return_type=float, when_used="json")]

def round_money(value) -> Decimal:
    """Rounds to 2 decimal places, half-up (0.125 -> 0.13)."""
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

BUSINESS_TZ = ZoneInfo("Asia/Kolkata")

def today() -> date:
    """Current date in Asia/Kolkata, used for every expiry decision (needs tzdata on Windows)."""
    return datetime.now(BUSINESS_TZ).date()

def require_future_expiry(expiry_date: date | None) -> None:
    """Raises 422 if expiry_date is today or earlier: checkout already treats such stock as expired."""
    current_date = today()
    if expiry_date is not None and expiry_date <= current_date:
        raise HTTPException(
            status_code=422,
            detail=f"expiry_date must be after today ({current_date}). Stock expiring today or earlier is already expired and can't be sold."
        )

def require_active(product: "Product") -> None:
    """Raises 409 if the product has been deactivated."""
    if not product.is_active:
        raise HTTPException(status_code=409, detail=f"{product.name} is inactive. Reactivate it first.")

def utc_now() -> datetime:
    """Current UTC time as a naive datetime. The timestamp columns are 'timestamp without time zone',
    and Postgres would shift an aware value into the session's time zone before storing it."""
    return datetime.now(timezone.utc).replace(tzinfo=None)

class UserRole(str, enum.Enum):
    OWNER = "Owner"
    STAFF = "Staff"
    MANAGER = "Manager"

def has_role(current_user: dict, *roles: UserRole) -> bool:
    """Returns True if the token's role matches one of roles (case-insensitive). Never raises on bad token data."""
    role = current_user.get("role") if isinstance(current_user, dict) else None
    allowed = {r.value.lower() for r in roles}
    return isinstance(role, str) and role.lower() in allowed

def require_role(current_user: dict, *allowed_roles: UserRole, detail: str = "You do not have permission to perform this action.") -> None:
    """Raises 403 unless the token's role matches one of allowed_roles (case-insensitive)."""
    if not has_role(current_user, *allowed_roles):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


# --- ID GENERATOR LOGIC ---
# No 0/O or 1/I, so IDs can be read out and typed without mix-ups
BUSINESS_ID_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
BUSINESS_ID_LENGTH = 8  # 32^8 ≈ 1.1 trillion IDs

def generate_business_id() -> str:
    """Generates a random 8-character business ID (e.g. "K7QM2XRA") using a cryptographically secure source."""
    return "".join(secrets.choice(BUSINESS_ID_ALPHABET) for _ in range(BUSINESS_ID_LENGTH))

# Which unique rule an IntegrityError broke, matched on the Postgres constraint name or the SQLite "table.column"
UNIQUE_VIOLATION_MARKERS = {
    "business_id": ("businessprofile_pkey", "businessprofile.id"),
    "username": ("ix_users_username", "users.username"),
    "email": ("ix_users_email", "users.email"),
}

def duplicate_field(exc: IntegrityError) -> str | None:
    """Returns "business_id", "username" or "email" if exc is a duplicate on that field, else None."""
    message = str(exc.orig)
    return next((field for field, markers in UNIQUE_VIOLATION_MARKERS.items()
                 if any(marker in message for marker in markers)), None)

# Explicit constraint names, identical to Postgres's own defaults, so Alembic migrations can refer to them
# (an unnamed constraint can't be dropped or altered by a migration). Must be set before the models below.
SQLModel.metadata.naming_convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "%(table_name)s_%(column_0_name)s_key",
    "ck": "%(table_name)s_%(constraint_name)s_check",
    "fk": "%(table_name)s_%(column_0_name)s_fkey",
    "pk": "%(table_name)s_pkey",
}

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
    product_id: int = Field(index=True) # What was sold
    user_id: int = Field(index=True)    # Who sold it (Ankit or Rahul)
    business_id: str = Field(foreign_key="businessprofile.id") # Multi-tenant lock; indexed by ix_sales_business_id_timestamp
    quantity: Quantity = Field(max_digits=12, decimal_places=3)
    total_price: Money = Field(max_digits=12, decimal_places=2)

    # Automatically stamps the exact millisecond the sale happens
    timestamp: datetime = Field(default_factory=utc_now)

class Supplier(SQLModel, table=True):
    __tablename__ = "suppliers"
    
    id: int | None = Field(default=None, primary_key=True)
    name: str
    contact_email: str | None = None
    phone: str | None = None
    business_id: str = Field(foreign_key="businessprofile.id", index=True) # Locks this supplier to FreshMart only


PROJECT_ROOT = Path(__file__).resolve().parent
SCHEMA_OUT_OF_DATE = "Database schema is out of date. Run: alembic upgrade head"

def check_schema_is_current(db_engine) -> None:
    """Raises RuntimeError unless the database is at the Alembic head revision(s) of this code."""
    heads = set(ScriptDirectory.from_config(AlembicConfig(str(PROJECT_ROOT / "alembic.ini"))).get_heads())
    with db_engine.connect() as connection:
        current = set(MigrationContext.configure(connection).get_current_heads())
    if current != heads:
        raise RuntimeError(f"{SCHEMA_OUT_OF_DATE} "
                           f"(database: {', '.join(sorted(current)) or 'none'}; code: {', '.join(sorted(heads))})")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # The schema is managed by Alembic (docs/DATABASE.md); never run against a database that isn't up to date
    check_schema_is_current(engine)
    yield
    
app = FastAPI(lifespan=lifespan)


# --- 4. FEATURE 1: DYNAMIC ONBOARDING ---

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

BUSINESS_ID_RETRIES = 5  # extra attempts, only when the generated business ID is already taken

@app.post("/onboard-business/")
def onboard_new_business(request: OnboardingRequest):
    hashed_password = get_password_hash(request.password)  # hashed once, not on every retry

    for _ in range(1 + BUSINESS_ID_RETRIES):
        with Session(engine) as session:
            new_business = BusinessProfile(
                id=generate_business_id(),
                business_name=request.business_name,
                category=request.category
            )
            # Create the Owner Profile; like every user, its id comes from the database sequence
            new_user = User(
                username=request.owner_username,
                email=request.email,
                hashed_password=hashed_password,
                role="Owner",
                business_id=new_business.id
            )
            session.add(new_business)
            session.add(new_user)

            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                duplicate = duplicate_field(exc)
                if duplicate == "business_id":
                    continue  # pick a new ID and try again
                if duplicate == "username":
                    raise HTTPException(status_code=409, detail=f"Username '{request.owner_username}' is already taken.")
                if duplicate == "email":
                    raise HTTPException(status_code=409, detail=f"Email '{request.email}' is already registered.")
                raise

            session.refresh(new_business)
            session.refresh(new_user)
            return {
                "success": True,
                "business_id": new_business.id,
                "owner_user_id": new_user.id
            }

    raise HTTPException(status_code=503, detail="Couldn't allocate a unique business ID. Please try again.")
    
@app.post("/login/")
def login(form_data: OAuth2PasswordRequestForm = Depends()):
    # We use your existing 'with Session(engine)' pattern here
    with Session(engine) as session:
        # 1. Search the database for the username the user typed in
        statement = select(User).where(User.username == form_data.username)
        user = session.exec(statement).first()

        # 2. If the user doesn't exist, kick them out
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect username or password",
            )

        # 3. If the user exists, run their typed password against the database hash
        is_password_correct = verify_password(form_data.password, user.hashed_password)
        
        if not is_password_correct:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect username or password",
            )

       # 4. If everything matches, generate the VIP Wristband (JWT)
        # We store the user.id as the "sub" (subject) which is standard practice
        token_payload = {
            "sub": str(user.id),
            "business_id": str(user.business_id),
            "role": user.role
        }
        
        access_token = create_access_token(data=token_payload)

        # 5. Return the token in the exact format standard Next.js frontends expect
        return {
            "access_token": access_token,
            "token_type": "bearer"
        }
    

    # --- 5. FEATURE 2 & 5: SECURE INVENTORY MANAGEMENT ---

@app.post("/products/")
def add_product(
    product_data: ProductCreate, 
    current_user: dict = Depends(get_current_user) # The Bouncer checks the token first!
):
    
    # SECURITY CHECK
    require_role(current_user, UserRole.OWNER, UserRole.MANAGER, detail="Staff cannot create new products.")
    require_future_expiry(product_data.expiry_date)

    with Session(engine) as session:
        # Create the database record, combining user data with the Bouncer's secure ID
        new_product = Product(
            name=product_data.name,
            sku=product_data.sku,
            price=product_data.price,
            quantity=product_data.quantity,
            unit=product_data.unit,
            description=product_data.description,
            business_id=current_user["business_id"] # <-- THE MULTI-TENANT LOCK
        )
        
        session.add(new_product)
        session.flush()  # assigns new_product.id for the opening batch

        # Opening stock gets its own batch so batch totals always match Product.quantity
        if new_product.quantity > 0:
            opening_batch = ProductBatch(
                product_id=new_product.id,
                po_id=None,
                business_id=current_user["business_id"],
                quantity=new_product.quantity,
                received_date=today(),
                expiry_date=product_data.expiry_date
            )
            session.add(opening_batch)
            session.flush()  # assigns opening_batch.id for the ledger
            record_movements(session, new_product, MovementReason.OPENING,
                             [(opening_batch.id, new_product.quantity)], user_id=acting_user_id(current_user))

        session.commit()
        session.refresh(new_product)

        return {
            "success": True,
            "message": f"Successfully added {new_product.name} to inventory.",
            "product": new_product
        }
    
@app.get("/products/")
def get_inventory(
    include_inactive: bool = Query(False, description="Also list deactivated products"),
    current_user: dict = Depends(get_current_user)
):
    with Session(engine) as session:
        # The ultimate security filter: ONLY return products matching this user's business_id
        statement = select(Product).where(Product.business_id == current_user["business_id"])
        if not include_inactive:
            statement = statement.where(Product.is_active == True)  # noqa: E712 (SQL expression)
        products = session.exec(statement).all()
        
        return {
            "success": True,
            "total_items": len(products),
            "inventory": products
        }
    
@app.patch("/products/{product_id}")
def update_product(
    product_id: int,
    product_update: ProductUpdate,
    current_user: dict = Depends(get_current_user)
):
    
    # SECURITY CHECK
    require_role(current_user, UserRole.OWNER, UserRole.MANAGER, detail="Staff cannot edit product details.")
    
    # Stock is batch-tracked, so it only changes through stocking, checkout, or a manual audit
    if "quantity" in product_update.model_fields_set:
        raise HTTPException(
            status_code=422,
            detail="Stock can't be changed here. Use purchase-order stocking, checkout, or a manual audit."
        )

    with Session(engine) as session:
        # 1. The Ultimate Security Check: Find the product, but ONLY if they own it
        # Locked so no stock can arrive between the unit check below and the save
        statement = select(Product).where(
            Product.id == product_id,
            Product.business_id == current_user["business_id"]
        ).with_for_update()
        product = session.exec(statement).first()

        # 2. If it doesn't exist (or they don't own it), reject them
        if not product:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Product not found or access denied"
            )

        # 3. The unit can only change while nothing has ever been measured in it
        if product_update.unit is not None and product_update.unit != product.unit:
            has_batches = session.exec(
                select(ProductBatch.id).where(ProductBatch.product_id == product.id).limit(1)
            ).first() is not None
            if product.quantity != 0 or has_batches:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Can't change the unit of {product.name} from {product.unit.value} to {product_update.unit.value}: "
                        "it has stock or batch history, and those quantities would change meaning."
                    )
                )
            product.unit = product_update.unit

        # 4. Update only the fields the frontend specifically asked to change
        if product_update.name is not None:
            product.name = product_update.name
        if product_update.price is not None:
            product.price = product_update.price
        if product_update.description is not None:
            product.description = product_update.description

        # 5. Save the changes to the vault
        session.add(product)
        session.commit()
        session.refresh(product)

        return {
            "success": True,
            "message": f"Successfully updated {product.name}",
            "product": product
        }
    
@app.delete("/products/{product_id}")
def delete_product(
    product_id: int, 
    current_user: dict = Depends(get_current_user)
):
    """Deactivates the product. Nothing is deleted: its batches, sales and ledger history stay."""
    # SECURITY CHECK
    require_role(current_user, UserRole.OWNER, detail="Only the Owner can delete products from the system.")
    
    with Session(engine) as session:
        # 1. Search for the product using the ID AND the Business ID (The Multi-Tenant Lock)
        # Locked, so stock or a new purchase order can't arrive between the checks below and the save
        statement = select(Product).where(
            Product.id == product_id, 
            Product.business_id == current_user["business_id"]
        ).with_for_update()
        product = session.exec(statement).first()

        # 2. If it's not there or belongs to someone else, say it's not found
        if not product:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, 
                detail="Product not found or access denied"
            )

        if not product.is_active:
            return {"success": True, "message": f"{product.name} is already inactive.", "product_id": product.id, "is_active": False}

        # 3. Only an empty product with no incoming stock can be deactivated
        if product.quantity > 0:
            raise HTTPException(
                status_code=409,
                detail=(f"{product.name} still has {format_qty(product.quantity, product.unit)} in stock. "
                        "Run a manual audit to bring it to 0 before deactivating.")
            )
        open_pos = session.exec(
            select(PurchaseOrder.id).where(
                PurchaseOrder.product_id == product.id,
                PurchaseOrder.status.in_(["PENDING", "DELIVERED"])
            ).order_by(PurchaseOrder.id)
        ).all()
        if open_pos:
            raise HTTPException(
                status_code=409,
                detail=(f"{product.name} has purchase orders that aren't stocked yet "
                        f"({', '.join(f'#{po_id}' for po_id in open_pos)}). Stock them before deactivating.")
            )

        # 4. Deactivate (history stays)
        product.is_active = False
        session.add(product)
        session.commit()

        return {"success": True, "message": f"{product.name} has been deactivated.", "product_id": product.id, "is_active": False}

@app.post("/products/{product_id}/reactivate")
def reactivate_product(product_id: int, current_user: dict = Depends(get_current_user)):
    require_role(current_user, UserRole.OWNER, detail="Only the Owner can reactivate products.")

    with Session(engine) as session:
        product = session.exec(
            select(Product).where(
                Product.id == product_id,
                Product.business_id == current_user["business_id"]
            ).with_for_update()
        ).first()
        if not product:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found or access denied")

        if product.is_active:
            return {"success": True, "message": f"{product.name} is already active.", "product_id": product.id, "is_active": True}

        product.is_active = True
        session.add(product)
        session.commit()
        return {"success": True, "message": f"{product.name} has been reactivated.", "product_id": product.id, "is_active": True}
    
    # --- 6. FEATURE 2: EMPLOYEE MANAGEMENT (The RBAC Loop) ---

class EmployeeCreate(SQLModel):
    username: str
    full_name: str
    email: str
    password: str # Plain text from the frontend, we will hash it below
    role: UserRole = UserRole.STAFF

@app.post("/employees/")
def add_employee(
    employee_data: EmployeeCreate, 
    current_user: dict = Depends(get_current_user) # THE BOUNCER
):
    # Optional Security: Only allow 'Owner' to add employees
    require_role(current_user, UserRole.OWNER, detail="Only business owners can add employees.")

    with Session(engine) as session:
        # 1. Check if the username is already taken
        existing_user = session.exec(select(User).where(User.username == employee_data.username)).first()
        if existing_user:
            raise HTTPException(status_code=400, detail="Username already exists.")

        # 2. Hash the employee's password before saving
        hashed_pw = get_password_hash(employee_data.password)

        # 3. Create the new User record linked to the Owner's business_id
        new_employee = User(
            username=employee_data.username,
            hashed_password=hashed_pw,
            email=employee_data.email,
            role=employee_data.role,
            business_id=current_user["business_id"] # SECURE LINKAGE
        )

        session.add(new_employee)
        session.commit()
        session.refresh(new_employee)

        return {
            "success": True,
            "message": f"Employee {new_employee.username} added to {current_user['business_id']}",
            "employee_id": new_employee.id
        }
    

  # --- 7. FEATURE 8: EXPIRY MANAGEMENT (The Alarm System) ---

@app.get("/alerts/expiring-soon/")
def get_expiring_batches(
    days: int = Query(7, ge=1, le=3650, description="How many days ahead to look"),
    current_user: dict = Depends(get_current_user)
):
    """Returns this business's batches expiring after today and up to today + days, soonest first.
    A batch expiring today is already expired (same rule as checkout and the daily check)."""
    current_date = today()
    window_end = current_date + timedelta(days=days)

    with Session(engine) as session:
        statement = (
            select(ProductBatch, Product.name)
            .join(Product, Product.id == ProductBatch.product_id)
            .where(
                ProductBatch.business_id == current_user["business_id"],
                ProductBatch.expiry_date > current_date,
                ProductBatch.expiry_date <= window_end,
                ProductBatch.quantity > 0
            )
            .order_by(ProductBatch.expiry_date, ProductBatch.id)
        )
        rows = session.exec(statement).all()

        batches = [
            {
                "batch_id": batch.id,
                "product_name": product_name,
                "expiry_date": batch.expiry_date,
                "quantity": batch.quantity,
                "days_left": (batch.expiry_date - current_date).days
            }
            for batch, product_name in rows
        ]

        return {
            "success": True,
            "business_id": current_user["business_id"],
            "window_end": window_end,
            "alert_count": len(batches),
            "batches": batches
        }
    
@app.get("/dev/me/")
def get_my_profile(current_user: dict = Depends(get_current_user)):
    """A protected route. You can only see this if the Bouncer lets you in."""
    return {
        "success": True,
        "message": "You made it past the bouncer!",
        "your_secure_data": current_user
    }

class CheckoutRequest(SQLModel):
    product_id: int
    quantity: Decimal = Field(gt=0, max_digits=12, decimal_places=3)

@app.post("/checkout/")
def process_checkout(
    request: CheckoutRequest,
    current_user: dict = Depends(get_current_user)
):    
    with Session(engine) as session:
        
        # 1. The Bulletproof Primary Key Lookup
        # We force it to be a string, strip any invisible spaces, then force to integer
        clean_user_id = int(str(current_user["user_id"]).strip())
        
        # session.get() is the safest way to find by ID
        user = session.get(User, clean_user_id)
        
        if not user:
            raise HTTPException(
                status_code=401, 
                detail=f"User ID {clean_user_id} not found."
            )

        # 2. Find and lock the product
        # Lock order is always product first, then its batches, so concurrent checkouts can't deadlock

        statement = select(Product).where(
            Product.id == request.product_id,
            Product.business_id == current_user["business_id"]
        ).with_for_update()
        product = session.exec(statement).first()

        # 3. Validation: Does it exist?
        if not product:
            raise HTTPException(status_code=404, detail="Product not found in your inventory.")
        require_active(product)

        # 4. Validation: piece products are sold in whole units only
        if product.unit == StockUnit.PIECE and request.quantity != request.quantity.to_integral_value():
            raise HTTPException(
                status_code=422,
                detail=f"{product.name} is sold by the piece, so quantity must be a whole number."
            )

        # 5. Lock the product's batches that still hold stock, in FIFO order
        batches = lock_batches_fifo(
            session,
            ProductBatch.product_id == product.id,
            ProductBatch.business_id == current_user["business_id"],
            ProductBatch.quantity > 0
        )

        # Same rule as /system/daily-check: a batch is expired from its expiry_date onward; NULL never expires
        current_date = today()
        sellable = [b for b in batches if b.expiry_date is None or b.expiry_date > current_date]
        expired = [b for b in batches if b.expiry_date is not None and b.expiry_date <= current_date]
        sellable_qty = sum((b.quantity for b in sellable), Decimal("0"))
        expired_qty = sum((b.quantity for b in expired), Decimal("0"))

        # 6. Validation: Do we have enough sellable stock?
        if sellable_qty < request.quantity:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Not enough stock! Only {format_qty(sellable_qty, product.unit)} of {product.name} can be sold; "
                    f"{format_qty(expired_qty, product.unit)} has expired and is awaiting disposal."
                )
            )

        # 7. Create the receipt (flush to get its id for the allocations)
        new_sale = Sale(
            product_id=product.id,
            user_id=user.id,
            business_id=current_user["business_id"],
            quantity=request.quantity,
            total_price=round_money(product.price * request.quantity)
        )
        session.add(new_sale)
        session.flush()

        # 8. Deduct from batches in FIFO order, recording which batches the sale drew from
        remaining = request.quantity
        allocations = []
        for batch in sellable:
            if remaining == 0:
                break
            taken = min(batch.quantity, remaining)
            batch.quantity -= taken
            remaining -= taken
            session.add(batch)
            session.add(SaleBatchAllocation(
                sale_id=new_sale.id,
                batch_id=batch.id,
                quantity=taken,
                business_id=current_user["business_id"]
            ))
            allocations.append({"batch_id": batch.id, "quantity": taken, "expiry_date": batch.expiry_date})

        product.quantity -= request.quantity
        session.add(product)
        record_movements(session, product, MovementReason.SALE,
                         [(a["batch_id"], -a["quantity"]) for a in allocations],
                         sale_id=new_sale.id, user_id=user.id)

        # 9. COMMIT! (stock, batches, sale, allocations and ledger together)
        session.commit()
        session.refresh(product)
        session.refresh(new_sale)

        return {
            "success": True,
            "message": f"Successfully sold {format_qty(request.quantity, product.unit)} of {product.name}",
            "revenue": new_sale.total_price,
            "stock_remaining": product.quantity,
            "sale_id": new_sale.id,
            "allocations": allocations
        }
    
@app.get("/sales/")
def get_sales_history(current_user: dict = Depends(get_current_user)):
    with Session(engine) as session:
        # Clean the token data just like we did in checkout
        clean_user_id = int(str(current_user["user_id"]).strip())
        
        # The Logic Split: Owner vs Staff
        if has_role(current_user, UserRole.OWNER, UserRole.MANAGER):
            # The Boss sees EVERYTHING for this specific business
            statement = select(Sale).where(Sale.business_id == current_user["business_id"])
        else:
            # The Staff only sees the sales attached to their specific user_id
            statement = select(Sale).where(
                Sale.business_id == current_user["business_id"],
                Sale.user_id == clean_user_id
            )
        
        sales = session.exec(statement).all()
        
        # Calculate quick analytics for the response
        total_revenue = sum(sale.total_price for sale in sales)
        total_items_sold = sum(sale.quantity for sale in sales)

        return {
            "total_records": len(sales),
            "total_revenue": total_revenue,
            "total_items_sold": total_items_sold,
            "sales_data": sales
        }
    

class SupplierCreate(SQLModel):
    name: str
    contact_email: str | None = None
    phone: str | None = None


@app.post("/suppliers/")
def add_supplier(
    supplier: SupplierCreate,
    current_user: dict = Depends(get_current_user)
):
    with Session(engine) as session:

        # Create the new supplier in the database
        new_supplier = Supplier(
            name=supplier.name,
            contact_email=supplier.contact_email,
            phone=supplier.phone,
            business_id=current_user["business_id"]
        )

        session.add(new_supplier)
        session.commit()
        session.refresh(new_supplier)

        return {
            "success": True, 
            "message": f"Successfully added vendor: {new_supplier.name}",
            "supplier_id": new_supplier.id
        }
    
    
class PurchaseOrderCreate(SQLModel):
    supplier_id: int
    product_id: int
    quantity: Decimal = Field(gt=0, max_digits=12, decimal_places=3)  # whole numbers for piece products (checked below)
    unit_cost: Money = Field(ge=0, max_digits=12, decimal_places=2)
    expected_delivery_date: date | None = None  # must be after today()

class PurchaseOrder(SQLModel, table=True):
    __tablename__ = "purchase_order" 
    
    id: int | None = Field(default=None, primary_key=True)
    supplier_id: int = Field(index=True)
    product_id: int = Field(index=True)
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
    timestamp: datetime = Field(default_factory=utc_now) # Step 1: Placed Order
    delivered_at: datetime | None = None                         # Step 2: Reached Loading Dock
    stocked_at: datetime | None = None                           # Step 3: Scanned to Shelf
    # Only pending POs can be cancelled; status becomes CANCELLED
    cancelled_at: datetime | None = None
    cancellation_reason: str | None = None

@app.post("/purchase-orders/")
def process_purchase_order(
    request: PurchaseOrderCreate,
    current_user: dict = Depends(get_current_user)
):
    current_date = today()
    if request.expected_delivery_date is not None and request.expected_delivery_date <= current_date:
        raise HTTPException(status_code=422, detail=f"expected_delivery_date must be after today ({current_date}).")

    with Session(engine) as session:

        # 1. Verify the Supplier belongs to this business
        supplier = session.exec(
            select(Supplier).where(
                Supplier.id == request.supplier_id, 
                Supplier.business_id == current_user["business_id"]
            )
        ).first()
        if not supplier:
            raise HTTPException(status_code=404, detail="Supplier not found.")

        # 2. Verify the Product belongs to this business
        # Locked, so a deactivation can't slip in between this check and the new PO
        product = session.exec(
            select(Product).where(
                Product.id == request.product_id, 
                Product.business_id == current_user["business_id"]
            ).with_for_update()
        ).first()
        if not product:
            raise HTTPException(status_code=404, detail="Product not found in inventory.")
        require_active(product)
        if product.unit == StockUnit.PIECE and request.quantity != request.quantity.to_integral_value():
            raise HTTPException(
                status_code=422,
                detail=f"{product.name} is counted by the piece, so quantity must be a whole number."
            )

        # 3.  PO Receipt 
        calculated_total_cost = round_money(request.quantity * request.unit_cost)
        
        new_po = PurchaseOrder(
            supplier_id=supplier.id,
            product_id=product.id,
            business_id=current_user["business_id"],
            quantity=request.quantity,
            unit_cost=request.unit_cost,          
            total_cost=calculated_total_cost,
            status="PENDING",     # <--- Explicitly mark it as waiting for delivery
            expected_delivery_date=request.expected_delivery_date
        )

        # Add ONLY the receipt to the vault (Notice we don't add the product anymore)
        session.add(new_po)

        # 4. COMMIT! 
        session.commit()
        session.refresh(new_po)

        return {
            "success": True,
            "message": f"Order placed for {format_qty(request.quantity, product.unit)} of {product.name}. Awaiting delivery.",
            "current_stock_level": product.quantity,  # Unchanged!
            "expense": calculated_total_cost,
            "po_id": new_po.id,
            "status": new_po.status,
            "expected_delivery_date": new_po.expected_delivery_date
        }
    
@app.post("/purchase-orders/{po_id}/cancel")
def cancel_purchase_order(
    po_id: int,
    reason: str | None = Query(None, max_length=500, description="Why the order was cancelled"),
    current_user: dict = Depends(get_current_user)
):
    require_role(current_user, UserRole.OWNER, UserRole.MANAGER, detail="Only the Owner or a Manager can cancel purchase orders.")

    with Session(engine) as session:
        # Locked, so it can't be delivered or stocked while it is being cancelled
        po = session.exec(
            select(PurchaseOrder).where(
                PurchaseOrder.id == po_id,
                PurchaseOrder.business_id == current_user["business_id"]
            ).with_for_update()
        ).first()
        if not po:
            raise HTTPException(status_code=404, detail="Purchase Order not found.")

        if po.status == "CANCELLED":  # keep the original time and reason
            return {"success": True, "message": f"PO #{po.id} is already cancelled.", "po_id": po.id, "status": po.status,
                    "cancelled_at": po.cancelled_at, "reason": po.cancellation_reason}
        if po.status == "DELIVERED":
            raise HTTPException(
                status_code=409,
                detail=(f"PO #{po.id} has already been delivered and can't be cancelled. To refuse the goods, "
                        "stock it with rejected_quantity equal to received_quantity.")
            )
        if po.status != "PENDING":
            raise HTTPException(status_code=409, detail=f"PO #{po.id} has already been {po.status.lower()} and can't be cancelled.")

        po.status = "CANCELLED"
        po.cancelled_at = utc_now()
        po.cancellation_reason = reason
        session.add(po)
        session.commit()
        session.refresh(po)

        return {"success": True, "message": f"PO #{po.id} has been cancelled.", "po_id": po.id, "status": po.status,
                "cancelled_at": po.cancelled_at, "reason": po.cancellation_reason}

@app.put("/purchase-orders/{po_id}/deliver")
def mark_po_delivered(
    po_id: int,
    current_user: dict = Depends(get_current_user)
):
    with Session(engine) as session:
        # Locked, so a concurrent cancellation and delivery can't both succeed
        po = session.exec(select(PurchaseOrder).where(PurchaseOrder.id == po_id, PurchaseOrder.business_id == current_user["business_id"]).with_for_update()).first()
        
        if not po:
            raise HTTPException(status_code=404, detail="Purchase Order not found.")
        if po.status == "CANCELLED":
            raise HTTPException(status_code=409, detail=f"PO #{po.id} was cancelled and can't be delivered.")
        if po.status != "PENDING":
            raise HTTPException(status_code=400, detail=f"Cannot deliver. Order is currently {po.status}")

        po.status = "DELIVERED"
        po.delivered_at = utc_now() # Stamps the exact millisecond the truck arrived (naive UTC, like timestamp)
        
        session.add(po)
        session.commit()
        session.refresh(po)

        return {
            "success": True,
            "message": "Boxes arrived at the loading dock! Supplier clock stopped. (Inventory NOT updated yet).",
            "status": po.status
        }

# --- STEP 3: The Shelf (Scan into Inventory) ---
# This tracks how fast your staff puts boxes away, and FINALLY adds the stock.
@app.put("/purchase-orders/{po_id}/stock")
def stock_purchase_order(
    po_id: int,
    expiry_date: date | None = Query(None, description="Expiry of the stocked batch; leave out if it never expires"),
    received_quantity: Decimal | None = Query(None, ge=0, max_digits=12, decimal_places=3,
                                              description="How much arrived (default: the ordered quantity)"),
    rejected_quantity: Decimal = Query(Decimal("0"), ge=0, max_digits=12, decimal_places=3,
                                       description="How much of it was rejected at the dock"),
    current_user: dict = Depends(get_current_user)
):
    # SECURITY CHECK
    require_role(current_user, UserRole.OWNER, UserRole.MANAGER, detail="Staff cannot stock inventory.")
    require_future_expiry(expiry_date)

    with Session(engine) as session:
        # 1. Lock the PO (so it can't be stocked twice), then its product (so a concurrent sale isn't overwritten)
        po = session.exec(
            select(PurchaseOrder).where(
                PurchaseOrder.id == po_id,
                PurchaseOrder.business_id == current_user["business_id"]
            ).with_for_update()
        ).first()
        if not po:
            raise HTTPException(status_code=404, detail="Purchase Order not found")
            
        if po.status == "CANCELLED":
            raise HTTPException(status_code=409, detail=f"PO #{po.id} was cancelled and can't be stocked.")
        if po.status != "DELIVERED":
            raise HTTPException(status_code=400, detail="PO must be DELIVERED before it can be STOCKED")

        product = session.exec(select(Product).where(Product.id == po.product_id).with_for_update()).first()
        require_active(product)

        # 2. What arrived, and how much of it is accepted
        received = po.quantity if received_quantity is None else received_quantity
        if rejected_quantity > received:
            raise HTTPException(status_code=422, detail="rejected_quantity can't be more than received_quantity.")
        if product.unit == StockUnit.PIECE and any(q != q.to_integral_value() for q in (Decimal(str(received)), rejected_quantity)):
            raise HTTPException(
                status_code=422,
                detail=f"{product.name} is counted by the piece, so received_quantity and rejected_quantity must be whole numbers."
            )
        accepted = received - rejected_quantity

        # 3. Close the PO, even if the delivery was short or fully rejected
        po.status = "STOCKED"
        po.stocked_at = utc_now()
        po.received_quantity = received
        po.rejected_quantity = rejected_quantity
        session.add(po)

        # 4. Only accepted stock becomes a batch, stock, and a ledger entry
        new_batch = None
        if accepted > 0:
            new_batch = ProductBatch(
                product_id=po.product_id,
                po_id=po.id,
                business_id=current_user["business_id"],
                quantity=accepted,
                received_date=today(),
                expiry_date=expiry_date  # None: never expires, like opening and audit batches
            )
            session.add(new_batch)
            session.flush()  # assigns new_batch.id for the ledger
            product.quantity += accepted
            session.add(product)
            record_movements(session, product, MovementReason.PURCHASE_RECEIPT, [(new_batch.id, accepted)],
                             po_id=po.id, user_id=acting_user_id(current_user))

        session.commit()

        counts = f"{format_qty(received, product.unit)} received, {format_qty(rejected_quantity, product.unit)} rejected"
        return {
            "message": (f"PO Stocked. {format_qty(accepted, product.unit)} added to main inventory ({counts})."
                        if new_batch else f"PO closed. Nothing added to inventory ({counts})."),
            "received_quantity": received,
            "rejected_quantity": rejected_quantity,
            "accepted_quantity": accepted,
            "batch_id": new_batch.id if new_batch else None,
            "batch_expiry": new_batch.expiry_date if new_batch else None
        }
    
# --- MANUAL STOCK AUDIT (PROTECTED) ---
def lock_batches_fifo(session: Session, *conditions) -> list["ProductBatch"]:
    """Locks the matching batches (SELECT ... FOR UPDATE) in FIFO order: expiry_date ascending with NULL last,
    then received_date, then id. Callers lock the product row(s) first, like checkout, so the lock order is
    always product -> batches and these paths can't deadlock with each other."""
    return session.exec(
        select(ProductBatch).where(*conditions).order_by(
            ProductBatch.expiry_date.asc().nulls_last(),
            ProductBatch.received_date,
            ProductBatch.id
        ).with_for_update()
    ).all()

@app.put("/products/{product_id}/manual-audit")
def manual_stock_adjustment(
    product_id: int,
    new_quantity: Decimal = Query(..., ge=0, max_digits=12, decimal_places=3, description="The physically counted stock"),
    expiry_date: date | None = Query(None, description="Expiry for an adjustment batch, if the count is higher than recorded"),
    note: str | None = Query(None, max_length=500, description="Why the count changed; stored on the ledger entries"),
    current_user: dict = Depends(get_current_user)
):
    # Updated Security Gate: Owner and Manager only
    require_role(current_user, UserRole.OWNER, UserRole.MANAGER, detail="Access Denied: Only the Owner or a Manager can manually adjust stock levels.")
    require_future_expiry(expiry_date)

    with Session(engine) as session:

        # 1. Lock the product, then its batches (same order as checkout)
        product = session.exec(
            select(Product).where(
                Product.id == product_id,
                Product.business_id == current_user["business_id"]
            ).with_for_update()
        ).first()

        if not product:
            raise HTTPException(status_code=404, detail="Product not found.")
        require_active(product)

        if product.unit == StockUnit.PIECE and new_quantity != new_quantity.to_integral_value():
            raise HTTPException(
                status_code=422,
                detail=f"{product.name} is counted by the piece, so new_quantity must be a whole number."
            )

        batches = lock_batches_fifo(
            session,
            ProductBatch.product_id == product.id,
            ProductBatch.business_id == current_user["business_id"],
            ProductBatch.quantity > 0
        )

        # 2. Bring the batches in line with the count. Measured against the batch total (not Product.quantity),
        # so an audit also repairs a product whose stock had drifted from its batches.
        old_qty = product.quantity
        batch_total = sum((b.quantity for b in batches), Decimal("0"))
        batches_reduced = []
        batch_created = None

        if new_quantity < batch_total:
            # Remove the shortfall in FIFO order; expired batches sort first, so they go first
            to_remove = batch_total - new_quantity
            for batch in batches:
                if to_remove == 0:
                    break
                taken = min(batch.quantity, to_remove)
                batch.quantity -= taken
                to_remove -= taken
                session.add(batch)
                batches_reduced.append({
                    "batch_id": batch.id,
                    "quantity_removed": taken,
                    "remaining": batch.quantity,
                    "expiry_date": batch.expiry_date
                })
        elif new_quantity > batch_total:
            # Found more than recorded: the surplus becomes its own adjustment batch (no purchase order)
            adjustment = ProductBatch(
                product_id=product.id,
                po_id=None,
                business_id=current_user["business_id"],
                quantity=new_quantity - batch_total,
                received_date=today(),
                expiry_date=expiry_date
            )
            session.add(adjustment)
            session.flush()
            batch_created = {"batch_id": adjustment.id, "quantity": adjustment.quantity, "expiry_date": adjustment.expiry_date}

        product.quantity = new_quantity
        session.add(product)
        refs = {"user_id": acting_user_id(current_user), "note": note}
        if batches_reduced:
            record_movements(session, product, MovementReason.AUDIT_DECREASE,
                             [(b["batch_id"], -b["quantity_removed"]) for b in batches_reduced], **refs)
        if batch_created:
            record_movements(session, product, MovementReason.AUDIT_INCREASE,
                             [(batch_created["batch_id"], batch_created["quantity"])], **refs)
        session.commit()

        return {
            "success": True,
            "message": f"Manual audit completed for {product.name}",
            "previous_qty": old_qty,
            "new_qty": new_quantity,
            "unit": product.unit.value,
            "batches_reduced": batches_reduced,
            "batch_created": batch_created,
            "authorized_by": f"{current_user['user_id']} ({current_user['role']})"
        }
    

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


# --- STOCK MOVEMENT LEDGER (append-only: nothing updates or deletes entries) ---

class MovementReason(str, Enum):
    OPENING = "opening"
    PURCHASE_RECEIPT = "purchase_receipt"
    SALE = "sale"
    AUDIT_INCREASE = "audit_increase"
    AUDIT_DECREASE = "audit_decrease"
    EXPIRY_DISPOSAL = "expiry_disposal"


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
    created_at: datetime = Field(default_factory=utc_now)


def acting_user_id(current_user: dict) -> int | None:
    """The token's user id as an int (None if the token's subject isn't numeric)."""
    try:
        return int(current_user["user_id"])
    except (KeyError, TypeError, ValueError):
        return None


def record_movements(session: Session, product: "Product", reason: MovementReason, changes, **refs) -> None:
    """Appends one StockMovement per (batch_id, quantity_change) in changes, in order, to the caller's transaction.
    Call after product.quantity holds its final value: product_quantity_after runs forward from the quantity
    before these changes, so the last entry always equals Product.quantity. refs: sale_id, po_id, user_id, note."""
    running = Decimal(str(product.quantity)) - sum((Decimal(str(change)) for _, change in changes), Decimal("0"))
    for batch_id, change in changes:
        running += Decimal(str(change))
        session.add(StockMovement(
            business_id=product.business_id,
            product_id=product.id,
            batch_id=batch_id,
            quantity_change=change,
            reason=reason,
            product_quantity_after=running,
            **refs
        ))


@app.get("/products/{product_id}/batches")
def get_product_batches(product_id: int, current_user: dict = Depends(get_current_user)):
    with Session(engine) as session:
        # 1. Verify the product exists and belongs to this business
        product = session.get(Product, product_id)
        if not product or product.business_id != current_user["business_id"]:
            raise HTTPException(status_code=404, detail="Product not found")
            
        # 2. Fetch all batches for this specific product
        statement = select(ProductBatch).where(
            ProductBatch.product_id == product_id,
            ProductBatch.business_id == current_user["business_id"]
        )
        batches = session.exec(statement).all()
        
        # 3. Return the data
        return batches
    

              #Low-Stock Alerts

@app.get("/inventory/alerts/low-stock")
def get_low_stock_alerts(current_user: dict = Depends(get_current_user)):
    with Session(engine) as session:
        # The AI Trigger Query
        statement = select(Product).where(
            Product.business_id == current_user["business_id"],
            Product.is_active == True,  # noqa: E712 (SQL expression)
            Product.quantity <= Product.min_stock_level
        )
        
        low_stock_items = session.exec(statement).all()
        
        # We format the response to be highly readable for both the frontend UI and future AI agents
        return {
            "alert_count": len(low_stock_items),
            "items_to_reorder": low_stock_items
        }
    

def run_daily_check(session: Session, business_id: str, user_id: int | None = None, note: str | None = None) -> dict:
    """Disposes of one business's expired stock inside the caller's transaction; the caller commits.
    Returns {"expired_batches_cleared", "products", "inconsistencies"}. Running it again the same day changes
    nothing: cleared batches hold 0, and inconsistent products are only reported, never changed."""
    # Same rule as checkout: a batch is expired from its expiry_date onward; NULL never expires
    current_date = today()
    expired_conditions = (
        ProductBatch.business_id == business_id,
        ProductBatch.expiry_date <= current_date,
        ProductBatch.quantity > 0
    )

    # 1. Which products have expired stock left? (read only; rechecked under lock below)
    product_ids = session.exec(
        select(ProductBatch.product_id).where(*expired_conditions).distinct()
    ).all()

    # 2. Lock those products in id order, then their expired batches, so this can't deadlock with checkout
    products = session.exec(
        select(Product).where(Product.id.in_(product_ids), Product.business_id == business_id)
        .order_by(Product.id).with_for_update()
    ).all()
    batches = lock_batches_fifo(session, ProductBatch.product_id.in_(product_ids), *expired_conditions)

    removed_per_product = []
    inconsistencies = []
    batches_cleared = 0

    # 3. Dispose of each product's expired batches
    for product in products:
        expired = [b for b in batches if b.product_id == product.id]
        if not expired:
            continue  # sold or cleared between the read and the lock
        expired_qty = sum((b.quantity for b in expired), Decimal("0"))

        # Recorded stock can't cover what's in its own expired batches: change nothing and report it
        if expired_qty > product.quantity:
            inconsistencies.append({
                "product_id": product.id,
                "product_name": product.name,
                "unit": product.unit.value,
                "stock": product.quantity,
                "expired_in_batches": expired_qty,
                "message": (
                    f"{product.name}: recorded stock is {format_qty(product.quantity, product.unit)} but "
                    f"{format_qty(expired_qty, product.unit)} is in expired batches. Nothing was changed; "
                    "run a manual audit to correct it."
                )
            })
            continue

        disposals = [(batch.id, -batch.quantity) for batch in expired]  # captured before zeroing
        for batch in expired:
            batch.quantity = Decimal("0")
            session.add(batch)
        product.quantity -= expired_qty
        session.add(product)
        record_movements(session, product, MovementReason.EXPIRY_DISPOSAL, disposals, user_id=user_id, note=note)

        batches_cleared += len(expired)
        removed_per_product.append({
            "product_id": product.id,
            "product_name": product.name,
            "removed": expired_qty,
            "unit": product.unit.value,
            "removed_display": format_qty(expired_qty, product.unit),
            "batches_cleared": [b.id for b in expired],
            "stock_remaining": product.quantity
        })

    return {
        "expired_batches_cleared": batches_cleared,
        "products": removed_per_product,
        "inconsistencies": inconsistencies
    }

@app.post("/system/daily-check")
def daily_inventory_health_check(current_user: dict = Depends(get_current_user)):
    # SECURITY CHECK: Only Owners/Managers can trigger system sweeps
    require_role(current_user, UserRole.OWNER, UserRole.MANAGER, detail="Unauthorized.")

    with Session(engine) as session:
        result = run_daily_check(session, current_user["business_id"], user_id=acting_user_id(current_user))
        # Save all changes to the database
        session.commit()

    return {"message": "Daily health check complete.", **result}

# --- REPORTS: SALES & TRENDS ---
# Sale timestamps are naive UTC; every report date and daily bucket is an India (Asia/Kolkata) calendar day.

class ist_date(FunctionElement):
    """SQL expression for the India calendar date of a naive-UTC timestamp column."""
    type = Date()
    name = "ist_date"
    inherit_cache = True

@compiles(ist_date)
def _ist_date_postgres(element, compiler, **kw):
    return f"CAST(timezone('{BUSINESS_TZ.key}', timezone('UTC', {compiler.process(element.clauses, **kw)})) AS DATE)"

@compiles(ist_date, "sqlite")
def _ist_date_sqlite(element, compiler, **kw):
    # SQLite has no time zone database; India has used a fixed +05:30 offset since 1945
    return f"date({compiler.process(element.clauses, **kw)}, '+330 minutes')"

def ist_day_start_utc(day: date) -> datetime:
    """00:00 India time on day, as the naive UTC datetime sale timestamps are compared against."""
    return datetime.combine(day, datetime.min.time(), tzinfo=BUSINESS_TZ).astimezone(timezone.utc).replace(tzinfo=None)

REPORT_DEFAULT_DAYS = 30
REPORT_MAX_SPAN_DAYS = 366

def report_range(from_date: date | None, to_date: date | None, default_days: int = REPORT_DEFAULT_DAYS) -> tuple[date, date]:
    """Fills in the default range (the last default_days days including today) and validates it."""
    to_date = to_date or today()
    from_date = from_date or to_date - timedelta(days=default_days - 1)
    if from_date > to_date:
        raise HTTPException(status_code=422, detail="from_date must be on or before to_date.")
    if (to_date - from_date).days > REPORT_MAX_SPAN_DAYS:
        raise HTTPException(status_code=422, detail=f"from_date and to_date can be at most {REPORT_MAX_SPAN_DAYS} days apart.")
    return from_date, to_date

def sales_in_range(business_id: str, from_date: date, to_date: date) -> tuple:
    """WHERE conditions for this business's sales on India dates from_date..to_date inclusive."""
    return (
        Sale.business_id == business_id,
        Sale.timestamp >= ist_day_start_utc(from_date),
        Sale.timestamp < ist_day_start_utc(to_date + timedelta(days=1)),
    )

REPORT_ROLE_MESSAGE = "Only the Owner or a Manager can view reports."

@app.get("/reports/sales-summary")
def sales_summary(
    from_date: date | None = Query(None, description="First India date (default: 29 days before to_date)"),
    to_date: date | None = Query(None, description="Last India date, inclusive (default: today)"),
    current_user: dict = Depends(get_current_user)
):
    require_role(current_user, UserRole.OWNER, UserRole.MANAGER, detail=REPORT_ROLE_MESSAGE)
    from_date, to_date = report_range(from_date, to_date)
    in_range = sales_in_range(current_user["business_id"], from_date, to_date)
    day = ist_date(Sale.timestamp)

    with Session(engine) as session:
        sales_count, revenue = session.exec(
            select(func.count(Sale.id), func.coalesce(func.sum(Sale.total_price), 0)).where(*in_range)
        ).one()
        per_day = session.exec(
            select(day, func.count(Sale.id), func.sum(Sale.total_price)).where(*in_range).group_by(day)
        ).all()

    by_day = {sale_day: (count, day_revenue) for sale_day, count, day_revenue in per_day}
    daily = []
    for offset in range((to_date - from_date).days + 1):  # zero-fill days without sales
        current = from_date + timedelta(days=offset)
        count, day_revenue = by_day.get(current, (0, 0))
        daily.append({"date": current, "revenue": round_money(day_revenue), "sales_count": count})

    revenue = round_money(revenue)
    return {
        "from_date": from_date,
        "to_date": to_date,
        "timezone": BUSINESS_TZ.key,
        "total_revenue": revenue,
        "sales_count": sales_count,
        "average_sale_value": round_money(revenue / sales_count) if sales_count else None,
        "daily": daily
    }

@app.get("/reports/top-products")
def top_products(
    from_date: date | None = Query(None, description="First India date (default: 29 days before to_date)"),
    to_date: date | None = Query(None, description="Last India date, inclusive (default: today)"),
    by: Literal["revenue", "quantity"] = Query("revenue"),
    unit: StockUnit = Query(StockUnit.PIECE, description="With by=quantity, rank only products sold in this unit; ignored for revenue"),
    limit: int = Query(10, ge=1, le=100),
    current_user: dict = Depends(get_current_user)
):
    require_role(current_user, UserRole.OWNER, UserRole.MANAGER, detail=REPORT_ROLE_MESSAGE)
    from_date, to_date = report_range(from_date, to_date)

    quantity_sold = func.sum(Sale.quantity)  # one product, one unit: never sums across units
    revenue = func.sum(Sale.total_price)
    ranking = (quantity_sold, revenue) if by == "quantity" else (revenue, quantity_sold)
    conditions = [*sales_in_range(current_user["business_id"], from_date, to_date),
                  Product.business_id == current_user["business_id"]]
    if by == "quantity":
        conditions.append(Product.unit == unit)  # quantities are only comparable within one unit

    with Session(engine) as session:
        rows = session.exec(
            select(Product.id, Product.name, Product.unit, quantity_sold, revenue, func.count(Sale.id))
            .join(Product, Product.id == Sale.product_id)
            .where(*conditions)
            .group_by(Product.id, Product.name, Product.unit)
            .order_by(ranking[0].desc(), ranking[1].desc(), Product.id)
            .limit(limit)
        ).all()

    return {
        "from_date": from_date,
        "to_date": to_date,
        "by": by,
        "unit": unit.value if by == "quantity" else None,
        "products": [
            {
                "rank": rank,
                "product_id": product_id,
                "name": name,
                "unit": unit.value,
                "quantity_sold": Decimal(str(qty)).quantize(Decimal("0.001")),
                "revenue": round_money(product_revenue),
                "sales_count": count
            }
            for rank, (product_id, name, unit, qty, product_revenue, count) in enumerate(rows, start=1)
        ]
    }

@app.get("/reports/dead-stock")
def dead_stock(
    days: int = Query(30, ge=1, le=3650, description="No sales in the last N India days, including today"),
    current_user: dict = Depends(get_current_user)
):
    require_role(current_user, UserRole.OWNER, UserRole.MANAGER, detail=REPORT_ROLE_MESSAGE)
    business_id = current_user["business_id"]
    current_date = today()
    window_start = current_date - timedelta(days=days - 1)

    last_sale = (
        select(Sale.product_id, func.max(ist_date(Sale.timestamp)).label("last_sale_date"))
        .where(Sale.business_id == business_id)
        .group_by(Sale.product_id)
        .subquery()
    )

    with Session(engine) as session:
        rows = session.exec(
            select(Product, last_sale.c.last_sale_date)
            .outerjoin(last_sale, last_sale.c.product_id == Product.id)
            .where(
                Product.business_id == business_id,
                Product.quantity > 0,
                or_(last_sale.c.last_sale_date.is_(None), last_sale.c.last_sale_date < window_start)
            )
            .order_by((Product.quantity * Product.price).desc(), Product.id)
        ).all()

    return {
        "days": days,
        "since": window_start,
        "products": [
            {
                "product_id": product.id,
                "name": product.name,
                "unit": product.unit.value,
                "stock": product.quantity,
                "price": product.price,
                "stock_value": round_money(product.quantity * product.price),
                "last_sale_date": last_sale_date,
                "days_since_last_sale": (current_date - last_sale_date).days if last_sale_date else None
            }
            for product, last_sale_date in rows
        ]
    }


# --- STOCK MOVEMENT HISTORY & WASTE ---

def movements_in_range(business_id: str, from_date: date, to_date: date) -> tuple:
    """WHERE conditions for this business's ledger entries on India dates from_date..to_date inclusive."""
    return (
        StockMovement.business_id == business_id,
        StockMovement.created_at >= ist_day_start_utc(from_date),
        StockMovement.created_at < ist_day_start_utc(to_date + timedelta(days=1)),
    )

@app.get("/inventory/movements")
def list_stock_movements(
    product_id: int | None = Query(None),
    reason: MovementReason | None = Query(None),
    from_date: date | None = Query(None, description="First India date (default: 29 days before to_date)"),
    to_date: date | None = Query(None, description="Last India date, inclusive (default: today)"),
    limit: int = Query(100, ge=1, le=500),
    current_user: dict = Depends(get_current_user)
):
    require_role(current_user, UserRole.OWNER, UserRole.MANAGER, detail="Only the Owner or a Manager can view stock movements.")
    from_date, to_date = report_range(from_date, to_date)

    conditions = [*movements_in_range(current_user["business_id"], from_date, to_date)]
    if product_id is not None:
        conditions.append(StockMovement.product_id == product_id)
    if reason is not None:
        conditions.append(StockMovement.reason == reason)

    with Session(engine) as session:
        rows = session.exec(
            select(StockMovement, Product.name, Product.unit)
            .join(Product, Product.id == StockMovement.product_id)
            .where(*conditions)
            .order_by(StockMovement.created_at.desc(), StockMovement.id.desc())
            .limit(limit)
        ).all()

    return {
        "from_date": from_date,
        "to_date": to_date,
        "count": len(rows),
        "movements": [
            {
                "id": m.id,
                "created_at": m.created_at.replace(tzinfo=timezone.utc),  # stored as naive UTC; sent with its offset
                "product_id": m.product_id,
                "product_name": name,
                "unit": unit.value,
                "batch_id": m.batch_id,
                "quantity_change": m.quantity_change,
                "reason": m.reason.value,
                "product_quantity_after": m.product_quantity_after,
                "sale_id": m.sale_id,
                "po_id": m.po_id,
                "user_id": m.user_id,
                "note": m.note
            }
            for m, name, unit in rows
        ]
    }

@app.get("/reports/waste")
def waste_report(
    from_date: date | None = Query(None, description="First India date (default: 29 days before to_date)"),
    to_date: date | None = Query(None, description="Last India date, inclusive (default: today)"),
    current_user: dict = Depends(get_current_user)
):
    require_role(current_user, UserRole.OWNER, UserRole.MANAGER, detail=REPORT_ROLE_MESSAGE)
    from_date, to_date = report_range(from_date, to_date)
    disposals = (*movements_in_range(current_user["business_id"], from_date, to_date),
                 StockMovement.reason == MovementReason.EXPIRY_DISPOSAL)
    disposed = -func.sum(StockMovement.quantity_change)  # one product, one unit: never sums across units

    with Session(engine) as session:
        total_entries = session.exec(select(func.count(StockMovement.id)).where(*disposals)).one()
        rows = session.exec(
            select(Product.id, Product.name, Product.unit, disposed, func.count(StockMovement.id))
            .join(Product, Product.id == StockMovement.product_id)
            .where(*disposals)
            .group_by(Product.id, Product.name, Product.unit)
            .order_by(Product.name, Product.id)
        ).all()

    return {
        "from_date": from_date,
        "to_date": to_date,
        "total_entries": total_entries,
        "products": [
            {
                "product_id": product_id,
                "name": name,
                "unit": unit.value,
                "quantity_disposed": Decimal(str(quantity)).quantize(Decimal("0.001")),
                "entries": entries
            }
            for product_id, name, unit, quantity, entries in rows
        ]
    }


# --- SUPPLIER SCORECARDS ---
# Based on the POs stocked (closed) on India dates in the range. Computed in Python from one query's rows:
# most metrics are averages of per-PO ratios, and SQLite (used in tests) has no standard deviation.

SCORECARD_DEFAULT_DAYS = 90
RATIO_PLACES = Decimal("0.0001")
DAYS_PLACES = Decimal("0.01")

def ist_date_of(utc_naive: datetime) -> date:
    """India calendar date of a naive-UTC timestamp."""
    return utc_naive.replace(tzinfo=timezone.utc).astimezone(BUSINESS_TZ).date()

def _rounded_mean(values: list[Decimal], places: Decimal) -> Decimal | None:
    return (sum(values, Decimal("0")) / len(values)).quantize(places, rounding=ROUND_HALF_UP) if values else None

def supplier_metrics(pos: list["PurchaseOrder"]) -> dict:
    """Scorecard metrics for one supplier's stocked POs. Every metric is None when there are no POs, and each is
    None when no PO qualifies for it."""
    metrics = {"po_count": len(pos), "avg_lead_time_days": None, "on_time_rate": None,
               "fill_rate": None, "defect_rate": None, "price_volatility": None}
    if not pos:
        return metrics

    lead_days = [Decimal(str((po.delivered_at - po.timestamp).total_seconds())) / 86400
                 for po in pos if po.delivered_at and po.timestamp]
    on_time = [Decimal(1) if ist_date_of(po.delivered_at) <= po.expected_delivery_date else Decimal(0)
               for po in pos if po.expected_delivery_date and po.delivered_at]

    fills, defects = [], []
    for po in pos:
        ordered = Decimal(str(po.quantity))
        # POs stocked before these columns existed took the full order with nothing rejected
        received = Decimal(str(po.received_quantity)) if po.received_quantity is not None else ordered
        rejected = Decimal(str(po.rejected_quantity)) if po.rejected_quantity is not None else Decimal("0")
        if ordered > 0:
            fills.append(min(received / ordered, Decimal(1)))
        if received > 0:
            defects.append(rejected / received)

    # Price volatility: coefficient of variation (population std dev / mean) of unit_cost per product
    costs_by_product: dict[int, list[Decimal]] = {}
    for po in pos:
        costs_by_product.setdefault(po.product_id, []).append(Decimal(str(po.unit_cost)))
    volatilities = [statistics.pstdev(costs) / statistics.mean(costs)
                    for costs in costs_by_product.values() if len(costs) >= 2 and statistics.mean(costs) > 0]

    metrics.update({
        "avg_lead_time_days": _rounded_mean(lead_days, DAYS_PLACES),
        "on_time_rate": _rounded_mean(on_time, RATIO_PLACES),
        "fill_rate": _rounded_mean(fills, RATIO_PLACES),
        "defect_rate": _rounded_mean(defects, RATIO_PLACES),
        "price_volatility": _rounded_mean(volatilities, RATIO_PLACES),
    })
    return metrics

def stocked_pos_in_range(session: Session, business_id: str, from_date: date, to_date: date,
                         supplier_id: int | None = None) -> list["PurchaseOrder"]:
    conditions = [
        PurchaseOrder.business_id == business_id,
        PurchaseOrder.status == "STOCKED",
        PurchaseOrder.stocked_at >= ist_day_start_utc(from_date),
        PurchaseOrder.stocked_at < ist_day_start_utc(to_date + timedelta(days=1)),
    ]
    if supplier_id is not None:
        conditions.append(PurchaseOrder.supplier_id == supplier_id)
    return session.exec(select(PurchaseOrder).where(*conditions).order_by(PurchaseOrder.id)).all()

SCORECARD_ROLE_MESSAGE = "Only the Owner or a Manager can view supplier scorecards."

@app.get("/suppliers/scorecards")
def supplier_scorecards(
    from_date: date | None = Query(None, description="First India date (default: 89 days before to_date)"),
    to_date: date | None = Query(None, description="Last India date, inclusive (default: today)"),
    current_user: dict = Depends(get_current_user)
):
    require_role(current_user, UserRole.OWNER, UserRole.MANAGER, detail=SCORECARD_ROLE_MESSAGE)
    from_date, to_date = report_range(from_date, to_date, default_days=SCORECARD_DEFAULT_DAYS)
    with Session(engine) as session:
        suppliers = session.exec(
            select(Supplier).where(Supplier.business_id == current_user["business_id"]).order_by(Supplier.name, Supplier.id)
        ).all()
        pos_by_supplier: dict[int, list] = {}
        for po in stocked_pos_in_range(session, current_user["business_id"], from_date, to_date):
            pos_by_supplier.setdefault(po.supplier_id, []).append(po)

    return {
        "from_date": from_date,
        "to_date": to_date,
        "suppliers": [
            {"supplier_id": s.id, "name": s.name, **supplier_metrics(pos_by_supplier.get(s.id, []))}
            for s in suppliers
        ]
    }

@app.get("/suppliers/{supplier_id}/scorecard")
def supplier_scorecard(
    supplier_id: int,
    from_date: date | None = Query(None, description="First India date (default: 89 days before to_date)"),
    to_date: date | None = Query(None, description="Last India date, inclusive (default: today)"),
    current_user: dict = Depends(get_current_user)
):
    require_role(current_user, UserRole.OWNER, UserRole.MANAGER, detail=SCORECARD_ROLE_MESSAGE)
    from_date, to_date = report_range(from_date, to_date, default_days=SCORECARD_DEFAULT_DAYS)
    with Session(engine) as session:
        supplier = session.exec(
            select(Supplier).where(Supplier.id == supplier_id, Supplier.business_id == current_user["business_id"])
        ).first()
        if not supplier:
            raise HTTPException(status_code=404, detail="Supplier not found.")
        pos = stocked_pos_in_range(session, current_user["business_id"], from_date, to_date, supplier_id=supplier.id)

    return {"supplier_id": supplier.id, "name": supplier.name, "from_date": from_date, "to_date": to_date,
            **supplier_metrics(pos)}
