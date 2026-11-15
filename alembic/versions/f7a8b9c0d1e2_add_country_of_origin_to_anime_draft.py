"""add country of origin and sync staging fields to anime draft and awards

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
Create Date: 2026-11-14 23:15:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f7a8b9c0d1e2'
down_revision = 'e6f7a8b9c0d1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    draft_cols = [c['name'] for c in insp.get_columns('anime_draft')]

    new_draft_cols = [
        ('country_of_origin', sa.Column('country_of_origin', sa.String(length=10), nullable=True, server_default='JP')),
        ('key_points_en', sa.Column('key_points_en', sa.Text(), nullable=True)),
        ('key_points_hi', sa.Column('key_points_hi', sa.Text(), nullable=True)),
        ('premiere_date', sa.Column('premiere_date', sa.DateTime(timezone=True), nullable=True)),
        ('status_manual', sa.Column('status_manual', sa.String(length=100), nullable=True)),
        ('status_manual_url', sa.Column('status_manual_url', sa.String(length=500), nullable=True)),
        ('status_manual_date', sa.Column('status_manual_date', sa.DateTime(timezone=True), nullable=True)),
        ('start_date', sa.Column('start_date', sa.DateTime(timezone=True), nullable=True)),
        ('end_date', sa.Column('end_date', sa.DateTime(timezone=True), nullable=True)),
        ('linked_anime_title', sa.Column('linked_anime_title', sa.String(length=300), nullable=True)),
    ]
    for col_name, col_def in new_draft_cols:
        if col_name not in draft_cols:
            op.add_column('anime_draft', col_def)

    if 'award_ceremony' in insp.get_table_names():
        ceremony_cols = [c['name'] for c in insp.get_columns('award_ceremony')]
        if 'status' not in ceremony_cols:
            op.add_column('award_ceremony', sa.Column('status', sa.String(length=50), nullable=False, server_default='verified'))

    if 'award_entry' in insp.get_table_names():
        entry_cols = [c['name'] for c in insp.get_columns('award_entry')]
        if 'status' not in entry_cols:
            op.add_column('award_entry', sa.Column('status', sa.String(length=50), nullable=False, server_default='verified'))


def downgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    draft_cols = [c['name'] for c in insp.get_columns('anime_draft')]

    for col_name in ['linked_anime_title', 'end_date', 'start_date', 'status_manual_date',
                     'status_manual_url', 'status_manual', 'premiere_date', 'key_points_hi',
                     'key_points_en', 'country_of_origin']:
        if col_name in draft_cols:
            op.drop_column('anime_draft', col_name)
