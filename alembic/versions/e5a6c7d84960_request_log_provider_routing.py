"""request_logs: record resolved provider_name + credential_id

Revision ID: e5a6c7d84960
Revises: d4f5b6c73849
Create Date: 2026-06-18 10:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'e5a6c7d84960'
down_revision = 'd4f5b6c73849'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('request_logs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('provider_name', sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column('credential_id', sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('request_logs', schema=None) as batch_op:
        batch_op.drop_column('credential_id')
        batch_op.drop_column('provider_name')
