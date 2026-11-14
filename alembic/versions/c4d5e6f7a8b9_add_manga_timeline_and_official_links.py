"""add manga timeline and official links to anime_draft

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
Create Date: 2026-11-13 20:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c4d5e6f7a8b9'
down_revision = 'b3c4d5e6f7a8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('anime_draft', sa.Column('latest_chapter', sa.String(length=50), nullable=True))
    op.add_column('anime_draft', sa.Column('next_volume_date', sa.DateTime(timezone=True), nullable=True))
    op.add_column('anime_draft', sa.Column('score', sa.Float(), nullable=True))
    op.add_column('anime_draft', sa.Column('rank', sa.Integer(), nullable=True))
    op.add_column('anime_draft', sa.Column('official_links', sa.Text(), nullable=True))
    op.add_column('anime_draft', sa.Column('anime_progress', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('anime_draft', 'anime_progress')
    op.drop_column('anime_draft', 'official_links')
    op.drop_column('anime_draft', 'rank')
    op.drop_column('anime_draft', 'score')
    op.drop_column('anime_draft', 'next_volume_date')
    op.drop_column('anime_draft', 'latest_chapter')

