"""Add chapters, volumes, publication_status, airing_day, airing_time to anime_draft.

Revision ID: f1a2b3c4d5e6
Revises: e5f6a7b8c9d0
Create Date: 2026-11-13
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = [col['name'] for col in inspector.get_columns('anime_draft')]
    existing_indexes = [idx['name'] for idx in inspector.get_indexes('anime_draft')]

    if 'chapters' not in existing_columns:
        op.add_column('anime_draft', sa.Column('chapters', sa.Integer(), nullable=True))
    if 'volumes' not in existing_columns:
        op.add_column('anime_draft', sa.Column('volumes', sa.Integer(), nullable=True))
    if 'publication_status' not in existing_columns:
        op.add_column('anime_draft', sa.Column('publication_status', sa.String(length=50), nullable=True))
    if 'airing_day' not in existing_columns:
        op.add_column('anime_draft', sa.Column('airing_day', sa.String(length=20), nullable=True))
    if 'airing_time' not in existing_columns:
        op.add_column('anime_draft', sa.Column('airing_time', sa.String(length=50), nullable=True))

    if 'ix_anime_draft_status_published' not in existing_indexes:
        op.create_index('ix_anime_draft_status_published', 'anime_draft', ['status', 'published_at'])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = [col['name'] for col in inspector.get_columns('anime_draft')]
    existing_indexes = [idx['name'] for idx in inspector.get_indexes('anime_draft')]

    if 'ix_anime_draft_status_published' in existing_indexes:
        op.drop_index('ix_anime_draft_status_published', table_name='anime_draft')
    if 'airing_time' in existing_columns:
        op.drop_column('anime_draft', 'airing_time')
    if 'airing_day' in existing_columns:
        op.drop_column('anime_draft', 'airing_day')
    if 'publication_status' in existing_columns:
        op.drop_column('anime_draft', 'publication_status')
    if 'volumes' in existing_columns:
        op.drop_column('anime_draft', 'volumes')
    if 'chapters' in existing_columns:
        op.drop_column('anime_draft', 'chapters')
