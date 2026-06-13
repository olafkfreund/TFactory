"""Single source of truth for the completion-envelope schema version (#360).

The RFC-0001 / CloudEvents completion envelope has its version declared in two
places that MUST agree but historically drifted:

  1. the vendored JSON schema ``contracts/completion-event.schema.json`` (the
     *published* contract CFactory and the sibling factories validate against),
     whose ``$id`` ends in ``...completion-event-<version>.json``;
  2. a Python literal (``_COMPLETION_SCHEMA_VERSION``) that the Triager stamps
     into every emitted envelope's ``schema_version`` field.

If (2) is bumped without (1) — or vice versa — CFactory sees a ``schema_version``
that disagrees with the ``$id`` it validated, and the drift is silent.

This module makes the **JSON schema the single source of truth**: the version is
parsed from its ``$id`` at import time, and both ``apps/backend`` (the Triager,
producer) and ``apps/web-server`` (the relay, which imports this module via the
shared ``agents`` package) read the same constant. There is no second literal to
forget to bump — change the schema's ``$id`` and the Python side follows.

A test (``tests/test_completion_schema_version.py``) asserts the schema ``$id``,
the schema ``title``, and this constant all report the identical version, so the
contract can never silently drift again.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

# The vendored published contract — the one source of truth for the version.
COMPLETION_SCHEMA_PATH = (
    Path(__file__).resolve().parent.parent
    / "contracts"
    / "completion-event.schema.json"
)

# ``$id`` shape: https://factory.dev/schemas/rfc-0001/completion-event-<ver>.json
_ID_VERSION_RE = re.compile(r"completion-event-(?P<version>[0-9]+(?:\.[0-9]+)*)\.json$")


@lru_cache(maxsize=1)
def _schema() -> dict:
    """Load and cache the vendored completion-event JSON schema."""
    return json.loads(COMPLETION_SCHEMA_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def schema_version() -> str:
    """Return the completion-envelope schema version parsed from the schema ``$id``.

    Raises ``ValueError`` if the vendored schema's ``$id`` does not carry a
    parseable ``completion-event-<version>.json`` suffix — a loud failure is
    intended, since a missing/garbled ``$id`` is exactly the drift we guard
    against and must never pass silently.
    """
    schema_id = _schema().get("$id", "")
    match = _ID_VERSION_RE.search(schema_id)
    if not match:
        raise ValueError(
            "completion-event.schema.json $id does not encode a version "
            f"(got {schema_id!r}); cannot derive the schema version"
        )
    return match.group("version")


def schema_id() -> str:
    """Return the vendored schema's ``$id`` (the canonical contract identifier)."""
    return _schema().get("$id", "")


# Module-level constant for ergonomic imports. The JSON schema is authoritative;
# this is just the parsed version both apps stamp into / assert on envelopes.
COMPLETION_SCHEMA_VERSION: str = schema_version()
