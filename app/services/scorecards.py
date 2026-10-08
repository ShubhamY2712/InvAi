"""Supplier scorecards, based on the POs stocked (closed) on India dates in the range.
Computed in Python from one query's rows: most metrics are averages of per-PO ratios, and SQLite (used in tests)
has no standard deviation."""
import statistics
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlmodel import Session, select

from app import db
from app.errors import NotFound
from app.models import PurchaseOrder, Supplier
from app.services.reports import report_range
from app.timeutils import ist_date_of, ist_day_start_utc

SCORECARD_DEFAULT_DAYS = 90
RATIO_PLACES = Decimal("0.0001")
DAYS_PLACES = Decimal("0.01")


def _rounded_mean(values: list[Decimal], places: Decimal) -> Decimal | None:
    return (sum(values, Decimal("0")) / len(values)).quantize(places, rounding=ROUND_HALF_UP) if values else None


def supplier_metrics(pos: list[PurchaseOrder]) -> dict:
    """Scorecard metrics for one supplier's stocked POs. Every metric is None when there are no POs, and each is
    None when no PO qualifies for it."""
    metrics = {"po_count": len(pos), "avg_lead_time_days": None, "on_time_rate": None,
               "fill_rate": None, "defect_rate": None, "price_volatility": None}
    if not pos:
        return metrics

    lead_days = [Decimal(str((po.delivered_at - po.timestamp).total_seconds())) / 86400
                 for po in pos if po.delivered_at and po.timestamp]
    on_time = [Decimal(1) if ist_date_of(po.delivered_at) <= po.expected_delivery_date else Decimal(0)
               for po in pos if po.expected_delivery_date and po.delivered_at]

    fills, defects = [], []
    for po in pos:
        ordered = Decimal(str(po.quantity))
        # POs stocked before these columns existed took the full order with nothing rejected
        received = Decimal(str(po.received_quantity)) if po.received_quantity is not None else ordered
        rejected = Decimal(str(po.rejected_quantity)) if po.rejected_quantity is not None else Decimal("0")
        if ordered > 0:
            fills.append(min(received / ordered, Decimal(1)))
        if received > 0:
            defects.append(rejected / received)

    # Price volatility: coefficient of variation (population std dev / mean) of unit_cost per product
    costs_by_product: dict[int, list[Decimal]] = {}
    for po in pos:
        costs_by_product.setdefault(po.product_id, []).append(Decimal(str(po.unit_cost)))
    volatilities = [statistics.pstdev(costs) / statistics.mean(costs)
                    for costs in costs_by_product.values() if len(costs) >= 2 and statistics.mean(costs) > 0]

    metrics.update({
        "avg_lead_time_days": _rounded_mean(lead_days, DAYS_PLACES),
        "on_time_rate": _rounded_mean(on_time, RATIO_PLACES),
        "fill_rate": _rounded_mean(fills, RATIO_PLACES),
        "defect_rate": _rounded_mean(defects, RATIO_PLACES),
        "price_volatility": _rounded_mean(volatilities, RATIO_PLACES),
    })
    return metrics


def stocked_pos_in_range(session: Session, business_id: str, from_date: date, to_date: date,
                         supplier_id: int | None = None) -> list[PurchaseOrder]:
    conditions = [
        PurchaseOrder.business_id == business_id,
        PurchaseOrder.status == "STOCKED",
        PurchaseOrder.stocked_at >= ist_day_start_utc(from_date),
        PurchaseOrder.stocked_at < ist_day_start_utc(to_date + timedelta(days=1)),
    ]
    if supplier_id is not None:
        conditions.append(PurchaseOrder.supplier_id == supplier_id)
    return session.exec(select(PurchaseOrder).where(*conditions).order_by(PurchaseOrder.id)).all()


def all_scorecards(business_id: str, from_date: date | None, to_date: date | None) -> dict:
    from_date, to_date = report_range(from_date, to_date, default_days=SCORECARD_DEFAULT_DAYS)
    with db.new_session() as session:
        suppliers = session.exec(
            select(Supplier).where(Supplier.business_id == business_id).order_by(Supplier.name, Supplier.id)
        ).all()
        pos_by_supplier: dict[int, list] = {}
        for po in stocked_pos_in_range(session, business_id, from_date, to_date):
            pos_by_supplier.setdefault(po.supplier_id, []).append(po)

    return {
        "from_date": from_date,
        "to_date": to_date,
        "suppliers": [
            {"supplier_id": s.id, "name": s.name, **supplier_metrics(pos_by_supplier.get(s.id, []))}
            for s in suppliers
        ]
    }


def supplier_scorecard(supplier_id: int, business_id: str, from_date: date | None, to_date: date | None) -> dict:
    from_date, to_date = report_range(from_date, to_date, default_days=SCORECARD_DEFAULT_DAYS)
    with db.new_session() as session:
        supplier = session.exec(
            select(Supplier).where(Supplier.id == supplier_id, Supplier.business_id == business_id)
        ).first()
        if not supplier:
            raise NotFound("Supplier not found.")
        pos = stocked_pos_in_range(session, business_id, from_date, to_date, supplier_id=supplier.id)

    return {"supplier_id": supplier.id, "name": supplier.name, "from_date": from_date, "to_date": to_date,
            **supplier_metrics(pos)}
