"""RFC-0016 Phase 2 — dispatch the verify orchestration as a k8s Job (TFactory #466).

Env-gated control/execution split for the verify pipeline. Today the control
plane runs the evaluate→triage pipeline **in-pod** (``run_evaluator`` /
``run_triager`` as background tasks). This module adds an **opt-in** path that
instead dispatches a single Kubernetes Job per spec that runs the whole verify
(``python -m agents.verify_pipeline``) on the **TFactory runtime image** (which
ships the ``agents`` backend package — the thin nix-runner image does NOT, and
running the orchestration there died ``ModuleNotFoundError: No module named
'agents'``, TFactory #466), so the control plane stays thin and verifies scale
across nodes + survive a control-plane roll. The orchestration Job itself does
not need nix; the per-lane test Jobs it dispatches still run on the nix-runner
image and get the SUT toolchain from ``nix develop`` (mirrors AIFactory #686).

Default is OFF: ``verify_exec_mode()`` returns ``inpod`` unless
``TFACTORY_VERIFY_EXEC=kubejob``. When kubejob is selected but the sandbox isn't
configured (no ``TFACTORY_NIX_RUNNER_IMAGE``), callers fall back to in-pod — the
split never hard-fails a verify on a config gap.

Reused seams (no new infra):
  - ``tools.runners.kube_sandbox`` — the proven apply/watch/log/delete lifecycle
    and the pure ``build_job_manifest`` (nix-base image, warm ``/nix`` store PVC,
    worktree co-mount, ``automountServiceAccountToken: false``, ttl + deadline).
  - ``agents.nix_env.nix_runner_from_env`` — builds the sandbox from the
    deployment's ``TFACTORY_*`` env (image, workspaces PVC, warm-store PVC, ns).
  - The durable Postgres ``job-state`` row (#465/#468) — the Job writes its own
    terminal row; the control plane **reconciles by polling Postgres** so a
    missed completion event never strands a job (concurrency-conventions.md §3).
  - The shared job-dispatch contract constants (hub ``scripts/job_dispatch.py``):
    Job naming ``factory-<service>-<job_id_short>`` and the reconcile-by-poll
    + terminal-state semantics, restated here (TFactory does not vendor the hub
    builder; it reuses its own kube_sandbox builder which predates it).

Reaper: ``reap_if_orphaned`` marks a vanished / deadline-exceeded Job ``stuck``
in the durable store so a no-verdict verify surfaces instead of stranding (#464).

Credential SCOPE (TFactory #466 env round-4): the verify Job inherits the LLM
provider credentials the in-pod control plane reads from **environment** —
``CLAUDE_CODE_OAUTH_TOKEN`` plus the non-Claude provider env the evaluator routes
to (OpenAI / OpenAI-compatible / Gemini / Google / Ollama-cloud / GitHub Models).
After this change the in-Job verify works for any model whose provider authenticates
via **env** (the common case). It does NOT yet seed CLI **credential FILES**
(``~/.codex/auth.json``, ``~/.gemini/oauth_creds.json``, copilot ``apps.json``,
or a file-mounted ``~/.claude/.credentials.json``) into the Job pod; providers
that authenticate only via those files (file-only Codex/Copilot/Gemini-OAuth) are
deferred to a follow-up that adds a credential-seeding initContainer to the Job
pod spec. This repo's chart does not currently define such a seed/cli-creds path
for the control-plane pod either, so the env set is the credential surface that
exists here today.

This module is I/O-light and unit-tested with a mocked sandbox + store; no test
needs a real cluster or Postgres.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

# ── Dispatch/reconcile contract (hub apis/concurrency-conventions.md §3) ──────
SERVICE = "tfactory"
KIND = "verify"
# Job named factory-tfactory-<job_id_short> (hub job_dispatch.JOB_NAME_PREFIX).
JOB_NAME_PREFIX = "factory"
_DNS_LABEL_MAX = 63
# Canonical terminal lifecycle states a reconciler treats as done with the row.
TERMINAL_STATES = ("done", "failed", "stuck")
# The control plane reconciles by polling the durable job-state table, so a
# missed completion event never strands a job; reporting is idempotent.
RECONCILE_BY = "postgres-poll"

_INPOD = "inpod"
_KUBEJOB = "kubejob"

# Verify orchestration entrypoint the Job runs (see agents/verify_pipeline.py).
_VERIFY_MODULE = "agents.verify_pipeline"

# ── Verify-orchestration Job image + backend path (TFactory #466, mirrors #686) ─
#
# The orchestration Job runs ``python -m agents.verify_pipeline`` — the TFactory
# *backend*, not the SUT. It therefore needs an image that SHIPS the ``agents``
# package, i.e. the TFactory runtime image — NOT the thin nix-runner image
# (TFACTORY_NIX_RUNNER_IMAGE), which only carries the per-task SUT toolchain and
# has no ``agents``. Running the orchestration on the nix runner died with
# ``ModuleNotFoundError: No module named 'agents'``.
#
# The orchestration Job does NOT itself need nix: it only imports + runs the
# pipeline; the per-LANE test Jobs it dispatches (run_evaluator →
# run_pytest_lane_via_nix → kube_sandbox) still run on the nix runner image and
# get the SUT toolchain from ``nix develop`` there. So the verify Job is a plain
# ``python -m agents.verify_pipeline`` on the TFactory image, no outer nix
# develop — exactly the AIFactory #686/#671 "run the service's own image" fix.
#
# Image resolution precedence (first non-empty wins), mirroring AIFactory's
# ``_resolve_build_image``:
#   1. TFACTORY_VERIFY_IMAGE  — explicit operator override for the verify Job ONLY.
#   2. TFACTORY_IMAGE         — the running Deployment's own image ref, injected by
#                               the chart (the pod can't read its own image from
#                               the downward API, so flat-manifest gitops pins it).
#                               Guaranteed to ship the agents package + python.
#   3. the thin nix-runner image — last-resort fallback (logged WARNING): keeps
#                               the manifest buildable in dev/test where neither
#                               var is set, but it CANNOT import agents.
#
# TFACTORY_NIX_RUNNER_IMAGE is deliberately NOT repointed: the per-lane test Jobs
# still need the thin SUT-toolchain image.
_ENV_VERIFY_IMAGE = "TFACTORY_VERIFY_IMAGE"
_ENV_RUNNING_IMAGE = "TFACTORY_IMAGE"
# Where the TFactory backend (the ``agents`` package) lives inside the runtime
# image. Set on PYTHONPATH so ``python -m agents.verify_pipeline`` imports. The
# image bakes this layout (Dockerfile ENV APP_BACKEND_PATH); gitops can override.
_ENV_BACKEND_PATH = "APP_BACKEND_PATH"
_DEFAULT_BACKEND_PATH = "/home/projects/MagesticAI/apps/backend"
# The verify pipeline's terminal store write imports ``server.*`` (the web-server
# sibling app), so its dir goes on PYTHONPATH too. It sits next to the backend
# (``…/apps/web-server``), derived from the backend path's parent.
_WEB_SERVER_DIRNAME = "web-server"

# ── LLM credential for the verify orchestration Job (TFactory #466, env round-4) ─
#
# The verify pipeline runs the evaluator/test-gen, which calls
# ``agents.evaluator → create_client → core.auth.require_auth_token``. The Job
# env (round-3) carried only JOB_ID/FACTORY_SERVICE/DATABASE_URL/PYTHONPATH, so
# the evaluator died ``ValueError: No OAuth token found`` and the Job ended
# terminal Failed with no verdict — the SAME class of defect as the AIFactory
# build Job. The fix injects the Claude Code OAuth token into the Job container
# ``env`` (NEVER argv — see also the separate PAT-in-argv issue #477), so the
# orchestration resolves the same credential the in-pod control plane uses.
#
# Two injection modes, preferred first:
#   1. secretKeyRef — when ``TFACTORY_VERIFY_OAUTH_SECRET_NAME`` (+ optional
#      ``TFACTORY_VERIFY_OAUTH_SECRET_KEY``, default ``oauth-token``) is set, the
#      env is sourced ``valueFrom.secretKeyRef`` so the literal token never lands
#      in the manifest / etcd / Job spec — k8s injects it at pod start. This is
#      the recommended production path: the operator creates one Secret with a
#      flat ``oauth-token`` key and rotates it independently.
#   2. resolved value — otherwise the token is resolved at dispatch time via
#      ``core.auth.get_auth_token`` (env → TFactory profiles → ~/.claude
#      credentials file → keychain, the exact in-pod resolution order) and set as
#      a literal ``env`` value. Still NOT argv; mirrors how the in-pod path reads
#      its own credential. Used in dev / single-replica deploys that mount the
#      credentials file but have no flat-key Secret.
#
# The evaluator routes by model (agents/evaluator._make_evaluator_client →
# infer_provider_from_model): Claude uses create_client (Claude OAuth above);
# non-Claude models (openai / openai-compatible / gemini / ollama-cloud / github
# models) resolve their OWN provider credentials from env in phase_config +
# providers/. So a verify routed to a non-Claude model needs that provider's env
# too — Claude-only injection would green the default verify but break any
# non-Claude one. We therefore forward the FULL provider/runtime env the in-pod
# control plane reads (each only when set on the pod), as container ``env`` —
# secretKeyRef for secrets when an env-source Secret is configured, else a
# resolved value (never argv, cf. #477). ``ANTHROPIC_API_KEY`` is deliberately
# excluded (core.auth never falls back to it — forwarding would risk silent API
# billing). CLI-FILE creds (~/.codex/auth.json, ~/.gemini/oauth_creds.json,
# copilot apps.json) are NOT seeded into the Job here — see the SCOPE note below.
# These are env-var / Secret-KEY NAMES, not credentials (S105 false positives).
_OAUTH_TOKEN_ENV = "CLAUDE_CODE_OAUTH_TOKEN"  # noqa: S105 - env var name, not a secret
_ENV_OAUTH_SECRET_NAME = "TFACTORY_VERIFY_OAUTH_SECRET_NAME"  # noqa: S105 - env var name
_ENV_OAUTH_SECRET_KEY = "TFACTORY_VERIFY_OAUTH_SECRET_KEY"  # noqa: S105 - env var name
_DEFAULT_OAUTH_SECRET_KEY = "oauth-token"  # noqa: S105 - default Secret key name
# Provider/runtime env forwarded to the verify Job when set on the pod. Two sets:
# the Claude SDK custom-endpoint/model/runtime knobs (mirrors core.auth.
# SDK_ENV_VARS) AND the non-Claude provider keys/base-urls the evaluator reads
# from env (verified against phase_config.py + providers/ in this repo). Secrets
# (API keys/tokens) and config (base URLs/model defaults) are forwarded the same
# way — value when resolved on the pod, or secretKeyRef when an env-Secret is
# configured (see _provider_env_entries). ANTHROPIC_API_KEY is intentionally
# absent (no silent API billing). QA_LLM_PROVIDER is also forwarded so the Job
# infers the same provider as the control plane when set.
_SDK_PASSTHROUGH_ENV: tuple[str, ...] = (
    # Claude SDK custom endpoint + model overrides + runtime knobs.
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "NO_PROXY",
    "DISABLE_TELEMETRY",
    "DISABLE_COST_WARNINGS",
    "API_TIMEOUT_MS",
    # Non-Claude provider credentials + config the evaluator resolves from env
    # (phase_config.py / providers/*). Forwarded only when present on the pod.
    "OPENAI_API_KEY",
    "OPENAI_COMPATIBLE_API_KEY",
    "OPENAI_COMPATIBLE_BASE_URL",
    "OPENAI_COMPATIBLE_REASONING_EFFORT",
    "OPENAI_COMPATIBLE_MAX_TOKENS",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "OLLAMA_API_KEY",
    "OLLAMA_CLOUD_BASE_URL",
    "GITHUB_TOKEN",
    "GITHUB_MODELS_DEFAULT",
    "QA_LLM_PROVIDER",
)
# Of the forwarded set, these are SECRETS (API keys / tokens). When an env-Secret
# is configured (_ENV_PROVIDER_SECRET_NAME) they are sourced via secretKeyRef so
# the literal never lands in the manifest; the rest (base URLs / model defaults /
# provider name) are non-secret config forwarded as plain values regardless.
_PROVIDER_SECRET_ENV: frozenset[str] = frozenset(
    {
        "ANTHROPIC_AUTH_TOKEN",
        "OPENAI_API_KEY",
        "OPENAI_COMPATIBLE_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "OLLAMA_API_KEY",
        "GITHUB_TOKEN",
    }
)
# Optional env-source Secret for the provider secrets above. When set, each secret
# env is sourced ``valueFrom.secretKeyRef`` (name = this value, key = the lower-
# kebab of the env var, e.g. OPENAI_API_KEY -> openai-api-key) so no literal key
# lands in the manifest/etcd. When unset, secrets are forwarded as resolved values
# from the pod env (still env, never argv).
_ENV_PROVIDER_SECRET_NAME = "TFACTORY_VERIFY_PROVIDER_SECRET_NAME"  # noqa: S105 - env var name


def resolve_verify_image(fallback_image: str) -> str:
    """Resolve the image for the verify-orchestration Job (TFactory #466).

    The orchestration Job runs ``python -m agents.verify_pipeline`` and so must
    land on an image that ships the TFactory ``agents`` package (the runtime
    image), never the thin nix-runner image (which has no ``agents`` →
    ``ModuleNotFoundError``). See the module-level note for precedence.

    ``fallback_image`` is the thin nix-runner image; it is returned only as a
    last resort (with a WARNING) so the manifest stays buildable in dev/test
    where neither image env is set.
    """
    explicit = os.environ.get(_ENV_VERIFY_IMAGE, "").strip()
    if explicit:
        return explicit
    running = os.environ.get(_ENV_RUNNING_IMAGE, "").strip()
    if running:
        return running
    _log.warning(
        "[verify-dispatch] neither %s nor %s set — falling back to the thin nix "
        "runner image %r for the verify-orchestration Job. That image has no "
        "`agents` package and will fail with ModuleNotFoundError; set %s (or the "
        "chart-injected %s) to the TFactory runtime image.",
        _ENV_VERIFY_IMAGE,
        _ENV_RUNNING_IMAGE,
        fallback_image,
        _ENV_VERIFY_IMAGE,
        _ENV_RUNNING_IMAGE,
    )
    return fallback_image


def resolve_backend_path() -> str:
    """Absolute path of the TFactory backend dir inside the verify image.

    Set on the Job's PYTHONPATH so ``python -m agents.verify_pipeline`` resolves
    the ``agents`` package. Defaults to the image's baked layout; gitops can
    override via ``APP_BACKEND_PATH``.
    """
    return os.environ.get(_ENV_BACKEND_PATH, "").strip() or _DEFAULT_BACKEND_PATH


def _oauth_secret_ref() -> tuple[str, str] | None:
    """Return ``(secret_name, secret_key)`` for the OAuth token, or None.

    The preferred injection path: when ``TFACTORY_VERIFY_OAUTH_SECRET_NAME`` is
    set the verify Job sources ``CLAUDE_CODE_OAUTH_TOKEN`` via
    ``valueFrom.secretKeyRef`` so the literal token never lands in the manifest /
    etcd. The key defaults to ``oauth-token`` and is overridable via
    ``TFACTORY_VERIFY_OAUTH_SECRET_KEY``.
    """
    name = os.environ.get(_ENV_OAUTH_SECRET_NAME, "").strip()
    if not name:
        return None
    key = os.environ.get(_ENV_OAUTH_SECRET_KEY, "").strip() or _DEFAULT_OAUTH_SECRET_KEY
    return name, key


def _resolve_oauth_token() -> str | None:
    """Resolve the Claude Code OAuth token the in-pod control plane would use.

    Delegates to ``core.auth.get_auth_token`` so the verify Job's resolution order
    matches the control plane exactly (env → TFactory profiles → ~/.claude
    credentials file → keychain). Returns None when no token is resolvable (the
    Job is then dispatched without it and the same fail-closed ValueError surfaces
    inside the Job, rather than silently running unauthenticated).
    """
    from core.auth import get_auth_token  # noqa: PLC0415 - lazy by design

    return get_auth_token()


def _oauth_env_entry() -> dict[str, Any] | None:
    """Build the ``CLAUDE_CODE_OAUTH_TOKEN`` env entry for the verify Job.

    Prefers a ``secretKeyRef`` (no literal token in the manifest); falls back to a
    resolved ``value`` from ``core.auth`` (still env, never argv). Returns None
    when neither a Secret is configured nor a token resolves — the Job then runs
    without the credential and fails closed inside (``No OAuth token found``)
    instead of being given a blank/poison value.
    """
    ref = _oauth_secret_ref()
    if ref is not None:
        name, key = ref
        return {
            "name": _OAUTH_TOKEN_ENV,
            "valueFrom": {"secretKeyRef": {"name": name, "key": key}},
        }
    token = _resolve_oauth_token()
    if token:
        return {"name": _OAUTH_TOKEN_ENV, "value": token}
    # NOTE: the Secret-name env var is named as a STATIC string literal in the
    # message below (not passed as a logging arg). The constant
    # ``_ENV_OAUTH_SECRET_NAME`` is the env-var NAME ("TFACTORY_VERIFY_OAUTH_..."),
    # never a credential value — but CodeQL's clear-text-logging heuristic taints
    # any ``*SECRET*``-named symbol that reaches a logger. Inlining the literal
    # keeps the message identical while removing the (mis-flagged) symbol from the
    # log call. No token value is ever logged here: this branch only runs when no
    # token resolves, and a resolved token only lands in a Job env entry.
    _log.warning(
        "[verify-dispatch] no OAuth credential for the verify Job: neither "
        "TFACTORY_VERIFY_OAUTH_SECRET_NAME is set nor a token resolves via "
        "core.auth (env/profiles/~/.claude/keychain). The verify Job will fail "
        "closed with 'No OAuth token found'. Set TFACTORY_VERIFY_OAUTH_SECRET_NAME "
        "to a Secret with a flat token key, or ensure the credential is resolvable "
        "in the control-plane pod."
    )
    return None


def _env_to_secret_key(var: str) -> str:
    """Map an env var name to its kebab-case Secret key (OPENAI_API_KEY ->
    openai-api-key)."""
    return var.lower().replace("_", "-")


def _provider_env_entries() -> list[dict[str, Any]]:
    """Provider/runtime env entries to forward to the verify Job, when set.

    Forwards the full provider/runtime set (Claude SDK custom endpoint + model
    overrides AND the non-Claude provider keys/base-urls the evaluator reads from
    env) so a verify routed to ANY provider resolves the same credentials the
    in-pod control plane does — not just Claude.

    Secrets (API keys/tokens) are sourced via ``secretKeyRef`` when an env-Secret
    is configured (``TFACTORY_VERIFY_PROVIDER_SECRET_NAME``) so no literal lands in
    the manifest; otherwise they are forwarded as resolved values from the pod env
    (still ``env``, never argv). Non-secret config (base URLs / model defaults /
    provider name) is always forwarded as a plain value. Only pod-set vars are
    emitted; ``ANTHROPIC_API_KEY`` is never in the set (no silent API billing).
    """
    secret_name = os.environ.get(_ENV_PROVIDER_SECRET_NAME, "").strip()
    out: list[dict[str, Any]] = []
    for var in _SDK_PASSTHROUGH_ENV:
        is_secret = var in _PROVIDER_SECRET_ENV
        # Secrets can come from the env-Secret even if not literally set on the
        # pod; non-secrets only forward when present on the pod env.
        if is_secret and secret_name:
            out.append(
                {
                    "name": var,
                    "valueFrom": {
                        "secretKeyRef": {
                            "name": secret_name,
                            "key": _env_to_secret_key(var),
                            "optional": True,
                        }
                    },
                }
            )
            continue
        val = os.environ.get(var)
        if val:
            out.append({"name": var, "value": val})
    return out


def _pythonpath_for(backend_path: str) -> str:
    """PYTHONPATH value for the verify Job: backend dir + web-server sibling.

    ``python -m agents.verify_pipeline`` needs the backend (``agents``) on the
    path; the pipeline's terminal store write additionally imports ``server.*``
    from the web-server sibling app (``…/apps/web-server``). Both go on the path,
    backend first. The web-server dir is derived from the backend's parent so a
    gitops override of ``APP_BACKEND_PATH`` keeps them co-located.
    """
    web_server = str(Path(backend_path).parent / _WEB_SERVER_DIRNAME)
    return f"{backend_path}:{web_server}"


def verify_exec_mode() -> str:
    """Return the verify execution mode: ``inpod`` (default) or ``kubejob``.

    Opt in to the Phase-2 Job-per-verify split with ``TFACTORY_VERIFY_EXEC=kubejob``.
    Any other value (incl. unset) keeps today's in-pod path, so the split is
    strictly additive and off by default.
    """
    return _KUBEJOB if os.environ.get("TFACTORY_VERIFY_EXEC") == _KUBEJOB else _INPOD


def _short(job_id: str) -> str:
    """k8s-safe short suffix from a job_id (DNS-1123, <=20 chars)."""
    s = re.sub(r"[^a-z0-9-]", "-", job_id.lower()).strip("-")
    return (s[-20:] or "job").strip("-") or "job"


def verify_job_name(job_id: str) -> str:
    """Job name ``factory-tfactory-<job_id_short>`` (DNS-1123 safe)."""
    return f"{JOB_NAME_PREFIX}-{SERVICE}-{_short(job_id)}"


def _verify_command(
    spec_subpath: str,
    project_subpath: str,
    job_id: str,
    correlation_key: str | int | None,
    mount: str,
) -> str:
    """The command the Job runs inside ``nix develop`` to perform the verify.

    Runs the orchestration entrypoint against the co-mounted spec + project. The
    paths are relative to the worktree mount (``/work``) so they resolve inside
    the Job regardless of the host data root.
    """
    spec = f"{mount}/{spec_subpath}" if spec_subpath else mount
    project = f"{mount}/{project_subpath}" if project_subpath else mount
    parts = [
        "python",
        "-m",
        _VERIFY_MODULE,
        "--spec",
        spec,
        "--project",
        project,
        "--job-id",
        job_id,
    ]
    if correlation_key is not None:
        parts += ["--correlation-key", str(correlation_key)]
    return " ".join(parts)


@dataclass(frozen=True)
class VerifyDispatch:
    """Result of dispatching a verify Job: the durable coordinates the control
    plane reconciles against."""

    job_id: str
    job_name: str
    namespace: str
    worker_ref: dict[str, Any]


@dataclass(frozen=True)
class VerifyJobConfig:
    """Inputs for the verify-orchestration Job manifest.

    A dataclass (mirroring the hub ``job_dispatch.JobSpec``) so the pure builder
    keeps a single parameter and stays within the strict arg cap. Short scalars
    only — never the contract blob (that lives in the co-mounted worktree)."""

    job_id: str
    image: str
    spec_subpath: str
    project_subpath: str
    repo_pvc: str | None
    namespace: str = "factory"
    service_account: str = "tfactory-sandbox"
    nix_store_pvc: str | None = None
    correlation_key: str | int | None = None
    database_url_env: str = "DATABASE_URL"
    mount: str = "/work"
    timeout: int = 3600
    ttl_seconds: int = 300
    nix_develop: bool = True
    # PYTHONPATH entry so ``python -m agents.verify_pipeline`` imports the TFactory
    # backend (the ``agents`` package) on the verify image. The verify Job runs the
    # TFactory backend, not the SUT, so it needs the backend on the path. Defaults
    # to the image's baked backend layout.
    backend_path: str = _DEFAULT_BACKEND_PATH
    # Mount-relative dir that holds the per-task ``flake.nix`` materialized before
    # dispatch. The verify Job ``nix develop``s THIS dir (not the data root) — the
    # data root is mounted at ``mount`` so spec+project resolve, but the flake is
    # materialized into the project worktree (like the lane path), so the develop
    # ref must point there. Defaults to the project subpath.
    flake_subpath: str | None = None


# -- file-auth CLI credential seeding (#481) --------------------------------- #
# Mirror of AIFactory #690 for the verify Job: file-auth providers
# (codex/gemini-oauth/copilot) authenticate via credential FILES seeded in-pod
# by the control-plane seed-creds path, NOT env. A fresh verify Job pod has
# none, so a verify routed to a file-auth provider fails in-Job. Opt-in via a
# configured secret name (default off → the env-auth path #480 is unchanged, and
# dev/test without the secret are unaffected). The control plane sets
# TFACTORY_VERIFY_CLI_CREDS_SECRET=factory-cli-creds.
_ENV_CLI_CREDS_SECRET = "TFACTORY_VERIFY_CLI_CREDS_SECRET"  # noqa: S105 - env var name
# Verify image HOME (data_root /home/nonroot/.tfactory) — where the CLIs look.
_VERIFY_HOME = "/home/nonroot"
# (secret key in the cli-creds secret  ->  path under HOME). Mirrors the
# control-plane seed-creds initContainer in factory-gitops.
_CLI_CRED_FILES: tuple[tuple[str, str], ...] = (
    ("claude-credentials.json", ".claude/.credentials.json"),
    ("codex-auth.json", ".codex/auth.json"),
    ("copilot-apps.json", ".config/github-copilot/apps.json"),
    ("gemini-oauth_creds.json", ".gemini/oauth_creds.json"),
)
# emptyDir home dirs shared between the seed initContainer and the verify container.
_SEED_HOME_VOLUMES: tuple[tuple[str, str], ...] = (
    ("cc-claude", f"{_VERIFY_HOME}/.claude"),
    ("cc-codex", f"{_VERIFY_HOME}/.codex"),
    ("cc-gemini", f"{_VERIFY_HOME}/.gemini"),
    ("cc-config", f"{_VERIFY_HOME}/.config"),
)


def _inject_verify_seed_creds(manifest: dict[str, Any]) -> None:
    """Add a ``seed-creds`` initContainer that materializes the file-auth CLI
    credentials into the verify Job pod (#481, mirrors AIFactory #690). No-op
    unless a secret name is configured. Mutates the manifest in place. Pure
    (env-only) → unit-testable.
    """
    secret = os.environ.get(_ENV_CLI_CREDS_SECRET, "").strip()
    if not secret:
        return  # default off: env-auth-only path (#480) unchanged
    pod = manifest["spec"]["template"]["spec"]
    home_mounts = [{"name": n, "mountPath": p} for n, p in _SEED_HOME_VOLUMES]

    volumes = pod.setdefault("volumes", [])
    for name, _path in _SEED_HOME_VOLUMES:
        volumes.append({"name": name, "emptyDir": {}})
    volumes.append({"name": "cli-creds", "secret": {"secretName": secret}})

    # cp is tolerant (a partial secret must not fail the whole verify): only copy
    # files that exist, and never let a missing one abort via ``set -e``.
    mkdirs = " ".join(
        f"{_VERIFY_HOME}/{d}"
        for d in (".claude", ".codex", ".gemini", ".config/github-copilot")
    )
    lines = [f"mkdir -p {mkdirs}"]
    lines += [
        f"[ -f /seed/{key} ] && cp /seed/{key} {_VERIFY_HOME}/{rel} || true"
        for key, rel in _CLI_CRED_FILES
    ]
    lines.append(
        f"chmod -R g+rwX {_VERIFY_HOME}/.claude {_VERIFY_HOME}/.codex "
        f"{_VERIFY_HOME}/.gemini {_VERIFY_HOME}/.config || true"
    )
    pod.setdefault("initContainers", []).append(
        {
            "name": "seed-creds",
            "image": "busybox:1.36",
            "command": ["sh", "-c"],
            "args": ["\n".join(lines)],
            "volumeMounts": [
                *home_mounts,
                {"name": "cli-creds", "mountPath": "/seed", "readOnly": True},
            ],
        }
    )
    pod["containers"][0].setdefault("volumeMounts", []).extend(home_mounts)


def build_verify_job_manifest(cfg: VerifyJobConfig) -> dict[str, Any]:
    """Build the k8s Job manifest that runs the verify orchestration. Pure.

    Wraps the proven ``kube_sandbox.build_job_manifest`` (warm ``/nix`` store,
    worktree co-mount, no API-token automount) and then layers the
    orchestration-Job specifics §3 requires that the lane builder does not:
      - the dedicated ``tfactory-sandbox`` service account (the verify Job writes
        its own job-state row + may touch the cluster, unlike a pure lane);
      - the short scalar env the Job needs to find its durable row + import the
        backend: ``JOB_ID``, ``CORRELATION_KEY``, ``FACTORY_SERVICE``,
        ``DATABASE_URL`` (passed through so the store write lands in the same
        Postgres), and ``PYTHONPATH`` (the backend + web-server dirs so
        ``agents`` / ``server`` import);
      - the LLM credential the evaluator/test-gen needs (#466 env round-4):
        ``CLAUDE_CODE_OAUTH_TOKEN`` via ``secretKeyRef`` when a Secret is
        configured (no literal token in the manifest), else a value resolved from
        ``core.auth`` — always via container ``env``, never argv (cf. #477) — plus
        the SDK provider/runtime passthrough env so custom endpoints / model maps
        resolve as they do in-pod;
      - the verify command ``python -m agents.verify_pipeline``. By default this
        runs directly on the verify image (``cfg.image`` — the TFactory runtime
        image, which ships ``agents``); the orchestration does not nix-develop
        (the per-lane test Jobs it spawns do). When ``cfg.nix_develop`` is set it
        is wrapped in ``nix develop path:.../#default`` for callers that want the
        toolchain from a per-task flake instead.
    """
    from tools.runners.kube_sandbox import (  # noqa: PLC0415 - lazy by design
        build_job_manifest,
    )

    name = verify_job_name(cfg.job_id)
    verify_cmd = _verify_command(
        cfg.spec_subpath,
        cfg.project_subpath,
        cfg.job_id,
        cfg.correlation_key,
        cfg.mount,
    )
    if cfg.nix_develop:
        # path: (not a bare ref) — a bare flake ref hits nix's git fetcher and
        # breaks on the Job-root vs worktree-uid mismatch (RFC-0016 §4.1 gotcha).
        # The flake lives in the project worktree (materialized before dispatch,
        # like the lane path), so develop THAT dir — not the data root, which has
        # no flake.nix and would fail with "flake.nix does not exist" at /work.
        flake_sub = (
            cfg.flake_subpath
            if cfg.flake_subpath is not None
            else (cfg.project_subpath)
        )
        flake_dir = f"{cfg.mount}/{flake_sub}" if flake_sub else cfg.mount
        inner = (
            f"nix develop path:{flake_dir}#default --command bash -c {_shq(verify_cmd)}"
        )
    else:
        inner = verify_cmd

    manifest = build_job_manifest(
        name,
        cfg.image,
        [inner],
        namespace=cfg.namespace,
        timeout=cfg.timeout,
        ttl_seconds=cfg.ttl_seconds,
        repo_pvc=cfg.repo_pvc,
        repo_subpath="",  # mount the data root; the command paths are mount-relative
        workdir=cfg.mount,
        nix_store_pvc=cfg.nix_store_pvc,
    )

    pod_spec = manifest["spec"]["template"]["spec"]
    # The verify Job (unlike a pure lane) writes its job-state row, so it gets the
    # dedicated SA. It ALSO dispatches nested per-lane Jobs (the Nix pytest/browser
    # lanes call ``create_namespaced_job`` via KubeJobSandbox), so it needs the SA
    # token actually mounted — otherwise the nested dispatch fails to authenticate
    # to the k8s API and the lane silently falls back to the host runner. Leaf lane
    # Jobs keep ``automountServiceAccountToken: False`` (they need no API).
    pod_spec["serviceAccountName"] = cfg.service_account
    pod_spec["automountServiceAccountToken"] = True

    env = [
        {"name": "JOB_ID", "value": cfg.job_id},
        {"name": "FACTORY_SERVICE", "value": SERVICE},
    ]
    # PYTHONPATH so ``python -m agents.verify_pipeline`` imports the TFactory
    # backend (``agents``) AND the web-server (``server.*``, for the terminal store
    # write) on the verify image. The orchestration Job runs the TFactory backend,
    # not the SUT. Without this the Job died ``ModuleNotFoundError: No module named
    # 'agents'`` (the thin nix runner has no backend; the runtime image does NOT
    # bake PYTHONPATH — the in-pod control plane runs from the web-server WORKDIR).
    # Set the dirs explicitly — no ``$(PYTHONPATH)`` self-ref: the image leaves
    # PYTHONPATH unset, so k8s would not expand it and the literal would poison the
    # path.
    if cfg.backend_path:
        env.append({"name": "PYTHONPATH", "value": _pythonpath_for(cfg.backend_path)})
    if cfg.correlation_key is not None:
        env.append({"name": "CORRELATION_KEY", "value": str(cfg.correlation_key)})
    # Pass DATABASE_URL through so the Job's terminal write lands in the same
    # Postgres the control plane polls. Only when actually set (dev/SQLite omits).
    db_url = os.environ.get(cfg.database_url_env)
    if db_url:
        env.append({"name": cfg.database_url_env, "value": db_url})
    # Propagate the Nix-lane sandbox coordinates so the verify pipeline running
    # inside THIS Job can dispatch the nested per-task Nix Job
    # (run_pytest_lane_via_nix -> nix_runner_from_env reads these). They live on
    # the control-plane Deployment but are NOT inherited by the dispatched Job, so
    # without this ``nix_runner_from_env`` returns None inside the Job and the lane
    # silently falls back to the host runner — the RFC-0005 flake env then never
    # runs in the kubejob path. (The mounted SA token lets the nested create Job
    # authenticate; these tell it what image/PVCs to use.)
    for _nix_var in (
        "TFACTORY_NIX_RUNNER_IMAGE",
        "TFACTORY_WORKSPACES_PVC",
        "TFACTORY_NIX_STORE_PVC",
        "TFACTORY_SANDBOX_NAMESPACE",
    ):
        _nix_val = os.environ.get(_nix_var)
        if _nix_val:
            env.append({"name": _nix_var, "value": _nix_val})
    # LLM credential (TFactory #466 env round-4): the verify pipeline's evaluator
    # calls create_client → require_auth_token. Without it the Job died
    # ``ValueError: No OAuth token found``. Inject CLAUDE_CODE_OAUTH_TOKEN via the
    # container env — secretKeyRef when a Secret is configured (no literal in the
    # manifest/etcd), else a resolved value. NEVER argv (cf. #477). Plus the SDK
    # provider/runtime env so custom endpoints / model maps resolve as in-pod.
    oauth_env = _oauth_env_entry()
    if oauth_env is not None:
        env.append(oauth_env)
    env.extend(_provider_env_entries())
    container = pod_spec["containers"][0]
    container["env"] = env

    # Label the durable coordinates so a reconciler can list verify Jobs.
    manifest["metadata"].setdefault("labels", {})
    manifest["metadata"]["labels"].update(
        {"factory.io/job-id": _short(cfg.job_id), "factory.io/kind": KIND}
    )
    # #481: seed file-auth CLI creds into the Job pod (opt-in; env-auth unchanged).
    _inject_verify_seed_creds(manifest)
    return manifest


def _shq(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


# ── Dispatch (opt-in) ────────────────────────────────────────────────────────


async def dispatch_verify_job(  # noqa: PLR0913 - 3 domain args + injectable seams
    *,
    job_id: str,
    spec_dir: Path,
    project_dir: Path,
    correlation_key: str | int | None = None,
    sandbox: Any = None,
    store: Any = None,
    apply_fn: Any = None,
) -> VerifyDispatch | None:
    """Dispatch a verify Job for ``spec_dir`` and record its durable coordinates.

    Returns the ``VerifyDispatch`` (job/namespace/worker_ref) on success, or
    ``None`` when the Nix-lane sandbox isn't configured **or the apply failed**
    (the caller then falls back to the in-pod path — the split never strands a
    verify on a config/cluster gap). Writes a ``queued`` job-state row with
    ``worker_ref`` set to the Job *before* applying, so the control plane can
    reconcile by polling Postgres and the reaper can find an orphaned dispatch.

    The orchestration Job runs ``python -m agents.verify_pipeline`` on the
    **TFactory runtime image** (``resolve_verify_image``), NOT the thin nix-runner
    image — the orchestration runs the TFactory *backend*, which only the runtime
    image ships (the nix runner died ``ModuleNotFoundError: No module named
    'agents'``, TFactory #466). The orchestration Job does NOT itself nix-develop:
    it imports + runs the pipeline; the per-LANE test Jobs it then dispatches
    (run_evaluator → run_pytest_lane_via_nix → kube_sandbox) still run on the
    nix-runner image and get the SUT toolchain from ``nix develop`` there. So the
    entrypoint is a plain ``python -m agents.verify_pipeline`` with the backend on
    PYTHONPATH — mirroring AIFactory's #686/#671 "run the service's own image".
    The nix-runner ``sandbox`` is still used for the PVC / namespace / data-root
    coordinates (and to gate on TFACTORY_NIX_RUNNER_IMAGE so a config gap falls
    back to in-pod cleanly); only the orchestration image differs.

    ``sandbox`` / ``store`` / ``apply_fn`` are injectable for tests; in production
    they default to ``nix_runner_from_env()``, the durable store opened on a fresh
    engine bound to the calling loop, and the real ``create_namespaced_job`` apply.
    """
    if sandbox is None:
        from agents.nix_env import nix_runner_from_env  # noqa: PLC0415 - lazy by design

        sandbox = nix_runner_from_env()
    if sandbox is None:
        _log.info(
            "[verify-dispatch] TFACTORY_NIX_RUNNER_IMAGE unset; "
            "cannot run verify as a k8s Job — caller should fall back to in-pod"
        )
        return None

    namespace = getattr(sandbox, "namespace", "factory")
    name = verify_job_name(job_id)
    worker_ref = {
        "kind": "k8s-job",
        "namespace": namespace,
        "job_name": name,
        "node": None,
    }

    # Record the queued row + worker_ref BEFORE applying the Job, so a reaper can
    # find an orphan even if the apply is interrupted (the row, not the cluster,
    # is the source of truth — concurrency-conventions.md §3).
    await _record_dispatch(
        job_id,
        correlation_key=correlation_key,
        worker_ref=worker_ref,
        store=store,
    )

    # Build the verify-orchestration manifest from the sandbox coordinates and
    # apply it. The manifest carries the dedicated SA + JOB_ID/CORRELATION_KEY/
    # DATABASE_URL/PYTHONPATH env the Job needs to import the backend and write its
    # own terminal row. The image is the TFactory RUNTIME image (resolve_verify_
    # image), not the thin nix runner — the orchestration runs the `agents` package.
    # No flake is materialized here and the orchestration does NOT nix-develop: the
    # per-lane test Jobs it dispatches materialize + nix-develop their own flake.
    spec_subpath = _pvc_subpath(spec_dir, sandbox)
    project_subpath = _pvc_subpath(project_dir, sandbox)
    verify_image = resolve_verify_image(getattr(sandbox, "image", ""))
    cfg = VerifyJobConfig(
        job_id=job_id,
        image=verify_image,
        spec_subpath=spec_subpath,
        project_subpath=project_subpath,
        repo_pvc=getattr(sandbox, "repo_pvc", None),
        namespace=namespace,
        nix_store_pvc=getattr(sandbox, "nix_store_pvc", None),
        correlation_key=correlation_key,
        backend_path=resolve_backend_path(),
        # The orchestration Job runs the verify pipeline directly on the runtime
        # image (it imports the backend); only the lanes it spawns nix-develop.
        nix_develop=False,
    )
    manifest = build_verify_job_manifest(cfg)
    _log.info(
        "[verify-dispatch] dispatching verify Job %s (spec=%s project=%s)",
        name,
        spec_subpath,
        project_subpath,
    )
    try:
        await _apply_verify_job(manifest, namespace, apply_fn=apply_fn)
    except Exception:  # noqa: BLE001 — apply gap must not strand: fall back in-pod
        _log.warning(
            "[verify-dispatch] apply of verify Job %s failed; caller should fall "
            "back to in-pod (the queued row will advance with the same job_id)",
            name,
            exc_info=True,
        )
        return None

    return VerifyDispatch(
        job_id=job_id, job_name=name, namespace=namespace, worker_ref=worker_ref
    )


async def _apply_verify_job(
    manifest: dict[str, Any], namespace: str, *, apply_fn: Any = None
) -> None:
    """Fire-and-forget create the verify Job (reconcile-by-poll owns the rest).

    Unlike the synchronous sandbox lane (``KubeJobSandbox.run`` applies, watches,
    then deletes), the verify Job is created and left to run: it writes its own
    terminal job-state row and is GC'd by ``ttlSecondsAfterFinished``. The control
    plane reconciles + reaps by polling Postgres, so no watch loop is held here.

    ``apply_fn(namespace, manifest)`` is injectable for tests; production loads
    the in-cluster (or kubeconfig) client lazily and calls ``create_namespaced_job``.
    """
    if apply_fn is not None:
        await apply_fn(namespace, manifest)
        return
    api, batch = await _k8s_batch()
    try:
        await batch.create_namespaced_job(namespace, manifest)
    finally:
        await api.close()


async def _k8s_batch() -> tuple[Any, Any]:
    """Load kube config (in-cluster, kubeconfig fallback) and return ``(api, batch)``.

    Isolates the untyped ``kubernetes_asyncio`` API behind a single ``Any`` seam so
    mypy --strict stays clean whether or not the (stub-less) package is installed,
    and the lazy import keeps the backend importable without a cluster.
    """
    k8s: Any = _import_kubernetes_asyncio()
    client, config = k8s.client, k8s.config
    try:
        config.load_incluster_config()
    except Exception:  # noqa: BLE001 - dev/test fallback
        await config.load_kube_config()
    api = client.ApiClient()
    return api, client.BatchV1Api(api)


def _import_kubernetes_asyncio() -> Any:
    """Lazily import the (untyped, stub-less) ``kubernetes_asyncio`` package."""
    import importlib  # noqa: PLC0415 - lazy by design

    return importlib.import_module("kubernetes_asyncio")


def _pvc_subpath(path: Path, sandbox: Any) -> str:
    """PVC-relative subpath for ``path`` under the sandbox data root, or ''."""
    from tools.runners.kube_sandbox import pvc_subpath  # noqa: PLC0415 - lazy by design

    data_root = getattr(sandbox, "data_root", "/home/nonroot/.tfactory")
    sub = pvc_subpath(str(path), data_root)
    return sub or ""


@asynccontextmanager
async def _store_for(store: Any) -> AsyncIterator[tuple[Any, bool]]:
    """Yield a durable job-state store + whether we own its session.

    When the caller injects a ``store`` (tests, or a request-scoped store) we use
    it and own nothing. Otherwise we open the web-server durable store on a
    **fresh engine bound to the current running loop** (the control plane / reaper
    isn't request-scoped). The web-server package is a sibling app not on the
    backend's import path at type-check time, so the import is lazy + ignored for
    mypy; at runtime it resolves in the pod.

    Why a fresh engine and not the process-global ``async_session_factory``:
    asyncpg (and aiosqlite) bind a connection to the loop that created it, so a
    pooled connection from the app's main-loop engine raises ``RuntimeError: got
    Future attached to a different loop`` when reused from the **blocking dispatch
    path**, which runs on its own private loop in a worker thread (see
    ``gen_functional._run_dispatch_blocking``). Creating the engine here means its
    connections are always opened on the loop that uses them. This mirrors how
    PFactory's durable store keeps DB I/O on a single, owned loop (PFactory #220).
    The owned engine is disposed when the context exits so no connection leaks.
    """
    if store is not None:
        yield store, False
        return
    from server.services import (  # type: ignore[import-not-found]  # noqa: PLC0415
        job_state_store as jss,
    )

    engine, factory = _fresh_store_engine()
    try:
        async with factory() as session:
            yield jss.get_job_state_store(session), True
    finally:
        await engine.dispose()


def _fresh_store_engine() -> tuple[Any, Any]:
    """Build a throwaway async engine + sessionmaker bound to the current loop.

    Reuses the web-server's resolved ``DATABASE_URL`` + driver connect-args so the
    fresh engine targets the same Postgres (or SQLite-dev fallback) the app uses —
    only the *loop affinity* differs. asyncpg/aiosqlite connections are created
    lazily on first use, i.e. on whichever loop drives the session, so opening the
    engine on the dispatch's private loop keeps every Future on that one loop.

    The SQLAlchemy async API is reached through a single ``Any`` seam
    (:func:`_import_sqlalchemy_async`) — the same shape as
    :func:`_import_kubernetes_asyncio` — so mypy --strict stays clean whether or
    not the SQLAlchemy stubs are installed in the lint env (the ratchet installs
    deps best-effort), and ``warn_unused_ignores`` never trips either way.
    """
    eng: Any = _import_engine_module()
    sa_async: Any = _import_sqlalchemy_async()

    # _resolve_database_url / _connect_args_for are module-level helpers in
    # engine.py (not class members); reuse them so the fresh engine matches the
    # app's URL + driver args exactly.
    url = eng._resolve_database_url()
    engine = sa_async.create_async_engine(
        url,
        echo=False,
        pool_pre_ping=True,
        connect_args=eng._connect_args_for(url),
    )
    factory = sa_async.async_sessionmaker(engine, expire_on_commit=False)
    return engine, factory


def _import_engine_module() -> Any:
    """Lazily import the web-server's DB engine module behind an ``Any`` seam.

    The web-server (``server.*``) is a sibling app not on the backend's import
    path at type-check time; the dynamic import keeps mypy from resolving it (and
    so from needing an ``# type: ignore`` that would be unused where it *is*
    resolvable). Resolves at runtime in the pod.
    """
    import importlib  # noqa: PLC0415 - lazy by design

    return importlib.import_module("server.database.engine")


def _import_sqlalchemy_async() -> Any:
    """Lazily import ``sqlalchemy.ext.asyncio`` behind an ``Any`` seam.

    Mirrors :func:`_import_kubernetes_asyncio`: a dynamic import so mypy --strict
    is clean regardless of whether the SQLAlchemy stubs are present in the lint
    env, with no static ``import-not-found`` and no unused-ignore.
    """
    import importlib  # noqa: PLC0415 - lazy by design

    return importlib.import_module("sqlalchemy.ext.asyncio")


async def _record_dispatch(
    job_id: str,
    *,
    correlation_key: str | int | None,
    worker_ref: dict[str, Any],
    store: Any = None,
) -> None:
    """Write the queued row + Job worker_ref. Best-effort (never breaks dispatch)."""
    try:
        async with _store_for(store) as (s, _owned):
            await s.enqueue(job_id, correlation_key=correlation_key)
            await s.update_status(
                job_id,
                service_status="queued",
                has_verdict=False,
                worker_ref=worker_ref,
            )
    except Exception:  # noqa: BLE001 — durable tracking must never break dispatch
        _log.warning(
            "[verify-dispatch] failed to record dispatch for job_id=%s (continuing)",
            job_id,
            exc_info=True,
        )


# ── Reconcile + reap (control plane, by polling Postgres) ────────────────────


async def reconcile_verify_job(
    job_id: str, *, store: Any = None
) -> dict[str, Any] | None:
    """Read the durable job-state row for ``job_id``.

    The control plane calls this on its reconcile poll: when the row's
    ``lifecycle_state`` is in :data:`TERMINAL_STATES` the verify is done from the
    control plane's perspective (the Job already wrote the verdict + artifacts).
    Returns the record, or ``None`` when the row is absent / the store is down.
    """
    try:
        async with _store_for(store) as (s, _owned):
            rec: dict[str, Any] | None = await s.get(job_id)
            return rec
    except Exception:  # noqa: BLE001
        _log.warning("[verify-dispatch] reconcile read failed for job_id=%s", job_id)
        return None


def is_terminal_record(record: dict[str, Any] | None) -> bool:
    """True when a reconciled record has reached a terminal lifecycle state."""
    if not record:
        return False
    return record.get("lifecycle_state") in TERMINAL_STATES


async def reap_if_orphaned(
    job_id: str,
    *,
    job_exists: bool,
    job_active: bool,
    store: Any = None,
) -> dict[str, Any] | None:
    """Reaper: mark a vanished / deadline-exceeded verify Job ``stuck`` (#464).

    The control plane (or a periodic reconciler) probes the cluster for the Job
    and passes the result here:
      - ``job_exists=False`` — the Job is gone (GC'd / deleted / never landed)
        but the durable row is still active (queued/running) → the Job died
        without writing a terminal row, so reap it ``stuck``.
      - ``job_exists=True, job_active=False`` — the Job finished (deadline /
        backoffLimit) but, again, left the row active → no verdict was written →
        reap it ``stuck``.
    A row already terminal is left untouched (idempotent — the Job's own write
    wins). Returns the updated record, or ``None`` when no reap was needed / the
    store is unavailable.
    """
    record = await reconcile_verify_job(job_id, store=store)
    if record is None:
        return None
    if is_terminal_record(record):
        return None  # the Job (or a prior reap) already wrote a terminal state
    if job_exists and job_active:
        return None  # still running — nothing to reap

    reason = (
        "verify Job vanished without writing a terminal job-state row "
        "(orphaned dispatch)"
        if not job_exists
        else "verify Job finished (deadline/backoff) with no verdict — "
        "lanes pending, no verdict (#464)"
    )
    try:
        async with _store_for(store) as (s, _owned):
            rec: dict[str, Any] | None = await s.mark_stuck(job_id, reason)
            return rec
    except Exception:  # noqa: BLE001
        _log.warning(
            "[verify-dispatch] reap failed for job_id=%s", job_id, exc_info=True
        )
        return None


# ── Control-plane reconcile + reap loop (wired into the app lifespan) ──────────
#
# Mirrors AIFactory build_backend's kubejob reconcile loop (RFC-0016 #671): when
# verifies run as k8s Jobs, the control plane polls Postgres for terminal
# transitions the Jobs wrote (so a missed completion event never strands a
# verify) and reaps vanished / deadline-exceeded Jobs on an interval. The loop is
# started from the web-server lifespan only when verify_exec_mode() == kubejob.


def _is_k8s_job_ref(record: dict[str, Any]) -> bool:
    """True when a durable row points at a dispatched verify k8s Job."""
    ref = record.get("worker_ref") or {}
    return isinstance(ref, dict) and ref.get("kind") == "k8s-job"


async def _probe_job(
    namespace: str, job_name: str, *, probe_fn: Any = None
) -> tuple[bool, bool]:
    """Return ``(job_exists, job_active)`` for the named Job. Fail-safe.

    Defaults to a lazy in-cluster ``read_namespaced_job`` probe; injectable for
    tests. On any probe error the Job is reported ``(exists=True, active=True)``
    so a transient API blip never makes the reaper reap a live verify.
    """
    if probe_fn is not None:
        result: tuple[bool, bool] = await probe_fn(namespace, job_name)
        return result
    try:
        api, batch = await _k8s_batch()
        try:
            job = await batch.read_namespaced_job(job_name, namespace)
        finally:
            await api.close()
    except Exception:  # noqa: BLE001 - a probe gap must not reap a live verify
        _log.debug(
            "[verify-dispatch] job probe failed for %s/%s (treating as active)",
            namespace,
            job_name,
            exc_info=True,
        )
        return True, True
    st = getattr(job, "status", None)
    active = bool(getattr(st, "active", 0)) if st is not None else False
    return True, active


async def reconcile_and_reap_once(*, store: Any = None, probe_fn: Any = None) -> int:
    """One reconcile + reap pass over active verify k8s-Job rows. Never raises.

    Lists the durable active (queued/running) verify rows, and for each one that
    points at a dispatched k8s Job: reconciles (a terminal row the Job wrote is
    left as-is) and reaps an orphan (Job vanished / finished with no verdict).
    Returns the number of rows reaped ``stuck`` (for observability / tests).
    """
    reaped = 0
    try:
        async with _store_for(store) as (s, _owned):
            rows = await s.recover_in_flight()
            for rec in rows:
                if not _is_k8s_job_ref(rec):
                    continue
                job_id = rec.get("job_id")
                if not job_id:
                    continue
                ref = rec.get("worker_ref") or {}
                namespace = ref.get("namespace") or "factory"
                job_name = ref.get("job_name") or verify_job_name(job_id)
                # Reconcile first: a terminal row the Job already wrote wins.
                if is_terminal_record(await reconcile_verify_job(job_id, store=s)):
                    continue
                exists, active = await _probe_job(
                    namespace, job_name, probe_fn=probe_fn
                )
                reaped_rec = await reap_if_orphaned(
                    job_id, job_exists=exists, job_active=active, store=s
                )
                if reaped_rec is not None:
                    reaped += 1
    except Exception:  # noqa: BLE001 - a bad tick must not crash the loop
        _log.warning("[verify-dispatch] reconcile/reap tick failed", exc_info=True)
    return reaped


async def reconcile_and_reap_loop(
    *, stop: asyncio.Event, interval_seconds: float = 15.0, probe_fn: Any = None
) -> None:
    """Periodic reconcile-by-poll + reaper for k8s-Job verifies (mirrors #671).

    Started from the web-server lifespan only when verify_exec_mode() == kubejob.
    Each tick reconciles terminal transitions the Jobs wrote and reaps vanished
    Jobs, so a missed completion event never strands a verify. Never raises — a
    bad tick is logged and the loop continues.
    """
    _log.info(
        "[verify-dispatch] reconcile loop started (interval=%.0fs)", interval_seconds
    )
    while not stop.is_set():
        await reconcile_and_reap_once(probe_fn=probe_fn)
        with suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval_seconds)
    _log.info("[verify-dispatch] reconcile loop stopped")
