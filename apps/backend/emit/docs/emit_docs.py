"""Docs-emit orchestrator (TFactory side): publish a rendered bundle to targets.

Vendored from PFactory's plan/emit/docs core (issue #341, "duplicate-then-
converge"). TFactory has no ``PlanSession`` — its producer is
``render_test_results`` (the triage report → a :class:`DocBundle`) — so this
copy keeps the plan-agnostic ``emit_bundle`` loop + target resolution and drops
the plan-specific ``emit_docs(session)`` / ``render_plan_docs`` entry point.

The whole stage is gated behind ``TFACTORY_DOCS_EMIT`` (default off) and is
best-effort — it never raises, so it cannot break the Triager.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .bundle import DocBundle
from .targets.backstage import BackstageTarget
from .targets.base import DocsTarget
from .targets.confluence import ConfluenceTarget
from .targets.repo import RepoDocsTarget

logger = logging.getLogger(__name__)


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def is_enabled() -> bool:
    """Master switch — the docs emit only runs when explicitly turned on."""
    return _truthy("TFACTORY_DOCS_EMIT")


def docs_root() -> Path:
    """Directory the repo target writes into (and the resolver reads from).

    ``TFACTORY_DOCS_DIR`` wins; else ``~/.tfactory/test-docs``. Public so a
    cross-factory resolver points at the same registry the emit writes. The
    registry.json here is intentionally the SAME shape PFactory writes, so plan
    docs and test-result docs share one ``correlation_key``-keyed index.
    """
    override = os.environ.get("TFACTORY_DOCS_DIR", "").strip()
    if override:
        return Path(override)
    return Path.home() / ".tfactory" / "test-docs"


def _resolve_targets(
    root: Path | None, updated_at: str, *, repo: str | None
) -> list[DocsTarget]:
    """The effective target set: repo always; Backstage/Confluence when configured.

    A remote target is added when its env config is present and its
    ``available()`` returns True. The repo/directory doc is the default and is
    always included.
    """
    out: list[DocsTarget] = [RepoDocsTarget(root or docs_root(), updated_at=updated_at)]
    git_write = _truthy("TFACTORY_DOCS_GIT_WRITE")
    if _truthy("TFACTORY_DOCS_BACKSTAGE") or os.environ.get("BACKSTAGE_BASE_URL"):
        out.append(BackstageTarget(repo=repo, git_write=git_write))
    if _truthy("TFACTORY_DOCS_CONFLUENCE") or os.environ.get("CONFLUENCE_BASE_URL"):
        out.append(ConfluenceTarget())
    return out


def connections_to_targets(
    connections: list[dict[str, Any]],
    *,
    repo: str | None = None,
    git_write: bool = False,
    selected: list[str] | None = None,
) -> list[DocsTarget]:
    """Build remote targets from Settings ``DocsTargetConnection`` dicts.

    The web-server passes user/org connection rows here so target selection is
    Settings-driven (and per-task, via ``selected``) instead of env. The
    repo/directory default is added by the orchestrator, not here.

    Each connection: ``{kind, base_url, api_token, space?, enabled_by_default?}``.
    A connection is used when ``selected`` lists its kind, else when
    ``enabled_by_default`` is set.
    """
    out: list[DocsTarget] = []
    for conn in connections:
        kind = (conn.get("kind") or "").lower()
        use = kind in selected if selected is not None else bool(conn.get("enabled_by_default"))
        if not use:
            continue
        if kind == "backstage":
            out.append(BackstageTarget(
                base_url=conn.get("base_url"), repo=repo, git_write=git_write,
            ))
        elif kind == "confluence":
            out.append(ConfluenceTarget(
                base_url=conn.get("base_url"), token=conn.get("api_token"),
                space=conn.get("space"),
            ))
    return out


def emit_bundle(
    bundle: DocBundle, *, targets: list[DocsTarget]
) -> list[dict[str, Any]]:
    """Publish an already-rendered :class:`DocBundle` to each available target.

    The plan-agnostic core of the docs emit: it knows nothing about plans or
    triage — only about ``DocBundle`` + the ``DocsTarget`` protocol. Any producer
    (PFactory's plan renderer, TFactory's ``render_test_results``) renders a
    bundle and hands it here, so the repo/Backstage/Confluence targets, the
    ``registry.json`` index and the ``correlation_key`` trail are shared, not
    re-implemented per factory (design §10.5). Returns per-target result dicts;
    never raises (each target's failure is isolated).
    """
    results: list[dict[str, Any]] = []
    for target in targets:
        try:
            if not target.available():
                results.append(
                    {"target": target.name, "status": "skipped", "detail": {}}
                )
                continue
            results.append(target.publish(bundle).as_dict())
        except Exception as exc:  # noqa: BLE001 — isolate target failures
            logger.warning(
                "docs target %s failed: %s", getattr(target, "name", "?"), exc
            )
            results.append(
                {
                    "target": getattr(target, "name", "?"),
                    "status": "error",
                    "detail": {"error": str(exc)},
                }
            )
    return results


def resolve_targets_for_emit(
    *,
    repo: str | None = None,
    root: Path | None = None,
    targets: list[DocsTarget] | None = None,
    connections: list[dict[str, Any]] | None = None,
    selected: list[str] | None = None,
) -> list[DocsTarget]:
    """Resolve the effective target list (precedence: explicit → connections → env).

    Mirrors PFactory's ``emit_docs`` target precedence, factored out so the
    TFactory producer (``render_test_results`` → ``emit_bundle``) shares it:

    * ``targets`` given → exactly those (test/explicit injection).
    * ``connections`` given → repo/directory default **plus** the Settings
      connections selected for this run.
    * neither → env-resolved set (repo always + Backstage/Confluence when
      env-configured).
    """
    updated_at = datetime.now(timezone.utc).isoformat()
    if targets is not None:
        return targets
    if connections is not None:
        effective: list[DocsTarget] = [
            RepoDocsTarget(root or docs_root(), updated_at=updated_at)
        ]
        effective += connections_to_targets(
            connections,
            repo=repo,
            git_write=_truthy("TFACTORY_DOCS_GIT_WRITE"),
            selected=selected,
        )
        return effective
    return _resolve_targets(root, updated_at, repo=repo)
