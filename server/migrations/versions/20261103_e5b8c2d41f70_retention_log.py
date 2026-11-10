"""retention_log

Revision ID: e5b8c2d41f70
Revises: d4a7b9c15e32
Create Date: 2026-11-03
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'e5b8c2d41f70'
down_revision: Union[str, Sequence[str], None] = 'd4a7b9c15e32'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'retention_log',
        sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column('container', sa.String(64), nullable=False),
        sa.Column('kind', sa.String(12), nullable=False),
        sa.Column('relpath', sa.String(255), nullable=False),
        sa.Column('size', sa.BigInteger(), nullable=False),
        sa.Column('backup_at', sa.DateTime(), nullable=False),
        sa.Column('reason', sa.String(200), nullable=False),
        sa.Column('at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_retention_log_at', 'retention_log', ['at'])


def downgrade() -> None:
    op.drop_index('ix_retention_log_at', table_name='retention_log')
    op.drop_table('retention_log')
