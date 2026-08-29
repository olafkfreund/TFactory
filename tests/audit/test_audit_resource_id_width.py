"""Issue #1252 — the migrated schema must give audit_logs.resource_id 255 chars.

Hermetic companion to ``tests/postgres/test_audit_resource_id_width.py``. That
one proves the row survives a real Postgres round-trip; this one runs with no
services at all, so the width cannot silently regress on a run where Postgres
was unavailable and the acceptance leg skipped.

It asserts on the MIGRATED schema (alembic upgrade head against a scratch
SQLite file), so reverting the migration fails it; the second test pins the ORM
declaration, so reverting the model fails that.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

from tests.secrets.helpers import WEB_SERVER_ROOT

# "<uuid>:pending-<hex8>" — the shape the task pipeline builds.
COMPOSITE_ID_LEN = len(f"{uuid.uuid4()}:pending-{uuid.uuid4().hex[:8]}")


@pytest.mark.audit
@pytest.mark.slow
def test_migrated_resource_id_is_255_wide() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        env = os.environ.copy()
        env["DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path}"
        env.setdefault(
            "KMS_FERNET_KEY",
            "dGVzdC1mZXJuZXQta2V5LWZvci10aGUtcmVncmVzc2lvbi10ZXN0cw==",
        )
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=WEB_SERVER_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, (
            f"alembic upgrade head failed:\n{result.stderr[-2000:]}"
        )

        conn = sqlite3.connect(db_path)
        try:
            cols = {
                row[1]: row[2]
                for row in conn.execute("PRAGMA table_info(audit_logs)").fetchall()
            }
        finally:
            conn.close()

        declared = cols.get("resource_id")
        assert declared is not None, f"audit_logs has no resource_id: {cols}"
        width = int(declared.strip().removeprefix("VARCHAR(").removesuffix(")"))
        assert width >= COMPOSITE_ID_LEN, (
            f"audit_logs.resource_id is VARCHAR({width}); composite task ids are "
            f"{COMPOSITE_ID_LEN} chars and would be truncated on Postgres"
        )
        assert width == 255, f"expected 255 to match resource_type, got {width}"
    finally:
        Path(db_path).unlink(missing_ok=True)


@pytest.mark.audit
def test_model_declares_the_same_width() -> None:
    """The ORM must agree with the migrated schema."""
    if str(WEB_SERVER_ROOT) not in sys.path:
        sys.path.insert(0, str(WEB_SERVER_ROOT))
    from server.database.models import AuditLog

    assert AuditLog.__table__.c.resource_id.type.length == 255
    assert (
        AuditLog.__table__.c.resource_id.type.length
        == AuditLog.__table__.c.resource_type.type.length
    ), "resource_id must match resource_type: both are free-form pointers"
