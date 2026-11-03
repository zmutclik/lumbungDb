"""notifications

Revision ID: a8d2e6f4b913
Revises: f6c9d3e52a81
Create Date: 2026-11-03
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a8d2e6f4b913'
down_revision: Union[str, Sequence[str], None] = 'f6c9d3e52a81'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'notifications',
        sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column('kind', sa.String(10), nullable=False),
        sa.Column('ref', sa.String(64)),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('status', sa.String(12), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('next_attempt_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('sent_at', sa.DateTime()),
    )
    op.create_index('ix_notifications_due', 'notifications', ['status', 'next_attempt_at'])
    op.create_index('ix_notifications_created', 'notifications', ['created_at'])


def downgrade() -> None:
    op.drop_table('notifications')
