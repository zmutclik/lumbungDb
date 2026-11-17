"""log slow query: slow_query_cursors, slow_query_log

Revision ID: e3a7c9d15f42
Revises: d5f1b9c3e702
Create Date: 2026-11-17
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'e3a7c9d15f42'
down_revision: Union[str, Sequence[str], None] = 'd5f1b9c3e702'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _counters() -> list[sa.Column]:
    return [
        sa.Column('count', sa.BigInteger(), nullable=False),
        sa.Column('total_ps', sa.BigInteger(), nullable=False),
        *[sa.Column(name, sa.BigInteger(), nullable=False) for name in (
            'rows_examined', 'rows_sent', 'no_index_used', 'no_good_index_used', 'full_join', 'tmp_disk_tables')],
    ]


def upgrade() -> None:
    op.create_table(
        'slow_query_cursors',
        sa.Column('container', sa.String(64), sa.ForeignKey('containers.name'), primary_key=True),
        sa.Column('database', sa.String(64), primary_key=True),
        sa.Column('digest', sa.String(64), primary_key=True),
        *_counters(),
        sa.Column('uptime', sa.BigInteger(), nullable=False),
    )
    op.create_table(
        'slow_query_log',
        sa.Column('container', sa.String(64), sa.ForeignKey('containers.name'), primary_key=True),
        sa.Column('at', sa.DateTime(), primary_key=True),
        sa.Column('database', sa.String(64), primary_key=True),
        sa.Column('digest', sa.String(64), primary_key=True),
        sa.Column('text', sa.String(300), nullable=False),
        *_counters(),
        sa.Column('max_ps', sa.BigInteger(), nullable=False),
    )
    op.create_index('ix_slow_query_log_at', 'slow_query_log', ['at'])


def downgrade() -> None:
    op.drop_index('ix_slow_query_log_at', table_name='slow_query_log')
    op.drop_table('slow_query_log')
    op.drop_table('slow_query_cursors')
