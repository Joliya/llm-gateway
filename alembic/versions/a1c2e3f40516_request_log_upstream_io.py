"""request_log upstream io capture

Revision ID: a1c2e3f40516
Revises: bfb1660a6300
Create Date: 2026-06-16 15:40:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'a1c2e3f40516'
down_revision = 'bfb1660a6300'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('request_logs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('upstream_url', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('upstream_request', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('upstream_response', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('request_logs', schema=None) as batch_op:
        batch_op.drop_column('upstream_response')
        batch_op.drop_column('upstream_request')
        batch_op.drop_column('upstream_url')
