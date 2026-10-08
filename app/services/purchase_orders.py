"""Purchase orders: create, cancel, deliver and stock (receive into inventory)."""
from datetime import date
from decimal import Decimal

from sqlmodel import select

from app import db, timeutils
from app.errors import BadRequest, Conflict, InvalidInput, NotFound
from app.models import MovementReason, Product, ProductBatch, PurchaseOrder, StockUnit, Supplier
from app.schemas import PurchaseOrderCreate
from app.services.common import format_qty, require_active, require_future_expiry, round_money
from app.services.ledger import record_movements


def create_purchase_order(request: PurchaseOrderCreate, business_id: str) -> dict:
    current_date = timeutils.today()
    if request.expected_delivery_date is not None and request.expected_delivery_date <= current_date:
        raise InvalidInput(f"expected_delivery_date must be after today ({current_date}).")

    with db.new_session() as session:

        # 1. Verify the Supplier belongs to this business
        supplier = session.exec(
            select(Supplier).where(
                Supplier.id == request.supplier_id,
                Supplier.business_id == business_id
            )
        ).first()
        if not supplier:
            raise NotFound("Supplier not found.")

        # 2. Verify the Product belongs to this business
        # Locked, so a deactivation can't slip in between this check and the new PO
        product = session.exec(
            select(Product).where(
                Product.id == request.product_id,
                Product.business_id == business_id
            ).with_for_update()
        ).first()
        if not product:
            raise NotFound("Product not found in inventory.")
        require_active(product)
        if product.unit == StockUnit.PIECE and request.quantity != request.quantity.to_integral_value():
            raise InvalidInput(f"{product.name} is counted by the piece, so quantity must be a whole number.")

        # 3.  PO Receipt
        calculated_total_cost = round_money(request.quantity * request.unit_cost)

        new_po = PurchaseOrder(
            supplier_id=supplier.id,
            product_id=product.id,
            business_id=business_id,
            quantity=request.quantity,
            unit_cost=request.unit_cost,
            total_cost=calculated_total_cost,
            status="PENDING",     # <--- Explicitly mark it as waiting for delivery
            expected_delivery_date=request.expected_delivery_date
        )

        # Add ONLY the receipt to the vault (Notice we don't add the product anymore)
        session.add(new_po)

        # 4. COMMIT!
        session.commit()
        session.refresh(new_po)

        return {
            "success": True,
            "message": f"Order placed for {format_qty(request.quantity, product.unit)} of {product.name}. Awaiting delivery.",
            "current_stock_level": product.quantity,  # Unchanged!
            "expense": calculated_total_cost,
            "po_id": new_po.id,
            "status": new_po.status,
            "expected_delivery_date": new_po.expected_delivery_date
        }


def cancel_purchase_order(po_id: int, reason: str | None, business_id: str) -> dict:
    """Cancels a pending PO. Cancelling one that is already cancelled changes nothing."""
    with db.new_session() as session:
        # Locked, so it can't be delivered or stocked while it is being cancelled
        po = session.exec(
            select(PurchaseOrder).where(
                PurchaseOrder.id == po_id,
                PurchaseOrder.business_id == business_id
            ).with_for_update()
        ).first()
        if not po:
            raise NotFound("Purchase Order not found.")

        if po.status == "CANCELLED":  # keep the original time and reason
            return {"success": True, "message": f"PO #{po.id} is already cancelled.", "po_id": po.id, "status": po.status,
                    "cancelled_at": po.cancelled_at, "reason": po.cancellation_reason}
        if po.status == "DELIVERED":
            raise Conflict(f"PO #{po.id} has already been delivered and can't be cancelled. To refuse the goods, "
                           "stock it with rejected_quantity equal to received_quantity.")
        if po.status != "PENDING":
            raise Conflict(f"PO #{po.id} has already been {po.status.lower()} and can't be cancelled.")

        po.status = "CANCELLED"
        po.cancelled_at = timeutils.utc_now()
        po.cancellation_reason = reason
        session.add(po)
        session.commit()
        session.refresh(po)

        return {"success": True, "message": f"PO #{po.id} has been cancelled.", "po_id": po.id, "status": po.status,
                "cancelled_at": po.cancelled_at, "reason": po.cancellation_reason}


def deliver_purchase_order(po_id: int, business_id: str) -> dict:
    with db.new_session() as session:
        # Locked, so a concurrent cancellation and delivery can't both succeed
        po = session.exec(select(PurchaseOrder).where(PurchaseOrder.id == po_id, PurchaseOrder.business_id == business_id).with_for_update()).first()

        if not po:
            raise NotFound("Purchase Order not found.")
        if po.status == "CANCELLED":
            raise Conflict(f"PO #{po.id} was cancelled and can't be delivered.")
        if po.status != "PENDING":
            raise BadRequest(f"Cannot deliver. Order is currently {po.status}")

        po.status = "DELIVERED"
        po.delivered_at = timeutils.utc_now() # Stamps the exact moment the truck arrived (aware UTC)

        session.add(po)
        session.commit()
        session.refresh(po)

        return {
            "success": True,
            "message": "Boxes arrived at the loading dock! Supplier clock stopped. (Inventory NOT updated yet).",
            "status": po.status
        }


# --- STEP 3: The Shelf (Scan into Inventory) ---
# This tracks how fast your staff puts boxes away, and FINALLY adds the stock.
def stock_purchase_order(po_id: int, expiry_date: date | None, received_quantity: Decimal | None,
                         rejected_quantity: Decimal, business_id: str, user_id: int | None) -> dict:
    """Closes a delivered PO; only received - rejected becomes a batch, stock and a ledger entry."""
    require_future_expiry(expiry_date)

    with db.new_session() as session:
        # 1. Lock the PO (so it can't be stocked twice), then its product (so a concurrent sale isn't overwritten)
        po = session.exec(
            select(PurchaseOrder).where(
                PurchaseOrder.id == po_id,
                PurchaseOrder.business_id == business_id
            ).with_for_update()
        ).first()
        if not po:
            raise NotFound("Purchase Order not found")

        if po.status == "CANCELLED":
            raise Conflict(f"PO #{po.id} was cancelled and can't be stocked.")
        if po.status != "DELIVERED":
            raise BadRequest("PO must be DELIVERED before it can be STOCKED")

        product = session.exec(select(Product).where(Product.id == po.product_id).with_for_update()).first()
        require_active(product)

        # 2. What arrived, and how much of it is accepted
        received = po.quantity if received_quantity is None else received_quantity
        if rejected_quantity > received:
            raise InvalidInput("rejected_quantity can't be more than received_quantity.")
        if product.unit == StockUnit.PIECE and any(q != q.to_integral_value() for q in (Decimal(str(received)), rejected_quantity)):
            raise InvalidInput(
                f"{product.name} is counted by the piece, so received_quantity and rejected_quantity must be whole numbers."
            )
        accepted = received - rejected_quantity

        # 3. Close the PO, even if the delivery was short or fully rejected
        po.status = "STOCKED"
        po.stocked_at = timeutils.utc_now()
        po.received_quantity = received
        po.rejected_quantity = rejected_quantity
        session.add(po)

        # 4. Only accepted stock becomes a batch, stock, and a ledger entry
        new_batch = None
        if accepted > 0:
            new_batch = ProductBatch(
                product_id=po.product_id,
                po_id=po.id,
                business_id=business_id,
                quantity=accepted,
                received_date=timeutils.today(),
                expiry_date=expiry_date  # None: never expires, like opening and audit batches
            )
            session.add(new_batch)
            session.flush()  # assigns new_batch.id for the ledger
            product.quantity += accepted
            session.add(product)
            record_movements(session, product, MovementReason.PURCHASE_RECEIPT, [(new_batch.id, accepted)],
                             po_id=po.id, user_id=user_id)

        session.commit()

        counts = f"{format_qty(received, product.unit)} received, {format_qty(rejected_quantity, product.unit)} rejected"
        return {
            "message": (f"PO Stocked. {format_qty(accepted, product.unit)} added to main inventory ({counts})."
                        if new_batch else f"PO closed. Nothing added to inventory ({counts})."),
            "received_quantity": received,
            "rejected_quantity": rejected_quantity,
            "accepted_quantity": accepted,
            "batch_id": new_batch.id if new_batch else None,
            "batch_expiry": new_batch.expiry_date if new_batch else None
        }
