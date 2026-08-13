"""The vendored log sanitizer must stop a forged log record (CWE-117).

CodeQL reported 181 ``py/log-injection`` sinks in ``apps/web-server/server``: a
task id, project id, org id or a line of coder stderr goes straight into an
f-string log message. A newline in any of those writes the attacker's own record
into the server log, which is what the audit trail is read from.

The fix wraps those values in ``factory_common.logsafe.sanitize_log`` (the hub
canonical, vendored at ``apps/web-server/factory_common/``). This test is the
behaviour lock: it drives a real ``logging`` handler and asserts the forged
record is NOT emitted. Break the sanitizer - let a raw newline through - and it
goes red.
"""

from __future__ import annotations

import io
import logging
import sys
from pathlib import Path

_WEB_SERVER = Path(__file__).resolve().parents[1]
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))

from factory_common.logsafe import sanitize_log  # noqa: E402

# What an attacker puts in a task id / project id to forge an audit record.
FORGED_TASK_ID = "spec-001\nWARNING:server.audit:api key revoked by admin"


def _emit(task_id: str) -> list[str]:
    """Log a task id exactly as the routes do; return the emitted records."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s"))
    logger = logging.getLogger("server.tests.logsafe")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    logger.info("[StartTask] ===== START ENDPOINT CALLED ===== task_id: %s", task_id)
    handler.flush()
    return stream.getvalue().splitlines()


def test_raw_value_forges_a_record() -> None:
    """Precondition: without the sanitizer the attack works."""
    records = _emit(FORGED_TASK_ID)
    assert len(records) == 2
    assert records[1] == "WARNING:server.audit:api key revoked by admin"


def test_sanitized_value_cannot_forge_a_record() -> None:
    records = _emit(sanitize_log(FORGED_TASK_ID))
    assert len(records) == 1
    assert not any(r.startswith("WARNING:server.audit:") for r in records)
    # The payload is still there, inert and greppable - debuggability preserved.
    assert records[0].endswith(
        "task_id: spec-001\\nWARNING:server.audit:api key revoked by admin"
    )


def test_real_identifiers_are_not_mangled() -> None:
    """A sanitizer that ruins normal log lines is worse than the alert."""
    for value in (
        "spec-042-add-login:1",
        "/home/dev/projects/api/.tfactory/specs/spec-042",
        "org_7f3a-9c11",
        "Traceback (most recent call last): ValueError('bad id')",
    ):
        assert sanitize_log(value) == value
