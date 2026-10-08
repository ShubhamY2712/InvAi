"""The product catalogue: create, list, edit, deactivate and reactivate."""
from sqlmodel import select

from app import db, timeutils
from app.errors import Conflict, InvalidInput, NotFound
from app.models import MovementReason, Product, ProductBatch, PurchaseOrder
from app.schemas import ProductCreate, ProductUpdate
from app.services.common import format_qty, require_future_expiry
from app.services.ledger import record_movements


def create_product(product_data: ProductCreate, business_id: str, user_id: int | None) -> dict:
    """Creates the product; any initial quantity becomes an opening batch with its ledger entry."""
    require_future_expiry(product_data.expiry_date)

    with db.new_session() as session:
        # Create the database record, combining user data with the Bouncer's secure ID
        new_product = Product(
            name=product_data.name,
            sku=product_data.sku,
            price=product_data.price,
            quantity=product_data.quantity,
            unit=product_data.unit,
            description=product_data.description,
            business_id=business_id # <-- THE MULTI-TENANT LOCK
        )

        session.add(new_product)
        session.flush()  # assigns new_product.id for the opening batch

        # Opening stock gets its own batch so batch totals always match Product.quantity
        if new_product.quantity > 0:
            opening_batch = ProductBatch(
                product_id=new_product.id,
                po_id=None,
                business_id=business_id,
                quantity=new_product.quantity,
                received_date=timeutils.today(),
                expiry_date=product_data.expiry_date
            )
            session.add(opening_batch)
            session.flush()  # assigns opening_batch.id for the ledger
            record_movements(session, new_product, MovementReason.OPENING,
                             [(opening_batch.id, new_product.quantity)], user_id=user_id)

        session.commit()
        session.refresh(new_product)

        return {
            "success": True,
            "message": f"Successfully added {new_product.name} to inventory.",
            "product": new_product
        }


def list_products(business_id: str, include_inactive: bool) -> dict:
    with db.new_session() as session:
        # The ultimate security filter: ONLY return products matching this user's business_id
        statement = select(Product).where(Product.business_id == business_id)
        if not include_inactive:
            statement = statement.where(Product.is_active == True)  # noqa: E712 (SQL expression)
        products = session.exec(statement).all()

        return {
            "success": True,
            "total_items": len(products),
            "inventory": products
        }


def update_product(product_id: int, product_update: ProductUpdate, business_id: str) -> dict:
    """Edits name, price, description or (while it has never held stock) unit. Stock can't be changed here."""
    # Stock is batch-tracked, so it only changes through stocking, checkout, or a manual audit
    if "quantity" in product_update.model_fields_set:
        raise InvalidInput("Stock can't be changed here. Use purchase-order stocking, checkout, or a manual audit.")

    with db.new_session() as session:
        # 1. The Ultimate Security Check: Find the product, but ONLY if they own it
        # Locked so no stock can arrive between the unit check below and the save
        statement = select(Product).where(
            Product.id == product_id,
            Product.business_id == business_id
        ).with_for_update()
        product = session.exec(statement).first()

        # 2. If it doesn't exist (or they don't own it), reject them
        if not product:
            raise NotFound("Product not found or access denied")

        # 3. The unit can only change while nothing has ever been measured in it
        if product_update.unit is not None and product_update.unit != product.unit:
            has_batches = session.exec(
                select(ProductBatch.id).where(ProductBatch.product_id == product.id).limit(1)
            ).first() is not None
            if product.quantity != 0 or has_batches:
                raise InvalidInput(
                    f"Can't change the unit of {product.name} from {product.unit.value} to {product_update.unit.value}: "
                    "it has stock or batch history, and those quantities would change meaning."
                )
            product.unit = product_update.unit

        # 4. Update only the fields the frontend specifically asked to change
        if product_update.name is not None:
            product.name = product_update.name
        if product_update.price is not None:
            product.price = product_update.price
        if product_update.description is not None:
            product.description = product_update.description

        # 5. Save the changes to the vault
        session.add(product)
        session.commit()
        session.refresh(product)

        return {
            "success": True,
            "message": f"Successfully updated {product.name}",
            "product": product
        }


def deactivate_product(product_id: int, business_id: str) -> dict:
    """Deactivates the product. Nothing is deleted: its batches, sales and ledger history stay."""
    with db.new_session() as session:
        # 1. Search for the product using the ID AND the Business ID (The Multi-Tenant Lock)
        # Locked, so stock or a new purchase order can't arrive between the checks below and the save
        statement = select(Product).where(
            Product.id == product_id,
            Product.business_id == business_id
        ).with_for_update()
        product = session.exec(statement).first()

        # 2. If it's not there or belongs to someone else, say it's not found
        if not product:
            raise NotFound("Product not found or access denied")

        if not product.is_active:
            return {"success": True, "message": f"{product.name} is already inactive.", "product_id": product.id, "is_active": False}

        # 3. Only an empty product with no incoming stock can be deactivated
        if product.quantity > 0:
            raise Conflict(f"{product.name} still has {format_qty(product.quantity, product.unit)} in stock. "
                           "Run a manual audit to bring it to 0 before deactivating.")
        open_pos = session.exec(
            select(PurchaseOrder.id).where(
                PurchaseOrder.product_id == product.id,
                PurchaseOrder.status.in_(["PENDING", "DELIVERED"])
            ).order_by(PurchaseOrder.id)
        ).all()
        if open_pos:
            raise Conflict(f"{product.name} has purchase orders that aren't stocked yet "
                           f"({', '.join(f'#{po_id}' for po_id in open_pos)}). Stock them before deactivating.")

        # 4. Deactivate (history stays)
        product.is_active = False
        session.add(product)
        session.commit()

        return {"success": True, "message": f"{product.name} has been deactivated.", "product_id": product.id, "is_active": False}


def reactivate_product(product_id: int, business_id: str) -> dict:
    with db.new_session() as session:
        product = session.exec(
            select(Product).where(
                Product.id == product_id,
                Product.business_id == business_id
            ).with_for_update()
        ).first()
        if not product:
            raise NotFound("Product not found or access denied")

        if product.is_active:
            return {"success": True, "message": f"{product.name} is already active.", "product_id": product.id, "is_active": True}

        product.is_active = True
        session.add(product)
        session.commit()
        return {"success": True, "message": f"{product.name} has been reactivated.", "product_id": product.id, "is_active": True}
