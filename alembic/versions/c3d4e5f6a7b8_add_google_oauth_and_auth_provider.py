"""add_google_oauth_and_auth_provider

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-10-21
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector

revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = Inspector.from_engine(bind)
    columns = [c["name"] for c in inspector.get_columns("user")]

    with op.batch_alter_table("user", schema=None) as batch_op:
        if "hashed_password" in columns:
            batch_op.alter_column("hashed_password", existing_type=sa.String(length=255), nullable=True)
        if "google_sub" not in columns:
            batch_op.add_column(sa.Column("google_sub", sa.String(length=255), nullable=True))
            batch_op.create_index("ix_user_google_sub", ["google_sub"], unique=True)
        if "email_verified" not in columns:
            batch_op.add_column(sa.Column("email_verified", sa.Boolean(), server_default=sa.text("false"), nullable=False))
        if "auth_provider" not in columns:
            batch_op.add_column(sa.Column("auth_provider", sa.String(length=50), server_default="password", nullable=False))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = Inspector.from_engine(bind)
    columns = [c["name"] for c in inspector.get_columns("user")]

    with op.batch_alter_table("user", schema=None) as batch_op:
        if "auth_provider" in columns:
            batch_op.drop_column("auth_provider")
        if "email_verified" in columns:
            batch_op.drop_column("email_verified")
        if "google_sub" in columns:
            batch_op.drop_index("ix_user_google_sub")
            batch_op.drop_column("google_sub")
        if "hashed_password" in columns:
            batch_op.alter_column("hashed_password", existing_type=sa.String(length=255), nullable=False)
