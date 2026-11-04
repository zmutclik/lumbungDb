"""lapisan AI: tabel ai_explanations

Revision ID: d4f8b0a26e35
Revises: c3e7a9f15d24
Create Date: 2026-11-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'd4f8b0a26e35'
down_revision: Union[str, Sequence[str], None] = 'c3e7a9f15d24'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'ai_explanations',
        sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column('container', sa.String(64), sa.ForeignKey('containers.name'), nullable=False),
        sa.Column('requested_by', sa.String(100), nullable=False),
        sa.Column('provider', sa.String(20), nullable=False),
        sa.Column('model', sa.String(80), nullable=False),
        sa.Column('input', sa.JSON(), nullable=False),
        sa.Column('text', sa.Text()),
        sa.Column('error', sa.String(500)),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_ai_explanations_container', 'ai_explanations', ['container', 'id'])


def downgrade() -> None:
    op.drop_table('ai_explanations')
