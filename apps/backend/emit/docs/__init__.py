"""Documentation emit (#341) — render a test run into durable docs.

Vendored from PFactory's ``plan/emit/docs`` core (duplicate-then-converge): the
plan-agnostic ``emit_bundle`` loop, the repo/Backstage/Confluence targets, the
``correlation_key``-keyed ``registry.json`` index and the ``PlanDocsResolver``
read surface are shared verbatim. TFactory's only new producer is
``render_test_results`` — the triage report → a :class:`DocBundle` carrying the
plan's ``correlation_key`` and ``generated_by="tfactory"`` — so a run's results
sit next to the plan + epic in the same registry, closing the PARR doc trail:
plan → code → verify.

Gated behind ``TFACTORY_DOCS_EMIT`` (default off), best-effort (never breaks a
run). See guide ``guides/docs-emit.md``.
"""

from .bundle import DocBundle, TargetResult
from .emit_docs import (
    connections_to_targets,
    docs_root,
    emit_bundle,
    is_enabled,
    resolve_targets_for_emit,
)
from .render_test_results import render_test_results
from .resolve import PlanDocsResolver
from .targets.base import DocsTarget

__all__ = [
    "DocBundle",
    "DocsTarget",
    "PlanDocsResolver",
    "TargetResult",
    "connections_to_targets",
    "docs_root",
    "emit_bundle",
    "is_enabled",
    "render_test_results",
    "resolve_targets_for_emit",
]
