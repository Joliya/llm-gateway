"""deployments: explicit thinking-translation dialect override

Revision ID: f6b7d8e95a71
Revises: e5a6c7d84960
Create Date: 2026-06-23 10:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'f6b7d8e95a71'
down_revision = 'e5a6c7d84960'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('deployments', schema=None) as batch_op:
        batch_op.add_column(sa.Column('dialect', sa.String(length=50), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('deployments', schema=None) as batch_op:
        batch_op.drop_column('dialect')
