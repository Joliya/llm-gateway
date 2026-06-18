"""request_id on request_logs + admin_audit_logs table

Revision ID: c3e4a5b62738
Revises: b2d3f4a51627
Create Date: 2026-06-17 10:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'c3e4a5b62738'
down_revision = 'b2d3f4a51627'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('request_logs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('request_id', sa.String(length=64), nullable=True))
        batch_op.create_index('ix_request_logs_request_id', ['request_id'])

    op.create_table(
        'admin_audit_logs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('ts', sa.DateTime(timezone=True), nullable=True),
        sa.Column('request_id', sa.String(length=64), nullable=True),
        sa.Column('actor', sa.String(length=150), nullable=True),
        sa.Column('method', sa.String(length=10), nullable=False),
        sa.Column('path', sa.String(length=300), nullable=False),
        sa.Column('status', sa.Integer(), nullable=False, server_default='0'),
    )
    op.create_index('ix_admin_audit_logs_ts', 'admin_audit_logs', ['ts'])


def downgrade() -> None:
    op.drop_index('ix_admin_audit_logs_ts', table_name='admin_audit_logs')
    op.drop_table('admin_audit_logs')
    with op.batch_alter_table('request_logs', schema=None) as batch_op:
        batch_op.drop_index('ix_request_logs_request_id')
        batch_op.drop_column('request_id')
