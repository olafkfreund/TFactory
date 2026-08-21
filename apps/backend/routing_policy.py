"""Per-stage model routing policy for TFactory (RFC-0014 v1).

Opt-in via the ``TFACTORY_ROUTING_POLICY`` environment variable, which holds
either the policy JSON itself or a path to a JSON file (Helm configmap mount).
Shape::

    {
      "tiers":  {"small": "haiku", "mid": "sonnet", "frontier": "opus"},
      "stages": {"qa": "mid", "qa_fixer": "small"}
    }

Tier values are model strings in any form ``phase_config.resolve_model_id``
accepts: a shorthand (``sonnet``), a full id (``claude-opus-4-8``), or a
provider-prefixed local model (``openai-compatible:qwen3.8:27b``).

Why this exists: TFactory's verify stage was the only leg of the fleet with no
operator-facing model control. AIFactory has ``AIFACTORY_ROUTING_POLICY`` and
PFactory has ``PFACTORY_ROUTING_POLICY``; retargeting TFactory meant editing
``DEFAULT_PHASE_MODELS`` and shipping a release, so evaluating a model on the
verify leg cost a deploy to switch on and another to switch back. The env var
form is deliberately identical to AIFactory's so one policy document describes
the whole fleet.

Precedence (wired in ``phase_config.get_phase_model``)::

    auto-profile phaseModels > cli_model > metadata.model > policy > default

The policy sits directly ABOVE the default and BELOW ``metadata.model``: it is
an operator's replacement for the built-in default, not an override of an
explicit per-task choice. Note this differs from AIFactory, where the policy
sits above ``metadata.model`` because there that field carries a tier-assigned
default rather than a user's selection.

Fail-closed contract: an absent/unparseable policy, an unmapped stage, or a
stage mapped to an unknown tier all yield ``None`` — the caller falls through to
today's default behaviour, so a broken policy can never change routing. Stdlib
only, and no import of ``phase_config`` at module scope, since ``phase_config``
imports this module.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ENV_VAR = "TFACTORY_ROUTING_POLICY"

# Default tier -> model, used when a policy maps a stage to a tier but supplies
# no ``tiers`` map of its own. Shorthands; resolve_model_id expands them.
DEFAULT_TIERS = {"small": "haiku", "mid": "sonnet", "frontier": "opus"}


def load_policy() -> dict[str, Any] | None:
    """Parse the routing policy from ``TFACTORY_ROUTING_POLICY``.

    The variable carries either the JSON document itself or a filesystem path to
    it. Returns ``None`` (fail-closed to default routing) when the variable is
    unset/empty, the file is unreadable, or the content is not a JSON object.
    """
    raw = os.environ.get(ENV_VAR, "").strip()
    if not raw:
        return None
    text = raw
    if not raw.startswith("{"):
        try:
            text = Path(raw).read_text(encoding="utf-8")
        except OSError:
            logger.warning("routing policy file %r unreadable; policy ignored", raw)
            return None
    try:
        data = json.loads(text)
    except ValueError:
        logger.warning("routing policy is not valid JSON; policy ignored")
        return None
    if not isinstance(data, dict):
        logger.warning("routing policy is not a JSON object; policy ignored")
        return None
    return data


def _str_map(policy: dict[str, Any], key: str) -> dict[str, str]:
    raw = policy.get(key)
    if not isinstance(raw, dict):
        return {}
    return {str(k): v for k, v in raw.items() if isinstance(v, str) and v}


def policy_route(stage: str) -> tuple[str, str] | None:
    """Resolve ``stage`` through the active policy to ``(model, tier)``.

    ``model`` is the raw policy value (shorthand or full id — the caller
    resolves it). Returns ``None`` when there is no policy, the stage is not
    mapped, or the stage's tier has no model.
    """
    policy = load_policy()
    if policy is None:
        return None
    tier = _str_map(policy, "stages").get(stage)
    if tier is None:
        return None
    model = _str_map(policy, "tiers").get(tier) or DEFAULT_TIERS.get(tier)
    if model is None:
        logger.warning(
            "routing policy maps stage %r to unknown tier %r; using default",
            stage,
            tier,
        )
        return None
    return model, tier
