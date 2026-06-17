"""provider model price book

Revision ID: b2d3f4a51627
Revises: a1c2e3f40516
Create Date: 2026-06-16 16:30:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'b2d3f4a51627'
down_revision = 'a1c2e3f40516'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('providers', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('model_prices', sa.JSON(), nullable=False, server_default='{}')
        )


def downgrade() -> None:
    with op.batch_alter_table('providers', schema=None) as batch_op:
        batch_op.drop_column('model_prices')
