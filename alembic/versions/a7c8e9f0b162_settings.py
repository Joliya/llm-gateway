"""settings: console-wide key/value settings store

Revision ID: a7c8e9f0b162
Revises: f6b7d8e95a71
Create Date: 2026-06-24 10:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'a7c8e9f0b162'
down_revision = 'f6b7d8e95a71'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'settings',
        sa.Column('key', sa.String(length=100), nullable=False),
        sa.Column('value', sa.JSON(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('key'),
    )


def downgrade() -> None:
    op.drop_table('settings')
