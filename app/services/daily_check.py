"""The daily check: dispose of expired stock. Used by POST /system/daily-check and scripts/run_daily_check.py."""
from decimal import Decimal

from sqlmodel import Session, select

from app import db, timeutils
from app.models import MovementReason, Product, ProductBatch
from app.services.common import format_qty
from app.services.ledger import record_movements
from app.services.stock import lock_batches_fifo


def run_daily_check(session: Session, business_id: str, user_id: int | None = None, note: str | None = None) -> dict:
    """Disposes of one business's expired stock inside the caller's transaction; the caller commits.
    Returns {"expired_batches_cleared", "products", "inconsistencies"}. Running it again the same day changes
    nothing: cleared batches hold 0, and inconsistent products are only reported, never changed."""
    # Same rule as checkout: a batch is expired from its expiry_date onward; NULL never expires
    current_date = timeutils.today()
    expired_conditions = (
        ProductBatch.business_id == business_id,
        ProductBatch.expiry_date <= current_date,
        ProductBatch.quantity > 0
    )

    # 1. Which products have expired stock left? (read only; rechecked under lock below)
    product_ids = session.exec(
        select(ProductBatch.product_id).where(*expired_conditions).distinct()
    ).all()

    # 2. Lock those products in id order, then their expired batches, so this can't deadlock with checkout
    products = session.exec(
        select(Product).where(Product.id.in_(product_ids), Product.business_id == business_id)
        .order_by(Product.id).with_for_update()
    ).all()
    batches = lock_batches_fifo(session, ProductBatch.product_id.in_(product_ids), *expired_conditions)

    removed_per_product = []
    inconsistencies = []
    batches_cleared = 0

    # 3. Dispose of each product's expired batches
    for product in products:
        expired = [b for b in batches if b.product_id == product.id]
        if not expired:
            continue  # sold or cleared between the read and the lock
        expired_qty = sum((b.quantity for b in expired), Decimal("0"))

        # Recorded stock can't cover what's in its own expired batches: change nothing and report it
        if expired_qty > product.quantity:
            inconsistencies.append({
                "product_id": product.id,
                "product_name": product.name,
                "unit": product.unit.value,
                "stock": product.quantity,
                "expired_in_batches": expired_qty,
                "message": (
                    f"{product.name}: recorded stock is {format_qty(product.quantity, product.unit)} but "
                    f"{format_qty(expired_qty, product.unit)} is in expired batches. Nothing was changed; "
                    "run a manual audit to correct it."
                )
            })
            continue

        disposals = [(batch.id, -batch.quantity) for batch in expired]  # captured before zeroing
        for batch in expired:
            batch.quantity = Decimal("0")
            session.add(batch)
        product.quantity -= expired_qty
        session.add(product)
        record_movements(session, product, MovementReason.EXPIRY_DISPOSAL, disposals, user_id=user_id, note=note)

        batches_cleared += len(expired)
        removed_per_product.append({
            "product_id": product.id,
            "product_name": product.name,
            "removed": expired_qty,
            "unit": product.unit.value,
            "removed_display": format_qty(expired_qty, product.unit),
            "batches_cleared": [b.id for b in expired],
            "stock_remaining": product.quantity
        })

    return {
        "expired_batches_cleared": batches_cleared,
        "products": removed_per_product,
        "inconsistencies": inconsistencies
    }


def run_for_business(business_id: str, user_id: int | None) -> dict:
    """Runs the daily check for one business in its own transaction and commits it."""
    with db.new_session() as session:
        result = run_daily_check(session, business_id, user_id=user_id)
        # Save all changes to the database
        session.commit()
    return result
