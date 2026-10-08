"""Reports: sales summary, top products, dead stock, stock movements and waste.
Every report date and daily bucket is an India (Asia/Kolkata) calendar day; the aggregation runs in SQL."""
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

from sqlalchemy import func, or_
from sqlmodel import select

from app import db, timeutils
from app.errors import InvalidInput
from app.models import MovementReason, Product, Sale, StockMovement, StockUnit
from app.services.common import round_money
from app.timeutils import BUSINESS_TZ, ist_date, ist_day_start_utc

REPORT_DEFAULT_DAYS = 30
REPORT_MAX_SPAN_DAYS = 366


def report_range(from_date: date | None, to_date: date | None, default_days: int = REPORT_DEFAULT_DAYS) -> tuple[date, date]:
    """Fills in the default range (the last default_days days including today) and validates it."""
    to_date = to_date or timeutils.today()
    from_date = from_date or to_date - timedelta(days=default_days - 1)
    if from_date > to_date:
        raise InvalidInput("from_date must be on or before to_date.")
    if (to_date - from_date).days > REPORT_MAX_SPAN_DAYS:
        raise InvalidInput(f"from_date and to_date can be at most {REPORT_MAX_SPAN_DAYS} days apart.")
    return from_date, to_date


def sales_in_range(business_id: str, from_date: date, to_date: date) -> tuple:
    """WHERE conditions for this business's sales on India dates from_date..to_date inclusive."""
    return (
        Sale.business_id == business_id,
        Sale.timestamp >= ist_day_start_utc(from_date),
        Sale.timestamp < ist_day_start_utc(to_date + timedelta(days=1)),
    )


def movements_in_range(business_id: str, from_date: date, to_date: date) -> tuple:
    """WHERE conditions for this business's ledger entries on India dates from_date..to_date inclusive."""
    return (
        StockMovement.business_id == business_id,
        StockMovement.created_at >= ist_day_start_utc(from_date),
        StockMovement.created_at < ist_day_start_utc(to_date + timedelta(days=1)),
    )


def sales_summary(business_id: str, from_date: date | None, to_date: date | None) -> dict:
    from_date, to_date = report_range(from_date, to_date)
    in_range = sales_in_range(business_id, from_date, to_date)
    day = ist_date(Sale.timestamp)

    with db.new_session() as session:
        sales_count, revenue = session.exec(
            select(func.count(Sale.id), func.coalesce(func.sum(Sale.total_price), 0)).where(*in_range)
        ).one()
        per_day = session.exec(
            select(day, func.count(Sale.id), func.sum(Sale.total_price)).where(*in_range).group_by(day)
        ).all()

    by_day = {sale_day: (count, day_revenue) for sale_day, count, day_revenue in per_day}
    daily = []
    for offset in range((to_date - from_date).days + 1):  # zero-fill days without sales
        current = from_date + timedelta(days=offset)
        count, day_revenue = by_day.get(current, (0, 0))
        daily.append({"date": current, "revenue": round_money(day_revenue), "sales_count": count})

    revenue = round_money(revenue)
    return {
        "from_date": from_date,
        "to_date": to_date,
        "timezone": BUSINESS_TZ.key,
        "total_revenue": revenue,
        "sales_count": sales_count,
        "average_sale_value": round_money(revenue / sales_count) if sales_count else None,
        "daily": daily
    }


def top_products(business_id: str, from_date: date | None, to_date: date | None,
                 by: Literal["revenue", "quantity"], unit: StockUnit, limit: int) -> dict:
    from_date, to_date = report_range(from_date, to_date)

    quantity_sold = func.sum(Sale.quantity)  # one product, one unit: never sums across units
    revenue = func.sum(Sale.total_price)
    ranking = (quantity_sold, revenue) if by == "quantity" else (revenue, quantity_sold)
    conditions = [*sales_in_range(business_id, from_date, to_date),
                  Product.business_id == business_id]
    if by == "quantity":
        conditions.append(Product.unit == unit)  # quantities are only comparable within one unit

    with db.new_session() as session:
        rows = session.exec(
            select(Product.id, Product.name, Product.unit, quantity_sold, revenue, func.count(Sale.id))
            .join(Product, Product.id == Sale.product_id)
            .where(*conditions)
            .group_by(Product.id, Product.name, Product.unit)
            .order_by(ranking[0].desc(), ranking[1].desc(), Product.id)
            .limit(limit)
        ).all()

    return {
        "from_date": from_date,
        "to_date": to_date,
        "by": by,
        "unit": unit.value if by == "quantity" else None,
        "products": [
            {
                "rank": rank,
                "product_id": product_id,
                "name": name,
                "unit": unit.value,
                "quantity_sold": Decimal(str(qty)).quantize(Decimal("0.001")),
                "revenue": round_money(product_revenue),
                "sales_count": count
            }
            for rank, (product_id, name, unit, qty, product_revenue, count) in enumerate(rows, start=1)
        ]
    }


def dead_stock(business_id: str, days: int) -> dict:
    current_date = timeutils.today()
    window_start = current_date - timedelta(days=days - 1)

    last_sale = (
        select(Sale.product_id, func.max(ist_date(Sale.timestamp)).label("last_sale_date"))
        .where(Sale.business_id == business_id)
        .group_by(Sale.product_id)
        .subquery()
    )

    with db.new_session() as session:
        rows = session.exec(
            select(Product, last_sale.c.last_sale_date)
            .outerjoin(last_sale, last_sale.c.product_id == Product.id)
            .where(
                Product.business_id == business_id,
                Product.quantity > 0,
                or_(last_sale.c.last_sale_date.is_(None), last_sale.c.last_sale_date < window_start)
            )
            .order_by((Product.quantity * Product.price).desc(), Product.id)
        ).all()

    return {
        "days": days,
        "since": window_start,
        "products": [
            {
                "product_id": product.id,
                "name": product.name,
                "unit": product.unit.value,
                "stock": product.quantity,
                "price": product.price,
                "stock_value": round_money(product.quantity * product.price),
                "last_sale_date": last_sale_date,
                "days_since_last_sale": (current_date - last_sale_date).days if last_sale_date else None
            }
            for product, last_sale_date in rows
        ]
    }


def list_movements(business_id: str, product_id: int | None, reason: MovementReason | None,
                   from_date: date | None, to_date: date | None, limit: int) -> dict:
    from_date, to_date = report_range(from_date, to_date)

    conditions = [*movements_in_range(business_id, from_date, to_date)]
    if product_id is not None:
        conditions.append(StockMovement.product_id == product_id)
    if reason is not None:
        conditions.append(StockMovement.reason == reason)

    with db.new_session() as session:
        rows = session.exec(
            select(StockMovement, Product.name, Product.unit)
            .join(Product, Product.id == StockMovement.product_id)
            .where(*conditions)
            .order_by(StockMovement.created_at.desc(), StockMovement.id.desc())
            .limit(limit)
        ).all()

    return {
        "from_date": from_date,
        "to_date": to_date,
        "count": len(rows),
        "movements": [
            {
                "id": m.id,
                "created_at": m.created_at,  # aware UTC, so it is sent with +00:00
                "product_id": m.product_id,
                "product_name": name,
                "unit": unit.value,
                "batch_id": m.batch_id,
                "quantity_change": m.quantity_change,
                "reason": m.reason.value,
                "product_quantity_after": m.product_quantity_after,
                "sale_id": m.sale_id,
                "po_id": m.po_id,
                "user_id": m.user_id,
                "note": m.note
            }
            for m, name, unit in rows
        ]
    }


def waste_report(business_id: str, from_date: date | None, to_date: date | None) -> dict:
    from_date, to_date = report_range(from_date, to_date)
    disposals = (*movements_in_range(business_id, from_date, to_date),
                 StockMovement.reason == MovementReason.EXPIRY_DISPOSAL)
    disposed = -func.sum(StockMovement.quantity_change)  # one product, one unit: never sums across units

    with db.new_session() as session:
        total_entries = session.exec(select(func.count(StockMovement.id)).where(*disposals)).one()
        rows = session.exec(
            select(Product.id, Product.name, Product.unit, disposed, func.count(StockMovement.id))
            .join(Product, Product.id == StockMovement.product_id)
            .where(*disposals)
            .group_by(Product.id, Product.name, Product.unit)
            .order_by(Product.name, Product.id)
        ).all()

    return {
        "from_date": from_date,
        "to_date": to_date,
        "total_entries": total_entries,
        "products": [
            {
                "product_id": product_id,
                "name": name,
                "unit": unit.value,
                "quantity_disposed": Decimal(str(quantity)).quantize(Decimal("0.001")),
                "entries": entries
            }
            for product_id, name, unit, quantity, entries in rows
        ]
    }
