"""TFactory #829: a configured tenant token must reach the GitHub REST provider.

Both provider-selection sites passed the tenant token as ``_token``. The gh-CLI
``GitHubProvider`` has no such field, so that raised ``TypeError``; and because
the vendored factory selects the REST provider on the presence of ``token``,
``_token`` also failed to select it at all.

The second assertion in each test is the one that matters: asserting only "a
provider came back carrying the token" would still pass if the token were
silently dropped in favour of the ambient ``gh`` login. Requesting a tenant's
repo as whoever the pod happens to be logged in as is a wrong-identity call, not
a degraded one, so the gh-CLI provider must NOT be returned when a token is set.
"""

# ruff: noqa: S101
# The shared bar already exempts tests from S101 (standards/ruff.toml
# per-file-ignores: "**/tests/**"), but scripts/ratchet_lint.py lints a COPY of
# each changed file under a temp name (``tmpXXXX__<basename>.py``) that those
# globs no longer match — so every net-new test file trips S101 under the
# ratchet only. Exempted explicitly rather than contorting the assertions.
#
# Note the catch-22 this creates: under the REAL path S101 is already ignored,
# so RUF100 reads this directive as unused. Nothing enforces that today (the
# ratchet is the only ruff gate covering apps/web-server, and it lints the temp
# name where the directive IS used), but a future whole-repo strict run would
# see it. Tracked as #834; drop this line once the ratchet preserves the
# filename it lints under.

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

_WEB_SERVER = Path(__file__).resolve().parents[1]
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))

_BACKEND = _WEB_SERVER.parent / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# ``runners.*`` resolves at runtime via the sys.path insert above, but not from
# mypy's import base (apps/web-server) — the same unresolvable import the
# production modules under test already carry.
from runners.github.providers.github_provider import (  # type: ignore[import-not-found]  # noqa: E402
    GitHubProvider,
)
from runners.github.providers.http_github_provider import (  # type: ignore[import-not-found]  # noqa: E402
    HttpGitHubProvider,
)
from server.routes.github import _get_project_provider  # noqa: E402
from server.services.auto_fix_service import _provider_for  # noqa: E402

# Not a real credential — a marker value, asserted on identity and never used
# to authenticate against anything.
_TENANT_CRED = "ghp-tenant-cred-829"

# Both call sites resolve ``load_projects`` to this same module attribute.
_LOAD_PROJECTS = "server.routes.projects.load_projects"

_SELECTORS = [
    pytest.param(_get_project_provider, id="routes.github"),
    pytest.param(_provider_for, id="services.auto_fix"),
]


def _install_project(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, settings: dict[str, Any]
) -> None:
    """Point both call sites at a single fake project with the given settings."""
    project = {"path": str(tmp_path), "settings": settings}
    monkeypatch.setattr(_LOAD_PROJECTS, lambda: {"p1": project})


@pytest.mark.parametrize("select", _SELECTORS)
def test_configured_token_selects_rest_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, select: Any
) -> None:
    _install_project(
        monkeypatch, tmp_path, {"gitProvider": "github", "gitToken": _TENANT_CRED}
    )

    provider = select("p1")

    # (1) the token reaches a REST provider instead of raising TypeError
    assert isinstance(provider, HttpGitHubProvider)
    assert provider._token == _TENANT_CRED

    # (2) AMBIENT CANNOT SUBSTITUTE — a gh-CLI provider here would run as the
    # pod's ambient login and ignore the tenant credential entirely. Asserted
    # against the shell-out seam (``_gh_client``) as well as the class, so it
    # keeps its teeth even if the provider hierarchy is later refactored.
    assert not isinstance(provider, GitHubProvider)
    assert getattr(provider, "_gh_client", None) is None


@pytest.mark.parametrize("select", _SELECTORS)
def test_unconfigured_token_still_falls_back_to_ambient_gh_cli(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, select: Any
) -> None:
    """No token configured is a real 'use the ambient gh login' request."""
    _install_project(monkeypatch, tmp_path, {"gitProvider": "github"})

    provider = select("p1")

    assert isinstance(provider, GitHubProvider)
    assert not isinstance(provider, HttpGitHubProvider)
