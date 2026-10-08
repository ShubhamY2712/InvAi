from fastapi import FastAPI, Depends, HTTPException, Query, status
from fastapi.security import OAuth2PasswordRequestForm, OAuth2PasswordBearer
from sqlmodel import Field, Session, SQLModel, create_engine, select
from typing import Annotated, List
from pydantic import PlainSerializer
import os
from dotenv import load_dotenv
from contextlib import asynccontextmanager
from enum import Enum
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo
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


import random

# --- ID GENERATOR LOGIC ---
def generate_business_id() -> str:
    """Generates a random 4-digit ID (e.g., 4092)"""
    return str(random.randint(1000, 9999))

# --- MULTI-TENANT DATABASE TABLES ---
class BusinessProfile(SQLModel, table=True):
    # ID is now a String, and automatically generates a 4-digit number
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
    business_id: str = Field(foreign_key="businessprofile.id") 

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


class Sale(SQLModel, table=True):
    __tablename__ = "sales"
    
    id: int | None = Field(default=None, primary_key=True)
    product_id: int = Field(index=True) # What was sold
    user_id: int = Field(index=True)    # Who sold it (Ankit or Rahul)
    business_id: str = Field(index=True) # Multi-tenant lock
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
    business_id: str = Field(index=True) # Locks this supplier to FreshMart only


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ONLY UNCOMMENT THIS TO BUILD THE NEW TABLES:
    SQLModel.metadata.create_all(engine)
 #   print("✅ SUPPLIER & PO TABLES SYNCED ✅")
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
    quantity: int | None = None
    price: float | None = None
    description: str | None = None

@app.post("/onboard-business/")
def onboard_new_business(request: OnboardingRequest):
    with Session(engine) as session:
        new_business = BusinessProfile(
            business_name=request.business_name,
            category=request.category
        )
        session.add(new_business)
        session.flush() # Saves the business temporarily so we can grab the new 4-digit ID
        
        # Assemble Owner ID: "BusinessID" + "001" (e.g., 4092001)
        owner_id = f"{new_business.id}001"
        
        # Create the Owner Profile with a mathematically secured password
        new_user = User(
            id=owner_id,
            username=request.owner_username, # Fixed: Uses the username from the JSON
            email=request.email,
            hashed_password=get_password_hash(request.password), # Fixed: Hashes the actual password!
            role="Owner",
            business_id=new_business.id
        )
        
        session.add(new_user)
        session.commit()
        session.refresh(new_business)
        session.refresh(new_user)
        
        return {
            "success": True,
            "business_id": new_business.id,
            "owner_user_id": new_user.id
        }
    
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
            session.add(ProductBatch(
                product_id=new_product.id,
                po_id=None,
                business_id=current_user["business_id"],
                quantity=new_product.quantity,
                received_date=today(),
                expiry_date=product_data.expiry_date
            ))

        session.commit()
        session.refresh(new_product)

        return {
            "success": True,
            "message": f"Successfully added {new_product.name} to inventory.",
            "product": new_product
        }
    
@app.get("/products/")
def get_inventory(current_user: dict = Depends(get_current_user)):
    with Session(engine) as session:
        # The ultimate security filter: ONLY return products matching this user's business_id
        statement = select(Product).where(Product.business_id == current_user["business_id"])
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
    
    with Session(engine) as session:
        # 1. The Ultimate Security Check: Find the product, but ONLY if they own it
        statement = select(Product).where(
            Product.id == product_id,
            Product.business_id == current_user["business_id"]
        )
        product = session.exec(statement).first()

        # 2. If it doesn't exist (or they don't own it), reject them
        if not product:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, 
                detail="Product not found or access denied"
            )

        # 3. Update only the fields the frontend specifically asked to change
        if product_update.quantity is not None:
            product.quantity = product_update.quantity
        if product_update.price is not None:
            product.price = product_update.price
        if product_update.description is not None:
            product.description = product_update.description

        # 4. Save the changes to the vault
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
    
    # SECURITY CHECK
    require_role(current_user, UserRole.OWNER, detail="Only the Owner can delete products from the system.")
    
    with Session(engine) as session:
        # 1. Search for the product using the ID AND the Business ID (The Multi-Tenant Lock)
        statement = select(Product).where(
            Product.id == product_id, 
            Product.business_id == current_user["business_id"]
        )
        product = session.exec(statement).first()

        # 2. If it's not there or belongs to someone else, say it's not found
        if not product:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, 
                detail="Product not found or access denied"
            )

        # 3. Remove it from the database
        session.delete(product)
        session.commit()

        return {
            "success": True, 
            "message": f"Product '{product.name}' has been permanently removed from InvAi."
        }
    
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

        # 2. Find and lock the product (Also stripping the business_id just to be safe!)
        # Lock order is always product first, then its batches, so concurrent checkouts can't deadlock
        clean_business_id = str(current_user["business_id"]).strip()

        statement = select(Product).where(
            Product.id == request.product_id,
            Product.business_id == clean_business_id
        ).with_for_update()
        product = session.exec(statement).first()

        # 3. Validation: Does it exist?
        if not product:
            raise HTTPException(status_code=404, detail="Product not found in your inventory.")

        # 4. Validation: piece products are sold in whole units only
        if product.unit == StockUnit.PIECE and request.quantity != request.quantity.to_integral_value():
            raise HTTPException(
                status_code=422,
                detail=f"{product.name} is sold by the piece, so quantity must be a whole number."
            )

        # 5. Lock the product's batches that still hold stock, in FIFO order
        batches = session.exec(
            select(ProductBatch).where(
                ProductBatch.product_id == product.id,
                ProductBatch.business_id == clean_business_id,
                ProductBatch.quantity > 0
            ).order_by(
                ProductBatch.expiry_date.asc().nulls_last(),
                ProductBatch.received_date,
                ProductBatch.id
            ).with_for_update()
        ).all()

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
            business_id=clean_business_id,
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
                business_id=clean_business_id
            ))
            allocations.append({"batch_id": batch.id, "quantity": taken, "expiry_date": batch.expiry_date})

        product.quantity -= request.quantity
        session.add(product)

        # 9. COMMIT! (stock, batches, sale and allocations together)
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
        clean_business_id = str(current_user["business_id"]).strip()
        clean_user_id = int(str(current_user["user_id"]).strip())
        
        # The Logic Split: Owner vs Staff
        if has_role(current_user, UserRole.OWNER, UserRole.MANAGER):
            # The Boss sees EVERYTHING for this specific business
            statement = select(Sale).where(Sale.business_id == clean_business_id)
        else:
            # The Staff only sees the sales attached to their specific user_id
            statement = select(Sale).where(
                Sale.business_id == clean_business_id,
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
        # Clean the business ID from the token for safety
        clean_business_id = str(current_user["business_id"]).strip()

        # Create the new supplier in the database
        new_supplier = Supplier(
            name=supplier.name,
            contact_email=supplier.contact_email,
            phone=supplier.phone,
            business_id=clean_business_id
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
    quantity: int
    unit_cost: Money = Field(max_digits=12, decimal_places=2)

class PurchaseOrder(SQLModel, table=True):
    __tablename__ = "purchase_order" 
    
    id: int | None = Field(default=None, primary_key=True)
    supplier_id: int = Field(index=True)
    product_id: int = Field(index=True)
    business_id: str = Field(index=True)
    quantity: Quantity = Field(max_digits=12, decimal_places=3)
    unit_cost: Money = Field(max_digits=12, decimal_places=2)
    total_cost: Money = Field(max_digits=12, decimal_places=2)
    status: str = Field(default="PENDING") 
    
    # --- The 3-Step AI Analytics Timestamps ---
    timestamp: datetime = Field(default_factory=utc_now) # Step 1: Placed Order
    delivered_at: datetime | None = None                         # Step 2: Reached Loading Dock
    stocked_at: datetime | None = None                           # Step 3: Scanned to Shelf

@app.post("/purchase-orders/")
def process_purchase_order(
    request: PurchaseOrderCreate,
    current_user: dict = Depends(get_current_user)
):
    with Session(engine) as session:
        clean_business_id = str(current_user["business_id"]).strip()

        # 1. Verify the Supplier belongs to this business
        supplier = session.exec(
            select(Supplier).where(
                Supplier.id == request.supplier_id, 
                Supplier.business_id == clean_business_id
            )
        ).first()
        if not supplier:
            raise HTTPException(status_code=404, detail="Supplier not found.")

        # 2. Verify the Product belongs to this business
        product = session.exec(
            select(Product).where(
                Product.id == request.product_id, 
                Product.business_id == clean_business_id
            )
        ).first()
        if not product:
            raise HTTPException(status_code=404, detail="Product not found in inventory.")

        # 3.  PO Receipt 
        calculated_total_cost = round_money(request.quantity * request.unit_cost)
        
        new_po = PurchaseOrder(
            supplier_id=supplier.id,
            product_id=product.id,
            business_id=clean_business_id,
            quantity=request.quantity,
            unit_cost=request.unit_cost,          
            total_cost=calculated_total_cost,
            status="PENDING"      # <--- Explicitly mark it as waiting for delivery
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
            "status": new_po.status
        }
    
@app.put("/purchase-orders/{po_id}/deliver")
def mark_po_delivered(
    po_id: int,
    current_user: dict = Depends(get_current_user)
):
    with Session(engine) as session:
        clean_business_id = str(current_user["business_id"]).strip()
        po = session.exec(select(PurchaseOrder).where(PurchaseOrder.id == po_id, PurchaseOrder.business_id == clean_business_id)).first()
        
        if not po:
            raise HTTPException(status_code=404, detail="Purchase Order not found.")
        if po.status != "PENDING":
            raise HTTPException(status_code=400, detail=f"Cannot deliver. Order is currently {po.status}")

        po.status = "DELIVERED"
        po.delivered_at = datetime.utcnow() # Stamps the exact millisecond the truck arrived
        
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
def stock_purchase_order(po_id: int, expiry_date: date, current_user: dict = Depends(get_current_user)):
    # SECURITY CHECK
    require_role(current_user, UserRole.OWNER, UserRole.MANAGER, detail="Staff cannot stock inventory.")
    require_future_expiry(expiry_date)

    with Session(engine) as session:
        # 1. Get the PO
        po = session.get(PurchaseOrder, po_id)
        if not po or po.business_id != current_user["business_id"]:
            raise HTTPException(status_code=404, detail="Purchase Order not found")
            
        if po.status != "DELIVERED":
            raise HTTPException(status_code=400, detail="PO must be DELIVERED before it can be STOCKED")
            
        # 2. Update the PO status
        po.status = "STOCKED"
        
        # 3. Create the new Product Batch (Feature 8 Logic)
        new_batch = ProductBatch(
            product_id=po.product_id,
            po_id=po.id,
            business_id=current_user["business_id"],
            quantity=po.quantity,
            received_date=today(),
            expiry_date=expiry_date  # The user provides this when stocking
        )
        session.add(new_batch)
        
        # 4. Update the main Product total quantity
        product = session.get(Product, po.product_id)
        if product:
            product.quantity += po.quantity
            session.add(product)
            
        session.add(po)
        session.commit()
        
        return {
            "message": f"PO Stocked. {format_qty(po.quantity, product.unit if product else None)} added to main inventory.",
            "batch_expiry": new_batch.expiry_date
        }
    
# --- MANUAL STOCK AUDIT (PROTECTED) ---
@app.put("/products/{product_id}/manual-audit")
def manual_stock_adjustment(
    product_id: int, 
    new_quantity: int,
    current_user: dict = Depends(get_current_user)
):
    # Updated Security Gate: Owner and Manager only
    require_role(current_user, UserRole.OWNER, UserRole.MANAGER, detail="Access Denied: Only the Owner or a Manager can manually adjust stock levels.")

    with Session(engine) as session:
        clean_business_id = str(current_user["business_id"]).strip()
        
        product = session.exec(
            select(Product).where(
                Product.id == product_id, 
                Product.business_id == clean_business_id
            )
        ).first()

        if not product:
            raise HTTPException(status_code=404, detail="Product not found.")

        # Log the change (In a real audit, you'd want to know the old vs new)
        old_qty = product.quantity
        product.quantity = new_quantity
        
        session.add(product)
        session.commit()
        
        return {
            "success": True,
            "message": f"Manual audit completed for {product.name}",
            "previous_qty": old_qty,
            "new_qty": product.quantity,
            "authorized_by": f"{current_user['user_id']} ({current_user['role']})"
        }
    

class ProductBatch(SQLModel, table=True):
    __tablename__ = "product_batch"  # <--- FORCE THE TABLE NAME
    id: int | None = Field(default=None, primary_key=True)
    
    # Notice how we use the exact strings we defined above
    product_id: int = Field(foreign_key="product.id", index=True)
    po_id: int | None = Field(default=None, foreign_key="purchase_order.id")
    
    business_id: str = Field(index=True)
    quantity: Quantity = Field(default=Decimal("0"), max_digits=12, decimal_places=3)
    received_date: date
    expiry_date: date | None = None  # NULL = never expires (e.g. opening stock)


class SaleBatchAllocation(SQLModel, table=True):
    __tablename__ = "sale_batch_allocation"
    id: int | None = Field(default=None, primary_key=True)
    sale_id: int = Field(foreign_key="sales.id", index=True)
    batch_id: int = Field(foreign_key="product_batch.id", index=True)
    quantity: Quantity = Field(max_digits=12, decimal_places=3)
    business_id: str = Field(index=True)


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
            Product.quantity <= Product.min_stock_level
        )
        
        low_stock_items = session.exec(statement).all()
        
        # We format the response to be highly readable for both the frontend UI and future AI agents
        return {
            "alert_count": len(low_stock_items),
            "items_to_reorder": low_stock_items
        }
    

@app.post("/system/daily-check")
def daily_inventory_health_check(current_user: dict = Depends(get_current_user)):
    # SECURITY CHECK: Only Owners/Managers can trigger system sweeps
    require_role(current_user, UserRole.OWNER, UserRole.MANAGER, detail="Unauthorized.")

    with Session(engine) as session:
        # 1. Find batches that expired today (or earlier) that still have items left in them
        statement = select(ProductBatch).where(
            ProductBatch.business_id == current_user["business_id"],
            ProductBatch.expiry_date <= today(),
            ProductBatch.quantity > 0
        )
        expired_batches = session.exec(statement).all()
        
        items_removed = 0
        
        # 2. Process each expired batch
        for batch in expired_batches:
            # Find the main product on the shelf
            product = session.get(Product, batch.product_id)
            if product:
                # Remove the spoiled amount from the main sellable inventory
                product.quantity -= batch.quantity
                if product.quantity < 0:
                    product.quantity = 0  # Safety net to prevent negative inventory
                session.add(product)
                
            # "Trash" the batch quantity so the sweeper doesn't count it again tomorrow
            items_removed += batch.quantity
            batch.quantity = 0 
            session.add(batch)
            
        # Save all changes to the database
        session.commit()
        
        return {
            "message": "Daily health check complete.",
            "expired_batches_cleared": len(expired_batches),
            "total_items_removed_from_shelf": items_removed
        }