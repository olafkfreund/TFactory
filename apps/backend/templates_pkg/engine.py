"""Tiny template engine for TFactory test-file templates.

Templates have a YAML front-matter metadata block + a body using
``${var}`` placeholders (Python's string.Template syntax). The engine
substitutes vars + validates the var set against the metadata.

Usage::

    from templates_pkg.engine import load_template, load_templates_for_framework

    tmpl = load_template(Path("frameworks/pytest/templates/function-pure.py.tmpl"))
    result = tmpl.instantiate(
        module_path="myapp.math",
        function_name="add",
        input_args="2, 3",
        expected_output="5",
    )
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from string import Template

import yaml


class TemplateError(Exception):
    """Raised when template parsing or substitution fails."""

    def __init__(self, template_path: Path, reason: str):
        self.template_path = template_path
        self.reason = reason
        super().__init__(f"{template_path}: {reason}")


@dataclass(frozen=True)
class TemplateMetadata:
    """Parsed YAML front-matter for a TFactory template file.

    Attributes:
        description: One-line description of what the template generates.
        requires_target: Whether the template needs a running application target
            (e.g. a base URL for Playwright browser tests).
        requires_auth: Whether the template requires an authenticated user
            session (e.g. login-flow tests against a protected route).
        vars: Ordered tuple of placeholder names expected in the body.
            These must exactly match the keys passed to ``instantiate()``.
    """

    description: str
    requires_target: bool = False
    requires_auth: bool = False
    vars: tuple[str, ...] = ()  # ordered for stable rendering


@dataclass(frozen=True)
class TemplateFile:
    """A parsed TFactory template: metadata + substitutable body.

    Attributes:
        path: Filesystem path of the ``.tmpl`` file (for error messages).
        metadata: Parsed YAML front-matter as a :class:`TemplateMetadata`.
        body: The post-front-matter text, ready for ``string.Template``
            substitution with ``${var}`` placeholders.
    """

    path: Path
    metadata: TemplateMetadata
    body: str  # the post-front-matter body, ready for Template substitution

    def instantiate(self, **values: str) -> str:
        """Substitute vars in the body; raises TemplateError on missing or unknown vars.

        Args:
            **values: Keyword arguments whose names must exactly match
                ``self.metadata.vars``.

        Returns:
            The fully-substituted file text.

        Raises:
            TemplateError: If any required var is missing, any unknown var is
                passed, or the body contains an unresolved ``${placeholder}``.
        """
        missing = set(self.metadata.vars) - set(values.keys())
        unknown = set(values.keys()) - set(self.metadata.vars)
        if missing:
            raise TemplateError(self.path, f"missing required vars: {sorted(missing)}")
        if unknown:
            raise TemplateError(self.path, f"unknown vars passed: {sorted(unknown)}")
        try:
            return Template(self.body).substitute(values)
        except KeyError as exc:
            raise TemplateError(
                self.path, f"unsubstituted placeholder in body: {exc}"
            ) from exc


def load_template(path: Path) -> TemplateFile:
    """Parse a template file: front-matter metadata + body.

    The file must begin with ``---\\n``, followed by YAML content, followed by
    ``\\n---\\n`` on its own line, followed by the body text.

    Args:
        path: Path to the ``.tmpl`` file.

    Returns:
        A :class:`TemplateFile` with parsed metadata and the raw body string.

    Raises:
        TemplateError: If the file is missing front-matter delimiters, has
            invalid YAML, or is missing the required ``description`` field.
    """
    text = path.read_text()
    if not text.startswith("---\n"):
        raise TemplateError(path, "missing YAML front-matter (must start with ---)")
    parts = text.split("\n---\n", 1)
    if len(parts) != 2:
        raise TemplateError(
            path, "front-matter not terminated with --- on its own line"
        )
    front_raw = parts[0][4:]  # strip leading "---\n"
    body = parts[1].lstrip("\n")
    try:
        raw_meta = yaml.safe_load(front_raw)
    except yaml.YAMLError as exc:
        raise TemplateError(path, f"YAML parse error in front-matter: {exc}") from exc
    if not isinstance(raw_meta, dict):
        raise TemplateError(path, "front-matter must be a YAML mapping")
    for required in ("description",):
        if required not in raw_meta:
            raise TemplateError(
                path, f"front-matter missing required field: {required}"
            )
    metadata = TemplateMetadata(
        description=str(raw_meta["description"]),
        requires_target=bool(raw_meta.get("requires_target", False)),
        requires_auth=bool(raw_meta.get("requires_auth", False)),
        vars=tuple(raw_meta.get("vars", [])),
    )
    return TemplateFile(path=path, metadata=metadata, body=body)


def load_templates_for_framework(
    framework_name: str, root: Path | None = None
) -> dict[str, TemplateFile]:
    """Load all templates for a framework from ``frameworks/{name}/templates/``.

    Args:
        framework_name: One of ``"pytest"``, ``"jest"``, ``"playwright"``
            (or any future framework whose directory lives under
            ``frameworks/``).
        root: Repository root override.  When ``None``, the root is inferred
            from the location of this module file
            (``templates_pkg`` → ``apps/backend`` → ``apps`` → repo root).

    Returns:
        A mapping of ``{filename: TemplateFile}`` for every ``*.tmpl`` file
        found in ``frameworks/{name}/templates/``.  Returns an empty dict if
        the directory does not exist.
    """
    if root is None:
        # templates_pkg lives at apps/backend/templates_pkg/
        # → parents[0] = apps/backend/templates_pkg
        # → parents[1] = apps/backend
        # → parents[2] = apps
        # → parents[3] = repo root
        root = Path(__file__).resolve().parents[3]
    tdir = root / "frameworks" / framework_name / "templates"
    if not tdir.is_dir():
        return {}
    return {p.name: load_template(p) for p in sorted(tdir.glob("*.tmpl"))}
