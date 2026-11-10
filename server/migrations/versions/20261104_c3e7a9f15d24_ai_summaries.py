"""lapisan AI: tabel ai_summaries

Revision ID: c3e7a9f15d24
Revises: b2d6f8e04c13
Create Date: 2026-11-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c3e7a9f15d24'
down_revision: Union[str, Sequence[str], None] = 'b2d6f8e04c13'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'ai_summaries',
        sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column('report_id', sa.BigInteger(), sa.ForeignKey('reports.id'), nullable=False, unique=True),
        sa.Column('provider', sa.String(20), nullable=False),
        sa.Column('model', sa.String(80), nullable=False),
        sa.Column('input', sa.JSON(), nullable=False),
        sa.Column('text', sa.Text()),
        sa.Column('error', sa.String(500)),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('ai_summaries')
