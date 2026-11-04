"""lapisan AI: tabel ai_questions

Revision ID: e5a9c1b37f46
Revises: d4f8b0a26e35
Create Date: 2026-11-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'e5a9c1b37f46'
down_revision: Union[str, Sequence[str], None] = 'd4f8b0a26e35'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'ai_questions',
        sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column('asked_by', sa.String(100), nullable=False),
        sa.Column('question', sa.String(300), nullable=False),
        sa.Column('provider', sa.String(20), nullable=False),
        sa.Column('model', sa.String(80), nullable=False),
        sa.Column('input', sa.JSON(), nullable=False),
        sa.Column('answer', sa.Text()),
        sa.Column('error', sa.String(500)),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_ai_questions_user', 'ai_questions', ['asked_by', 'id'])


def downgrade() -> None:
    op.drop_table('ai_questions')
