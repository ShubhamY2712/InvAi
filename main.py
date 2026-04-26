from fastapi import FastAPI, HTTPException, Header
from sqlmodel import Field, Session, SQLModel, create_engine, select
from typing import Optional, List
import os
from dotenv import load_dotenv
from contextlib import asynccontextmanager
from enum import Enum
from datetime import date, timedelta

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_engine(DATABASE_URL, echo=True)

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

    # --- NEW: FEATURE 8 (EXPIRY MANAGEMENT) ---
    batch_number: Optional[str] = None
    expiry_date: Optional[date] = None


# --- 3. LIFESPAN ALERTS ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    yield

app = FastAPI(lifespan=lifespan)

# --- 4. FEATURE 1: DYNAMIC ONBOARDING ---

class OnboardingRequest(SQLModel):
    business_name: str
    category: BusinessCategory
    owner_username: str

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
        
        new_user = User(
            id=owner_id,
            username=request.owner_username,
            role=UserRole.OWNER,
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
    

    # --- 5. FEATURE 2 & 5: SECURE INVENTORY MANAGEMENT ---

@app.post("/add-item/")
def add_item(item: InventoryItem, x_user_id: str = Header(...)):
    """
    Adds an item, but ONLY if the user is an Owner/Manager.
    Automatically locks the item to the user's specific business.
    """
    with Session(engine) as session:
        # 1. Identity Check
        user = session.get(User, x_user_id)
        if not user:
            raise HTTPException(status_code=401, detail="Unauthorized: User not found.")
            
        # 2. RBAC (Role-Based Access Control) Security Gate
        if user.role == UserRole.CASHIER:
            raise HTTPException(status_code=403, detail="Forbidden: Cashiers cannot add new inventory types.")
            
        # 3. The SaaS Lock: Force the item into the user's business silo
        item.business_id = user.business_id
        
        session.add(item)
        session.commit()
        session.refresh(item)
        return {"success": True, "message": "Item securely added.", "data": item}

@app.get("/items/")
def get_all_items(x_user_id: int = Header(...)):
    """
    Tenant Isolation: Returns ONLY the items belonging to the user's business.
    """
    with Session(engine) as session:
        user = session.get(User, x_user_id)
        if not user:
            raise HTTPException(status_code=401, detail="Unauthorized.")
            
        # 4. Data Isolation: Filter the database by business_id
        items = session.exec(select(InventoryItem).where(InventoryItem.business_id == user.business_id)).all()
        
        return {
            "success": True, 
            "business_id": user.business_id, 
            "total_items": len(items), 
            "data": items
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