"""database cadangan: kolom jadwal, cadangan, cadangan_runs

Revision ID: f6c9d3e52a81
Revises: e5b8c2d41f70
Create Date: 2026-11-03
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'f6c9d3e52a81'
down_revision: Union[str, Sequence[str], None] = 'e5b8c2d41f70'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('container_settings', sa.Column('standby_enabled', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('container_settings', sa.Column('standby_full_schedule', sa.String(120)))
    op.add_column('container_settings', sa.Column('standby_binlog_schedule', sa.String(120)))
    op.create_table(
        'cadangan',
        sa.Column('container', sa.String(64), sa.ForeignKey('containers.name'), primary_key=True),
        sa.Column('runner', sa.String(64)),
        sa.Column('full_backup_id', sa.BigInteger()),
        sa.Column('chain_id', sa.Integer()),
        sa.Column('applied_binlog_no', sa.Integer()),
        sa.Column('data_until', sa.DateTime()),
        sa.Column('port', sa.Integer()),
        sa.Column('restored_at', sa.DateTime()),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        'cadangan_runs',
        sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column('container', sa.String(64), sa.ForeignKey('containers.name'), nullable=False),
        sa.Column('job_id', sa.BigInteger(), sa.ForeignKey('jobs.id'), nullable=False),
        sa.Column('runner', sa.String(64), nullable=False),
        sa.Column('kind', sa.String(20), nullable=False),
        sa.Column('manual', sa.Boolean(), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('note', sa.String(500)),
        sa.Column('at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_cadangan_runs_container', 'cadangan_runs', ['container', 'id'])


def downgrade() -> None:
    op.drop_table('cadangan_runs')
    op.drop_table('cadangan')
    op.drop_column('container_settings', 'standby_binlog_schedule')
    op.drop_column('container_settings', 'standby_full_schedule')
    op.drop_column('container_settings', 'standby_enabled')
