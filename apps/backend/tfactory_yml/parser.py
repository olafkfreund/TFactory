"""
.tfactory.yml file parser.
===========================

``load_tfactory_yml(repo_root)`` is the single entry-point callers use to
read and validate a ``.tfactory.yml`` file.  It returns ``None`` when no
file is present (the file is optional at v0.2), and raises
:exc:`~tfactory_yml.exceptions.TFactoryYmlError` for any parse or validation
problem.

Design note (Decision 7):
    Env-var values are **not** resolved during parsing.  Auth models store
    only the env-var NAMES (e.g. ``token_env: "STAGING_API_TOKEN"``).
    Resolution happens at Executor invocation time via
    ``tfactory_yml.secrets.resolve_env_var()``.  This allows ``.tfactory.yml``
    to be committed and shared in PRs without leaking credentials.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from .exceptions import TFactoryYmlError
from .schema import TFactoryConfig

_FILENAME = ".tfactory.yml"
_EXAMPLE_FILENAME = ".tfactory.yml.example"


def _has_env_var_references(raw: Any) -> bool:
    """Return True if *raw* (a parsed YAML structure) contains any ``*_env``
    key whose value looks like an env-var name.

    This is a diagnostic helper — the Snapshotter can use it to warn operators
    that their config references secrets that must be set at Executor time.
    It does NOT resolve or validate the values.
    """
    if isinstance(raw, dict):
        for k, v in raw.items():
            if isinstance(k, str) and k.endswith("_env") and isinstance(v, str):
                return True
            if _has_env_var_references(v):
                return True
    elif isinstance(raw, list):
        for item in raw:
            if _has_env_var_references(item):
                return True
    return False


def load_tfactory_yml(repo_root: Path) -> TFactoryConfig | None:
    """Parse and validate ``<repo_root>/.tfactory.yml``.

    Parameters
    ----------
    repo_root:
        Absolute or relative path to the root of an AIFactory repository
        (the directory that contains / would contain ``.tfactory.yml``).

    Returns
    -------
    TFactoryConfig | None
        Validated config if the file is present; ``None`` if the file does
        not exist (it is optional at v0.2).

    Raises
    ------
    TFactoryYmlError
        - If the file exists but contains a YAML syntax error.
        - If the YAML parses but the data fails Pydantic validation (e.g. a
          required field is missing, or an unknown target ``type`` is used).

    Notes
    -----
    Env-var values are **not** resolved here.  The parsed models store only
    the env-var *names* (e.g. ``token_env: "STAGING_API_TOKEN"``).  Secret
    resolution happens at Executor invocation time via
    ``tfactory_yml.secrets.resolve_env_var()``.
    """
    path = Path(repo_root) / _FILENAME

    if not path.exists():
        return None

    # --- YAML parse ---
    try:
        raw_text = path.read_text(encoding="utf-8")
        raw_data = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        raise TFactoryYmlError(
            path,
            f"YAML syntax error: {exc}",
            errors=[],
        ) from exc

    if raw_data is None:
        # Empty file — treat as an absent config
        return None

    if not isinstance(raw_data, dict):
        raise TFactoryYmlError(
            path,
            f"Expected a YAML mapping at the top level, got {type(raw_data).__name__}",
            errors=[],
        )

    # --- Pydantic validation ---
    try:
        return TFactoryConfig.model_validate(raw_data)
    except ValidationError as exc:
        # Re-raise with file path context so callers get a useful error.
        errors = exc.errors(include_url=False)
        lines = [
            f"  [{' → '.join(str(loc) for loc in e['loc'])}] {e['msg']}" for e in errors
        ]
        message = f"{len(errors)} validation error(s):\n" + "\n".join(lines)
        raise TFactoryYmlError(path, message, errors=errors) from exc


def load_tfactory_yml_text(
    text: str, *, source_path: Path | None = None
) -> TFactoryConfig:
    """Parse and validate raw YAML *text* as a ``.tfactory.yml`` config.

    Useful for in-process testing and template rendering where the content is
    already in memory rather than on disk.

    Parameters
    ----------
    text:
        Raw YAML content.
    source_path:
        Optional path used in error messages (helps identify the file in
        test output).  Defaults to ``<in-memory>``.

    Returns
    -------
    TFactoryConfig
        Validated config object.

    Raises
    ------
    TFactoryYmlError
        On YAML syntax errors or Pydantic validation failures.
    """
    path = source_path or Path("<in-memory>")

    try:
        raw_data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise TFactoryYmlError(path, f"YAML syntax error: {exc}", errors=[]) from exc

    if not isinstance(raw_data, dict):
        raise TFactoryYmlError(
            path,
            f"Expected a YAML mapping at the top level, got {type(raw_data).__name__}",
            errors=[],
        )

    try:
        return TFactoryConfig.model_validate(raw_data)
    except ValidationError as exc:
        errors = exc.errors(include_url=False)
        lines = [
            f"  [{' → '.join(str(loc) for loc in e['loc'])}] {e['msg']}" for e in errors
        ]
        message = f"{len(errors)} validation error(s):\n" + "\n".join(lines)
        raise TFactoryYmlError(path, message, errors=errors) from exc
