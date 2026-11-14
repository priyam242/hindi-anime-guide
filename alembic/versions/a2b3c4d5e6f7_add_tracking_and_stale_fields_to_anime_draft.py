"""Add fetched_at, anilist_id, mal_id, season, season_year, is_stale to anime_draft.

Revision ID: a2b3c4d5e6f7
Revises: f1a2b3c4d5e6
Create Date: 2026-11-13
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'a2b3c4d5e6f7'
down_revision: Union[str, None] = 'f1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = [col['name'] for col in inspector.get_columns('anime_draft')]
    existing_indexes = [idx['name'] for idx in inspector.get_indexes('anime_draft')]

    if 'fetched_at' not in existing_columns:
        op.add_column('anime_draft', sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=True))
    if 'anilist_id' not in existing_columns:
        op.add_column('anime_draft', sa.Column('anilist_id', sa.Integer(), nullable=True))
    if 'mal_id' not in existing_columns:
        op.add_column('anime_draft', sa.Column('mal_id', sa.Integer(), nullable=True))
    if 'season' not in existing_columns:
        op.add_column('anime_draft', sa.Column('season', sa.String(length=20), nullable=True))
    if 'season_year' not in existing_columns:
        op.add_column('anime_draft', sa.Column('season_year', sa.Integer(), nullable=True))
    if 'is_stale' not in existing_columns:
        op.add_column('anime_draft', sa.Column('is_stale', sa.Boolean(), server_default=sa.text('false'), nullable=False))

    if 'ix_anime_draft_is_stale' not in existing_indexes:
        op.create_index('ix_anime_draft_is_stale', 'anime_draft', ['is_stale'])
    if 'ix_anime_draft_anilist_id' not in existing_indexes:
        op.create_index('ix_anime_draft_anilist_id', 'anime_draft', ['anilist_id'])
    if 'ix_anime_draft_mal_id' not in existing_indexes:
        op.create_index('ix_anime_draft_mal_id', 'anime_draft', ['mal_id'])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = [col['name'] for col in inspector.get_columns('anime_draft')]
    existing_indexes = [idx['name'] for idx in inspector.get_indexes('anime_draft')]

    if 'ix_anime_draft_mal_id' in existing_indexes:
        op.drop_index('ix_anime_draft_mal_id', table_name='anime_draft')
    if 'ix_anime_draft_anilist_id' in existing_indexes:
        op.drop_index('ix_anime_draft_anilist_id', table_name='anime_draft')
    if 'ix_anime_draft_is_stale' in existing_indexes:
        op.drop_index('ix_anime_draft_is_stale', table_name='anime_draft')

    if 'is_stale' in existing_columns:
        op.drop_column('anime_draft', 'is_stale')
    if 'season_year' in existing_columns:
        op.drop_column('anime_draft', 'season_year')
    if 'season' in existing_columns:
        op.drop_column('anime_draft', 'season')
    if 'mal_id' in existing_columns:
        op.drop_column('anime_draft', 'mal_id')
    if 'anilist_id' in existing_columns:
        op.drop_column('anime_draft', 'anilist_id')
    if 'fetched_at' in existing_columns:
        op.drop_column('anime_draft', 'fetched_at')

