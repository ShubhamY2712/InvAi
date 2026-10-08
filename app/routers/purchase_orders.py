"""Purchase orders: order, cancel, deliver, stock."""
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, Query

from app.models import UserRole
from app.schemas import PurchaseOrderCreate
from app.security import acting_user_id, get_current_user, require_role
from app.services import purchase_orders

router = APIRouter()


@router.post("/purchase-orders/")
def process_purchase_order(
    request: PurchaseOrderCreate,
    current_user: dict = Depends(get_current_user)
):
    return purchase_orders.create_purchase_order(request, current_user["business_id"])


@router.post("/purchase-orders/{po_id}/cancel")
def cancel_purchase_order(
    po_id: int,
    reason: str | None = Query(None, max_length=500, description="Why the order was cancelled"),
    current_user: dict = Depends(get_current_user)
):
    require_role(current_user, UserRole.OWNER, UserRole.MANAGER, detail="Only the Owner or a Manager can cancel purchase orders.")
    return purchase_orders.cancel_purchase_order(po_id, reason, current_user["business_id"])


@router.put("/purchase-orders/{po_id}/deliver")
def mark_po_delivered(
    po_id: int,
    current_user: dict = Depends(get_current_user)
):
    return purchase_orders.deliver_purchase_order(po_id, current_user["business_id"])


# --- STEP 3: The Shelf (Scan into Inventory) ---
@router.put("/purchase-orders/{po_id}/stock")
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
    return purchase_orders.stock_purchase_order(po_id, expiry_date, received_quantity, rejected_quantity,
                                                current_user["business_id"], acting_user_id(current_user))
