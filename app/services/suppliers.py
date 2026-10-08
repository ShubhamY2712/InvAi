"""Suppliers."""
from app import db
from app.models import Supplier
from app.schemas import SupplierCreate


def add_supplier(supplier: SupplierCreate, business_id: str) -> dict:
    with db.new_session() as session:

        # Create the new supplier in the database
        new_supplier = Supplier(
            name=supplier.name,
            contact_email=supplier.contact_email,
            phone=supplier.phone,
            business_id=business_id
        )

        session.add(new_supplier)
        session.commit()
        session.refresh(new_supplier)

        return {
            "success": True,
            "message": f"Successfully added vendor: {new_supplier.name}",
            "supplier_id": new_supplier.id
        }
