"""backups.plain_sha256: pengenal isi sebelum enkripsi

Revision ID: b3e8d27c4a16
Revises: a7c41e2f9b10
Create Date: 2026-11-03
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'b3e8d27c4a16'
down_revision: Union[str, Sequence[str], None] = 'a7c41e2f9b10'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('backups', sa.Column('plain_sha256', sa.CHAR(64), nullable=True))
    op.execute('UPDATE backups SET plain_sha256 = sha256')
    op.alter_column('backups', 'plain_sha256', existing_type=sa.CHAR(64), nullable=False)
    op.create_unique_constraint('uq_backups_version_plain', 'backups', ['container', 'kind', 'relpath', 'plain_sha256'])
    op.drop_constraint('uq_backups_version', 'backups', type_='unique')
    op.execute('ALTER TABLE backups RENAME INDEX uq_backups_version_plain TO uq_backups_version')


def downgrade() -> None:
    op.create_unique_constraint('uq_backups_version_old', 'backups', ['container', 'kind', 'relpath', 'sha256'])
    op.drop_constraint('uq_backups_version', 'backups', type_='unique')
    op.execute('ALTER TABLE backups RENAME INDEX uq_backups_version_old TO uq_backups_version')
    op.drop_column('backups', 'plain_sha256')
