#!/usr/bin/env python3
"""The lint ratchet applies the test assert bar to tests, and only to tests.

Factory#510. The ratchet linted a temp COPY of each changed file, and ruff
relativises a path against the project root before matching per-file-ignores —
so a path outside that root falls back to matching the BASENAME only. Two of
standards/ruff.toml's three carve-outs therefore worked under the gate
(``**/test_*.py``, ``**/*_test.py``) and one could never match at all
(``**/tests/**``). A helper under ``tests/`` named neither way was held to the
production assert bar by the gate while ``ruff check`` on the real tree exempted
it: two tools disagreeing about what a test is, the mismatch the shared
``is_test_file`` was extracted to prevent (Factory#403).

Latent in this repo rather than biting — measured, the three in-scope helpers
under a ``tests/`` dir (all under apps/web-server) use no asserts today, so the
count was 0 either way. In PFactory the identical defect blocked commits. A gate
that is wrong and currently unexercised is still wrong, and this is the test
that keeps it from becoming exercised silently.

The fix is not a better temp path: mirroring the directories inside the temp dir
(``<tmpdir>/tests/helpers.py``) still reports S101, measured. Ruff is told the
file's REAL repo-relative path via ``--stdin-filename`` and gets the source on
stdin, so there is no copy to misjudge.

The second test is the one with teeth: exempting unconditionally — or losing the
path in a way that made everything look like a test — passes the first and
silently drops S101 for the whole repo.
"""

from __future__ import annotations

import ratchet_helpers as rh

# scripts/ is put on sys.path by tests/conftest.py.
import ratchet_lint as rl

_ASSERTION = "x = 1\nassert x == 2\n"


def test_ruff_exempts_every_shape_of_test_path() -> None:
    named = rl.ruff_counts(_ASSERTION, "apps/web-server/tests/test_x.py")
    helper = rl.ruff_counts(_ASSERTION, "apps/web-server/tests/verify_performance.py")
    assert "S101" not in helper, "a helper under tests/ must get the test assert carve-out"
    assert helper == named, "two files under tests/ must not get two different verdicts"


def test_ruff_still_holds_production_to_the_assert_bar() -> None:
    # THE ASSERTION WITH TEETH.
    assert "S101" in rl.ruff_counts(_ASSERTION, "apps/backend/prod.py")


def test_the_ruff_rule_lives_in_the_canonical_module() -> None:
    """The ratchet must CONSUME the shared rules, not carry its own copy."""
    for name in ("is_test_file", "ruff_stdin_argv", "MYPY_TEST_RELAX"):
        assert getattr(rl, name) is getattr(rh, name), f"{name} is not the canonical object"
