"""The stock movement ledger (append-only: entries are only ever added)."""
from decimal import Decimal

from sqlmodel import Session

from app.models import MovementReason, Product, StockMovement


def record_movements(session: Session, product: Product, reason: MovementReason, changes, **refs) -> None:
    """Appends one StockMovement per (batch_id, quantity_change) in changes, in order, to the caller's transaction.
    Call after product.quantity holds its final value: product_quantity_after runs forward from the quantity
    before these changes, so the last entry always equals Product.quantity. refs: sale_id, po_id, user_id, note."""
    running = Decimal(str(product.quantity)) - sum((Decimal(str(change)) for _, change in changes), Decimal("0"))
    for batch_id, change in changes:
        running += Decimal(str(change))
        session.add(StockMovement(
            business_id=product.business_id,
            product_id=product.id,
            batch_id=batch_id,
            quantity_change=change,
            reason=reason,
            product_quantity_after=running,
            **refs
        ))
