"""The ratchet's vendored-skip predicate must skip the mirrors and nothing else.

A predicate that over-matches silently disables the ruff/mypy no-regression gate
for real source, which looks identical to "the gate passed".
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from ratchet_lint import is_vendored  # noqa: E402


def test_skips_exact_vendored_files():
    assert is_vendored("apps/backend/agents/verification_gate.py")
    assert is_vendored("apps/backend/tools/runners/nix_provisioner.py")
    assert is_vendored("apps/backend/tools/runners/artifact_store.py")


def test_skips_the_factory_github_mirror():
    assert is_vendored("apps/backend/runners/github/gh_client.py")
    assert is_vendored("apps/backend/runners/github/providers/gitlab_provider.py")


def test_does_not_skip_repo_owned_source():
    assert not is_vendored("apps/backend/runners/github_helper.py")
    assert not is_vendored("apps/backend/runners/triager.py")
    assert not is_vendored("apps/backend/main.py")
    assert not is_vendored("scripts/ratchet_lint.py")
