"""Portal-managed project workspaces (#82 PR-A).

When TFactory runs on a developer laptop, the user's git repo lives on
the same filesystem as the portal and the existing ``POST /api/projects
{path}`` route just registers that directory. That model breaks for
every other deployment shape:

- **Single-user VPS** — repo is on the laptop, portal on the VPS, no
  shared filesystem.
- **Kubernetes** — portal pod has no view into the user's machine.
- **Shared/SaaS** — the path concept doesn't even map.

This service backs the alternative path: the portal accepts a Git URL
and clones it into a local workspace directory. The workspace root is
configurable via ``PROJECT_WORKSPACE_ROOT`` (defaults to
``~/.tfactory/workspaces/`` on laptop installs, expected to be a
mounted PVC in K8s installs). The returned path is what the rest of
TFactory (Auto-Fix, agent_service, etc.) uses as the project's
on-disk root — they don't need to know whether the project was added
via path or URL.

Auth in PR-A is whatever the host's git config already provides —
i.e. public HTTPS URLs and SSH keys configured in ``~/.ssh/``. Stored
git credentials (Deploy Keys, GitHub App install IDs, PATs) land in
PR-C.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import stat
import tempfile
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlparse

from ..routes._specpath import safe_component

logger = logging.getLogger(__name__)

DEFAULT_WORKSPACE_ROOT = Path.home() / ".tfactory" / "workspaces"

# Default git operation timeout — long enough for a fresh clone of a
# medium-sized repo over a slow link, short enough that a hung remote
# doesn't lock up the portal forever.
DEFAULT_GIT_TIMEOUT_SECONDS = 600


def workspace_root() -> Path:
    """Resolve the directory under which all portal-managed clones live.

    Looks at ``PROJECT_WORKSPACE_ROOT`` env first (the K8s/SaaS path),
    falls back to ``~/.tfactory/workspaces/`` (laptop path).
    """
    env = os.environ.get("PROJECT_WORKSPACE_ROOT")
    if env:
        return Path(env).expanduser()
    return DEFAULT_WORKSPACE_ROOT


def slug_from_git_url(git_url: str) -> str:
    """Turn a git URL into a filesystem-safe directory slug.

    ``git@github.com:olaf/TFactory.git`` → ``olaf-TFactory``
    ``https://github.com/olaf/TFactory.git`` → ``olaf-TFactory``
    ``https://gitlab.com/group/sub/repo`` → ``group-sub-repo``

    The slug is used as the directory name under ``workspace_root()``.
    """
    # SCP-style ("git@host:owner/repo.git") — split on the colon, drop the host.
    if git_url.startswith("git@") and ":" in git_url:
        _, path = git_url.split(":", 1)
    else:
        parsed = urlparse(git_url)
        path = parsed.path.lstrip("/")
    # Strip .git suffix + lowercase + replace path separators with hyphens.
    if path.endswith(".git"):
        path = path[:-4]
    # Replace any non-alnum/hyphen char with hyphen; collapse repeats.
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", path).strip("-")
    return slug or "workspace"


def _inject_credential(git_url: str, username: str) -> str:
    """Rewrite an HTTPS git URL to carry the *username only* (#82 PR-C).

    ``https://github.com/owner/repo.git`` →
    ``https://oauth2@github.com/owner/repo.git``

    The token is deliberately NOT embedded in the URL: a URL passed to
    ``git`` becomes an argv element, readable by any process via
    ``/proc/<pid>/cmdline`` for the duration of the clone (security
    review H1). The password is supplied out-of-band via ``GIT_ASKPASS``
    (see :func:`_git_askpass_env`); git asks the helper for the password
    because the URL carries a username but no password.

    SSH URLs (``git@host:...``) are returned unchanged — they auth via
    keys, not URLs; stored Deploy Keys are a separate path (out of
    scope for V1 of PR-C).
    """
    if not git_url.startswith("https://"):
        return git_url
    rest = git_url[len("https://") :]
    return f"https://{username}@{rest}"


# Tiny POSIX askpass helper. git invokes it as ``<script> "<prompt>"`` and
# reads the answer from stdout. We branch on the prompt: git asks for the
# username first ("Username for '...'"), then the password. Both values come
# from the environment (``GIT_USER`` / ``GIT_PASS``) — never argv — so the
# token never appears in any process command line.
_GIT_ASKPASS_SCRIPT = """#!/bin/sh
case "$1" in
  Username*) printf '%s' "$GIT_USER" ;;
  *)         printf '%s' "$GIT_PASS" ;;
esac
"""


@contextlib.contextmanager
def _git_askpass_env(username: str, token: str) -> Iterator[dict[str, str]]:
    """Yield env vars that feed a git credential via ``GIT_ASKPASS``.

    Writes the askpass helper to a ``0700`` temp file and points
    ``GIT_ASKPASS`` at it. The token travels in ``GIT_PASS`` (read by the
    script), so it never lands in argv or in git's persisted config. The
    script is removed when the context exits.
    """
    handle = tempfile.NamedTemporaryFile(
        mode="w", prefix="git-askpass-", suffix=".sh", delete=False
    )
    try:
        handle.write(_GIT_ASKPASS_SCRIPT)
        handle.close()
        os.chmod(handle.name, stat.S_IRWXU)  # 0700 — owner-only rwx
        yield {
            "GIT_ASKPASS": handle.name,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_USER": username,
            "GIT_PASS": token,
        }
    finally:
        try:
            os.unlink(handle.name)
        except OSError:
            pass


_ALLOWED_GIT_URL_SCHEMES = ("https://", "http://", "ssh://", "git://")
_SCP_LIKE_GIT_URL = re.compile(r"^[A-Za-z0-9._-]+@[A-Za-z0-9._-]+:")
_SAFE_GIT_REF = re.compile(r"[\w./@+-]+")


def validate_git_url(git_url: str) -> str:
    """Reject a clone URL that could trigger git transport-helper RCE (review C1).

    Allows only standard remote transports (https/http/ssh/git) and scp-like
    ``user@host:path``; rejects ``ext::``/other ``::`` transport helpers,
    ``file://`` (local-repo read), and any leading-dash (option-injection) value.
    Returns the validated URL; raises ``GitOperationError`` otherwise.
    """
    url = (git_url or "").strip()
    if not url or url.startswith("-") or "::" in url:
        raise GitOperationError(f"Disallowed git URL: {git_url!r}")
    if url.startswith(_ALLOWED_GIT_URL_SCHEMES) or _SCP_LIKE_GIT_URL.match(url):
        return url
    raise GitOperationError(f"Disallowed git URL scheme: {git_url!r}")


def _validate_git_ref(ref: str) -> str:
    """Reject an option-like / out-of-charset git ref before it becomes argv."""
    if ref.startswith("-") or not _SAFE_GIT_REF.fullmatch(ref):
        raise GitOperationError(f"Invalid git ref: {ref!r}")
    return ref


# Per-workspace clone locks (#806): two concurrent first-ingests of the same
# project both clone+set-url into the same dir and collide on the git config lock
# ("could not lock config file .git/config"), 500-ing one. Serialize per
# workspace path so the second caller waits, then finds the clone present and
# takes the idempotent fetch path. In-process is sufficient: the workspaces PVC is
# ReadWriteOnce, so a single pod owns the clone dir.
_clone_locks: dict[str, asyncio.Lock] = {}


def _clone_lock_for(workspace: Path) -> asyncio.Lock:
    # setdefault is atomic across the single-threaded event loop (no await here).
    return _clone_locks.setdefault(str(workspace), asyncio.Lock())


async def clone_or_update(
    git_url: str,
    branch: str | None = None,
    slug: str | None = None,
    *,
    root: Path | None = None,
    timeout_seconds: float = DEFAULT_GIT_TIMEOUT_SECONDS,
    credential: tuple[str, str] | None = None,
) -> Path:
    """Clone the repo into the workspace root, or fast-forward an existing clone.

    Args:
        git_url: HTTPS or SSH URL to the repository.
        branch: Optional branch to checkout after clone. ``None`` uses
            the remote's HEAD.
        slug: Optional override for the workspace directory name.
            Defaults to ``slug_from_git_url(git_url)``.
        root: Optional override for the workspace root. Defaults to
            ``workspace_root()`` (PROJECT_WORKSPACE_ROOT env or
            ``~/.tfactory/workspaces/``).
        timeout_seconds: Per-operation timeout.
        credential: Optional ``(username, token)`` tuple. When provided
            and ``git_url`` is HTTPS, the credential is injected into
            the URL for the network operation only — never persisted to
            git's config (the workspace dir gets a sanitized origin
            via ``git remote set-url`` after the fetch). Use this with
            credentials from the ``git_credentials`` table (#82 PR-C).

    Returns:
        Absolute path to the local clone.

    Raises:
        GitOperationError: On any non-zero ``git`` exit code or timeout.
    """
    # Reject transport-helper / option-injection clone URLs before any git op
    # (review C1: ext::sh -c ... RCE). Defense in depth with GIT_ALLOW_PROTOCOL.
    git_url = validate_git_url(git_url)
    if branch is not None:
        branch = _validate_git_ref(branch)
    # The directory name derives from the request-supplied git URL (or an
    # explicit slug override); confirm it is a single literal path component so
    # it can't escape the workspace root (py/path-injection).
    workspace = (root or workspace_root()) / safe_component(
        slug or slug_from_git_url(git_url)
    )
    workspace.parent.mkdir(parents=True, exist_ok=True)

    # Build the URL that actually gets passed to ``git`` for network ops, plus
    # the credential env. The token is NEVER embedded in the URL/argv (security
    # review H1): the URL carries only the username and the password is fed via
    # GIT_ASKPASS, so it can't be read from ``/proc/<pid>/cmdline``.
    # ``credential`` is the secret material — never log it.
    fetch_url = git_url
    askpass_ctx: contextlib.AbstractContextManager[dict[str, str]]
    if credential is not None:
        username, token = credential
        fetch_url = _inject_credential(git_url, username)
        askpass_ctx = _git_askpass_env(username, token)
    else:
        askpass_ctx = contextlib.nullcontext({})

    async with _clone_lock_for(workspace):
        with askpass_ctx as cred_env:
            if (workspace / ".git").is_dir():
                # Existing clone — fetch + reset/fast-forward.
                # For credentialed pulls, point origin at the username-only URL
                # FOR THIS OPERATION ONLY, then restore the bare origin afterwards.
                if credential is not None:
                    await _run_git(
                        ["remote", "set-url", "origin", fetch_url],
                        cwd=workspace,
                        timeout=timeout_seconds,
                    )
                try:
                    await _run_git(
                        ["fetch", "--prune", "origin"],
                        cwd=workspace,
                        timeout=timeout_seconds,
                        extra_env=cred_env,
                    )
                    if branch:
                        await _run_git(
                            ["checkout", branch],
                            cwd=workspace,
                            timeout=timeout_seconds,
                        )
                    await _run_git(
                        ["pull", "--ff-only"],
                        cwd=workspace,
                        timeout=timeout_seconds,
                        extra_env=cred_env,
                    )
                finally:
                    if credential is not None:
                        # Restore origin to the bare URL so even the username
                        # doesn't linger in ``.git/config``.
                        try:
                            await _run_git(
                                ["remote", "set-url", "origin", git_url],
                                cwd=workspace,
                                timeout=timeout_seconds,
                            )
                        except GitOperationError:
                            pass
                logger.info("[workspace] pulled latest into %s", workspace)
                return workspace

            # Fresh clone. ``--`` ends option parsing so a hostile URL/dir starting
            # with ``-`` can't be read as a git flag (review C1 arg-injection).
            cmd = ["clone"]
            if branch:
                cmd.extend(["--branch", branch])
            cmd.extend(["--", fetch_url, str(workspace)])
            await _run_git(
                cmd, cwd=workspace.parent, timeout=timeout_seconds, extra_env=cred_env
            )
            if credential is not None:
                # Strip the credential username from origin so it isn't persisted
                # in the workspace's ``.git/config``.
                try:
                    await _run_git(
                        ["remote", "set-url", "origin", git_url],
                        cwd=workspace,
                        timeout=timeout_seconds,
                    )
                except GitOperationError:
                    pass
            logger.info("[workspace] cloned %s → %s", git_url, workspace)
            return workspace


class GitOperationError(RuntimeError):
    """Raised when a git operation fails or times out."""


async def _run_git(
    args: list[str],
    *,
    cwd: Path,
    timeout: float,
    extra_env: dict[str, str] | None = None,
) -> str:
    """Run ``git <args>`` with a timeout. Returns stdout on success.

    ``extra_env`` is merged on top of the base environment — used to thread
    the ``GIT_ASKPASS`` credential vars (security review H1) into the network
    operations without ever putting the token on the command line.
    """
    cmd = ["git", *args]
    logger.debug("[workspace] running: git %s (cwd=%s)", " ".join(args), cwd)
    # Defense in depth against a malicious clone URL (Factory security review C1):
    # restrict git's transports so the ``ext::`` / transport-helper RCE vector
    # (e.g. ``git clone 'ext::sh -c ...'``) is refused even if URL validation is
    # bypassed. Only the standard fetch transports are allowed.
    env = {**os.environ, "GIT_ALLOW_PROTOCOL": "https:http:ssh:git:file"}
    if extra_env:
        env.update(extra_env)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(cwd),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as e:
        raise GitOperationError(f"git executable not found on PATH: {e}") from e

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError as e:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        raise GitOperationError(
            f"git {' '.join(args)} timed out after {timeout}s"
        ) from e

    if proc.returncode != 0:
        raise GitOperationError(
            f"git {' '.join(args)} failed (exit {proc.returncode}): "
            f"{stderr.decode('utf-8', 'replace').strip() or 'no stderr'}"
        )
    return stdout.decode("utf-8", "replace")
