"""One SSRF check for the per-project ``gitBaseUrl`` setting (#1110).

Its own module, rather than a private helper in ``routes/github.py``, because
both readers of the setting need it and the repo's strict bar bans the relative
and function-local imports that sharing it across those two packages would
otherwise need (TID252 / PLC0415). A leaf module they can both import at the top
also keeps ``auto_fix_service`` from importing a routes module for one function.

This adds no SSRF logic: it calls ``url_safety.assert_safe_outbound_url``, the
guard every live web-server call site already uses (cf. #1111, which is about
not growing a second dialect of this check).
"""

from __future__ import annotations

from factory_common.url_safety import assert_safe_outbound_url


def safe_git_base_url(base_url: str | None) -> str | None:
    """SSRF-check the per-project ``gitBaseUrl`` before a credential rides on it (#1110).

    ``gitBaseUrl`` is stored per project and settable through
    ``PATCH /api/projects/{id}/settings``, a route with no ``Depends`` of its own:
    ``TokenAuthMiddleware`` admits any JWT access token, the legacy ``API_TOKEN``
    or an ``api:full`` API key, and there is no role or ownership check beyond
    that. The stored value becomes the provider's ``_base_url``, and every
    provider attaches a real credential to the requests it addresses -- a GitLab
    ``PRIVATE-TOKEN``, a Duo ``Authorization: Bearer``, an Azure DevOps
    Basic-auth PAT, a GitHub bearer token. So a non-operator can steer a
    credentialed outbound request.

    The check sits here, where the untrusted value is read, and not inside
    ``runners/github/providers/factory.py``: that file is the vendored
    factory-github canonical, byte-identical (``2dfd1ced``) across TFactory,
    PFactory, AIFactory and CFactory and gated by a blocking drift check.
    Guarding at TFactory's own trust boundary keeps the canonical undrifted and
    puts the check next to the input it distrusts.

    An earlier version of this docstring added "none of which feed it a stored
    URL". That was wrong: PFactory reads the same setting into the same
    ``_base_url`` from ``routes/github.py``, and was unguarded until
    PFactory#610 closed it with a copy of this module. The placement argument
    does not depend on that claim -- the guard belongs at each repo's own trust
    boundary whether or not the siblings share the exposure -- so the sentence
    is removed rather than corrected in place. AIFactory and CFactory carry the
    canonical but showed no backend reader of the setting; that was a grep, and
    greps under-count, so treat it as unconfirmed.

    ``allow_private=True``: a self-hosted GitLab CE/EE or Azure DevOps Server on
    a LAN is a legitimate target, so refusing RFC-1918 would break real
    deployments. Both postures still refuse the cloud-metadata range, which is
    the one with no legitimate use and a credential-harvesting payoff.

    Returns the checked URL, so the caller is forced to use the value the check
    actually saw rather than re-reading the setting.
    """
    if not base_url:
        return base_url
    assert_safe_outbound_url(base_url, allow_private=True)
    return base_url
