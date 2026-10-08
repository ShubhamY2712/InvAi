"""The product catalogue."""
from fastapi import APIRouter, Depends, Query

from app.models import UserRole
from app.schemas import ProductCreate, ProductUpdate
from app.security import acting_user_id, get_current_user, require_role
from app.services import products as product_service

router = APIRouter()


@router.post("/products/")
def add_product(
    product_data: ProductCreate,
    current_user: dict = Depends(get_current_user) # The Bouncer checks the token first!
):
    # SECURITY CHECK
    require_role(current_user, UserRole.OWNER, UserRole.MANAGER, detail="Staff cannot create new products.")
    return product_service.create_product(product_data, current_user["business_id"], acting_user_id(current_user))


@router.get("/products/")
def get_inventory(
    include_inactive: bool = Query(False, description="Also list deactivated products"),
    current_user: dict = Depends(get_current_user)
):
    return product_service.list_products(current_user["business_id"], include_inactive)


@router.patch("/products/{product_id}")
def update_product(
    product_id: int,
    product_update: ProductUpdate,
    current_user: dict = Depends(get_current_user)
):
    # SECURITY CHECK
    require_role(current_user, UserRole.OWNER, UserRole.MANAGER, detail="Staff cannot edit product details.")
    return product_service.update_product(product_id, product_update, current_user["business_id"])


@router.delete("/products/{product_id}")
def delete_product(
    product_id: int,
    current_user: dict = Depends(get_current_user)
):
    """Deactivates the product. Nothing is deleted: its batches, sales and ledger history stay."""
    # SECURITY CHECK
    require_role(current_user, UserRole.OWNER, detail="Only the Owner can delete products from the system.")
    return product_service.deactivate_product(product_id, current_user["business_id"])


@router.post("/products/{product_id}/reactivate")
def reactivate_product(product_id: int, current_user: dict = Depends(get_current_user)):
    require_role(current_user, UserRole.OWNER, detail="Only the Owner can reactivate products.")
    return product_service.reactivate_product(product_id, current_user["business_id"])
