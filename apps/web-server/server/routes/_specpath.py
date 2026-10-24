"""Safe filesystem-path construction from request-supplied identifiers.

Spec and task identifiers arrive from request URLs and are attacker-controlled.
Joining one straight onto a base directory lets a value like ``../../etc``
escape the intended root -- a path-traversal vulnerability (CodeQL
``py/path-injection``). ``safe_spec_dir`` validates the identifier before it ever
reaches the filesystem, so every caller that routes through it is safe by
construction.

The value that actually reaches the filesystem is the result of
``os.path.basename`` -- a normalization CodeQL recognizes as stripping every
directory component, so the taint is cleared at the source and propagates to
callers. We additionally *reject* (rather than silently strip) any input that
isn't already a bare component, so a traversal attempt fails loudly with HTTP
400 instead of being quietly rewritten.
"""

from __future__ import annotations

import contextlib
import os
import re
from collections.abc import Iterable
from pathlib import Path

from fastapi import HTTPException

# The slug charset shared by every request-supplied id in this app (project_id,
# spec_id, test_id, framework/template name). Kept here rather than re-declared
# per route: it was copy-pasted into seven modules, and each copy carried the
# same hole (#866).
_SLUG_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def is_safe_slug(value: str) -> bool:
    """True when *value* is a slug that is also a real single path component.

    The charset alone does not guard. ``.`` and ``..`` are both full matches and
    both traverse once interpolated into a path -- ``<root>/..`` is the parent
    of the root -- so they are excluded explicitly. Multi-segment traversal is
    already impossible without ``/``, which the charset omits.

    This is a predicate, not a raiser, because the callers do not share a
    failure mode: most answer HTTP 400 (:func:`safe_slug`), the badge endpoint
    answers a grey "no data" image, and the MCP lookup answers ``None``.
    """
    return value not in (".", "..") and bool(_SLUG_RE.match(value))


def safe_slug(value: str, detail: str) -> str:
    """Return *value* when :func:`is_safe_slug`, else HTTP 400 with *detail*.

    Returns the validated slug rather than ``None`` so callers assign it back
    and the value that reaches the filesystem is the checked one. The custom
    CodeQL pack (``.github/codeql/custom-queries``) registers this function as a
    path-injection barrier, so that assignment is also what clears the taint --
    no ``os.path.basename`` round-trip is needed, the charset already excludes
    every separator.
    """
    if not is_safe_slug(value):
        raise HTTPException(status_code=400, detail=detail)
    return value


def safe_component(name: str) -> str:
    """Return ``name`` if it is a single, literal path component, else raise.

    ``os.path.basename`` strips any directory portion; if the result differs
    from the input (or is empty / ``.`` / ``..``), the input was a traversal
    attempt and is refused with HTTP 400. The returned value is what callers
    must feed to the filesystem -- it is provably a bare component, which clears
    the path-injection taint.
    """
    base = os.path.basename(name)
    if (
        not base
        or base in (".", "..")
        or base != name
        or "\\" in base
        or "\x00" in base
    ):
        raise HTTPException(status_code=400, detail="Invalid spec identifier")
    return base


def safe_join(base: Path, *parts: str) -> Path:
    """Join request-controlled ``parts`` onto ``base``, staying within ``base``.

    Unlike :func:`safe_component`, each part may legitimately contain separators
    (e.g. a relative file path). The joined path is resolved and confirmed to be
    inside ``base``; anything that escapes (via ``..`` or an absolute component)
    is refused with HTTP 400. Returns the validated absolute path.
    """
    base_real = os.path.realpath(base)
    joined_real = os.path.realpath(base.joinpath(*parts))
    if joined_real != base_real and not joined_real.startswith(base_real + os.sep):
        raise HTTPException(status_code=400, detail="Invalid path")
    return Path(joined_real)


def trusted_project_root(path: str) -> Path:
    """Resolve a request-supplied local project root, or raise ``ValueError``.

    Under the local-install trust model an authenticated client may point the
    server at any local directory it can access (like an editor opening a
    folder) -- the *whole path* is user-chosen by design, so containment checks
    do not apply. This helper is the single named choke point for that trust
    decision: it resolves the path and requires an existing directory, and the
    custom CodeQL pack (``.github/codeql/custom-queries``) recognises it as a
    path-injection barrier so deliberately-accepted project roots stop firing
    ``py/path-injection-sanitized`` at every derived filesystem access.

    Raises ``ValueError`` (not ``HTTPException``) so service-layer callers keep
    their existing error contracts.
    """
    root = Path(path).resolve()
    if not root.is_dir():
        raise ValueError(f"Project path does not exist: {path}")
    return root


def safe_spec_dir(base: Path, spec_id: str) -> Path:
    """Return ``<base>/.tfactory/specs/<spec_id>``, rejecting path traversal.

    ``spec_id`` is request-controlled; a value containing a path separator or
    ``..`` is refused with HTTP 400 before it can escape the specs root. A
    valid-but-absent id is returned as-is (callers probe ``.exists()``); only a
    malicious component raises.
    """
    return base / ".tfactory" / "specs" / safe_component(spec_id)


def contained_path(
    candidate: object, roots: Iterable[object], what: str, expand_user: bool = False
) -> Path:
    """Return *candidate*, resolved, if it lands inside one of *roots*.

    For a request that supplies a whole PATH while the server owns the permitted
    roots. Resolves first, then tests containment on the resolved form, so ``..``
    and symlinks cannot escape. Raises ``ValueError`` on a miss. ``exists()`` /
    ``is_dir()`` do not satisfy it: they say a path is THERE, not ALLOWED.
    ``expand_user`` expands ``~`` inside the barrier, not before it.
    """
    raw = Path(str(candidate))
    resolved = (raw.expanduser() if expand_user else raw).resolve()
    for root in roots:
        try:
            root_resolved = Path(str(root)).resolve()
        except (OSError, ValueError):
            continue
        if resolved == root_resolved or root_resolved in resolved.parents:
            return resolved
    raise ValueError(f"path is outside the {what}")


def within_roots(
    candidate: object, roots: Iterable[object], what: str, expand_user: bool = False
) -> Path:
    """:func:`contained_path`, but HTTP 403 (no path echoed) instead of ValueError."""
    try:
        return contained_path(candidate, roots, what, expand_user)
    except ValueError:
        raise HTTPException(
            status_code=403, detail=f"Access denied: path is outside the {what}"
        ) from None


def registered_project_roots() -> list[Path]:
    """Resolved paths of every registered project."""
    # Function-local: late binding, and routes.projects imports this module.
    from .projects import load_projects

    roots: list[Path] = []
    for p in load_projects().values():
        # A malformed registry entry drops out of the root set, not the request.
        with contextlib.suppress(OSError, KeyError, TypeError):
            roots.append(Path(p["path"]).resolve())
    return roots


def browse_roots() -> list[Path]:
    """Registered projects + ``$HOME`` + ``APP_FILE_BROWSE_ROOTS`` (os.pathsep list).

    For flows pointing at a directory that is not registered yet (scanning for
    projects to add); registered-only roots would make the first project
    impossible to add.
    """
    roots = registered_project_roots()
    with contextlib.suppress(OSError, RuntimeError):
        roots.append(Path.home().resolve())
    for raw_extra in os.environ.get("APP_FILE_BROWSE_ROOTS", "").split(os.pathsep):
        extra = raw_extra.strip()
        if extra:
            # A typo in the env var must not stop the server starting.
            with contextlib.suppress(OSError):
                roots.append(Path(extra).resolve())
    return roots
