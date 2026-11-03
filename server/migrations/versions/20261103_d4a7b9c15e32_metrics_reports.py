"""tahap 3: metrics, metric_counters, reports

Revision ID: d4a7b9c15e32
Revises: c9f14a6e2d58
Create Date: 2026-11-03
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'd4a7b9c15e32'
down_revision: Union[str, Sequence[str], None] = 'c9f14a6e2d58'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'metrics',
        sa.Column('container', sa.String(64), sa.ForeignKey('containers.name'), primary_key=True),
        sa.Column('at', sa.DateTime(), primary_key=True),
        sa.Column('name', sa.String(191), primary_key=True),
        sa.Column('value', sa.Double(), nullable=False),
        sa.Column('peak', sa.Double()),
        sa.Column('peak_at', sa.DateTime()),
    )
    op.create_index('ix_metrics_name_at', 'metrics', ['name', 'at'])
    op.create_table(
        'metric_counters',
        sa.Column('container', sa.String(64), sa.ForeignKey('containers.name'), primary_key=True),
        sa.Column('name', sa.String(191), primary_key=True),
        sa.Column('last_value', sa.BigInteger(), nullable=False),
        sa.Column('uptime', sa.BigInteger(), nullable=False),
    )
    op.create_table(
        'reports',
        sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column('kind', sa.String(10), nullable=False),
        sa.Column('period_start', sa.DateTime(), nullable=False),
        sa.Column('period_end', sa.DateTime(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('data', sa.JSON(), nullable=False),
        sa.Column('sent_at', sa.DateTime()),
        sa.UniqueConstraint('kind', 'period_start', name='uq_reports_period'),
    )


def downgrade() -> None:
    op.drop_table('reports')
    op.drop_table('metric_counters')
    op.drop_index('ix_metrics_name_at', table_name='metrics')
    op.drop_table('metrics')
