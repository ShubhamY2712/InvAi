"""Suppliers and their scorecards."""
from datetime import date

from fastapi import APIRouter, Depends, Query

from app.models import UserRole
from app.schemas import SupplierCreate
from app.security import get_current_user, require_role
from app.services import scorecards, suppliers

router = APIRouter()

SCORECARD_ROLE_MESSAGE = "Only the Owner or a Manager can view supplier scorecards."


@router.post("/suppliers/")
def add_supplier(
    supplier: SupplierCreate,
    current_user: dict = Depends(get_current_user)
):
    require_role(current_user, UserRole.OWNER, UserRole.MANAGER, detail="Only the Owner or a Manager can add suppliers.")
    return suppliers.add_supplier(supplier, current_user["business_id"])


@router.get("/suppliers/scorecards")
def supplier_scorecards(
    from_date: date | None = Query(None, description="First India date (default: 89 days before to_date)"),
    to_date: date | None = Query(None, description="Last India date, inclusive (default: today)"),
    current_user: dict = Depends(get_current_user)
):
    require_role(current_user, UserRole.OWNER, UserRole.MANAGER, detail=SCORECARD_ROLE_MESSAGE)
    return scorecards.all_scorecards(current_user["business_id"], from_date, to_date)


@router.get("/suppliers/{supplier_id}/scorecard")
def supplier_scorecard(
    supplier_id: int,
    from_date: date | None = Query(None, description="First India date (default: 89 days before to_date)"),
    to_date: date | None = Query(None, description="Last India date, inclusive (default: today)"),
    current_user: dict = Depends(get_current_user)
):
    require_role(current_user, UserRole.OWNER, UserRole.MANAGER, detail=SCORECARD_ROLE_MESSAGE)
    return scorecards.supplier_scorecard(supplier_id, current_user["business_id"], from_date, to_date)
