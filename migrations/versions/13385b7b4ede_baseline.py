"""baseline

The full schema as of the switch to Alembic, generated from the models against an empty database and
reviewed by hand. Databases that were built with create_all or the hand-written SQL in migrations/legacy/
are adopted with `alembic stamp 13385b7b4ede` instead of running this (see docs/DATABASE.md).

Revision ID: 13385b7b4ede
Revises: 
Create Date: 2026-10-08 19:12:41.546465

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel  # autogenerate renders SQLModel string columns as sqlmodel.sql.sqltypes.AutoString()


# revision identifiers, used by Alembic.
revision: str = '13385b7b4ede'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('businessprofile',
    sa.Column('id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('business_name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('category', sa.Enum('RETAIL', 'HEALTHCARE', 'FNB', 'FASHION', 'TECH', 'INDUSTRIAL', 'SERVICES', name='businesscategory'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('businessprofile_pkey'))
    )
    op.create_table('product',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('sku', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('description', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('price', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('quantity', sa.Numeric(precision=12, scale=3), nullable=False),
    sa.Column('unit', sa.Enum('PIECE', 'KG', 'G', 'LITRE', 'ML', name='stockunit'), nullable=False),
    sa.Column('business_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('min_stock_level', sa.Numeric(precision=12, scale=3), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['business_id'], ['businessprofile.id'], name=op.f('product_business_id_fkey')),
    sa.PrimaryKeyConstraint('id', name=op.f('product_pkey'))
    )
    op.create_index(op.f('ix_product_business_id'), 'product', ['business_id'], unique=False)
    op.create_index(op.f('ix_product_name'), 'product', ['name'], unique=False)
    op.create_index(op.f('ix_product_sku'), 'product', ['sku'], unique=False)
    op.create_table('purchase_order',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('supplier_id', sa.Integer(), nullable=False),
    sa.Column('product_id', sa.Integer(), nullable=False),
    sa.Column('business_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('quantity', sa.Numeric(precision=12, scale=3), nullable=False),
    sa.Column('unit_cost', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('total_cost', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('status', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('expected_delivery_date', sa.Date(), nullable=True),
    sa.Column('received_quantity', sa.Numeric(precision=12, scale=3), nullable=True),
    sa.Column('rejected_quantity', sa.Numeric(precision=12, scale=3), nullable=True),
    sa.Column('timestamp', sa.DateTime(), nullable=False),
    sa.Column('delivered_at', sa.DateTime(), nullable=True),
    sa.Column('stocked_at', sa.DateTime(), nullable=True),
    sa.Column('cancelled_at', sa.DateTime(), nullable=True),
    sa.Column('cancellation_reason', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.ForeignKeyConstraint(['business_id'], ['businessprofile.id'], name=op.f('purchase_order_business_id_fkey')),
    sa.PrimaryKeyConstraint('id', name=op.f('purchase_order_pkey'))
    )
    op.create_index(op.f('ix_purchase_order_business_id'), 'purchase_order', ['business_id'], unique=False)
    op.create_index(op.f('ix_purchase_order_product_id'), 'purchase_order', ['product_id'], unique=False)
    op.create_index(op.f('ix_purchase_order_supplier_id'), 'purchase_order', ['supplier_id'], unique=False)
    op.create_table('sales',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('product_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('business_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('quantity', sa.Numeric(precision=12, scale=3), nullable=False),
    sa.Column('total_price', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('timestamp', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['business_id'], ['businessprofile.id'], name=op.f('sales_business_id_fkey')),
    sa.PrimaryKeyConstraint('id', name=op.f('sales_pkey'))
    )
    op.create_index('ix_sales_business_id_timestamp', 'sales', ['business_id', 'timestamp'], unique=False)
    op.create_index(op.f('ix_sales_product_id'), 'sales', ['product_id'], unique=False)
    op.create_index(op.f('ix_sales_user_id'), 'sales', ['user_id'], unique=False)
    op.create_table('suppliers',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('contact_email', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('phone', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('business_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.ForeignKeyConstraint(['business_id'], ['businessprofile.id'], name=op.f('suppliers_business_id_fkey')),
    sa.PrimaryKeyConstraint('id', name=op.f('suppliers_pkey'))
    )
    op.create_index(op.f('ix_suppliers_business_id'), 'suppliers', ['business_id'], unique=False)
    op.create_table('users',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('username', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('email', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('hashed_password', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('role', sa.Enum('OWNER', 'STAFF', 'MANAGER', name='userrole'), nullable=False),
    sa.Column('business_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.ForeignKeyConstraint(['business_id'], ['businessprofile.id'], name=op.f('users_business_id_fkey')),
    sa.PrimaryKeyConstraint('id', name=op.f('users_pkey'))
    )
    op.create_index(op.f('ix_users_business_id'), 'users', ['business_id'], unique=False)
    op.create_index(op.f('ix_users_email'), 'users', ['email'], unique=True)
    op.create_index(op.f('ix_users_username'), 'users', ['username'], unique=True)
    op.create_table('product_batch',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('product_id', sa.Integer(), nullable=False),
    sa.Column('po_id', sa.Integer(), nullable=True),
    sa.Column('business_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('quantity', sa.Numeric(precision=12, scale=3), nullable=False),
    sa.Column('received_date', sa.Date(), nullable=False),
    sa.Column('expiry_date', sa.Date(), nullable=True),
    sa.ForeignKeyConstraint(['business_id'], ['businessprofile.id'], name=op.f('product_batch_business_id_fkey')),
    sa.ForeignKeyConstraint(['po_id'], ['purchase_order.id'], name=op.f('product_batch_po_id_fkey')),
    sa.ForeignKeyConstraint(['product_id'], ['product.id'], name=op.f('product_batch_product_id_fkey')),
    sa.PrimaryKeyConstraint('id', name=op.f('product_batch_pkey'))
    )
    op.create_index(op.f('ix_product_batch_business_id'), 'product_batch', ['business_id'], unique=False)
    op.create_index(op.f('ix_product_batch_product_id'), 'product_batch', ['product_id'], unique=False)
    op.create_table('sale_batch_allocation',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('sale_id', sa.Integer(), nullable=False),
    sa.Column('batch_id', sa.Integer(), nullable=False),
    sa.Column('quantity', sa.Numeric(precision=12, scale=3), nullable=False),
    sa.Column('business_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.ForeignKeyConstraint(['batch_id'], ['product_batch.id'], name=op.f('sale_batch_allocation_batch_id_fkey')),
    sa.ForeignKeyConstraint(['business_id'], ['businessprofile.id'], name=op.f('sale_batch_allocation_business_id_fkey')),
    sa.ForeignKeyConstraint(['sale_id'], ['sales.id'], name=op.f('sale_batch_allocation_sale_id_fkey')),
    sa.PrimaryKeyConstraint('id', name=op.f('sale_batch_allocation_pkey'))
    )
    op.create_index(op.f('ix_sale_batch_allocation_batch_id'), 'sale_batch_allocation', ['batch_id'], unique=False)
    op.create_index(op.f('ix_sale_batch_allocation_business_id'), 'sale_batch_allocation', ['business_id'], unique=False)
    op.create_index(op.f('ix_sale_batch_allocation_sale_id'), 'sale_batch_allocation', ['sale_id'], unique=False)
    op.create_table('stock_movement',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('business_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('product_id', sa.Integer(), nullable=False),
    sa.Column('batch_id', sa.Integer(), nullable=False),
    sa.Column('quantity_change', sa.Numeric(precision=12, scale=3), nullable=False),
    sa.Column('reason', sa.Enum('OPENING', 'PURCHASE_RECEIPT', 'SALE', 'AUDIT_INCREASE', 'AUDIT_DECREASE', 'EXPIRY_DISPOSAL', name='movementreason'), nullable=False),
    sa.Column('sale_id', sa.Integer(), nullable=True),
    sa.Column('po_id', sa.Integer(), nullable=True),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('note', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('product_quantity_after', sa.Numeric(precision=12, scale=3), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['batch_id'], ['product_batch.id'], name=op.f('stock_movement_batch_id_fkey')),
    sa.ForeignKeyConstraint(['business_id'], ['businessprofile.id'], name=op.f('stock_movement_business_id_fkey')),
    sa.ForeignKeyConstraint(['po_id'], ['purchase_order.id'], name=op.f('stock_movement_po_id_fkey')),
    sa.ForeignKeyConstraint(['product_id'], ['product.id'], name=op.f('stock_movement_product_id_fkey')),
    sa.ForeignKeyConstraint(['sale_id'], ['sales.id'], name=op.f('stock_movement_sale_id_fkey')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('stock_movement_user_id_fkey')),
    sa.PrimaryKeyConstraint('id', name=op.f('stock_movement_pkey'))
    )
    op.create_index('ix_stock_movement_business_product_created', 'stock_movement', ['business_id', 'product_id', 'created_at'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_stock_movement_business_product_created', table_name='stock_movement')
    op.drop_table('stock_movement')
    op.drop_index(op.f('ix_sale_batch_allocation_sale_id'), table_name='sale_batch_allocation')
    op.drop_index(op.f('ix_sale_batch_allocation_business_id'), table_name='sale_batch_allocation')
    op.drop_index(op.f('ix_sale_batch_allocation_batch_id'), table_name='sale_batch_allocation')
    op.drop_table('sale_batch_allocation')
    op.drop_index(op.f('ix_product_batch_product_id'), table_name='product_batch')
    op.drop_index(op.f('ix_product_batch_business_id'), table_name='product_batch')
    op.drop_table('product_batch')
    op.drop_index(op.f('ix_users_username'), table_name='users')
    op.drop_index(op.f('ix_users_email'), table_name='users')
    op.drop_index(op.f('ix_users_business_id'), table_name='users')
    op.drop_table('users')
    op.drop_index(op.f('ix_suppliers_business_id'), table_name='suppliers')
    op.drop_table('suppliers')
    op.drop_index(op.f('ix_sales_user_id'), table_name='sales')
    op.drop_index(op.f('ix_sales_product_id'), table_name='sales')
    op.drop_index('ix_sales_business_id_timestamp', table_name='sales')
    op.drop_table('sales')
    op.drop_index(op.f('ix_purchase_order_supplier_id'), table_name='purchase_order')
    op.drop_index(op.f('ix_purchase_order_product_id'), table_name='purchase_order')
    op.drop_index(op.f('ix_purchase_order_business_id'), table_name='purchase_order')
    op.drop_table('purchase_order')
    op.drop_index(op.f('ix_product_sku'), table_name='product')
    op.drop_index(op.f('ix_product_name'), table_name='product')
    op.drop_index(op.f('ix_product_business_id'), table_name='product')
    op.drop_table('product')
    op.drop_table('businessprofile')
    # Postgres enum types outlive their tables; autogenerate doesn't drop them
    for enum_name in ('movementreason', 'userrole', 'stockunit', 'businesscategory'):
        sa.Enum(name=enum_name).drop(op.get_bind(), checkfirst=True)
