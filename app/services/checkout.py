"""Selling stock (FIFO across batches) and the sales history."""
from decimal import Decimal

from sqlmodel import func, select

from app import db, timeutils
from app.errors import BadRequest, InvalidInput, NotFound, Unauthorized
from app.models import MovementReason, Product, ProductBatch, Sale, SaleBatchAllocation, StockUnit, User
from app.schemas import CheckoutRequest
from app.services.common import format_qty, require_active, round_money
from app.services.ledger import record_movements
from app.services.stock import lock_batches_fifo


def checkout(request: CheckoutRequest, business_id: str, user_id: int) -> dict:
    """Sells from the product's sellable batches in FIFO order, in one transaction with the ledger."""
    with db.new_session() as session:

        # 1. The Bulletproof Primary Key Lookup
        # session.get() is the safest way to find by ID
        user = session.get(User, user_id)

        if not user:
            raise Unauthorized(f"User ID {user_id} not found.")

        # 2. Find and lock the product
        # Lock order is always product first, then its batches, so concurrent checkouts can't deadlock

        statement = select(Product).where(
            Product.id == request.product_id,
            Product.business_id == business_id
        ).with_for_update()
        product = session.exec(statement).first()

        # 3. Validation: Does it exist?
        if not product:
            raise NotFound("Product not found in your inventory.")
        require_active(product)

        # 4. Validation: piece products are sold in whole units only
        if product.unit == StockUnit.PIECE and request.quantity != request.quantity.to_integral_value():
            raise InvalidInput(f"{product.name} is sold by the piece, so quantity must be a whole number.")

        # 5. Lock the product's batches that still hold stock, in FIFO order
        batches = lock_batches_fifo(
            session,
            ProductBatch.product_id == product.id,
            ProductBatch.business_id == business_id,
            ProductBatch.quantity > 0
        )

        # Same rule as /system/daily-check: a batch is expired from its expiry_date onward; NULL never expires
        current_date = timeutils.today()
        sellable = [b for b in batches if b.expiry_date is None or b.expiry_date > current_date]
        expired = [b for b in batches if b.expiry_date is not None and b.expiry_date <= current_date]
        sellable_qty = sum((b.quantity for b in sellable), Decimal("0"))
        expired_qty = sum((b.quantity for b in expired), Decimal("0"))

        # 6. Validation: Do we have enough sellable stock?
        if sellable_qty < request.quantity:
            raise BadRequest(
                f"Not enough stock! Only {format_qty(sellable_qty, product.unit)} of {product.name} can be sold; "
                f"{format_qty(expired_qty, product.unit)} has expired and is awaiting disposal."
            )

        # 7. Create the receipt (flush to get its id for the allocations)
        new_sale = Sale(
            product_id=product.id,
            user_id=user.id,
            business_id=business_id,
            quantity=request.quantity,
            total_price=round_money(product.price * request.quantity)
        )
        session.add(new_sale)
        session.flush()

        # 8. Deduct from batches in FIFO order, recording which batches the sale drew from
        remaining = request.quantity
        allocations = []
        for batch in sellable:
            if remaining == 0:
                break
            taken = min(batch.quantity, remaining)
            batch.quantity -= taken
            remaining -= taken
            session.add(batch)
            session.add(SaleBatchAllocation(
                sale_id=new_sale.id,
                batch_id=batch.id,
                quantity=taken,
                business_id=business_id
            ))
            allocations.append({"batch_id": batch.id, "quantity": taken, "expiry_date": batch.expiry_date})

        product.quantity -= request.quantity
        session.add(product)
        record_movements(session, product, MovementReason.SALE,
                         [(a["batch_id"], -a["quantity"]) for a in allocations],
                         sale_id=new_sale.id, user_id=user.id)

        # 9. COMMIT! (stock, batches, sale, allocations and ledger together)
        session.commit()
        session.refresh(product)
        session.refresh(new_sale)

        return {
            "success": True,
            "message": f"Successfully sold {format_qty(request.quantity, product.unit)} of {product.name}",
            "revenue": new_sale.total_price,
            "stock_remaining": product.quantity,
            "sale_id": new_sale.id,
            "allocations": allocations
        }


def sales_history(business_id: str, user_id: int, own_sales_only: bool) -> dict:
    """All of the business's sales, or only user_id's when own_sales_only, with quantities per unit."""
    with db.new_session() as session:
        # The Logic Split: Owner vs Staff
        conditions = [Sale.business_id == business_id]
        if own_sales_only:
            # The Staff only sees the sales attached to their specific user_id
            conditions.append(Sale.user_id == user_id)

        sales = session.exec(select(Sale).where(*conditions)).all()

        # Quantities sold per unit: kg and pieces are never added together.
        # Left join, so a sale whose product no longer exists is reported as "unknown" rather than dropped.
        per_unit = session.exec(
            select(Product.unit, func.sum(Sale.quantity))
            .select_from(Sale)
            .outerjoin(Product, Product.id == Sale.product_id)
            .where(*conditions)
            .group_by(Product.unit)
        ).all()
        sold = {unit: Decimal(str(quantity)).quantize(Decimal("0.001")) for unit, quantity in per_unit}
        items_sold_by_unit = {unit.value: sold[unit] for unit in StockUnit if unit in sold}
        if None in sold:
            items_sold_by_unit["unknown"] = sold[None]

        # Calculate quick analytics for the response
        total_revenue = sum(sale.total_price for sale in sales)

        return {
            "total_records": len(sales),
            "total_revenue": total_revenue,
            "items_sold_by_unit": items_sold_by_unit,
            "sales_data": sales
        }
