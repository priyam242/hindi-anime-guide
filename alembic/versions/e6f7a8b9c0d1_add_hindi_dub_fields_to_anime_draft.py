"""add hindi dub fields to anime draft

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-11-13 22:56:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e6f7a8b9c0d1'
down_revision = 'd5e6f7a8b9c0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    existing_cols = [c['name'] for c in insp.get_columns('anime_draft')]

    if 'hindi_dub' not in existing_cols:
        op.add_column(
            'anime_draft',
            sa.Column('hindi_dub', sa.Boolean(), nullable=False, server_default=sa.false())
        )
    if 'dub_platform' not in existing_cols:
        op.add_column(
            'anime_draft',
            sa.Column('dub_platform', sa.String(length=100), nullable=True)
        )
    if 'dub_source_url' not in existing_cols:
        op.add_column(
            'anime_draft',
            sa.Column('dub_source_url', sa.String(length=500), nullable=True)
        )


def downgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    existing_cols = [c['name'] for c in insp.get_columns('anime_draft')]

    if 'dub_source_url' in existing_cols:
        op.drop_column('anime_draft', 'dub_source_url')
    if 'dub_platform' in existing_cols:
        op.drop_column('anime_draft', 'dub_platform')
    if 'hindi_dub' in existing_cols:
        op.drop_column('anime_draft', 'hindi_dub')

