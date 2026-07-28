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

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_WEB_SERVER = Path(__file__).resolve().parents[1]
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))

_BACKEND = _WEB_SERVER.parent / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from runners.github.providers.github_provider import GitHubProvider  # noqa: E402
from runners.github.providers.http_github_provider import (  # noqa: E402
    HttpGitHubProvider,
)
from server.routes.github import _get_project_provider  # noqa: E402
from server.services.auto_fix_service import _provider_for  # noqa: E402

_TOKEN = "ghp_tenant_token_829"


def _install_project(monkeypatch, settings: dict) -> None:
    """Point both call sites at a single fake project with the given settings."""
    project = {"path": "/tmp/tfactory-829", "settings": settings}
    monkeypatch.setattr(
        "server.routes.projects.load_projects",
        lambda: {"p1": project},
    )


@pytest.mark.parametrize(
    "select",
    [
        pytest.param(_get_project_provider, id="routes.github"),
        pytest.param(_provider_for, id="services.auto_fix"),
    ],
)
def test_configured_token_selects_rest_provider(monkeypatch, select):
    _install_project(monkeypatch, {"gitProvider": "github", "gitToken": _TOKEN})

    provider = select("p1")

    # (1) the token reaches a REST provider instead of raising TypeError
    assert isinstance(provider, HttpGitHubProvider)
    assert provider._token == _TOKEN

    # (2) AMBIENT CANNOT SUBSTITUTE — a gh-CLI provider here would run as the
    # pod's ambient login and ignore the tenant credential entirely. Asserted
    # against the shell-out seam (`_gh_client`) rather than the class alone, so
    # it keeps its teeth even if the provider hierarchy is later refactored.
    assert not isinstance(provider, GitHubProvider)
    assert getattr(provider, "_gh_client", None) is None


@pytest.mark.parametrize(
    "select",
    [
        pytest.param(_get_project_provider, id="routes.github"),
        pytest.param(_provider_for, id="services.auto_fix"),
    ],
)
def test_unconfigured_token_still_falls_back_to_ambient_gh_cli(monkeypatch, select):
    """No token configured is a real 'use the ambient gh login' request."""
    _install_project(monkeypatch, {"gitProvider": "github"})

    provider = select("p1")

    assert isinstance(provider, GitHubProvider)
    assert not isinstance(provider, HttpGitHubProvider)
