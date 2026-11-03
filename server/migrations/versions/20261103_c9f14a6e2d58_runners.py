"""runners: heartbeat, penetapan per container, tugas, unduhan, hasil uji restore

Revision ID: c9f14a6e2d58
Revises: b3e8d27c4a16
Create Date: 2026-11-03
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c9f14a6e2d58'
down_revision: Union[str, Sequence[str], None] = 'b3e8d27c4a16'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'runner_heartbeats',
        sa.Column('runner', sa.String(64), primary_key=True),
        sa.Column('server', sa.String(255)),
        sa.Column('machine_id', sa.CHAR(64)),
        sa.Column('version', sa.String(40)),
        sa.Column('scratch_total', sa.BigInteger()),
        sa.Column('scratch_free', sa.BigInteger()),
        sa.Column('at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        'container_settings',
        sa.Column('container', sa.String(64), sa.ForeignKey('containers.name'), primary_key=True),
        sa.Column('test_runner', sa.String(64)),
        sa.Column('standby_runner', sa.String(64)),
        sa.Column('test_schedule', sa.String(120)),
        sa.Column('test_full', sa.Boolean(), nullable=False),
        sa.Column('test_databases', sa.JSON()),
    )
    op.create_table(
        'jobs',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('runner', sa.String(64), nullable=False),
        sa.Column('container', sa.String(64), sa.ForeignKey('containers.name'), nullable=False),
        sa.Column('kind', sa.String(20), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('step', sa.String(200)),
        sa.Column('note', sa.String(500)),
        sa.Column('requested_by', sa.String(100)),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('claimed_at', sa.DateTime()),
        sa.Column('deadline_at', sa.DateTime()),
        sa.Column('finished_at', sa.DateTime()),
    )
    op.create_index('ix_jobs_runner', 'jobs', ['runner', 'status', 'id'])
    op.create_index('ix_jobs_container', 'jobs', ['container', 'id'])
    op.create_index('ix_jobs_deadline', 'jobs', ['status', 'deadline_at'])
    op.create_table(
        'job_files',
        sa.Column('job_id', sa.BigInteger(), sa.ForeignKey('jobs.id'), primary_key=True),
        sa.Column('backup_id', sa.BigInteger(), sa.ForeignKey('backups.id'), primary_key=True),
    )
    op.create_table(
        'job_downloads',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('job_id', sa.BigInteger(), sa.ForeignKey('jobs.id'), nullable=False),
        sa.Column('backup_id', sa.BigInteger(), sa.ForeignKey('backups.id'), nullable=False),
        sa.Column('runner', sa.String(64), nullable=False),
        sa.Column('offset', sa.BigInteger(), nullable=False),
        sa.Column('ip', sa.String(45), nullable=False),
        sa.Column('at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_job_downloads_job', 'job_downloads', ['job_id'])
    op.create_table(
        'restore_tests',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('job_id', sa.BigInteger(), sa.ForeignKey('jobs.id'), nullable=False),
        sa.Column('container', sa.String(64), sa.ForeignKey('containers.name'), nullable=False),
        sa.Column('runner', sa.String(64), nullable=False),
        sa.Column('kind', sa.String(12), nullable=False),
        sa.Column('database', sa.String(64)),
        sa.Column('backup_id', sa.BigInteger(), sa.ForeignKey('backups.id')),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('failed_step', sa.String(200)),
        sa.Column('duration_seconds', sa.Integer()),
        sa.Column('log', sa.Text()),
        sa.Column('at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_restore_tests_container', 'restore_tests', ['container', 'kind', 'status', 'id'])
    op.create_index('ix_restore_tests_backup', 'restore_tests', ['backup_id'])


def downgrade() -> None:
    op.drop_table('restore_tests')
    op.drop_table('job_downloads')
    op.drop_table('job_files')
    op.drop_table('jobs')
    op.drop_table('container_settings')
    op.drop_table('runner_heartbeats')
