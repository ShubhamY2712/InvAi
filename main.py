from fastapi import FastAPI, Depends, HTTPException, Header, status
from fastapi.security import OAuth2PasswordRequestForm, OAuth2PasswordBearer
from sqlmodel import Field, Session, SQLModel, create_engine, select
from typing import Optional, List
import os
from dotenv import load_dotenv
from contextlib import asynccontextmanager
from enum import Enum
from datetime import date, timedelta
import bcrypt
from datetime import datetime, timedelta, timezone
from jose import jwt, JWTError

# --- JWT CONFIGURATION ---
# --- THE DOOR ---
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")
SECRET_KEY = "invai_super_secret_dev_key_123!" # Never share this in production
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

load_dotenv()
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

class UserRole(str, Enum):
    OWNER = "Owner"
    MANAGER = "Manager"
    CASHIER = "Cashier"


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
    # ID is a String. We will manually construct this (Business ID + Employee Number)
    id: str = Field(primary_key=True) 
    username: str
    email: str = Field(unique=True, index=True)
    hashed_password: str
    role: UserRole = UserRole.CASHIER
    business_id: str = Field(foreign_key="businessprofile.id") 

class InventoryItem(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    price: float
    quantity: float
    barcode: Optional[str] = None
    is_loose_item: bool = False
    parent_id: Optional[int] = Field(default=None, foreign_key="inventoryitem.id")
    units_per_parent: Optional[float] = None
    business_id: str = Field(foreign_key="businessprofile.id")

class Product(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    sku: str = Field(index=True) # Stock Keeping Unit (Barcode equivalent)
    description: str | None = None
    price: float
    quantity: int = Field(default=0)
    
    # The crucial multi-tenant lock: This ties the product to a specific business
    business_id: str = Field(foreign_key="businessprofile.id", index=True)

    # --- NEW: FEATURE 8 (EXPIRY MANAGEMENT) ---
    batch_number: Optional[str] = None
    expiry_date: Optional[date] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- DISARMED THE SLEDGEHAMMER ---
    #SQLModel.metadata.drop_all(engine)
    #SQLModel.metadata.create_all(engine)
    #print("🧨 DATABASE WIPED AND REBUILT 🧨")
    
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
    price: float
    quantity: int = 0
    description: str | None = None

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
    with Session(engine) as session:
        # Create the database record, combining user data with the Bouncer's secure ID
        new_product = Product(
            name=product_data.name,
            sku=product_data.sku,
            price=product_data.price,
            quantity=product_data.quantity,
            description=product_data.description,
            business_id=current_user["business_id"] # <-- THE MULTI-TENANT LOCK
        )
        
        session.add(new_product)
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

class EmployeeCreateRequest(SQLModel):
    username: str
    role: UserRole = UserRole.CASHIER  # Defaults to Cashier to prevent accidental admin creation

@app.post("/add-employee/")
def add_employee(
    request: EmployeeCreateRequest,
    x_user_id: str = Header(...) # Changed to str
):
    with Session(engine) as session:
        admin_user = session.get(User, x_user_id)
        if not admin_user or admin_user.role == UserRole.CASHIER:
            raise HTTPException(status_code=403, detail="Unauthorized")
            
        # Count existing users in this business to get the next number
        existing_users = session.exec(select(User).where(User.business_id == admin_user.business_id)).all()
        
        # If there are 3 users, this makes the next string "004"
        next_employee_number = str(len(existing_users) + 1).zfill(3) 
        
        # Assemble the New ID: e.g., "4092004"
        new_employee_id = f"{admin_user.business_id}{next_employee_number}"
        
        new_employee = User(
            id=new_employee_id,
            username=request.username,
            role=request.role,
            business_id=admin_user.business_id
        )
        
        session.add(new_employee)
        session.commit()
        session.refresh(new_employee)
        
        return {"success": True, "employee_id": new_employee.id}
    

  # --- 7. FEATURE 8: EXPIRY MANAGEMENT (The Alarm System) ---

@app.get("/alerts/expiring-soon/")
def get_expiring_items(
    days_warning: int = 30, 
    x_user_id: str = Header(..., description="Simulated Auth Token")
):
    """
    Scans the tenant's inventory and returns items expiring within the specified days.
    Defaults to a 30-day warning window.
    """
    with Session(engine) as session:
        # 1. Identity & SaaS Lock Check
        user = session.get(User, x_user_id)
        if not user:
            raise HTTPException(status_code=401, detail="Unauthorized.")
            
        # 2. Calculate the "Danger Zone" Date
        # If today is April 26, 2026, threshold_date becomes May 26, 2026.
        threshold_date = date.today() + timedelta(days=days_warning)
        
        # 3. Database Radar Scan
        # Find items where: Business matches AND expiry date exists AND expiry date is before the threshold
        statement = select(InventoryItem).where(
            InventoryItem.business_id == user.business_id,
            InventoryItem.expiry_date != None,
            InventoryItem.expiry_date <= threshold_date
        )
        
        expiring_items = session.exec(statement).all()
        
        return {
            "success": True,
            "business_id": user.business_id,
            "danger_zone_date": threshold_date,
            "alert_count": len(expiring_items),
            "data": expiring_items
        }
    
@app.get("/dev/me/")
def get_my_profile(current_user: dict = Depends(get_current_user)):
    """A protected route. You can only see this if the Bouncer lets you in."""
    return {
        "success": True,
        "message": "You made it past the bouncer!",
        "your_secure_data": current_user
    }

