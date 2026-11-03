"""nas_scans

Revision ID: b1f7c3a9d204
Revises: a8d2e6f4b913
Create Date: 2026-11-03
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'b1f7c3a9d204'
down_revision: Union[str, Sequence[str], None] = 'a8d2e6f4b913'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'nas_scans',
        sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column('requested_by', sa.String(100), nullable=False),
        sa.Column('status', sa.String(10), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('started_at', sa.DateTime()),
        sa.Column('finished_at', sa.DateTime()),
        sa.Column('seen', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('added', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('skipped', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('note', sa.String(500)),
    )


def downgrade() -> None:
    op.drop_table('nas_scans')
