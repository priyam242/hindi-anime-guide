"""add news_category and editorial flags to anime_draft

Revision ID: b3c4d5e6f7a8
Revises: a2b3c4d5e6f7
Create Date: 2026-11-13 19:38:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b3c4d5e6f7a8'
down_revision = 'a2b3c4d5e6f7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add columns news_category, is_featured, is_editor_pick to anime_draft
    op.add_column('anime_draft', sa.Column('news_category', sa.String(length=50), nullable=True, server_default='Announcement'))
    op.add_column('anime_draft', sa.Column('is_featured', sa.Boolean(), nullable=False, server_default=sa.text('false')))
    op.add_column('anime_draft', sa.Column('is_editor_pick', sa.Boolean(), nullable=False, server_default=sa.text('false')))

    # Create indices
    op.create_index('ix_anime_draft_news_category', 'anime_draft', ['news_category'], unique=False)
    op.create_index('ix_anime_draft_is_featured', 'anime_draft', ['is_featured'], unique=False)
    op.create_index('ix_anime_draft_is_editor_pick', 'anime_draft', ['is_editor_pick'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_anime_draft_is_editor_pick', table_name='anime_draft')
    op.drop_index('ix_anime_draft_is_featured', table_name='anime_draft')
    op.drop_index('ix_anime_draft_news_category', table_name='anime_draft')
    op.drop_column('anime_draft', 'is_editor_pick')
    op.drop_column('anime_draft', 'is_featured')
    op.drop_column('anime_draft', 'news_category')

