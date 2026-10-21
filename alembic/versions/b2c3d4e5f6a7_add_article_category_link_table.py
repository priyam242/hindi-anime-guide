"""add_article_category_link_table

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-10-21
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector

revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = Inspector.from_engine(bind)
    existing_tables = inspector.get_table_names()

    if "article_category_link" not in existing_tables:
        op.create_table(
            "article_category_link",
            sa.Column("article_id", sa.Integer(), nullable=False),
            sa.Column("category_name", sa.String(length=100), nullable=False),
            sa.ForeignKeyConstraint(["article_id"], ["article.id"], ),
            sa.ForeignKeyConstraint(["category_name"], ["category.name"], ),
            sa.PrimaryKeyConstraint("article_id", "category_name")
        )
        try:
            op.execute(
                "INSERT INTO article_category_link (article_id, category_name) "
                "SELECT id, category FROM article WHERE category IS NOT NULL"
            )
        except Exception:
            pass


def downgrade() -> None:
    bind = op.get_bind()
    inspector = Inspector.from_engine(bind)
    existing_tables = inspector.get_table_names()

    if "article_category_link" in existing_tables:
        op.drop_table("article_category_link")
