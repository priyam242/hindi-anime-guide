"""update action category display name

Revision ID: e3f4a5b6c7d8
Revises: 0f1a2b3c4d5e
Create Date: 2026-10-20 22:30:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'e3f4a5b6c7d8'
down_revision: Union[str, None] = '1a2b3c4d5e6f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()
    if 'category' in tables or 'categories' in tables:
        target_table = 'category' if 'category' in tables else 'categories'
        op.execute(f"UPDATE {target_table} SET display_name = 'एक्शन' WHERE name = 'action'")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()
    if 'category' in tables or 'categories' in tables:
        target_table = 'category' if 'category' in tables else 'categories'
        op.execute(f"UPDATE {target_table} SET display_name = 'Action' WHERE name = 'action'")
