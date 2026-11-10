"""lapisan AI: kolom kind di ai_explanations (keadaan | query)

Revision ID: f6b0d2c48a57
Revises: e5a9c1b37f46
Create Date: 2026-11-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'f6b0d2c48a57'
down_revision: Union[str, Sequence[str], None] = 'e5a9c1b37f46'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('ai_explanations', sa.Column('kind', sa.String(12), nullable=False, server_default='keadaan'))


def downgrade() -> None:
    op.drop_column('ai_explanations', 'kind')
