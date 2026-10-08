"""Sales & Trends reports (Owner and Manager only)."""
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Query

from app.models import StockUnit, UserRole
from app.security import get_current_user, require_role
from app.services import reports as report_service

router = APIRouter()

REPORT_ROLE_MESSAGE = "Only the Owner or a Manager can view reports."


@router.get("/reports/sales-summary")
def sales_summary(
    from_date: date | None = Query(None, description="First India date (default: 29 days before to_date)"),
    to_date: date | None = Query(None, description="Last India date, inclusive (default: today)"),
    current_user: dict = Depends(get_current_user)
):
    require_role(current_user, UserRole.OWNER, UserRole.MANAGER, detail=REPORT_ROLE_MESSAGE)
    return report_service.sales_summary(current_user["business_id"], from_date, to_date)


@router.get("/reports/top-products")
def top_products(
    from_date: date | None = Query(None, description="First India date (default: 29 days before to_date)"),
    to_date: date | None = Query(None, description="Last India date, inclusive (default: today)"),
    by: Literal["revenue", "quantity"] = Query("revenue"),
    unit: StockUnit = Query(StockUnit.PIECE, description="With by=quantity, rank only products sold in this unit; ignored for revenue"),
    limit: int = Query(10, ge=1, le=100),
    current_user: dict = Depends(get_current_user)
):
    require_role(current_user, UserRole.OWNER, UserRole.MANAGER, detail=REPORT_ROLE_MESSAGE)
    return report_service.top_products(current_user["business_id"], from_date, to_date, by, unit, limit)


@router.get("/reports/dead-stock")
def dead_stock(
    days: int = Query(30, ge=1, le=3650, description="No sales in the last N India days, including today"),
    current_user: dict = Depends(get_current_user)
):
    require_role(current_user, UserRole.OWNER, UserRole.MANAGER, detail=REPORT_ROLE_MESSAGE)
    return report_service.dead_stock(current_user["business_id"], days)


@router.get("/reports/waste")
def waste_report(
    from_date: date | None = Query(None, description="First India date (default: 29 days before to_date)"),
    to_date: date | None = Query(None, description="Last India date, inclusive (default: today)"),
    current_user: dict = Depends(get_current_user)
):
    require_role(current_user, UserRole.OWNER, UserRole.MANAGER, detail=REPORT_ROLE_MESSAGE)
    return report_service.waste_report(current_user["business_id"], from_date, to_date)
