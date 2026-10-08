"""Rules and helpers shared by several services."""
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from app import timeutils
from app.errors import Conflict, InvalidInput
from app.models import Product, StockUnit


def format_qty(quantity, unit: StockUnit | None = None) -> str:
    """Formats a quantity for messages: no trailing zeros, plus the unit (e.g. "1.5 kg")."""
    text = format(Decimal(str(quantity)).normalize(), "f")
    return f"{text} {unit.value}" if unit else text


def round_money(value) -> Decimal:
    """Rounds to 2 decimal places, half-up (0.125 -> 0.13)."""
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def require_future_expiry(expiry_date: date | None) -> None:
    """Raises 422 if expiry_date is today or earlier: checkout already treats such stock as expired."""
    current_date = timeutils.today()
    if expiry_date is not None and expiry_date <= current_date:
        raise InvalidInput(
            f"expiry_date must be after today ({current_date}). Stock expiring today or earlier is already expired and can't be sold."
        )


def require_active(product: Product) -> None:
    """Raises 409 if the product has been deactivated."""
    if not product.is_active:
        raise Conflict(f"{product.name} is inactive. Reactivate it first.")
