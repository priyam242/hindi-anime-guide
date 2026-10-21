"""feature_covers_bookmarks_notifications

Revision ID: 1a2b3c4d5e6f
Revises: 0f1a2b3c4d5e
Create Date: 2026-10-20 00:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector

revision: str = '1a2b3c4d5e6f'
down_revision: Union[str, None] = '0f1a2b3c4d5e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = Inspector.from_engine(bind)
    existing_tables = inspector.get_table_names()

    # 1. Add cover_image to article
    if 'article' in existing_tables:
        article_cols = {c['name'] for c in inspector.get_columns('article')}
        with op.batch_alter_table('article', schema=None) as batch_op:
            if 'cover_image' not in article_cols:
                batch_op.add_column(sa.Column('cover_image', sa.String(length=500), nullable=True))

    # 2. Add user_id to notifications1 and allow admin_id nullable
    if 'notifications1' in existing_tables:
        notif_cols = {c['name'] for c in inspector.get_columns('notifications1')}
        with op.batch_alter_table('notifications1', schema=None) as batch_op:
            if 'user_id' not in notif_cols:
                batch_op.add_column(sa.Column('user_id', sa.Integer(), nullable=True))
                batch_op.create_foreign_key('fk_notifications1_user_id', 'user', ['user_id'], ['id'])
                batch_op.create_index('ix_notifications1_user_id', ['user_id'])
            if 'admin_id' in notif_cols:
                batch_op.alter_column('admin_id', existing_type=sa.Integer(), nullable=True)

    # 3. Create bookmarks1 table
    if 'bookmarks1' not in existing_tables:
        op.create_table(
            'bookmarks1',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('article_id', sa.Integer(), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.ForeignKeyConstraint(['article_id'], ['article.id']),
            sa.ForeignKeyConstraint(['user_id'], ['user.id']),
            sa.UniqueConstraint('user_id', 'article_id', name='uq_bookmarks_user_article'),
        )
        op.create_index('ix_bookmarks1_article_id', 'bookmarks1', ['article_id'])
        op.create_index('ix_bookmarks1_user_id', 'bookmarks1', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_bookmarks1_user_id', table_name='bookmarks1')
    op.drop_index('ix_bookmarks1_article_id', table_name='bookmarks1')
    op.drop_table('bookmarks1')
    with op.batch_alter_table('notifications1', schema=None) as batch_op:
        batch_op.drop_index('ix_notifications1_user_id')
        batch_op.drop_constraint('fk_notifications1_user_id', type_='foreignkey')
        batch_op.drop_column('user_id')
        batch_op.alter_column('admin_id', existing_type=sa.Integer(), nullable=False)
    with op.batch_alter_table('article', schema=None) as batch_op:
        batch_op.drop_column('cover_image')
