from fastapi import FastAPI
from pydantic import BaseModel
from typing import Optional

# Initialize the InvAi application
app = FastAPI()

# 1. Define the Blueprint for an Inventory Item
class InventoryItem(BaseModel):
    name: str
    price: float
    quantity: int
    barcode: Optional[str] = None  # Optional, for items that actually have them
    is_loose_item: bool = False    # True for our 'khuli chize' like sugar sacks

# 2. Our original health check endpoint
@app.get("/")
def health_check():
    return {
        "system": "InvAi Backend",
        "status": "Online and Ready",
        "message": "Welcome to the future of inventory management."
    }

# 3. NEW: An endpoint to receive and log a new item
@app.post("/add-item/")
def add_item(item: InventoryItem):
    # For now, we just return the item back to prove the server caught it.
    # Later, we will write the code here to save it to PostgreSQL.
    return {
        "success": True,
        "message": f"{item.name} has been successfully logged!",
        "data_received": item
    }