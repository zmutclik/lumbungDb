"""tuning MariaDB dari LumbungDB: container_tuning

Revision ID: a7c3e5f91b20
Revises: f6b0d2c48a57
Create Date: 2026-11-13
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a7c3e5f91b20'
down_revision: Union[str, Sequence[str], None] = 'f6b0d2c48a57'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'container_tuning',
        sa.Column('container', sa.String(length=64), nullable=False),
        sa.Column('requested_mode', sa.String(length=4), nullable=False),
        sa.Column('requested_memory_mb', sa.Integer(), nullable=False),
        sa.Column('requested_max_connections', sa.Integer(), nullable=False),
        sa.Column('requested_disk', sa.String(length=3), nullable=False),
        sa.Column('requested_version', sa.Integer(), nullable=False),
        sa.Column('applied_version', sa.Integer(), nullable=False),
        sa.Column('error_version', sa.Integer(), nullable=True),
        sa.Column('error_message', sa.String(length=300), nullable=True),
        sa.Column('reported_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['container'], ['containers.name']),
        sa.PrimaryKeyConstraint('container'),
    )


def downgrade() -> None:
    op.drop_table('container_tuning')
