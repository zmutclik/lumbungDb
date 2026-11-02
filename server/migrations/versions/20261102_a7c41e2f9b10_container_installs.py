"""container installs

Revision ID: a7c41e2f9b10
Revises: d1563b177493
Create Date: 2026-11-02 19:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a7c41e2f9b10'
down_revision: Union[str, Sequence[str], None] = 'd1563b177493'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('container_installs',
    sa.Column('container', sa.String(length=64), nullable=False),
    sa.Column('token_hash', sa.CHAR(length=64), nullable=False),
    sa.Column('port', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by', sa.String(length=100), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.Column('machine_id', sa.CHAR(length=64), nullable=True),
    sa.Column('claimed_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['container'], ['containers.name'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('container'),
    sa.UniqueConstraint('token_hash'),
    )


def downgrade() -> None:
    op.drop_table('container_installs')
