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

import os
from pathlib import Path

from fastapi import HTTPException


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


def safe_spec_dir(base: Path, spec_id: str) -> Path:
    """Return ``<base>/.tfactory/specs/<spec_id>``, rejecting path traversal.

    ``spec_id`` is request-controlled; a value containing a path separator or
    ``..`` is refused with HTTP 400 before it can escape the specs root. A
    valid-but-absent id is returned as-is (callers probe ``.exists()``); only a
    malicious component raises.
    """
    return base / ".tfactory" / "specs" / safe_component(spec_id)
