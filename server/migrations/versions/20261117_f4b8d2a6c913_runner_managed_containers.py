"""container dikelola runner (managed_containers, container_updates) dan CPU/memori mesin runner di heartbeat

Revision ID: f4b8d2a6c913
Revises: e3a7c9d15f42
Create Date: 2026-11-17
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'f4b8d2a6c913'
down_revision: Union[str, Sequence[str], None] = 'e3a7c9d15f42'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('runner_heartbeats', sa.Column('cpu_percent', sa.Float(), nullable=True))
    op.add_column('runner_heartbeats', sa.Column('cpu_count', sa.Integer(), nullable=True))
    op.add_column('runner_heartbeats', sa.Column('load1', sa.Float(), nullable=True))
    op.add_column('runner_heartbeats', sa.Column('mem_total', sa.BigInteger(), nullable=True))
    op.add_column('runner_heartbeats', sa.Column('mem_available', sa.BigInteger(), nullable=True))
    op.add_column('runner_heartbeats', sa.Column('containers', sa.JSON(), nullable=True))
    op.create_table(
        'managed_containers',
        sa.Column('container', sa.String(64), sa.ForeignKey('containers.name'), primary_key=True),
        sa.Column('runner', sa.String(64), nullable=False),
        sa.Column('port', sa.Integer(), nullable=False),
        sa.Column('origin', sa.String(20), nullable=False),
        sa.Column('created_by', sa.String(100), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        'container_updates',
        sa.Column('job_id', sa.BigInteger(), sa.ForeignKey('jobs.id'), primary_key=True),
        sa.Column('image_tag', sa.String(40), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('container_updates')
    op.drop_table('managed_containers')
    for column in ('containers', 'mem_available', 'mem_total', 'load1', 'cpu_count', 'cpu_percent'):
        op.drop_column('runner_heartbeats', column)
