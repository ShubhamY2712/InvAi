"""Scheduled daily check: disposes of expired stock for every business.

Each business runs in its own transaction, so a failure in one doesn't stop or undo the others. Ledger entries
are written with no user and the note "scheduled daily check". Running it again the same day changes nothing.

Usage (from the project root, e.g. from cron or Task Scheduler):  python scripts/run_daily_check.py
Exit code: 0 if every business succeeded, 1 if any failed.
"""
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCHEDULED_NOTE = "scheduled daily check"

log = logging.getLogger("daily_check")


def _count(n: int, singular: str, plural: str) -> str:
    return f"{n} {singular if n == 1 else plural}"


def run_all(engine=None) -> int:
    """Runs the daily check for every business. Returns the process exit code."""
    import main
    from sqlmodel import Session, select

    engine = engine or main.engine
    try:
        with Session(engine) as session:
            business_ids = session.exec(select(main.BusinessProfile.id).order_by(main.BusinessProfile.id)).all()
    except Exception as exc:
        log.error("could not list businesses: %s: %s", type(exc).__name__, exc)
        return 1

    ok = failed = batches = products = inconsistencies = 0
    for business_id in business_ids:
        with Session(engine) as session:  # one transaction per business
            try:
                result = main.run_daily_check(session, business_id, user_id=None, note=SCHEDULED_NOTE)
                session.commit()
            except Exception as exc:
                session.rollback()
                failed += 1
                log.error("business %s: FAILED (%s: %s)", business_id, type(exc).__name__, exc)
                continue

        ok += 1
        batches += result["expired_batches_cleared"]
        products += len(result["products"])
        inconsistencies += len(result["inconsistencies"])
        line = (f"business {business_id}: {_count(result['expired_batches_cleared'], 'batch', 'batches')} cleared, "
                f"{_count(len(result['products']), 'product', 'products')} affected, "
                f"{_count(len(result['inconsistencies']), 'inconsistency', 'inconsistencies')}")
        if result["inconsistencies"]:
            line += " (" + ", ".join(i["product_name"] for i in result["inconsistencies"]) + ")"
            log.warning(line)
        else:
            log.info(line)

    log.info("daily check finished: %s, %d ok, %d failed; %s cleared, %s affected, %s",
             _count(len(business_ids), "business", "businesses"), ok, failed,
             _count(batches, "batch", "batches"), _count(products, "product", "products"),
             _count(inconsistencies, "inconsistency", "inconsistencies"))
    return 1 if failed else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    load_dotenv(PROJECT_ROOT / ".env")
    sys.path.insert(0, str(PROJECT_ROOT))
    sys.exit(run_all())
