"""naikkan ke production: tabel promotions

Revision ID: a1c5e7d93b02
Revises: b1f7c3a9d204
Create Date: 2026-11-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a1c5e7d93b02'
down_revision: Union[str, Sequence[str], None] = 'b1f7c3a9d204'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'promotions',
        sa.Column('job_id', sa.BigInteger(), sa.ForeignKey('jobs.id'), primary_key=True),
        sa.Column('new_name', sa.String(64), sa.ForeignKey('containers.name'), nullable=False),
        sa.Column('port', sa.Integer(), nullable=False),
        sa.Column('requested_by', sa.String(100), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('promotions')
