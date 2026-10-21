"""2FA Alembic migration — adds TOTP, backup codes, trusted devices, security log.

Revision ID: a1b2c3d4e5f6
Revises: e3f4a5b6c7d8
Create Date: 2026-10-21
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector

revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'e3f4a5b6c7d8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = Inspector.from_engine(bind)
    existing_tables = inspector.get_table_names()

    # ── 1. Add 2FA columns to admin1 ───────────────────────────────────
    existing_admin_cols = {c['name'] for c in inspector.get_columns('admin1')}

    for col_name, col_def in [
        ('totp_secret_enc', sa.Column('totp_secret_enc', sa.String(length=500), nullable=True)),
        ('totp_enabled', sa.Column('totp_enabled', sa.Boolean(), nullable=False, server_default='0')),
        ('totp_enabled_at', sa.Column('totp_enabled_at', sa.DateTime(), nullable=True)),
        ('token_version', sa.Column('token_version', sa.Integer(), nullable=False, server_default='1')),
        ('failed_2fa_count', sa.Column('failed_2fa_count', sa.Integer(), nullable=False, server_default='0')),
        ('locked_until', sa.Column('locked_until', sa.DateTime(), nullable=True)),
        ('last_2fa_at', sa.Column('last_2fa_at', sa.DateTime(), nullable=True)),
        ('bio', sa.Column('bio', sa.String(length=500), nullable=False, server_default='')),
        ('avatar_url', sa.Column('avatar_url', sa.String(length=500), nullable=True)),
    ]:
        if col_name not in existing_admin_cols:
            op.add_column('admin1', col_def)

    # ── 2. Add 2FA columns to user ─────────────────────────────────────
    existing_user_cols = {c['name'] for c in inspector.get_columns('user')}

    for col_name, col_def in [
        ('totp_secret_enc', sa.Column('totp_secret_enc', sa.String(length=500), nullable=True)),
        ('totp_enabled', sa.Column('totp_enabled', sa.Boolean(), nullable=False, server_default='0')),
        ('totp_enabled_at', sa.Column('totp_enabled_at', sa.DateTime(), nullable=True)),
        ('token_version', sa.Column('token_version', sa.Integer(), nullable=False, server_default='1')),
        ('failed_2fa_count', sa.Column('failed_2fa_count', sa.Integer(), nullable=False, server_default='0')),
        ('locked_until', sa.Column('locked_until', sa.DateTime(), nullable=True)),
        ('last_2fa_at', sa.Column('last_2fa_at', sa.DateTime(), nullable=True)),
    ]:
        if col_name not in existing_user_cols:
            op.add_column('user', col_def)

    # ── 3. backup_codes table ──────────────────────────────────────────
    if 'backup_codes' not in existing_tables:
        op.create_table(
            'backup_codes',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('owner_kind', sa.String(length=10), nullable=False),   # 'admin' | 'user'
            sa.Column('owner_id', sa.Integer(), nullable=False),
            sa.Column('code_hash', sa.String(length=255), nullable=False),
            sa.Column('used_at', sa.DateTime(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_backup_codes_owner', 'backup_codes', ['owner_kind', 'owner_id'])

    # ── 4. trusted_devices table ───────────────────────────────────────
    if 'trusted_devices' not in existing_tables:
        op.create_table(
            'trusted_devices',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('owner_kind', sa.String(length=10), nullable=False),
            sa.Column('owner_id', sa.Integer(), nullable=False),
            sa.Column('token_hash', sa.String(length=255), nullable=False, unique=True),
            sa.Column('label', sa.String(length=200), nullable=False, server_default=''),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('expires_at', sa.DateTime(), nullable=False),
            sa.Column('revoked_at', sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_trusted_devices_owner', 'trusted_devices', ['owner_kind', 'owner_id'])
        op.create_index('ix_trusted_devices_token_hash', 'trusted_devices', ['token_hash'], unique=True)

    # ── 5. security_log table ──────────────────────────────────────────
    if 'security_log' not in existing_tables:
        op.create_table(
            'security_log',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('owner_kind', sa.String(length=10), nullable=True),
            sa.Column('owner_id', sa.Integer(), nullable=True),
            sa.Column('action', sa.String(length=100), nullable=False),
            sa.Column('ip_address', sa.String(length=45), nullable=False, server_default=''),
            sa.Column('user_agent', sa.String(length=500), nullable=False, server_default=''),
            sa.Column('meta', sa.String(length=1000), nullable=False, server_default=''),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_security_log_owner', 'security_log', ['owner_kind', 'owner_id'])
        op.create_index('ix_security_log_created_at', 'security_log', ['created_at'])


def downgrade() -> None:
    bind = op.get_bind()

    # Drop new tables
    for tbl in ('security_log', 'trusted_devices', 'backup_codes'):
        op.execute(f"DROP TABLE IF EXISTS {tbl}")

    # Drop 2FA columns from user
    for col in ('totp_secret_enc', 'totp_enabled', 'totp_enabled_at',
                'token_version', 'failed_2fa_count', 'locked_until', 'last_2fa_at'):
        try:
            op.drop_column('user', col)
        except Exception:
            pass

    # Drop 2FA columns from admin1
    for col in ('totp_secret_enc', 'totp_enabled', 'totp_enabled_at',
                'token_version', 'failed_2fa_count', 'locked_until', 'last_2fa_at',
                'bio', 'avatar_url'):
        try:
            op.drop_column('admin1', col)
        except Exception:
            pass
