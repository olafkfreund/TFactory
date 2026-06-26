"""Safe filesystem-path construction from request-supplied identifiers.

Spec and task identifiers arrive from request URLs and are attacker-controlled.
Joining one straight onto a base directory lets a value like ``../../etc``
escape the intended root -- a path-traversal vulnerability (CodeQL
``py/path-injection``). ``safe_spec_dir`` validates the identifier before it ever
reaches the filesystem, so every caller that routes through it is safe by
construction.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException


def _validate_component(name: str) -> str:
    """Reject anything that is not a single, literal path component.

    Blocks empty values, ``.``/``..``, embedded path separators, and NUL bytes
    -- the traversal vectors that turn a request value into an arbitrary path.
    """
    if (
        not name
        or name in (".", "..")
        or "/" in name
        or "\\" in name
        or "\x00" in name
    ):
        raise HTTPException(status_code=400, detail="Invalid spec identifier")
    return name


def safe_spec_dir(base: Path, spec_id: str) -> Path:
    """Return ``<base>/.tfactory/specs/<spec_id>``, rejecting path traversal.

    ``spec_id`` is request-controlled; a value containing a path separator or
    ``..`` is refused with HTTP 400 before it can escape the specs root. A
    valid-but-absent id is returned as-is (callers probe ``.exists()``); only a
    malicious component raises.
    """
    specs_root = base / ".tfactory" / "specs"
    spec_dir = specs_root / _validate_component(spec_id)
    # Defense in depth: the resolved path must stay within the specs root.
    root_resolved = specs_root.resolve()
    dir_resolved = spec_dir.resolve()
    if dir_resolved != root_resolved and root_resolved not in dir_resolved.parents:
        raise HTTPException(status_code=400, detail="Invalid spec identifier")
    return spec_dir
