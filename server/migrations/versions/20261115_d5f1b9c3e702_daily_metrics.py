"""rekap harian: daily_metrics

Revision ID: d5f1b9c3e702
Revises: c2e8a4d07f61
Create Date: 2026-11-15
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'd5f1b9c3e702'
down_revision: Union[str, Sequence[str], None] = 'c2e8a4d07f61'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'daily_metrics',
        sa.Column('container', sa.String(64), sa.ForeignKey('containers.name'), primary_key=True),
        sa.Column('day', sa.Date(), primary_key=True),
        sa.Column('name', sa.String(64), primary_key=True),
        sa.Column('value', sa.Double(), nullable=False),
        sa.Column('peak_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_daily_metrics_day', 'daily_metrics', ['day'])


def downgrade() -> None:
    op.drop_index('ix_daily_metrics_day', table_name='daily_metrics')
    op.drop_table('daily_metrics')
