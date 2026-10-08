"""Manual stock audit: bring a product's batches in line with a physical count."""
from datetime import date
from decimal import Decimal

from sqlmodel import select

from app import db, timeutils
from app.errors import InvalidInput, NotFound
from app.models import MovementReason, Product, ProductBatch, StockUnit
from app.services.common import require_active, require_future_expiry
from app.services.ledger import record_movements
from app.services.stock import lock_batches_fifo


def manual_audit(product_id: int, new_quantity: Decimal, expiry_date: date | None, note: str | None,
                 business_id: str, user_id: int | None, authorized_by: str) -> dict:
    """Reduces batches in FIFO order (expired first) or adds an adjustment batch, then records the ledger entries."""
    require_future_expiry(expiry_date)

    with db.new_session() as session:

        # 1. Lock the product, then its batches (same order as checkout)
        product = session.exec(
            select(Product).where(
                Product.id == product_id,
                Product.business_id == business_id
            ).with_for_update()
        ).first()

        if not product:
            raise NotFound("Product not found.")
        require_active(product)

        if product.unit == StockUnit.PIECE and new_quantity != new_quantity.to_integral_value():
            raise InvalidInput(f"{product.name} is counted by the piece, so new_quantity must be a whole number.")

        batches = lock_batches_fifo(
            session,
            ProductBatch.product_id == product.id,
            ProductBatch.business_id == business_id,
            ProductBatch.quantity > 0
        )

        # 2. Bring the batches in line with the count. Measured against the batch total (not Product.quantity),
        # so an audit also repairs a product whose stock had drifted from its batches.
        old_qty = product.quantity
        batch_total = sum((b.quantity for b in batches), Decimal("0"))
        batches_reduced = []
        batch_created = None

        if new_quantity < batch_total:
            # Remove the shortfall in FIFO order; expired batches sort first, so they go first
            to_remove = batch_total - new_quantity
            for batch in batches:
                if to_remove == 0:
                    break
                taken = min(batch.quantity, to_remove)
                batch.quantity -= taken
                to_remove -= taken
                session.add(batch)
                batches_reduced.append({
                    "batch_id": batch.id,
                    "quantity_removed": taken,
                    "remaining": batch.quantity,
                    "expiry_date": batch.expiry_date
                })
        elif new_quantity > batch_total:
            # Found more than recorded: the surplus becomes its own adjustment batch (no purchase order)
            adjustment = ProductBatch(
                product_id=product.id,
                po_id=None,
                business_id=business_id,
                quantity=new_quantity - batch_total,
                received_date=timeutils.today(),
                expiry_date=expiry_date
            )
            session.add(adjustment)
            session.flush()
            batch_created = {"batch_id": adjustment.id, "quantity": adjustment.quantity, "expiry_date": adjustment.expiry_date}

        product.quantity = new_quantity
        session.add(product)
        refs = {"user_id": user_id, "note": note}
        if batches_reduced:
            record_movements(session, product, MovementReason.AUDIT_DECREASE,
                             [(b["batch_id"], -b["quantity_removed"]) for b in batches_reduced], **refs)
        if batch_created:
            record_movements(session, product, MovementReason.AUDIT_INCREASE,
                             [(batch_created["batch_id"], batch_created["quantity"])], **refs)
        session.commit()

        return {
            "success": True,
            "message": f"Manual audit completed for {product.name}",
            "previous_qty": old_qty,
            "new_qty": new_quantity,
            "unit": product.unit.value,
            "batches_reduced": batches_reduced,
            "batch_created": batch_created,
            "authorized_by": authorized_by
        }
