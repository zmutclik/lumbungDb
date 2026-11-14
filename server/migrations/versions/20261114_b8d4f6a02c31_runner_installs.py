"""perintah pasang runner: runner_installs

Revision ID: b8d4f6a02c31
Revises: a7c3e5f91b20
Create Date: 2026-11-14
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'b8d4f6a02c31'
down_revision: Union[str, Sequence[str], None] = 'a7c3e5f91b20'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'runner_installs',
        sa.Column('runner', sa.String(length=64), nullable=False),
        sa.Column('token_hash', sa.CHAR(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=100), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('machine_id', sa.CHAR(length=64), nullable=True),
        sa.Column('claimed_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('runner'),
        sa.UniqueConstraint('token_hash'),
    )


def downgrade() -> None:
    op.drop_table('runner_installs')
