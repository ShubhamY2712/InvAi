from fastapi import FastAPI, HTTPException
from sqlmodel import Field, Session, SQLModel, create_engine, select
from typing import Optional
import os
from dotenv import load_dotenv
from contextlib import asynccontextmanager

# Load the secret connection string from the .env file
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

# Create the Engine (The bridge to Neon PostgreSQL)
engine = create_engine(DATABASE_URL, echo=True)

# 1. Define the Database Table
class InventoryItem(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    price: float
    quantity: int
    barcode: Optional[str] = None
    is_loose_item: bool = False

# 2. Tell FastAPI to create the tables when the server starts
@asynccontextmanager
async def lifespan(app: FastAPI):
    SQLModel.metadata.create_all(engine)
    yield

# Initialize the InvAi application with the lifespan manager
app = FastAPI(lifespan=lifespan)

@app.get("/")
def health_check():
    return {
        "system": "InvAi Backend",
        "status": "Online and Connected to Cloud DB",
    }

# 3. Upgraded POST endpoint to save data permanently
@app.post("/add-item/")
def add_item(item: InventoryItem):
    # Open a session with the database, add the item, and commit (save) it
    with Session(engine) as session:
        session.add(item)
        session.commit()
        session.refresh(item) # Retrieves the newly generated ID from the database
        
        return {
            "success": True,
            "message": f"{item.name} has been securely saved to the database!",
            "data": item
        }
    
    # 4. NEW: Endpoint to fetch all inventory items
@app.get("/items/")
def get_all_items():
    with Session(engine) as session:
        # Fetch every row from the InventoryItem table
        items = session.exec(select(InventoryItem)).all()
        return {
            "success": True,
            "total_items": len(items),
            "data": items
        }
    
    # 5. NEW: Update an existing item (e.g., when a sale happens and stock drops)
@app.put("/items/{item_id}")
def update_item(item_id: int, updated_data: InventoryItem):
    with Session(engine) as session:
        # Find the specific item in the database
        db_item = session.get(InventoryItem, item_id)
        if not db_item:
            raise HTTPException(status_code=404, detail="Item not found in inventory")
        
        # Update the data
        db_item.name = updated_data.name
        db_item.price = updated_data.price
        db_item.quantity = updated_data.quantity
        db_item.barcode = updated_data.barcode
        db_item.is_loose_item = updated_data.is_loose_item
        
        # Save the changes permanently
        session.add(db_item)
        session.commit()
        session.refresh(db_item)
        
        return {
            "success": True, 
            "message": f"Item {item_id} successfully updated.", 
            "data": db_item
        }

# 6. NEW: Delete an item (e.g., shop stops selling a product)
@app.delete("/items/{item_id}")
def delete_item(item_id: int):
    with Session(engine) as session:
        db_item = session.get(InventoryItem, item_id)
        if not db_item:
            raise HTTPException(status_code=404, detail="Item not found in inventory")
        
        # Erase the item from the database
        session.delete(db_item)
        session.commit()
        
        return {"success": True, "message": f"Item {item_id} has been permanently deleted."}