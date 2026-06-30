"""Live dispatch of the RFC-0013 DRY-RUN deploy lane (#597).

The deploy executor (``tools/runners/deploy_runner``) and the gate
(``agents/deploy_policy`` → ``triager._deploy_gate_annotation``) both shipped
under RFC-0013, but nothing in the live verify pipeline ever produced the proof
the gate reads — so the gate could only ever block a high-risk change for lack of
a proof. This module is the missing dispatch: when a spec's contract marks the
change **high-risk** or **production**, it runs the DRY-RUN deploy lane and
persists ``findings/deploy_verification.json`` so the triager's deploy-gate reads
a *real* proof (and a passing dry-run can clear the hold).

DRY-RUN only: ``deploy_runner.assert_dry_run`` guards every step, so no real
apply can run (RFC-0006 VAL-4 is never produced here). Best-effort: any failure
degrades to "no proof written" and never breaks the verify pipeline.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

from tools.runners.deploy_runner import StepResult
from tools.runners.lane_dispatch import dispatch_deploy_lane

# File names / suffixes the deploy lane keys on — mirrors deploy_runner's
# _TERRAFORM_GLOBS / _HELM_GLOBS / _K8S_GLOBS so ``_matches`` detects the lane.
_IAC_NAMES = ("Chart.yaml", "values.yaml", "kustomization.yaml")


def _persist_deploy_verification(
    spec_dir: Path, verification: dict[str, object]
) -> None:
    """Write the gate-normalized VAL block to ``findings/deploy_verification.json``
    — the exact artifact the triager's deploy-gate reads. Mirrors the persistence
    ``lane_dispatch.dispatch_deploy_lane`` does for the local path, so the Nix
    path produces the same proof file. Best-effort: a write error is swallowed
    (deploy gating must never break the verify pipeline)."""
    import json  # noqa: PLC0415 - lazy

    try:
        findings = spec_dir / "findings"
        findings.mkdir(parents=True, exist_ok=True)
        (findings / "deploy_verification.json").write_text(
            json.dumps(verification, indent=2)
        )
    except OSError:
        pass


def _discover_deploy_files(project_dir: Path) -> list[str]:
    """Repo-relative paths of the IaC files the deploy lane keys on.

    The executor's commands target the working directory (``terraform plan``,
    ``helm template .``, ``kubectl apply --dry-run -f .``); this list only needs
    to let ``deploy_runner.plan_deploy_steps`` detect which step families apply.
    """
    files: list[str] = []
    for path in project_dir.rglob("*"):
        if not path.is_file():
            continue
        name = path.name
        is_iac = (
            path.suffix == ".tf"
            or name in _IAC_NAMES
            or name.endswith(".tf.json")
            or name.endswith(".k8s.yaml")
            or ("k8s" in path.parts and path.suffix in (".yaml", ".yml"))
        )
        if is_iac:
            files.append(str(path.relative_to(project_dir)))
    return files


def _project_run_fn(project_dir: Path) -> Callable[[tuple[str, ...]], StepResult]:
    """A step runner that shells out **in the project dir**.

    Mirrors ``deploy_runner._default_run_fn`` but sets ``cwd`` so terraform/helm/
    kubectl operate on the built code (they read the working directory).
    """

    def run(argv: tuple[str, ...]) -> StepResult:
        proc = subprocess.run(  # noqa: S603 - argv from fixed deploy descriptors
            list(argv),
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
            cwd=str(project_dir),
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

    return run


def maybe_run_deploy_lane(
    spec_dir: Path,
    project_dir: Path,
    *,
    run_fn: Callable[[tuple[str, ...]], StepResult] | None = None,
    tool_available: Callable[[str], bool] | None = None,
) -> dict[str, object] | None:
    """Dispatch the DRY-RUN deploy lane iff the contract requires it (#597).

    Reads the spec's task contract; when its ``deployment`` block marks the change
    high-risk or production, runs the dry-run deploy lane against the built code
    and persists ``findings/deploy_verification.json`` (the proof the triager's
    deploy-gate reads).

    Returns the gate-normalized verification block, or ``None`` when the lane is
    not required or on any error (best-effort — never raises into the pipeline).

    ``run_fn`` / ``tool_available`` are injectable for tests; live runs default to
    a cwd-scoped subprocess runner and ``shutil.which`` tool detection (a tool
    absent from the runner is an honest ``not_run``, never a silent pass).
    """
    try:
        from agents.deploy_policy import (  # noqa: PLC0415 - lazy, avoids import cycle
            deploy_requirement_from_contract,
            deployment_block_from_contract,
        )
        from agents.task_contract import read_task_contract  # noqa: PLC0415 - lazy

        contract = read_task_contract(Path(spec_dir))
        requirement = deploy_requirement_from_contract(contract)
        if not requirement.required:
            return None

        block = deployment_block_from_contract(contract) or {}
        raw_scans = block.get("required_scans")
        required_scans = (
            [str(s) for s in raw_scans]
            if isinstance(raw_scans, (list, tuple))
            else None
        )

        files = _discover_deploy_files(Path(project_dir))

        # Prefer the per-task Nix-Job substrate: the live verify pod ships no
        # deploy toolchain, so only the Nix path can produce a *real* proof there
        # (#597). Tests inject ``run_fn`` to drive the deterministic local path;
        # live runs (run_fn is None) try the Nix Job first and fall back to the
        # local runner when the sandbox isn't configured (dev/CI).
        if run_fn is None:
            from agents.nix_env import run_deploy_lane_via_nix  # noqa: PLC0415 - lazy

            nix_result = run_deploy_lane_via_nix(
                Path(project_dir),
                files=files,
                required_scans=required_scans,
                target_level=requirement.target_level,
            )
            if nix_result is not None:
                verification: dict[str, object] = nix_result.verification
                _persist_deploy_verification(Path(spec_dir), verification)
                return verification

        result = dispatch_deploy_lane(
            files=files,
            required_scans=required_scans,
            target_level=requirement.target_level,
            spec_dir=Path(spec_dir),
            run_fn=run_fn or _project_run_fn(Path(project_dir)),
            tool_available=tool_available,
        )
        if result.deploy_result is None:
            return None
        verification = result.deploy_result.verification
        return verification
    except Exception:  # noqa: BLE001 - deploy gating must never break the verify pipeline
        return None
