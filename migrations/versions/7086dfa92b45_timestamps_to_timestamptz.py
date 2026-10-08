"""timestamps to timestamptz

Converts the six timestamp columns from "timestamp without time zone" (naive values that were always UTC)
to "timestamp with time zone", keeping every value the same moment.

Each conversion says how to read the old value: `"col" AT TIME ZONE 'UTC'`.
  - upgrade, on a timestamp: "this wall time is UTC" -> the timestamptz for that moment
  - downgrade, on a timestamptz: "the wall time in UTC" -> the original naive UTC value
Without USING, Postgres casts with the session's TimeZone, so on a server set to Asia/Kolkata every value
would silently move 5h30m. With it, the result doesn't depend on the session at all.

Revision ID: 7086dfa92b45
Revises: 13385b7b4ede
Create Date: 2026-10-08 19:38:32.860791

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel  # autogenerate renders SQLModel string columns as sqlmodel.sql.sqltypes.AutoString()
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '7086dfa92b45'
down_revision: Union[str, Sequence[str], None] = '13385b7b4ede'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column('purchase_order', 'timestamp',
               existing_type=postgresql.TIMESTAMP(),
               type_=sa.DateTime(timezone=True),
               existing_nullable=False,
               postgresql_using='"timestamp" AT TIME ZONE \'UTC\'')
    op.alter_column('purchase_order', 'delivered_at',
               existing_type=postgresql.TIMESTAMP(),
               type_=sa.DateTime(timezone=True),
               existing_nullable=True,
               postgresql_using='delivered_at AT TIME ZONE \'UTC\'')
    op.alter_column('purchase_order', 'stocked_at',
               existing_type=postgresql.TIMESTAMP(),
               type_=sa.DateTime(timezone=True),
               existing_nullable=True,
               postgresql_using='stocked_at AT TIME ZONE \'UTC\'')
    op.alter_column('purchase_order', 'cancelled_at',
               existing_type=postgresql.TIMESTAMP(),
               type_=sa.DateTime(timezone=True),
               existing_nullable=True,
               postgresql_using='cancelled_at AT TIME ZONE \'UTC\'')
    op.alter_column('sales', 'timestamp',
               existing_type=postgresql.TIMESTAMP(),
               type_=sa.DateTime(timezone=True),
               existing_nullable=False,
               postgresql_using='"timestamp" AT TIME ZONE \'UTC\'')
    op.alter_column('stock_movement', 'created_at',
               existing_type=postgresql.TIMESTAMP(),
               type_=sa.DateTime(timezone=True),
               existing_nullable=False,
               postgresql_using='created_at AT TIME ZONE \'UTC\'')


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column('stock_movement', 'created_at',
               existing_type=sa.DateTime(timezone=True),
               type_=postgresql.TIMESTAMP(),
               existing_nullable=False,
               postgresql_using='created_at AT TIME ZONE \'UTC\'')
    op.alter_column('sales', 'timestamp',
               existing_type=sa.DateTime(timezone=True),
               type_=postgresql.TIMESTAMP(),
               existing_nullable=False,
               postgresql_using='"timestamp" AT TIME ZONE \'UTC\'')
    op.alter_column('purchase_order', 'cancelled_at',
               existing_type=sa.DateTime(timezone=True),
               type_=postgresql.TIMESTAMP(),
               existing_nullable=True,
               postgresql_using='cancelled_at AT TIME ZONE \'UTC\'')
    op.alter_column('purchase_order', 'stocked_at',
               existing_type=sa.DateTime(timezone=True),
               type_=postgresql.TIMESTAMP(),
               existing_nullable=True,
               postgresql_using='stocked_at AT TIME ZONE \'UTC\'')
    op.alter_column('purchase_order', 'delivered_at',
               existing_type=sa.DateTime(timezone=True),
               type_=postgresql.TIMESTAMP(),
               existing_nullable=True,
               postgresql_using='delivered_at AT TIME ZONE \'UTC\'')
    op.alter_column('purchase_order', 'timestamp',
               existing_type=sa.DateTime(timezone=True),
               type_=postgresql.TIMESTAMP(),
               existing_nullable=False,
               postgresql_using='"timestamp" AT TIME ZONE \'UTC\'')
