"""Add anime_draft table for staging anime updates and drafts.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-11-13
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()

    if 'anime_draft' not in existing_tables:
        op.create_table(
            'anime_draft',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('content_type', sa.String(length=50), nullable=False),
            sa.Column('status', sa.String(length=20), nullable=False),
            sa.Column('source_name', sa.String(length=100), nullable=False),
            sa.Column('source_url', sa.String(length=500), nullable=False),
            sa.Column('external_id', sa.String(length=150), nullable=False),
            sa.Column('title_en', sa.String(length=300), nullable=False),
            sa.Column('title_hi', sa.String(length=300), nullable=True),
            sa.Column('summary_en', sa.Text(), nullable=True),
            sa.Column('summary_hi', sa.Text(), nullable=True),
            sa.Column('image_url', sa.String(length=500), nullable=True),
            sa.Column('published_at', sa.DateTime(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_anime_draft_content_type', 'anime_draft', ['content_type'], unique=False)
        op.create_index('ix_anime_draft_external_id', 'anime_draft', ['external_id'], unique=True)
        op.create_index('ix_anime_draft_status', 'anime_draft', ['status'], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()

    if 'anime_draft' in existing_tables:
        op.drop_index('ix_anime_draft_status', table_name='anime_draft')
        op.drop_index('ix_anime_draft_external_id', table_name='anime_draft')
        op.drop_index('ix_anime_draft_content_type', table_name='anime_draft')
        op.drop_table('anime_draft')

