"""job_states table — durable verify job-state (RFC-0016, TFactory #465)

Adds the ``job_states`` table that persists one row per verify job conforming
to the Factory hub ``apis/job-state.schema.json`` (service="tfactory",
kind="verify"). This replaces the per-pod in-memory ``running_tasks`` dict and
pod-local SQLite ``emptyDir`` as the authoritative store for in-flight +
completed verifies, so the admission cap/queue survive a restart and are
multi-replica safe (writers take a row lock when advancing state). A
no-verdict job is representable as ``stuck`` so a reconciler can reap it (#464).

Artifacts are referenced by URI in ``artifacts_json`` — never inlined.

Revision ID: d4f6b9e1a3c7
Revises: c3e5a8b1d2f4
Create Date: 2026-06-20
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4f6b9e1a3c7"
down_revision: str | Sequence[str] | None = "c3e5a8b1d2f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "job_states",
        # job_id == TFactory spec/task id (service-assigned). Primary key.
        sa.Column("job_id", sa.String(length=255), primary_key=True, nullable=False),
        sa.Column(
            "schema_version",
            sa.String(length=8),
            nullable=False,
            server_default="1",
        ),
        sa.Column("correlation_key", sa.String(length=255), nullable=True),
        sa.Column(
            "service",
            sa.String(length=32),
            nullable=False,
            server_default="tfactory",
        ),
        sa.Column(
            "kind", sa.String(length=16), nullable=False, server_default="verify"
        ),
        sa.Column(
            "lifecycle_state",
            sa.String(length=16),
            nullable=False,
            server_default="queued",
        ),
        sa.Column("service_status", sa.String(length=64), nullable=True),
        sa.Column("phase", sa.String(length=64), nullable=True),
        sa.Column(
            "attempt", sa.Integer(), nullable=False, server_default="1"
        ),
        sa.Column("admission_json", sa.Text(), nullable=True),
        sa.Column("worker_ref_json", sa.Text(), nullable=True),
        sa.Column("artifacts_json", sa.Text(), nullable=True),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("usage_json", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_job_states_lifecycle_state", "job_states", ["lifecycle_state"]
    )
    op.create_index(
        "ix_job_states_correlation_key", "job_states", ["correlation_key"]
    )


def downgrade() -> None:
    op.drop_index("ix_job_states_correlation_key", table_name="job_states")
    op.drop_index("ix_job_states_lifecycle_state", table_name="job_states")
    op.drop_table("job_states")
