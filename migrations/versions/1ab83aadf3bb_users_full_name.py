"""users full_name

Stores the full name given when an employee is added (POST /employees/). Nullable: owners created by
onboarding and users added before this change have none. Adding a nullable column is instant in Postgres.

Revision ID: 1ab83aadf3bb
Revises: 20ef20ef2e0a
Create Date: 2026-10-09 09:08:42.437265

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel  # autogenerate renders SQLModel string columns as sqlmodel.sql.sqltypes.AutoString()


# revision identifiers, used by Alembic.
revision: str = '1ab83aadf3bb'
down_revision: Union[str, Sequence[str], None] = '20ef20ef2e0a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('users', sa.Column('full_name', sqlmodel.sql.sqltypes.AutoString(length=100), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('users', 'full_name')
