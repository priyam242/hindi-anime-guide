"""rbac_tables

Revision ID: 9d0e1f2a3b4c
Revises: 8c9d0e1f2a3b
Create Date: 2026-10-20 00:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector

revision: str = '9d0e1f2a3b4c'
down_revision: Union[str, None] = '8c9d0e1f2a3b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = Inspector.from_engine(bind)
    existing_tables = inspector.get_table_names()

    is_pg = bind.dialect.name == "postgresql"
    cascade_clause = " CASCADE" if is_pg else ""

    # Drop old plural tables if present to avoid constraint name collisions
    for old_table in ['availabilities', 'articles', 'platforms', 'categories', 'admin_user', 'users1']:
        if old_table in existing_tables:
            op.execute(f"DROP TABLE IF EXISTS {old_table}{cascade_clause}")

    # Refresh existing tables after cleanup
    existing_tables = inspector.get_table_names()

    # Ensure category table exists
    if 'category' not in existing_tables:
        op.create_table(
            'category',
            sa.Column('name', sa.String(length=100), nullable=False),
            sa.Column('display_name', sa.String(length=100), nullable=False),
            sa.Column('display_order', sa.Integer(), nullable=False, server_default='0'),
            sa.PrimaryKeyConstraint('name'),
        )

    # Ensure platform table exists
    if 'platform' not in existing_tables:
        op.create_table(
            'platform',
            sa.Column('name', sa.String(length=100), nullable=False),
            sa.Column('display_name', sa.String(length=100), nullable=False),
            sa.Column('official_url', sa.String(length=500), nullable=False),
            sa.Column('display_order', sa.Integer(), nullable=False, server_default='0'),
            sa.PrimaryKeyConstraint('name'),
            sa.CheckConstraint("official_url LIKE 'https://%'", name="ck_platforms_official_url_https"),
        )

    # Ensure article table exists
    if 'article' not in existing_tables:
        op.create_table(
            'article',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('slug', sa.String(length=200), nullable=False),
            sa.Column('title', sa.String(length=300), nullable=False),
            sa.Column('meta_description', sa.String(length=300), nullable=False, server_default=''),
            sa.Column('content_md', sa.Text(), nullable=False),
            sa.Column('published_at', sa.DateTime(), nullable=True),
            sa.Column('platform_links_count', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('last_checked', sa.DateTime(), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
            sa.Column('category', sa.String(length=100), nullable=True),
            sa.Column('anime_title_hi', sa.String(length=200), nullable=True),
            sa.Column('anime_title_en', sa.String(length=200), nullable=True),
            sa.Column('is_featured', sa.Boolean(), nullable=False, server_default='0'),
            sa.Column('reading_time_minutes', sa.Integer(), nullable=False, server_default='4'),
            sa.PrimaryKeyConstraint('id'),
            sa.ForeignKeyConstraint(['category'], ['category.name']),
        )
        op.create_index('ix_article_slug', 'article', ['slug'], unique=True)
        op.create_index('ix_article_category', 'article', ['category'])
    else:
        existing_cols = {c['name'] for c in inspector.get_columns('article')}
        if 'anime_title_hi' not in existing_cols:
            op.add_column('article', sa.Column('anime_title_hi', sa.String(length=200), nullable=True))
        if 'anime_title_en' not in existing_cols:
            op.add_column('article', sa.Column('anime_title_en', sa.String(length=200), nullable=True))
        if 'is_featured' not in existing_cols:
            op.add_column('article', sa.Column('is_featured', sa.Boolean(), nullable=False, server_default='0'))
        if 'reading_time_minutes' not in existing_cols:
            op.add_column('article', sa.Column('reading_time_minutes', sa.Integer(), nullable=False, server_default='4'))

    # Ensure availability table exists
    if 'availability' not in existing_tables:
        op.create_table(
            'availability',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('article_id', sa.Integer(), nullable=False),
            sa.Column('platform_id', sa.String(length=100), nullable=False),
            sa.Column('languages', sa.String(length=500), nullable=False),
            sa.Column('subtitle_languages', sa.String(length=500), nullable=False, server_default=''),
            sa.Column('requires_subscription', sa.Boolean(), nullable=False, server_default='0'),
            sa.Column('notes', sa.String(length=500), nullable=False, server_default=''),
            sa.Column('last_checked', sa.Date(), nullable=True),
            sa.Column('status', sa.String(length=50), nullable=True, server_default='available'),
            sa.Column('hindi_dub', sa.String(length=50), nullable=True, server_default='yes'),
            sa.PrimaryKeyConstraint('id'),
            sa.ForeignKeyConstraint(['article_id'], ['article.id']),
            sa.ForeignKeyConstraint(['platform_id'], ['platform.name']),
            sa.UniqueConstraint('article_id', 'platform_id', name='uq_availabilities_article_platform'),
        )
        op.create_index('ix_availability_article_id', 'availability', ['article_id'])
        op.create_index('ix_availability_platform_id', 'availability', ['platform_id'])
    else:
        existing_cols = {c['name'] for c in inspector.get_columns('availability')}
        if 'last_checked' not in existing_cols:
            op.add_column('availability', sa.Column('last_checked', sa.Date(), nullable=True))
        if 'status' not in existing_cols:
            op.add_column('availability', sa.Column('status', sa.String(length=50), nullable=True, server_default='available'))
        if 'hindi_dub' not in existing_cols:
            op.add_column('availability', sa.Column('hindi_dub', sa.String(length=50), nullable=True, server_default='yes'))

    # Create admin1 table
    if 'admin1' not in existing_tables:
        op.create_table(
            'admin1',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('email', sa.String(length=255), nullable=False),
            sa.Column('name', sa.String(length=100), nullable=False, server_default='Admin'),
            sa.Column('hashed_password', sa.String(length=255), nullable=False),
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default='1'),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('email'),
        )
        op.create_index('ix_admin1_email', 'admin1', ['email'], unique=True)

    # Create user table
    if 'user' not in existing_tables:
        op.create_table(
            'user',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('email', sa.String(length=255), nullable=False),
            sa.Column('name', sa.String(length=100), nullable=False),
            sa.Column('username', sa.String(length=50), nullable=False),
            sa.Column('hashed_password', sa.String(length=255), nullable=False),
            sa.Column('avatar_url', sa.String(length=500), nullable=True),
            sa.Column('bio', sa.String(length=500), nullable=False, server_default=''),
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default='1'),
            sa.Column('is_banned', sa.Boolean(), nullable=False, server_default='0'),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('email'),
            sa.UniqueConstraint('username'),
        )
        op.create_index('ix_user_email', 'user', ['email'], unique=True)
        op.create_index('ix_user_username', 'user', ['username'], unique=True)

    # Create comments1 table
    if 'comments1' not in existing_tables:
        op.create_table(
            'comments1',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('article_id', sa.Integer(), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('content', sa.String(length=2000), nullable=False),
            sa.Column('is_approved', sa.Boolean(), nullable=False, server_default='1'),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.ForeignKeyConstraint(['article_id'], ['article.id']),
            sa.ForeignKeyConstraint(['user_id'], ['user.id']),
        )
        op.create_index('ix_comments1_article_id', 'comments1', ['article_id'])
        op.create_index('ix_comments1_user_id', 'comments1', ['user_id'])


def downgrade() -> None:
    op.drop_table('comments1')
    op.drop_index('ix_user_username', table_name='user')
    op.drop_index('ix_user_email', table_name='user')
    op.drop_table('user')
    op.drop_index('ix_admin1_email', table_name='admin1')
    op.drop_table('admin1')
