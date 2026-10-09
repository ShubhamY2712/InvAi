"""Selling, and the sales history."""
from fastapi import APIRouter, Depends

from app.models import UserRole
from app.schemas import CheckoutRequest
from app.security import get_current_user, has_role, require_user_id
from app.services import checkout

router = APIRouter()


@router.post("/checkout/")
def process_checkout(
    request: CheckoutRequest,
    current_user: dict = Depends(get_current_user)
):
    return checkout.checkout(request, current_user["business_id"], require_user_id(current_user))


@router.get("/sales/")
def get_sales_history(current_user: dict = Depends(get_current_user)):
    # The Boss sees EVERYTHING for this business; Staff only their own sales
    own_sales_only = not has_role(current_user, UserRole.OWNER, UserRole.MANAGER)
    return checkout.sales_history(current_user["business_id"], require_user_id(current_user), own_sales_only)
