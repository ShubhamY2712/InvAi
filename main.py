from fastapi import FastAPI, HTTPException
from sqlmodel import Field, Session, SQLModel, create_engine, select
from typing import Optional
import os
from dotenv import load_dotenv
from contextlib import asynccontextmanager

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_engine(DATABASE_URL, echo=True)

# 1. UPGRADED DATABASE SCHEMA (Enterprise Parent-Child Architecture)
class InventoryItem(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    price: float
    quantity: float  # Changed to float to support 1.5kg etc.
    barcode: Optional[str] = None
    is_loose_item: bool = False
    
    # NEW: Parent-Child Linkage
    parent_id: Optional[int] = Field(default=None, foreign_key="inventoryitem.id")
    units_per_parent: Optional[float] = None  # e.g., 50 (if 1 sack = 50kg)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # DEVELOPMENT ONLY: Wipes the old table to build the new advanced architecture
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    yield

app = FastAPI(lifespan=lifespan)

@app.get("/")
def health_check():
    return {"system": "InvAi Backend", "status": "V2 Architecture Online"}

@app.post("/add-item/")
def add_item(item: InventoryItem):
    with Session(engine) as session:
        session.add(item)
        session.commit()
        session.refresh(item)
        return {"success": True, "message": "Saved to V2 Database", "data": item}

@app.get("/items/")
def get_all_items():
    with Session(engine) as session:
        items = session.exec(select(InventoryItem)).all()
        return {"success": True, "total_items": len(items), "data": items}

@app.put("/items/{item_id}")
def update_item(item_id: int, updated_data: InventoryItem):
    with Session(engine) as session:
        db_item = session.get(InventoryItem, item_id)
        if not db_item:
            raise HTTPException(status_code=404, detail="Item not found")
        
        db_item.name = updated_data.name
        db_item.price = updated_data.price
        db_item.quantity = updated_data.quantity
        db_item.barcode = updated_data.barcode
        db_item.is_loose_item = updated_data.is_loose_item
        db_item.parent_id = updated_data.parent_id
        db_item.units_per_parent = updated_data.units_per_parent
        
        session.add(db_item)
        session.commit()
        session.refresh(db_item)
        return {"success": True, "data": db_item}

@app.delete("/items/{item_id}")
def delete_item(item_id: int):
    with Session(engine) as session:
        db_item = session.get(InventoryItem, item_id)
        if not db_item:
            raise HTTPException(status_code=404, detail="Item not found")
        session.delete(db_item)
        session.commit()
        return {"success": True, "message": f"Item {item_id} deleted."}

        # 7. NEW: Enterprise "Unpack" Logic (Atomic Transaction)
@app.post("/unpack/{child_id}")
def unpack_parent_item(child_id: int):
    with Session(engine) as session:
        # 1. Find the loose item (Child)
        child_item = session.get(InventoryItem, child_id)
        if not child_item or not child_item.parent_id:
            raise HTTPException(status_code=400, detail="Item is not a valid child item.")
            
        # 2. Find the bulk item (Parent)
        parent_item = session.get(InventoryItem, child_item.parent_id)
        if not parent_item:
            raise HTTPException(status_code=404, detail="Parent bulk item not found.")
            
        # 3. Check if we actually have sacks left to unpack
        if parent_item.quantity < 1:
            raise HTTPException(status_code=400, detail=f"Not enough {parent_item.name} to unpack!")
            
        # 4. THE ATOMIC MATH: Deduct 1 sack, add the loose units
        parent_item.quantity -= 1
        child_item.quantity += child_item.units_per_parent
        
        # 5. Save BOTH changes simultaneously
        session.add(parent_item)
        session.add(child_item)
        session.commit()
        session.refresh(parent_item)
        session.refresh(child_item)
        
        return {
            "success": True,
            "message": f"Successfully unpacked 1 unit of {parent_item.name}.",
            "new_parent_stock": parent_item.quantity,
            "new_child_stock": child_item.quantity
        }