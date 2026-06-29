"""Drift guard for the completion-envelope schema version (#360).

The completion-envelope schema version lives in two places that MUST agree:

  1. the vendored JSON schema ``contracts/completion-event.schema.json`` (the
     published contract — its ``$id`` and ``title`` encode the version), and
  2. the ``schema_version`` the Triager stamps into every emitted envelope.

Historically each was an independent ``"1.2"`` literal, so a bump to one without
the other drifted silently. ``agents.completion_schema`` now derives the version
from the JSON schema's ``$id`` (single source of truth) and both ``apps/backend``
(the producer) and ``apps/web-server`` (the relay) import that one constant.

These tests assert the schema ``$id``, the schema ``title``, the backend
constant, the Triager's stamped value, AND the web-server's reported value all
report the identical version — so the contract can never drift again.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent.parent / "apps" / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from agents import completion_schema as cs  # noqa: E402


def _schema() -> dict:
    return json.loads(cs.COMPLETION_SCHEMA_PATH.read_text(encoding="utf-8"))


def test_schema_path_exists():
    assert cs.COMPLETION_SCHEMA_PATH.is_file(), cs.COMPLETION_SCHEMA_PATH


def test_version_parsed_from_schema_id():
    """The shared constant is whatever the schema ``$id`` encodes — nothing else."""
    schema_id = _schema()["$id"]
    match = re.search(r"completion-event-([0-9]+(?:\.[0-9]+)*)\.json$", schema_id)
    assert match, f"schema $id must encode a version: {schema_id!r}"
    assert cs.COMPLETION_SCHEMA_VERSION == match.group(1)
    assert cs.schema_version() == match.group(1)


def test_schema_title_matches_version():
    """The human-readable ``title`` carries the same ``vX.Y`` — guards a manual
    edit to one but not the other inside the schema file itself."""
    title = _schema().get("title", "")
    assert f"v{cs.COMPLETION_SCHEMA_VERSION}" in title, (
        f"schema title {title!r} must mention v{cs.COMPLETION_SCHEMA_VERSION}"
    )


# #471 cutover: the Triager no longer stamps ``schema_version`` into the envelope
# (the legacy field was dropped), so the two tests that asserted the emitted value
# (``test_triager_constant_is_the_shared_source_of_truth`` and
# ``test_emitted_envelope_schema_version_matches_schema``) were removed. The schema
# ``$id`` version + the web-server's reported version remain the source of truth,
# guarded by the tests below.


def test_both_apps_report_the_same_schema_version():
    """apps/backend and apps/web-server must report the identical schema version.

    The web-server imports the backend's single-source-of-truth constant rather
    than re-declaring a literal; this asserts the two never drift. The web-server
    module is imported via ``importlib`` (not a top-level ``from server ...``) so
    this single backend-runnable test does not get dropped by the conftest
    web-server skip rule — and the rest of this file's assertions always run."""
    import importlib

    _WEB_SERVER_DIR = Path(__file__).resolve().parent.parent / "apps" / "web-server"
    if str(_WEB_SERVER_DIR) not in sys.path:
        sys.path.insert(0, str(_WEB_SERVER_DIR))

    relay = importlib.import_module("server.background.completion_relay")

    assert relay.completion_schema_version() == cs.COMPLETION_SCHEMA_VERSION
