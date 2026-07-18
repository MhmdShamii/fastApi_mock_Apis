"""create orders table

Revision ID: b7f2c9a1d4e8
Revises: 046983b94232
Create Date: 2026-07-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7f2c9a1d4e8'
down_revision: Union[str, Sequence[str], None] = '046983b94232'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('orders',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('tracking_id', sa.String(length=12), nullable=False),
    sa.Column('status', sa.Enum('PENDING', 'CONFIRMED', 'PROCESSING', 'SUCCESS', 'FAILED', 'CANCELED', name='order_status'), server_default='PENDING', nullable=False),
    sa.Column('receiver_name', sa.String(), nullable=False),
    sa.Column('receiver_phone', sa.String(), nullable=False),
    sa.Column('receiver_address', sa.String(), nullable=False),
    sa.Column('receiver_area', sa.String(), nullable=True),
    sa.Column('collection_amount', sa.Numeric(precision=10, scale=2), server_default='0', nullable=False),
    sa.Column('currency', sa.Enum('USD', 'LBP', name='order_currency'), server_default='USD', nullable=False),
    sa.Column('package_quantity', sa.Integer(), nullable=False),
    sa.Column('note', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_orders_tracking_id'), 'orders', ['tracking_id'], unique=True)
    op.create_index(op.f('ix_orders_user_id'), 'orders', ['user_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_orders_user_id'), table_name='orders')
    op.drop_index(op.f('ix_orders_tracking_id'), table_name='orders')
    op.drop_table('orders')
    # Postgres keeps the enum types after drop_table; remove them explicitly.
    sa.Enum(name='order_status').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='order_currency').drop(op.get_bind(), checkfirst=True)
