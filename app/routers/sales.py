"""Selling, and the sales history."""
from fastapi import APIRouter, Depends

from app.models import UserRole
from app.schemas import CheckoutRequest
from app.security import get_current_user, has_role
from app.services import checkout

router = APIRouter()


@router.post("/checkout/")
def process_checkout(
    request: CheckoutRequest,
    current_user: dict = Depends(get_current_user)
):
    # We force it to be a string, strip any invisible spaces, then force to integer
    clean_user_id = int(str(current_user["user_id"]).strip())
    return checkout.checkout(request, current_user["business_id"], clean_user_id)


@router.get("/sales/")
def get_sales_history(current_user: dict = Depends(get_current_user)):
    # Clean the token data just like we did in checkout
    clean_user_id = int(str(current_user["user_id"]).strip())
    # The Boss sees EVERYTHING for this business; Staff only their own sales
    own_sales_only = not has_role(current_user, UserRole.OWNER, UserRole.MANAGER)
    return checkout.sales_history(current_user["business_id"], clean_user_id, own_sales_only)
