"""integrity: foreign keys and append-only ledger

1. Four missing foreign keys: sales.product_id, sales.user_id, purchase_order.supplier_id and
   purchase_order.product_id. Before adding any of them, every one is checked for rows that point at
   nothing; if there are any, the migration stops with a list of them and changes nothing.
2. stock_movement becomes append-only in the database: triggers reject UPDATE and DELETE (per row) and
   TRUNCATE (per statement). INSERT is unaffected. Autogenerate doesn't see triggers or functions, so
   they live only here.

Revision ID: 20ef20ef2e0a
Revises: 7086dfa92b45
Create Date: 2026-10-08 20:13:57.937713

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel  # autogenerate renders SQLModel string columns as sqlmodel.sql.sqltypes.AutoString()


# revision identifiers, used by Alembic.
revision: str = '20ef20ef2e0a'
down_revision: Union[str, Sequence[str], None] = '7086dfa92b45'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (constraint name, table, column, referenced table)
NEW_FOREIGN_KEYS = [
    ('sales_product_id_fkey', 'sales', 'product_id', 'product'),
    ('sales_user_id_fkey', 'sales', 'user_id', 'users'),
    ('purchase_order_supplier_id_fkey', 'purchase_order', 'supplier_id', 'suppliers'),
    ('purchase_order_product_id_fkey', 'purchase_order', 'product_id', 'product'),
]


def upgrade() -> None:
    """Upgrade schema."""
    # 1. Check every new foreign key for orphans first, so the migration never stops halfway
    bind = op.get_bind()
    orphans = []
    for _, table, column, referenced in NEW_FOREIGN_KEYS:
        count = bind.execute(sa.text(
            f'SELECT count(*) FROM {table} t WHERE t.{column} IS NOT NULL '
            f'AND NOT EXISTS (SELECT 1 FROM {referenced} r WHERE r.id = t.{column})'
        )).scalar()
        if count:
            orphans.append(f'{table}.{column}: {count} row(s) point at no {referenced}.id')
    if orphans:
        raise RuntimeError(
            'Cannot add foreign keys; nothing was changed. Repoint or remove these rows first: '
            + '; '.join(orphans)
        )

    # 2. The foreign keys
    for name, table, column, referenced in NEW_FOREIGN_KEYS:
        op.create_foreign_key(op.f(name), table, referenced, [column], ['id'])

    # 3. Append-only ledger
    op.execute("""
        CREATE OR REPLACE FUNCTION stock_movement_append_only() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'stock_movement is an append-only ledger: % is not allowed. Record a correcting entry instead.', TG_OP
                USING ERRCODE = 'restrict_violation';
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER stock_movement_no_update_or_delete
        BEFORE UPDATE OR DELETE ON stock_movement
        FOR EACH ROW EXECUTE FUNCTION stock_movement_append_only()
    """)
    op.execute("""
        CREATE TRIGGER stock_movement_no_truncate
        BEFORE TRUNCATE ON stock_movement
        FOR EACH STATEMENT EXECUTE FUNCTION stock_movement_append_only()
    """)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute('DROP TRIGGER IF EXISTS stock_movement_no_truncate ON stock_movement')
    op.execute('DROP TRIGGER IF EXISTS stock_movement_no_update_or_delete ON stock_movement')
    op.execute('DROP FUNCTION IF EXISTS stock_movement_append_only()')
    for name, table, _, _ in reversed(NEW_FOREIGN_KEYS):
        op.drop_constraint(op.f(name), table, type_='foreignkey')
