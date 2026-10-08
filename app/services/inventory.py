"""Stock state: a product's batches, low-stock and expiring-soon alerts."""
from datetime import timedelta

from sqlmodel import select

from app import db, timeutils
from app.errors import NotFound
from app.models import Product, ProductBatch


def expiring_soon(business_id: str, days: int) -> dict:
    """Returns this business's batches expiring after today and up to today + days, soonest first.
    A batch expiring today is already expired (same rule as checkout and the daily check)."""
    current_date = timeutils.today()
    window_end = current_date + timedelta(days=days)

    with db.new_session() as session:
        statement = (
            select(ProductBatch, Product.name)
            .join(Product, Product.id == ProductBatch.product_id)
            .where(
                ProductBatch.business_id == business_id,
                ProductBatch.expiry_date > current_date,
                ProductBatch.expiry_date <= window_end,
                ProductBatch.quantity > 0
            )
            .order_by(ProductBatch.expiry_date, ProductBatch.id)
        )
        rows = session.exec(statement).all()

        batches = [
            {
                "batch_id": batch.id,
                "product_name": product_name,
                "expiry_date": batch.expiry_date,
                "quantity": batch.quantity,
                "days_left": (batch.expiry_date - current_date).days
            }
            for batch, product_name in rows
        ]

        return {
            "success": True,
            "business_id": business_id,
            "window_end": window_end,
            "alert_count": len(batches),
            "batches": batches
        }


def product_batches(product_id: int, business_id: str) -> list[ProductBatch]:
    with db.new_session() as session:
        # 1. Verify the product exists and belongs to this business
        product = session.get(Product, product_id)
        if not product or product.business_id != business_id:
            raise NotFound("Product not found")

        # 2. Fetch all batches for this specific product
        statement = select(ProductBatch).where(
            ProductBatch.product_id == product_id,
            ProductBatch.business_id == business_id
        )
        batches = session.exec(statement).all()

        # 3. Return the data
        return batches


def low_stock(business_id: str) -> dict:
    with db.new_session() as session:
        # The AI Trigger Query
        statement = select(Product).where(
            Product.business_id == business_id,
            Product.is_active == True,  # noqa: E712 (SQL expression)
            Product.quantity <= Product.min_stock_level
        )

        low_stock_items = session.exec(statement).all()

        # We format the response to be highly readable for both the frontend UI and future AI agents
        return {
            "alert_count": len(low_stock_items),
            "items_to_reorder": low_stock_items
        }
