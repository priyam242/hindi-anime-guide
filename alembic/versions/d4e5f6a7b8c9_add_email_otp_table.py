"""Add email_otp table for mandatory email OTP verification.

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-10-21
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector

revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = Inspector.from_engine(bind)
    existing_tables = inspector.get_table_names()

    if 'email_otp' not in existing_tables:
        op.create_table(
            'email_otp',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('email', sa.String(length=255), nullable=False),
            sa.Column('purpose', sa.String(length=20), nullable=False),
            sa.Column('code_hash', sa.String(length=255), nullable=False),
            sa.Column('expires_at', sa.DateTime(), nullable=False),
            sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('used', sa.Boolean(), nullable=False, server_default='0'),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('pending_payload', sa.String(), nullable=True),
            sa.Column('ip', sa.String(length=45), nullable=True),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_email_otp_email', 'email_otp', ['email'])
        op.create_index('ix_email_otp_expires_at', 'email_otp', ['expires_at'])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = Inspector.from_engine(bind)
    existing_tables = inspector.get_table_names()

    if 'email_otp' in existing_tables:
        op.drop_index('ix_email_otp_expires_at', table_name='email_otp')
        op.drop_index('ix_email_otp_email', table_name='email_otp')
        op.drop_table('email_otp')
