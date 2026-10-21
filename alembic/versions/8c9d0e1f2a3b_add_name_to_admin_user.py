"""add_name_to_admin_user

Revision ID: 8c9d0e1f2a3b
Revises: 7a8b9c0d1e2f
Create Date: 2026-10-20 02:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = '8c9d0e1f2a3b'
down_revision: Union[str, Sequence[str], None] = '7a8b9c0d1e2f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'admin_user',
        sa.Column('name', sqlmodel.sql.sqltypes.AutoString(length=100), nullable=False, server_default='Admin')
    )


def downgrade() -> None:
    op.drop_column('admin_user', 'name')
