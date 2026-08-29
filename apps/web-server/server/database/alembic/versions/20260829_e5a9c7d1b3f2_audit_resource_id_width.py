"""audit_logs.resource_id holds any resource's id, not a UUID

Revision ID: e5a9c7d1b3f2
Revises: d4f6b9e1a3c7
Create Date: 2026-08-29

``resource_id`` was declared ``String(36)`` -- exactly a UUID's length, copied
from the ``id`` columns beside it. But it carries NO foreign key and sits next
to ``resource_type: String(255)``: it is a free-form pointer to a row in
whichever table ``resource_type`` names, and those tables do not all use UUID
keys.

The task pipeline builds a composite id, ``"{project_id}:{spec_slug}"``, which
is 53+ characters for a GitHub-backed project. Every audited task action
therefore failed with StringDataRightTruncationError -- and because
``audit_service`` catches the failure and logs it at WARNING, the API had
already returned success. The caller was told the action happened while no
audit row had been written.

Unlike PFactory (where the table was empty, and the emptiness was itself the
evidence), ``audit_logs`` here holds rows: the log looks populated while an
entire CLASS of action is silently absent. With ``prev_hash``/``entry_hash`` on
the table, that is a broken chain, not just a missing log line.

Widening to 255 matches ``resource_type`` and is a pure relaxation -- no
existing value can fail to fit, so the downgrade is only safe while every
stored value is short enough, which it asserts rather than assumes.

Ported from PFactory b8e1f4c7a2d9 (2026-08-19).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5a9c7d1b3f2"
down_revision: str | None = "d4f6b9e1a3c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # batch_alter_table for SQLite portability -- SQLite has no ALTER COLUMN, so
    # a bare op.alter_column is a syntax error there while being fine on
    # Postgres. The test suite migrates against SQLite. Same pattern as
    # c6e3b2d4a8f0.
    with op.batch_alter_table("audit_logs") as batch:
        batch.alter_column(
            "resource_id",
            existing_type=sa.String(36),
            type_=sa.String(255),
            existing_nullable=True,
        )


def downgrade() -> None:
    # Refuse rather than truncate: narrowing silently destroys audit references.
    conn = op.get_bind()
    too_long = conn.execute(
        sa.text("SELECT count(*) FROM audit_logs WHERE length(resource_id) > 36")
    ).scalar_one()
    if too_long:
        raise RuntimeError(
            f"{too_long} audit_logs row(s) have resource_id longer than 36 chars; "
            "narrowing would truncate audit references. Resolve those rows first."
        )
    with op.batch_alter_table("audit_logs") as batch:
        batch.alter_column(
            "resource_id",
            existing_type=sa.String(255),
            type_=sa.String(36),
            existing_nullable=True,
        )
