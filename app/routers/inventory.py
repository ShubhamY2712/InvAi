"""Stock state: batches, alerts, manual audits, the movement ledger, and the daily check."""
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, Query

from app.models import MovementReason, UserRole
from app.security import acting_user_id, get_current_user, require_role
from app.services import audit, daily_check, inventory, reports

router = APIRouter()


@router.get("/alerts/expiring-soon/")
def get_expiring_batches(
    days: int = Query(7, ge=1, le=3650, description="How many days ahead to look"),
    current_user: dict = Depends(get_current_user)
):
    """Returns this business's batches expiring after today and up to today + days, soonest first.
    A batch expiring today is already expired (same rule as checkout and the daily check)."""
    return inventory.expiring_soon(current_user["business_id"], days)


@router.put("/products/{product_id}/manual-audit")
def manual_stock_adjustment(
    product_id: int,
    new_quantity: Decimal = Query(..., ge=0, max_digits=12, decimal_places=3, description="The physically counted stock"),
    expiry_date: date | None = Query(None, description="Expiry for an adjustment batch, if the count is higher than recorded"),
    note: str | None = Query(None, max_length=500, description="Why the count changed; stored on the ledger entries"),
    current_user: dict = Depends(get_current_user)
):
    # Updated Security Gate: Owner and Manager only
    require_role(current_user, UserRole.OWNER, UserRole.MANAGER, detail="Access Denied: Only the Owner or a Manager can manually adjust stock levels.")
    return audit.manual_audit(product_id, new_quantity, expiry_date, note, current_user["business_id"],
                              acting_user_id(current_user), f"{current_user['user_id']} ({current_user['role']})")


@router.get("/products/{product_id}/batches")
def get_product_batches(product_id: int, current_user: dict = Depends(get_current_user)):
    return inventory.product_batches(product_id, current_user["business_id"])


@router.get("/inventory/alerts/low-stock")
def get_low_stock_alerts(current_user: dict = Depends(get_current_user)):
    return inventory.low_stock(current_user["business_id"])


@router.post("/system/daily-check")
def daily_inventory_health_check(current_user: dict = Depends(get_current_user)):
    # SECURITY CHECK: Only Owners/Managers can trigger system sweeps
    require_role(current_user, UserRole.OWNER, UserRole.MANAGER, detail="Unauthorized.")
    result = daily_check.run_for_business(current_user["business_id"], acting_user_id(current_user))
    return {"message": "Daily health check complete.", **result}


@router.get("/inventory/movements")
def list_stock_movements(
    product_id: int | None = Query(None),
    reason: MovementReason | None = Query(None),
    from_date: date | None = Query(None, description="First India date (default: 29 days before to_date)"),
    to_date: date | None = Query(None, description="Last India date, inclusive (default: today)"),
    limit: int = Query(100, ge=1, le=500),
    current_user: dict = Depends(get_current_user)
):
    require_role(current_user, UserRole.OWNER, UserRole.MANAGER, detail="Only the Owner or a Manager can view stock movements.")
    return reports.list_movements(current_user["business_id"], product_id, reason, from_date, to_date, limit)
