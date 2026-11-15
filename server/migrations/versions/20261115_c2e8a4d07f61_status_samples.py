"""grafik aktivitas: status_samples

Revision ID: c2e8a4d07f61
Revises: b8d4f6a02c31
Create Date: 2026-11-15
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c2e8a4d07f61'
down_revision: Union[str, Sequence[str], None] = 'b8d4f6a02c31'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'status_samples',
        sa.Column('container', sa.String(64), sa.ForeignKey('containers.name'), primary_key=True),
        sa.Column('at', sa.DateTime(), primary_key=True),
        sa.Column('uptime', sa.BigInteger(), nullable=False),
        sa.Column('data', sa.JSON(), nullable=False),
    )
    op.create_index('ix_status_samples_at', 'status_samples', ['at'])


def downgrade() -> None:
    op.drop_index('ix_status_samples_at', table_name='status_samples')
    op.drop_table('status_samples')
