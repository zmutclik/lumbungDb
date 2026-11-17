"""managed_containers.image_tag: tag image yang dipasang atau hasil Update image terakhir

Revision ID: a9c3e5f7b204
Revises: f4b8d2a6c913
Create Date: 2026-11-17
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a9c3e5f7b204'
down_revision: Union[str, Sequence[str], None] = 'f4b8d2a6c913'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('managed_containers', sa.Column('image_tag', sa.String(40), nullable=True))


def downgrade() -> None:
    op.drop_column('managed_containers', 'image_tag')
