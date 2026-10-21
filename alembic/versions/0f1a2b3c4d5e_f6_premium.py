"""f6_premium

Revision ID: 0f1a2b3c4d5e
Revises: 9d0e1f2a3b4c
Create Date: 2026-10-20 00:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector

revision: str = '0f1a2b3c4d5e'
down_revision: Union[str, None] = '9d0e1f2a3b4c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = Inspector.from_engine(bind)
    existing_tables = inspector.get_table_names()

    # Add bio and avatar_url to admin1 if not already present
    if 'admin1' in existing_tables:
        admin_cols = {c['name'] for c in inspector.get_columns('admin1')}
        with op.batch_alter_table('admin1', schema=None) as batch_op:
            if 'bio' not in admin_cols:
                batch_op.add_column(sa.Column('bio', sa.String(length=500), nullable=False, server_default=''))
            if 'avatar_url' not in admin_cols:
                batch_op.add_column(sa.Column('avatar_url', sa.String(length=500), nullable=True))

    # Create notifications1 table
    if 'notifications1' not in existing_tables:
        op.create_table(
            'notifications1',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('admin_id', sa.Integer(), nullable=False),
            sa.Column('title', sa.String(length=200), nullable=False),
            sa.Column('message', sa.String(length=500), nullable=False),
            sa.Column('is_read', sa.Boolean(), nullable=False, server_default='0'),
            sa.Column('notification_type', sa.String(length=50), nullable=False, server_default='info'),
            sa.Column('link', sa.String(length=500), nullable=False, server_default=''),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.ForeignKeyConstraint(['admin_id'], ['admin1.id']),
        )
        op.create_index('ix_notifications1_admin_id', 'notifications1', ['admin_id'])

    # Create activity_logs table
    if 'activity_logs' not in existing_tables:
        op.create_table(
            'activity_logs',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('admin_id', sa.Integer(), nullable=True),
            sa.Column('action', sa.String(length=100), nullable=False),
            sa.Column('target_type', sa.String(length=50), nullable=False, server_default=''),
            sa.Column('target_id', sa.Integer(), nullable=True),
            sa.Column('details', sa.String(length=500), nullable=False, server_default=''),
            sa.Column('ip_address', sa.String(length=45), nullable=False, server_default=''),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.ForeignKeyConstraint(['admin_id'], ['admin1.id']),
        )
        op.create_index('ix_activity_logs_admin_id', 'activity_logs', ['admin_id'])


def downgrade() -> None:
    op.drop_index('ix_activity_logs_admin_id', table_name='activity_logs')
    op.drop_table('activity_logs')
    op.drop_index('ix_notifications1_admin_id', table_name='notifications1')
    op.drop_table('notifications1')
    with op.batch_alter_table('admin1', schema=None) as batch_op:
        batch_op.drop_column('avatar_url')
        batch_op.drop_column('bio')
