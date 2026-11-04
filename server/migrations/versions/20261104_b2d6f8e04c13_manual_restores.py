"""restore manual: manual_restores, manual_restore_files, manual_restore_downloads

Revision ID: b2d6f8e04c13
Revises: a1c5e7d93b02
Create Date: 2026-11-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'b2d6f8e04c13'
down_revision: Union[str, Sequence[str], None] = 'a1c5e7d93b02'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'manual_restores',
        sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column('source', sa.String(64), sa.ForeignKey('containers.name'), nullable=False),
        sa.Column('new_name', sa.String(64), nullable=False),
        sa.Column('port', sa.Integer(), nullable=False),
        sa.Column('token_hash', sa.CHAR(64), nullable=False, unique=True),
        sa.Column('full_backup_id', sa.BigInteger(), sa.ForeignKey('backups.id'), nullable=False),
        sa.Column('binlog', sa.String(10), nullable=False),
        sa.Column('created_by', sa.String(100), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('max_expires_at', sa.DateTime(), nullable=False),
        sa.Column('machine_id', sa.CHAR(64)),
        sa.Column('claimed_at', sa.DateTime()),
        sa.Column('step', sa.String(200)),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('note', sa.String(500)),
        sa.Column('finished_at', sa.DateTime()),
    )
    op.create_index('ix_manual_restores_source', 'manual_restores', ['source', 'id'])
    op.create_index('ix_manual_restores_status', 'manual_restores', ['status', 'expires_at'])
    op.create_table(
        'manual_restore_files',
        sa.Column('restore_id', sa.BigInteger(), sa.ForeignKey('manual_restores.id'), primary_key=True),
        sa.Column('backup_id', sa.BigInteger(), sa.ForeignKey('backups.id'), primary_key=True),
    )
    op.create_table(
        'manual_restore_downloads',
        sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column('restore_id', sa.BigInteger(), sa.ForeignKey('manual_restores.id'), nullable=False),
        sa.Column('backup_id', sa.BigInteger(), sa.ForeignKey('backups.id'), nullable=False),
        sa.Column('offset', sa.BigInteger(), nullable=False),
        sa.Column('ip', sa.String(45), nullable=False),
        sa.Column('at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_manual_restore_downloads_restore', 'manual_restore_downloads', ['restore_id'])


def downgrade() -> None:
    op.drop_table('manual_restore_downloads')
    op.drop_table('manual_restore_files')
    op.drop_table('manual_restores')
