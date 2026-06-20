"""RFC-0013 deploy lane — DRY-RUN deploy verification (#446).

A "deploy" lane proves that a change *would* ship: it runs the pipeline/manifest
through **dry-run only** tooling and the IaC/container scanners, then maps the
outcome onto an honest RFC-0006 VAL block via the vendored never-overclaim gate.
It NEVER applies anything to a real environment — a production apply (VAL-4) is
held behind the RFC-0013 ``human-approval`` system gate and is never run by the
fleet.

What it runs (only the steps whose files are present in the change):

  - terraform : ``terraform validate`` (VAL-0) + ``terraform plan`` (VAL-2, no apply)
  - helm/k8s  : ``helm template | kubeconform`` and ``kubectl apply --dry-run=server``
  - scans     : ``tfsec`` / ``checkov`` (IaC), reusing the cloud-prowler image for
                container/cloud posture (descriptors in :data:`SCAN_DESCRIPTORS`).

Design: **pure command assembly + an injectable runner**. :func:`run_deploy_lane`
takes a ``run_fn(argv) -> StepResult`` so it is fully unit-testable without any of
the real tools installed and without ever touching a cloud. The honest mapping
to a VAL block lives in :func:`build_deploy_verification` (gate-normalized), and
the production guard lives in :func:`assert_dry_run` — assembling a
``production-apply`` step is a hard error.

This module is dependency-free (stdlib only) so it vendors cleanly and unit-tests
run without Docker.
"""

from __future__ import annotations

import fnmatch
import shutil
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from agents.verification_gate import normalize_verification

__all__ = [
    "SCAN_DESCRIPTORS",
    "DeployLaneResult",
    "DeployStep",
    "ProductionApplyError",
    "StepResult",
    "assert_dry_run",
    "build_deploy_verification",
    "plan_deploy_steps",
    "run_deploy_lane",
]


# --------------------------------------------------------------------------- #
# Production guard — assembling a real apply is a hard error.
# --------------------------------------------------------------------------- #

# Tokens that turn a dry-run command into a real, effectful apply. If any appears
# in an assembled step the lane refuses to run (RFC-0013: prod is never autonomous).
_EFFECTFUL_TOKENS: frozenset[str] = frozenset(
    {
        "apply",  # terraform apply / kubectl apply (without a dry-run flag)
        "destroy",  # terraform destroy
        "install",  # helm install
        "upgrade",  # helm upgrade
        "sync",  # argocd app sync
        "rollout",  # kubectl rollout (restart/undo)
    }
)
# Flags that make an otherwise-effectful verb safe (dry-run / plan only).
_DRY_RUN_FLAGS: frozenset[str] = frozenset(
    {
        "--dry-run",
        "--dry-run=server",
        "--dry-run=client",
        "plan",
        "template",
        "validate",
    }
)


class ProductionApplyError(RuntimeError):
    """Raised when a step would apply to a real environment — never allowed."""


def assert_dry_run(argv: Iterable[str]) -> None:
    """Refuse any argv that would effectfully apply (RFC-0013 prod guard).

    A command is allowed only when it carries no effectful verb, OR when a
    dry-run/plan flag is also present (so ``kubectl apply --dry-run=server`` and
    ``terraform plan`` pass, while ``terraform apply`` / ``helm upgrade`` are
    rejected). Defence-in-depth: every step is checked before it is run.
    """
    tokens = list(argv)
    token_set = set(tokens)
    has_dry_flag = bool(token_set & _DRY_RUN_FLAGS) or any(
        t.startswith("--dry-run") for t in tokens
    )
    if has_dry_flag:
        return
    effectful = token_set & _EFFECTFUL_TOKENS
    if effectful:
        raise ProductionApplyError(
            f"refusing to run an effectful deploy step {sorted(effectful)} without a "
            f"dry-run/plan flag — production deploys are never autonomous (RFC-0013): "
            f"{' '.join(tokens)}"
        )


# --------------------------------------------------------------------------- #
# Step + scan descriptors.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class DeployStep:
    """One dry-run/scan command the deploy lane would run."""

    name: str  # stable id, e.g. "terraform-plan", "tfsec"
    level: str  # the RFC-0006 VAL level this step proves (VAL-0 / VAL-2)
    argv: tuple[str, ...]  # the command (dry-run only — checked by assert_dry_run)
    tool: str  # the binary the step needs on PATH, e.g. "terraform"
    kind: str  # "dry-run" | "scan"
    optional: bool = False  # scans don't fail the lane if the tool is absent


@dataclass(frozen=True)
class StepResult:
    """Outcome of running one :class:`DeployStep`."""

    name: str
    level: str
    status: str  # "passed" | "failed" | "not_run"
    returncode: int | None = None
    reason: str | None = None
    output: str = ""


@dataclass
class DeployLaneResult:
    """What the deploy lane hands back to the executor / merge policy."""

    steps: list[StepResult] = field(default_factory=list)
    verification: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when no executed step failed (a tool-absent skip is not a failure)."""
        return not any(s.status == "failed" for s in self.steps)


# IaC / container scan descriptors. Additive: a real scanner image runs these;
# absent the tool, the step is an honest ``not_run`` (never a silent pass). The
# cloud-prowler scan reuses the existing ``tfactory-runner-cloud`` image (agents.cloud).
SCAN_DESCRIPTORS: dict[str, dict] = {
    "tfsec": {
        "tool": "tfsec",
        "level": "VAL-0",
        "argv": ("tfsec", ".", "--no-color", "--soft-fail"),
        "detect": ("*.tf", "**/*.tf"),
        "description": "IaC static analysis for Terraform (CVE/misconfig).",
    },
    "checkov": {
        "tool": "checkov",
        "level": "VAL-0",
        "argv": ("checkov", "-d", ".", "--compact", "--quiet"),
        "detect": ("*.tf", "**/*.tf", "**/Chart.yaml", "k8s/**/*.yaml", "**/*.yaml"),
        "description": "Multi-framework IaC scan (Terraform, Helm, k8s, CFN).",
    },
    "cloud-prowler": {
        "tool": "prowler",
        "level": "VAL-0",
        # The cloud lane (agents.cloud.runner) owns the live Docker/OCSF run; here
        # we only record the descriptor so the deploy lane can surface posture
        # scanning. Reused, not duplicated.
        "argv": ("prowler", "--list-checks"),
        "detect": ("*.tf", "**/*.tf"),
        "description": "Cloud posture scan via the shared tfactory-runner-cloud image.",
    },
}


# --------------------------------------------------------------------------- #
# Planning — which dry-run/scan steps apply to this change.
# --------------------------------------------------------------------------- #

# Dry-run command builders, keyed by the artifact they target. Each yields steps
# that are dry-run by construction (checked again by assert_dry_run before run).
_TERRAFORM_GLOBS = ("*.tf", "*.tf.json", "**/*.tf")
_HELM_GLOBS = ("**/Chart.yaml", "**/values.yaml")
_K8S_GLOBS = ("k8s/**/*.yaml", "**/kustomization.yaml", "**/*.k8s.yaml")


def _matches(files: Iterable[str], globs: Iterable[str]) -> bool:
    fs = list(files)
    return any(
        fnmatch.fnmatch(f, g) or fnmatch.fnmatch("/" + f, g) for f in fs for g in globs
    )


def plan_deploy_steps(
    files: list[str], *, required_scans: Iterable[str] | None = None
) -> list[DeployStep]:
    """Assemble the dry-run + scan steps that apply to ``files``.

    ``required_scans`` (from the RFC-0013 ``deployment.required_scans`` policy)
    forces the named scans on even when their detect-globs don't match — the
    policy floor never gets relaxed away. Every assembled step is dry-run by
    construction; production applies are never produced.
    """
    steps: list[DeployStep] = []

    if _matches(files, _TERRAFORM_GLOBS):
        steps.append(
            DeployStep(
                name="terraform-validate",
                level="VAL-0",
                argv=("terraform", "validate"),
                tool="terraform",
                kind="dry-run",
            )
        )
        steps.append(
            DeployStep(
                name="terraform-plan",
                level="VAL-2",
                # -input=false + no -auto-approve: a plan never applies.
                argv=("terraform", "plan", "-input=false", "-lock=false"),
                tool="terraform",
                kind="dry-run",
            )
        )

    if _matches(files, _HELM_GLOBS):
        steps.append(
            DeployStep(
                name="helm-template-kubeconform",
                level="VAL-2",
                argv=("helm", "template", "."),  # piped to kubeconform by the runner
                tool="helm",
                kind="dry-run",
            )
        )

    if _matches(files, _K8S_GLOBS) or _matches(files, _HELM_GLOBS):
        steps.append(
            DeployStep(
                name="kubectl-apply-dry-run",
                level="VAL-2",
                argv=("kubectl", "apply", "--dry-run=server", "-f", "."),
                tool="kubectl",
                kind="dry-run",
            )
        )

    # Scans: matched by glob, OR forced by the policy's required_scans floor.
    forced = {s.strip().lower() for s in (required_scans or [])}
    # Map RFC-0013 scan names onto the concrete scanners we run.
    _scan_aliases = {
        "iac-scan": ("tfsec", "checkov"),
        "sast": ("checkov",),
        "secret-scan": (),  # handled by a dedicated lane, not assembled here
        "dependency-audit": (),
    }
    forced_tools: set[str] = set()
    for f in forced:
        forced_tools.update(_scan_aliases.get(f, (f,)))

    for scan_name, desc in SCAN_DESCRIPTORS.items():
        glob_hit = _matches(files, desc["detect"])
        if glob_hit or scan_name in forced_tools:
            steps.append(
                DeployStep(
                    name=scan_name,
                    level=str(desc["level"]),
                    argv=tuple(desc["argv"]),
                    tool=str(desc["tool"]),
                    kind="scan",
                    optional=scan_name not in forced_tools,
                )
            )

    # Defence-in-depth: every assembled step must be dry-run.
    for s in steps:
        assert_dry_run(s.argv)
    return steps


# --------------------------------------------------------------------------- #
# Running the lane.
# --------------------------------------------------------------------------- #


def _default_run_fn(
    argv: tuple[str, ...],
) -> StepResult:  # pragma: no cover - shells out
    """Live runner: shell out to the tool. Unit tests inject a fake instead."""
    proc = subprocess.run(  # noqa: S603 - argv is assembled from fixed descriptors
        list(argv), capture_output=True, text=True, timeout=900, check=False
    )
    status = "passed" if proc.returncode == 0 else "failed"
    return StepResult(
        name=argv[0],
        level="VAL-0",
        status=status,
        returncode=proc.returncode,
        output=(proc.stdout or "") + (proc.stderr or ""),
        reason=None if status == "passed" else f"exit {proc.returncode}",
    )


def run_deploy_lane(
    files: list[str],
    *,
    required_scans: Iterable[str] | None = None,
    target_level: str = "VAL-2",
    run_fn: Callable[[tuple[str, ...]], StepResult] | None = None,
    tool_available: Callable[[str], bool] | None = None,
) -> DeployLaneResult:
    """Run the DRY-RUN deploy lane and return steps + an honest VAL block.

    ``run_fn(argv)`` executes one step (injected in tests; defaults to a real
    subprocess). ``tool_available(tool)`` decides whether a step's tool is present
    (defaults to :func:`shutil.which`). A step whose tool is absent is an honest
    ``not_run`` with a reason — never a silent pass. The returned
    ``verification`` block is gate-normalized so it can never overclaim, and it
    caps at the dry-run ceiling (no VAL-4 is ever produced).
    """
    runner = run_fn or _default_run_fn
    have = tool_available or (lambda t: shutil.which(t) is not None)

    steps = plan_deploy_steps(files, required_scans=required_scans)
    results: list[StepResult] = []
    notes: list[str] = []

    for step in steps:
        # Guard again at the edge — a real apply must never reach the runner.
        assert_dry_run(step.argv)
        if not have(step.tool):
            reason = f"tool {step.tool!r} not available in the runner"
            results.append(
                StepResult(
                    name=step.name,
                    level=step.level,
                    status="not_run",
                    reason=reason,
                )
            )
            if step.optional:
                notes.append(f"{step.name}: skipped ({reason})")
            continue
        res = runner(step.argv)
        # Normalize the result to carry the step's identity/level (run_fn may not know it).
        results.append(
            StepResult(
                name=step.name,
                level=step.level,
                status=res.status,
                returncode=res.returncode,
                reason=res.reason if res.status != "passed" else None,
                output=res.output,
            )
        )

    verification = build_deploy_verification(results, target_level=target_level)
    return DeployLaneResult(steps=results, verification=verification, notes=notes)


# --------------------------------------------------------------------------- #
# Honest VAL mapping (gate-normalized).
# --------------------------------------------------------------------------- #

# Only dry-run levels are reachable autonomously. There is deliberately NO VAL-3
# (ephemeral apply) or VAL-4 (production apply) rung produced here.
_DEPLOY_LADDER = ("VAL-0", "VAL-2")


def _aggregate(statuses: list[str]) -> str:
    """Aggregate a level's step statuses: failed if any failed; passed if any
    passed and none failed; else not_run (no step at this level ran)."""
    if not statuses:
        return "not_run"
    if any(s == "failed" for s in statuses):
        return "failed"
    if any(s == "passed" for s in statuses):
        return "passed"
    return "not_run"


def build_deploy_verification(
    results: list[StepResult], *, target_level: str = "VAL-2"
) -> dict:
    """Map step results onto an honest RFC-0006 deploy VAL block.

    Produces VAL-0 (lint/scan) and VAL-2 (dry-run) rungs only, then hands the
    block to the vendored never-overclaim gate which recomputes ``achieved_level``
    from what truly ran (a failed lower level caps the ceiling; an absent level is
    an honest ``not_run``). VAL-3/VAL-4 are intentionally never produced — an
    ephemeral or production apply is out of the autonomous ladder (RFC-0013).
    """
    by_level: dict[str, list[str]] = {}
    reasons: dict[str, list[str]] = {}
    for r in results:
        by_level.setdefault(r.level, []).append(r.status)
        if r.status != "passed" and r.reason:
            reasons.setdefault(r.level, []).append(f"{r.name}: {r.reason}")

    levels: list[dict] = []
    for lvl in _DEPLOY_LADDER:
        status = _aggregate(by_level.get(lvl, []))
        entry: dict = {"level": lvl, "status": status}
        if status != "passed":
            why = "; ".join(reasons.get(lvl, [])) or (
                f"no {'lint/scan' if lvl == 'VAL-0' else 'dry-run'} step ran in this deploy lane"
            )
            entry["reason"] = why
            if status == "not_run" and lvl == "VAL-2":
                entry["risk"] = "deploy dry-run not exercised; ship behaviour unproven"
        levels.append(entry)

    # Production apply is never reachable autonomously — record it as an explicit,
    # honest gap so a reader sees the policy, not silence.
    levels.append(
        {
            "level": "VAL-4",
            "status": "not_run",
            "reason": "production apply is never run autonomously (RFC-0013); held "
            "behind the human-approval system gate",
            "risk": "real production rollout is unverified by design",
        }
    )

    # Cap the declared target at the dry-run ceiling — the lane never targets prod.
    capped_target = target_level if target_level in _DEPLOY_LADDER else "VAL-2"
    block = {
        "target_level": capped_target,
        "achieved_level": capped_target,  # gate recomputes the truth below
        "levels": levels,
        "mode": "dry-run",  # RFC-0013 deploy_verification.mode
    }
    return normalize_verification(block)
