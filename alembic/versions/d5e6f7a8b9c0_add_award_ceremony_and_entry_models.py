"""add award ceremony and award entry models

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-11-13 21:27:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd5e6f7a8b9c0'
down_revision = 'c4d5e6f7a8b9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Create award_ceremony table
    op.create_table(
        'award_ceremony',
        sa.Column('id', sa.Integer(), nullable=False, autoincrement=True),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('name_hi', sa.String(length=200), nullable=True),
        sa.Column('edition_year', sa.Integer(), nullable=False),
        sa.Column('date', sa.Date(), nullable=True),
        sa.Column('location', sa.String(length=200), nullable=True),
        sa.Column('source_url', sa.String(length=500), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_award_ceremony_edition_year', 'award_ceremony', ['edition_year'])

    # 2. Create award_entry table
    op.create_table(
        'award_entry',
        sa.Column('id', sa.Integer(), nullable=False, autoincrement=True),
        sa.Column('ceremony_id', sa.Integer(), sa.ForeignKey('award_ceremony.id', ondelete='CASCADE'), nullable=False),
        sa.Column('category', sa.String(length=200), nullable=False),
        sa.Column('category_hi', sa.String(length=200), nullable=True),
        sa.Column('winner_media_id', sa.Integer(), nullable=False),
        sa.Column('winner_title', sa.String(length=300), nullable=True),
        sa.Column('winner_title_hi', sa.String(length=300), nullable=True),
        sa.Column('winner_poster', sa.String(length=500), nullable=True),
        sa.Column('nominee_media_ids', sa.Text(), nullable=True),
        sa.Column('source_url', sa.String(length=500), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_award_entry_ceremony_id', 'award_entry', ['ceremony_id'])
    op.create_index('ix_award_entry_winner_media_id', 'award_entry', ['winner_media_id'])


def downgrade() -> None:
    op.drop_index('ix_award_entry_winner_media_id', table_name='award_entry')
    op.drop_index('ix_award_entry_ceremony_id', table_name='award_entry')
    op.drop_table('award_entry')
    op.drop_index('ix_award_ceremony_edition_year', table_name='award_ceremony')
    op.drop_table('award_ceremony')

