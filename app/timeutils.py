"""Time: the business's time zone, today's date there, UTC timestamps, and India dates in SQL.
Timestamps are timestamptz (aware UTC in Python); every report date and daily bucket is an India (Asia/Kolkata)
calendar day. Callers use timeutils.today() (not a copied reference) so tests can pin the date."""
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import Date
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.expression import FunctionElement

BUSINESS_TZ = ZoneInfo("Asia/Kolkata")


def today() -> date:
    """Current date in Asia/Kolkata, used for every expiry decision (needs tzdata on Windows)."""
    return datetime.now(BUSINESS_TZ).date()


def utc_now() -> datetime:
    """Current time as an aware UTC datetime."""
    return datetime.now(timezone.utc)


def ist_day_start_utc(day: date) -> datetime:
    """00:00 India time on day, as an aware UTC datetime (the instant timestamps are compared against)."""
    return datetime.combine(day, datetime.min.time(), tzinfo=BUSINESS_TZ).astimezone(timezone.utc)


def ist_date_of(moment: datetime) -> date:
    """India calendar date of an aware timestamp."""
    return moment.astimezone(BUSINESS_TZ).date()


class ist_date(FunctionElement):
    """SQL expression for the India calendar date of a timestamptz column."""
    type = Date()
    name = "ist_date"
    inherit_cache = True


@compiles(ist_date)
def _ist_date_postgres(element, compiler, **kw):
    # timezone(zone, timestamptz) gives the wall time in that zone, whatever the session's time zone is
    return f"CAST(timezone('{BUSINESS_TZ.key}', {compiler.process(element.clauses, **kw)}) AS DATE)"


@compiles(ist_date, "sqlite")
def _ist_date_sqlite(element, compiler, **kw):
    # SQLite stores the UTC wall time (UTCDateTime) and has no time zone database;
    # India has used a fixed +05:30 offset since 1945
    return f"date({compiler.process(element.clauses, **kw)}, '+330 minutes')"
