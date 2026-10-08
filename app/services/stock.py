"""Locking stock rows. Every path that changes stock locks the product row(s) first, then their batches."""
from sqlmodel import Session, select

from app.models import ProductBatch


def lock_batches_fifo(session: Session, *conditions) -> list[ProductBatch]:
    """Locks the matching batches (SELECT ... FOR UPDATE) in FIFO order: expiry_date ascending with NULL last,
    then received_date, then id. Callers lock the product row(s) first, like checkout, so the lock order is
    always product -> batches and these paths can't deadlock with each other."""
    return session.exec(
        select(ProductBatch).where(*conditions).order_by(
            ProductBatch.expiry_date.asc().nulls_last(),
            ProductBatch.received_date,
            ProductBatch.id
        ).with_for_update()
    ).all()
