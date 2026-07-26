"""The task contract's provider-qualified repo reference (RFC-0020 3.5, Factory#366).

**The bug this closes.** A GitLab tenant's PARR run ended on GitHub. TFactory's
share of that is the verdict: the Triager posts its report to the pull request
through ``tools/pr_comment.py``, which shells out to ``gh pr comment``. Nothing
asked which host the work was on, so a GitLab tenant's verify either posted
nowhere or errored out against a repo slug ``gh`` could not resolve — after the
verification had already run and produced a report worth reading.

The contract already carried a repo reference. Since phase 5 that reference may
be **provider-qualified**::

    owner/repo                              -> ("github", "owner/repo")
    gitlab:group/subgroup/project           -> ("gitlab", "group/subgroup/project")
    azure_devops:org/project/repo           -> ("azure_devops", "org/project/repo")

Three rules, and they are the whole contract:

1. **GitHub is the unqualified default.** An unqualified reference reads as
   ``github``, so every pre-phase-5 spec and every GitHub spec is unchanged and
   nothing needs backfilling.
2. **Only a KNOWN provider is a qualification.** A clone URL contains a colon
   and must survive intact; an unrecognised prefix is part of the path.
3. **The reference is not a credential.** It says WHERE the code lives.

Deliberately not in ``runners/github/`` — that tree is a byte-for-byte vendored
copy of the hub's canonical provider layer behind a drift gate, this is
contract-reading code rather than a VCS client, and putting it there would mean
a hub change plus four re-vendors plus four pinned-SHA bumps to ship a
twelve-line parser.
"""

from __future__ import annotations

# The providers this fleet implements. Bitbucket and Gitea are declared in the
# canonical ProviderType and unimplemented.
GITHUB = "github"
GITLAB = "gitlab"
AZURE_DEVOPS = "azure_devops"
SUPPORTED_PROVIDERS: tuple[str, ...] = (GITHUB, GITLAB, AZURE_DEVOPS)


def parse_repo_ref(ref: str | None) -> tuple[str, str] | None:
    """``(provider, project)`` for a repo reference, or ``None`` if there is none."""
    value = (ref or "").strip()
    if not value:
        return None
    head, sep, tail = value.partition(":")
    if sep and head.strip().lower() in SUPPORTED_PROVIDERS and tail.strip():
        return head.strip().lower(), tail.strip()
    return GITHUB, value


def provider_of(ref: str | None) -> str:
    """Which host ``ref`` names. Unqualified, absent or a URL all read GitHub."""
    parsed = parse_repo_ref(ref)
    return parsed[0] if parsed else GITHUB


def project_of(ref: str | None) -> str:
    """The bare project path, with any qualification stripped.

    What ``gh pr comment -R`` takes: ``owner/repo``, never ``gitlab:owner/repo``.
    """
    parsed = parse_repo_ref(ref)
    return parsed[1] if parsed else ""


def qualify_repo(provider: str | None, project: str | None) -> str:
    """``project`` tagged with its host — the inverse of :func:`parse_repo_ref`."""
    if not project:
        return ""
    kind = (provider or GITHUB).strip().lower()
    return project if kind == GITHUB else f"{kind}:{project}"


def is_github(provider: str | None) -> bool:
    """True when ``provider`` is GitHub, so a ``gh``-CLI path may run.

    Phrased positively on purpose: a new caller has to opt IN to the ``gh`` CLI
    rather than remembering to exclude two hosts, and silently running against a
    third that gets added later.
    """
    return (provider or GITHUB).strip().lower() == GITHUB
