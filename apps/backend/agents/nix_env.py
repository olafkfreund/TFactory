"""RFC-0005 Tier A consumption — materialize a per-task Nix flake for verify.

Reads the contract ``environment`` manifest (declared by the PFactory planner)
and, when ``provisioning.method == "nix"``, writes a ``flake.nix`` into the
project checkout so the lane runner can ``nix develop /work#default -c
<verify_commands>`` against it. The flake is generated from the manifest by the
vendored ``nix_provisioner`` (the single source of truth), so the verify env
matches the build env declared in the same contract — no drift.

If the repo already carries a ``flake.nix`` and ``provisioning.generated`` is
False, that one is respected (the repo owns its env); we only generate when the
planner marked the manifest as generated or no flake exists.

This module only PREPARES the flake + commands; actually running them in a
hermetic sandbox is the Job backend's job (TFactory pods have no container
runtime — the lane runs as a k8s Job using the tfactory-runner-nix image).
"""

from __future__ import annotations

import contextlib
import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from agents.task_contract import read_task_contract
from tools.runners.docker_runner import DockerRunResult
from tools.runners.nix_provisioner import (
    Manifest,
    generate_flake,
    nix_develop_argv,
)

if TYPE_CHECKING:
    from agents.execution_sandbox import ExecutionSandbox
    from tools.runners.deploy_runner import DeployLaneResult
    from tools.runners.kube_sandbox import KubeJobSandbox

_log = logging.getLogger(__name__)

_FLAKE = "flake.nix"


@dataclass
class NixPlan:
    """A materialized Nix environment ready to run via the Job backend."""

    flake_dir: Path
    verify_commands: list[str]
    proof_verify: list[str]
    network: str
    generated: bool

    def develop_argv(
        self, commands: list[str] | None = None, *, path_ref: bool = True
    ) -> list[str]:
        # path_ref=True by default: the flake dir is a co-mounted git worktree in
        # the Job, where a bare ref hits nix's git fetcher (uid-ownership reject +
        # untracked-flake invisibility). path: copies the dir directly.
        return nix_develop_argv(
            str(self.flake_dir), commands or self.verify_commands, path_ref=path_ref
        )


def detect_serve_command(
    project_dir: Path, env: dict | None = None, *, port: int = 8099
) -> str | None:
    """How to start the app inside the materialized env for a browser/api lane.

    Order: the contract ``environment.serve_command`` (authoritative) → else
    detect from the checkout (FastAPI/Flask via uvicorn, or a node start script).
    Returns None when nothing is detectable (the lane then runs without serving —
    honest, not a guess).
    """
    if env and env.get("serve_command"):
        return str(env["serve_command"])
    pd = Path(project_dir)
    # Python ASGI: prefer a root app.py exposing `app`, then a src/app package.
    if (pd / "app.py").is_file() and "app" in (pd / "app.py").read_text(
        errors="ignore"
    ):
        return f"python -m uvicorn app:app --host 127.0.0.1 --port {port}"
    if (pd / "src" / "app" / "main.py").is_file():
        return f"python -m uvicorn app.main:app --host 127.0.0.1 --port {port}"
    if (pd / "main.py").is_file() and "app" in (pd / "main.py").read_text(
        errors="ignore"
    ):
        return f"python -m uvicorn main:app --host 127.0.0.1 --port {port}"
    pkg = pd / "package.json"
    if pkg.is_file() and '"start"' in pkg.read_text(errors="ignore"):
        return "npm start"
    return None


def nix_runner_from_env() -> KubeJobSandbox | None:
    """Build a KubeJobSandbox from the deployment's TFACTORY_* env, or None when
    the Nix-lane sandbox isn't configured (so callers degrade gracefully)."""
    import os

    image = os.environ.get("TFACTORY_NIX_RUNNER_IMAGE")
    if not image:
        return None
    from tools.runners.kube_sandbox import KubeJobSandbox

    pvc = os.environ.get("TFACTORY_WORKSPACES_PVC")
    ns = os.environ.get("TFACTORY_SANDBOX_NAMESPACE", "factory")
    # RFC-0016 #197: opt-in warm /nix/store PVC so the toolchain closure persists
    # across Nix lane Jobs instead of cold-fetching each run. Absent → no mount,
    # so nothing breaks if the PVC is not provisioned.
    nix_store_pvc = os.environ.get("TFACTORY_NIX_STORE_PVC") or None
    return KubeJobSandbox(
        image, namespace=ns, repo_pvc=pvc, nix_store_pvc=nix_store_pvc
    )


_JOB_SCRIPT = "_tf_nix_job.sh"
_E2E_STAGE = ".tf_e2e"  # staged generated browser specs (in the worktree)
_PW_CONFIG = "_tf_pw.config.ts"
_SHOTS = "shots"
_PYTEST_STAGE = ".tf_pytest"  # staged junit/coverage the Nix Job writes back
_GOTEST_STAGE = ".tf_gotest"  # staged junit/coverage the Go Nix Job writes back
_DEPLOY_STAGE = ".tf_deploy"  # generated deploy flake dir (co-mounted in the Job)
_NIX_MOUNT = "/work"  # where KubeJobSandbox co-mounts the worktree in the Job

# The deploy-lane tools we can run hermetically inside a per-task Nix Job (#597,
# #603): ``tfsec`` + ``trivy`` are pure Go binaries (no insecure transitive deps),
# ``opentofu`` is the free Terraform (``tofu``) — all evaluate cleanly in the flake
# and run offline with no cluster. ``tfsec`` = terraform report-only scan, ``trivy
# config`` = multi-framework misconfig gate (``--skip-check-update`` = embedded
# rego checks), ``opentofu`` = the ``tofu init``/``validate``/``plan`` rung
# (init -backend=false installs any declared providers via the Job's network).
# ``checkov`` was dropped (#603): in the pinned nixpkgs it pulls
# ``python3.13-ecdsa`` marked insecure (CVE-2024-23342), so ``pkgs.checkov`` refuses
# to evaluate and aborts the whole ``nix develop`` (proven live 2026-06-30). Unfree
# ``terraform`` and ``kubectl apply --dry-run=server`` (live cluster API + RBAC)
# stay an honest ``not_run``. A tool absent from this set is never a silent pass —
# ``run_deploy_lane``'s ``tool_available`` records it as ``not_run``.
_DEPLOY_NIX_TOOLS: tuple[str, ...] = ("tfsec", "trivy", "opentofu")


def run_pytest_lane_via_nix(
    spec_dir: Path,
    project_dir: Path,
    test_file: Path,
    *,
    extra_env: dict[str, str] | None = None,
    timeout: int = 900,
) -> DockerRunResult | None:
    """Run ONE pytest file inside the per-task Nix dev shell as a k8s Job.

    The toolchain (python + pytest + pytest-cov) comes from the materialized flake
    (declared in the contract ``environment``), not the image — so the verify env
    matches the build env with no drift. The worktree is co-mounted at
    ``_NIX_MOUNT``; the test is staged into ``tests/`` there (the Job sees the real
    worktree, not a host scratch copy, so ``from <module> import ...`` resolves the
    same way the DockerRunner path does), pytest writes junit + coverage into a
    staging dir on the worktree, and we collect them back as a DockerRunResult-
    shaped result.

    Returns None when there's no nix environment or the sandbox isn't configured,
    so the caller falls back to the host/docker runner. Mirrors the staging +
    collection pattern of ``run_browser_evidence``.
    """
    mount = _NIX_MOUNT
    env = environment_from_contract(spec_dir)
    plan = materialize_flake(spec_dir, project_dir, env=env)
    if plan is None:
        return None
    # Consume the engine purely through the unified seam (#426): this lane works
    # with any ExecutionSandbox the factory returns, not just KubeJobSandbox.
    sandbox: ExecutionSandbox | None = nix_runner_from_env()
    if sandbox is None:
        _log.info("run_pytest_lane_via_nix: TFACTORY_NIX_RUNNER_IMAGE unset; skipping")
        return None

    pd = Path(project_dir)
    name = Path(test_file).name
    tests_dir = pd / "tests"
    tests_dir.mkdir(exist_ok=True)
    staged_test = tests_dir / name
    # Stage the specific (generated or mutated) test into the worktree's tests/
    # dir so the co-mounted Job runs THAT file. The SUT already lives in the
    # worktree, so unlike the host/docker path we don't copy the whole project.
    if Path(test_file).resolve() != staged_test.resolve():
        shutil.copy2(test_file, staged_test)
    stage = pd / _PYTEST_STAGE
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True, exist_ok=True)
    # The Job runs as a non-root uid against the co-mounted worktree; make the
    # staging dir writable so pytest can drop junit/coverage there.
    with contextlib.suppress(OSError):
        stage.chmod(0o777)

    # Inject seed/credentials the host/docker path would set, exported in-shell so
    # the test process inherits them (PYTHONHASHSEED, TFACTORY_TARGET_URL, ...).
    exports = "".join(
        f"export {k}={_shquote(str(v))}\n" for k, v in (extra_env or {}).items()
    )
    # src-layout: make ``<work>/src`` (and ``<work>``) importable so
    # ``from <pkg> import ...`` resolves inside the hermetic Nix Job — the host
    # runner does the same (evaluator._run_pytest_on_host). Without it a
    # ``src/<pkg>/`` package fails at collection and every AC shows as an error
    # rather than its real pass/fail. Prepended to any PYTHONPATH exported above
    # (#615).
    srcpath = f'export PYTHONPATH="{mount}/src:{mount}${{PYTHONPATH:+:$PYTHONPATH}}"\n'
    pytest_cmd = (
        f"cd {mount} && "
        f"python -m pytest tests/{name} -p no:cacheprovider -q "
        f"--junitxml={mount}/{_PYTEST_STAGE}/junit.xml "
        f"--cov-report=xml:{mount}/{_PYTEST_STAGE}/coverage.xml --cov=. 2>&1; "
        "echo __PYTEST_EXIT=$?"
    )
    (pd / _JOB_SCRIPT).write_text(
        "#!/usr/bin/env bash\nset +e\n" + exports + srcpath + pytest_cmd + "\n",
        encoding="utf-8",
    )
    job_cmd = f"nix develop path:{mount}#default --command bash {mount}/{_JOB_SCRIPT}"
    # #623: a per-task Nix build can fail transiently — concurrent stability +
    # mutation lane Jobs contend for the RWO /nix-store PVC, and the nixpkgs
    # tarball fetch can flake. Retry ONLY when pytest never emitted its exit
    # marker (i.e. the build/setup failed before the test ran, not a genuine test
    # failure) — so a real fail (e.g. a caught hardcode) is never masked.
    attempts = 2
    try:
        res = sandbox.run([job_cmd], workdir=str(pd), timeout=timeout)
        for attempt in range(1, attempts):
            if "__PYTEST_EXIT=" in (res.stdout or ""):
                break
            _log.warning(
                "run_pytest_lane_via_nix: no pytest exit marker (nix build likely "
                "failed transiently); retry %d/%d. tail=%r",
                attempt + 1,
                attempts,
                (res.stdout or "")[-300:],
            )
            res = sandbox.run([job_cmd], workdir=str(pd), timeout=timeout)
    finally:
        (pd / _JOB_SCRIPT).unlink(missing_ok=True)
        staged_test.unlink(missing_ok=True)

    code = _parse_pytest_exit(res.stdout)
    junit = stage / "junit.xml"
    cov = stage / "coverage.xml"
    return DockerRunResult(
        returncode=code,
        stdout=res.stdout or "",
        stderr="",
        junit_xml_path=junit if junit.is_file() else None,
        coverage_xml_path=cov if cov.is_file() else None,
        argv=["nix", "develop", f"path:{mount}#default", "--", "pytest", name],
    )


def go_environment(spec_dir: Path) -> dict:
    """The Go nix environment for the verify lane.

    Prefer the contract ``environment`` block when it declares a *Go* nix env
    (authoritative — the same toolchain the build used, no drift). Otherwise
    SYNTHESIZE a minimal Go devShell: a spec-ingest task (a raw acceptance spec,
    no contract) carries no environment block, so a Go plan would have no
    toolchain to run against. The synthesized env pins bare ``go`` plus the
    JUnit/coverage tools (gotestsum, gocover-cobertura) as system packages —
    PR-A taught ``generate_flake`` to render exactly this.
    """
    env = environment_from_contract(spec_dir)
    if is_nix_environment(env) and (env.get("language") or "").lower() == "go":
        return env  # type: ignore[return-value]  # narrowed by is_nix_environment
    return {
        "language": "go",
        "toolchain": {},
        "system_packages": ["gotestsum", "gocover-cobertura"],
        "verify_commands": ["go test ./..."],
        "provisioning": {"method": "nix", "generated": True},
        "network": "none",
    }


def _go_module_dir(project_dir: Path, hint: Path | None) -> Path:
    """Resolve the Go module root (the dir holding ``go.mod``) inside the worktree.

    ``go test ./...`` runs from the module root, which is often a subdir of the
    clone (e.g. ``scenarios/go-hello/``), not the repo root. Prefer the module
    enclosing ``hint`` (a test file / target path — walk up to its ``go.mod``);
    else the shallowest ``go.mod`` under the project; else the project root.
    Always returns a directory at or below ``project_dir``.
    """
    pd = Path(project_dir).resolve()
    if hint is not None:
        start = Path(hint)
        start = start if start.is_absolute() else pd / start
        if start.suffix:  # a file path -> begin the walk at its parent dir
            start = start.parent
        start = start.resolve()
        if pd == start or pd in start.parents:
            for d in (start, *start.parents):
                if (d / "go.mod").is_file():
                    return d
                if d == pd:
                    break
    mods = sorted(
        (m for m in pd.rglob("go.mod") if m.is_file()), key=lambda p: len(p.parts)
    )
    return mods[0].parent if mods else pd


def run_gotest_lane_via_nix(
    spec_dir: Path,
    project_dir: Path,
    *,
    hint: Path | None = None,
    extra_env: dict[str, str] | None = None,
    timeout: int = 600,
) -> DockerRunResult | None:
    """Run the Go module's tests inside the per-task Nix dev shell as a k8s Job.

    The Go toolchain (go + gotestsum + gocover-cobertura) comes from the
    materialized flake — declared in the contract ``environment`` or, for a
    spec-ingest task with no contract, synthesized by :func:`go_environment` — so
    the verify env never drifts from the build env. The worktree is co-mounted at
    ``_NIX_MOUNT``; the Job ``cd``s into the resolved module root and runs
    ``gotestsum ... ./...`` over the WHOLE module (Go ``_test.go`` files live next
    to the code they test, so there's no single-file staging like the pytest
    lane), then converts the coverage profile to Cobertura XML. JUnit + coverage
    are written into a staging dir on the worktree and collected back as a
    DockerRunResult, exactly like :func:`run_pytest_lane_via_nix`.

    Returns None when the sandbox isn't configured (caller falls back).
    """
    mount = _NIX_MOUNT
    env = go_environment(spec_dir)
    plan = materialize_flake(spec_dir, project_dir, env=env)
    if plan is None:
        return None
    sandbox: ExecutionSandbox | None = nix_runner_from_env()
    if sandbox is None:
        _log.info("run_gotest_lane_via_nix: TFACTORY_NIX_RUNNER_IMAGE unset; skipping")
        return None

    pd = Path(project_dir)
    module_dir = _go_module_dir(pd, hint)
    rel = (
        "." if module_dir == pd.resolve() else str(module_dir.relative_to(pd.resolve()))
    )
    run_dir = mount if rel == "." else f"{mount}/{rel}"

    stage = pd / _GOTEST_STAGE
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True, exist_ok=True)
    # The Job runs as a non-root uid against the co-mounted worktree; make the
    # staging dir writable so gotestsum/gocover-cobertura can drop reports there.
    with contextlib.suppress(OSError):
        stage.chmod(0o777)

    exports = "".join(
        f"export {k}={_shquote(str(v))}\n" for k, v in (extra_env or {}).items()
    )
    sd = f"{mount}/{_GOTEST_STAGE}"
    # gotestsum runs `go test ./...` (emitting JUnit) and forwards -coverprofile;
    # set +e + the marker line recover the real test exit (the wrapper exits 0 so
    # the Job "succeeds"); gocover-cobertura converts the profile to Cobertura XML.
    gotest_cmd = (
        f"cd {run_dir} && "
        f"gotestsum --junitfile={sd}/junit.xml --format=testname -- "
        f"-coverprofile={sd}/cover.out -covermode=atomic ./... 2>&1; "
        f"echo __GOTEST_EXIT=$?; "
        f"gocover-cobertura < {sd}/cover.out > {sd}/coverage.xml 2>/dev/null || true"
    )
    (pd / _JOB_SCRIPT).write_text(
        "#!/usr/bin/env bash\nset +e\n" + exports + gotest_cmd + "\n",
        encoding="utf-8",
    )
    job_cmd = f"nix develop path:{mount}#default --command bash {mount}/{_JOB_SCRIPT}"
    try:
        res = sandbox.run([job_cmd], workdir=str(pd), timeout=timeout)
    finally:
        (pd / _JOB_SCRIPT).unlink(missing_ok=True)

    code = _parse_exit_marker(res.stdout, "__GOTEST_EXIT=")
    junit = stage / "junit.xml"
    cov = stage / "coverage.xml"
    return DockerRunResult(
        returncode=code,
        stdout=res.stdout or "",
        stderr="",
        junit_xml_path=junit if junit.is_file() else None,
        coverage_xml_path=cov if cov.is_file() else None,
        argv=["nix", "develop", f"path:{mount}#default", "--", "go", "test", "./..."],
    )


def _shquote(s: str) -> str:
    """Minimal POSIX single-quote escaping for an in-shell `export`."""
    return "'" + s.replace("'", "'\"'\"'") + "'"


def _parse_exit_marker(output: str | None, prefix: str) -> int:
    """Recover an exit code from a ``<prefix><n>`` marker line emitted by a Job.

    The Job wraps the test command in a shell that always exits 0 (so the Job is
    "succeeded") and appends the real code on a marker line. Returns the last
    parseable marker, or 1 when none is present (treat a missing marker as a
    failure rather than a false pass)."""
    code = 1
    for line in (output or "").splitlines():
        if line.startswith(prefix):
            with contextlib.suppress(ValueError):
                code = int(line.split("=", 1)[1])
    return code


def _parse_pytest_exit(output: str | None) -> int:
    """Recover the pytest exit code from the ``__PYTEST_EXIT=<n>`` marker line."""
    return _parse_exit_marker(output, "__PYTEST_EXIT=")


def _slice_marked_segment(output: str, begin: str, end_prefix: str) -> str:
    """Return the text between a ``begin`` marker line and the next ``end_prefix``
    marker line — one deploy step's captured output. Best-effort: returns "" when
    the markers aren't both present (the step's status still comes from the exit
    marker, so a missing segment never changes the verdict)."""
    lines = (output or "").splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if ln.strip() == begin)
    except StopIteration:
        return ""
    seg: list[str] = []
    for ln in lines[start + 1 :]:
        if ln.startswith(end_prefix):
            break
        seg.append(ln)
    return "\n".join(seg)


def deploy_environment() -> dict[str, object]:
    """Synthesize the Nix env manifest for the DRY-RUN deploy lane (#597).

    The deploy *verifier's* toolchain — not the app's — so it is synthesized here
    rather than read from the contract ``environment`` (which declares the build/
    test toolchain). Pins the static IaC scanners we can run hermetically in a Job
    (:data:`_DEPLOY_NIX_TOOLS`); the heavier dry-run rungs ride the #597 follow-up.
    Mirrors :func:`go_environment`'s synthesis shape so ``generate_flake`` renders
    a reproducible single-devShell flake.
    """
    return {
        "language": "python",  # harmless base shell; the scanners ride as system_packages
        "toolchain": {},
        "system_packages": list(_DEPLOY_NIX_TOOLS),
        "verify_commands": [],
        "provisioning": {"method": "nix", "generated": True},
        "network": "none",
    }


def run_deploy_lane_via_nix(  # noqa: PLR0913 - explicit keyword-only deploy-lane knobs
    project_dir: Path,
    *,
    files: list[str],
    required_scans: list[str] | None = None,
    target_level: str = "VAL-2",
    sandbox: ExecutionSandbox | None = None,
    timeout: int = 600,
) -> DeployLaneResult | None:
    """Run the RFC-0013 DRY-RUN deploy lane inside a per-task Nix Job (#597).

    The live k3d verify pod ships no terraform/helm/kubectl/scanners, so the
    deploy executor's local runner could only ever record an honest ``not_run``
    (VAL-0). This mirrors :func:`run_gotest_lane_via_nix`: it writes a generated
    deploy flake into a dedicated ``.tf_deploy`` dir in the co-mounted worktree
    (never clobbering an app-owned ``flake.nix``), runs the available-tool deploy
    steps in ONE Nix Job against the IaC at the worktree root, recovers each
    step's exit code from a marker line, then feeds the results to
    :func:`run_deploy_lane` so the honest, gate-normalized VAL block is built by
    the same code the local path uses (a tool absent from the flake stays an
    honest ``not_run`` — never a silent pass).

    Returns a :class:`DeployLaneResult`, or ``None`` when the Nix sandbox isn't
    configured or no deploy step can run in-Job (caller falls back to the local
    runner, which yields the same honest block).
    """
    from tools.runners.deploy_runner import (  # noqa: PLC0415 - lazy, avoids import cost
        StepResult,
        plan_deploy_steps,
        run_deploy_lane,
    )

    sandbox = sandbox if sandbox is not None else nix_runner_from_env()
    if sandbox is None:
        _log.info("run_deploy_lane_via_nix: TFACTORY_NIX_RUNNER_IMAGE unset; skipping")
        return None

    available = set(_DEPLOY_NIX_TOOLS)
    planned = plan_deploy_steps(files, required_scans=required_scans)
    runnable = [s for s in planned if s.tool in available]
    if not runnable:
        # No deploy step is runnable in a hermetic Job; let the caller fall back
        # to the local runner (which produces the identical honest not_run block).
        return None

    pd = Path(project_dir)
    mount = _NIX_MOUNT

    # Write the generated deploy flake into a dedicated subdir so it never
    # overwrites an app-owned flake.nix at the worktree root.
    flake_dir = pd / _DEPLOY_STAGE
    flake_dir.mkdir(parents=True, exist_ok=True)
    (flake_dir / _FLAKE).write_text(
        generate_flake(deploy_environment()), encoding="utf-8"
    )

    # One Job runs every runnable step, each bracketed by markers so we can
    # recover per-step status + output. ``cd`` to the worktree root so the
    # scanners see the IaC; ``set +e`` keeps a failing scan from aborting the rest.
    body = [f"cd {mount}"]
    for i, step in enumerate(runnable):
        argv = " ".join(_shquote(a) for a in step.argv)
        body.append(f"echo __DEPLOY_STEP_{i}_BEGIN")
        body.append(f"{argv}; echo __DEPLOY_STEP_{i}_EXIT=$?")
    (pd / _JOB_SCRIPT).write_text(
        "#!/usr/bin/env bash\nset +e\n" + "\n".join(body) + "\n", encoding="utf-8"
    )

    job_cmd = (
        f"nix develop path:{mount}/{_DEPLOY_STAGE}#default "
        f"--command bash {mount}/{_JOB_SCRIPT}"
    )
    try:
        res = sandbox.run([job_cmd], workdir=str(pd), timeout=timeout)
    finally:
        (pd / _JOB_SCRIPT).unlink(missing_ok=True)

    out = res.stdout or ""
    precomputed: dict[tuple[str, ...], StepResult] = {}
    for i, step in enumerate(runnable):
        code = _parse_exit_marker(out, f"__DEPLOY_STEP_{i}_EXIT=")
        seg = _slice_marked_segment(
            out, f"__DEPLOY_STEP_{i}_BEGIN", f"__DEPLOY_STEP_{i}_EXIT="
        )
        status = "passed" if code == 0 else "failed"
        precomputed[tuple(step.argv)] = StepResult(
            name=step.name,
            level=step.level,
            status=status,
            returncode=code,
            reason=None if status == "passed" else f"exit {code}",
            output=seg,
        )

    def _run_fn(argv: tuple[str, ...]) -> StepResult:
        return precomputed.get(
            tuple(argv),
            StepResult(
                name=argv[0],
                level="VAL-0",
                status="not_run",
                reason="step not executed in deploy Job",
            ),
        )

    # Rebuild the honest VAL block through the shared core: tool_available reflects
    # exactly what the deploy flake provides, so terraform/kubectl/prowler are an
    # honest not_run while tfsec/trivy carry their real Job verdict.
    return run_deploy_lane(
        files,
        required_scans=required_scans,
        target_level=target_level,
        run_fn=_run_fn,
        tool_available=lambda t: t in available,
    )


def _stage_browser_specs(spec_dir: Path, project_dir: Path) -> int:
    """Copy the GENERATED browser specs from the spec workspace into a clean dir
    in the co-mounted worktree, so the Job runs THOSE — not whatever stale
    *.spec.ts the project repo happens to carry (a real bug found live: the Job
    picked up a leftover frontend-board.spec.ts pointing at the wrong port).
    Returns the number staged.
    """
    import shutil

    dest = Path(project_dir) / _E2E_STAGE
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True, exist_ok=True)
    n = 0
    for src in sorted((Path(spec_dir) / "tests").rglob("*.spec.ts")):
        shutil.copy2(src, dest / src.name)
        n += 1
    return n


def _write_pw_config(project_dir: Path, *, port: int) -> None:
    """Force screenshots from the RUNNER, not the generated test. `screenshot:
    'on'` captures one per test into outputDir even when the spec never calls
    page.screenshot (the generated specs don't). baseURL covers specs that read
    it; the in-job BASE_URL env covers those that read process.env.BASE_URL.
    """
    cfg = f"""import {{ defineConfig }} from '@playwright/test';
export default defineConfig({{
  testDir: '{_E2E_STAGE}',
  outputDir: '{_SHOTS}',
  reporter: [['junit', {{ outputFile: '{_SHOTS}/junit.xml' }}], ['list']],
  use: {{
    baseURL: 'http://127.0.0.1:{port}',
    screenshot: 'on',
    video: 'on',
    trace: 'off',
  }},
}});
"""
    (Path(project_dir) / _PW_CONFIG).write_text(cfg, encoding="utf-8")


def run_browser_evidence(
    spec_dir: Path, project_dir: Path, *, port: int = 8099, mount: str = "/work"
) -> dict | None:
    """Materialize the flake, dispatch a Nix k8s Job that serves the app + runs
    the GENERATED browser specs, and collect screenshots into
    ``findings/screenshots``.

    Returns a result dict, or None when there's no nix env, the sandbox isn't
    configured, or there are no generated browser specs to run (caller records
    the gap honestly). Proven live 2026-06-17.
    """
    env = environment_from_contract(spec_dir)
    plan = materialize_flake(spec_dir, project_dir, env=env)
    if plan is None:
        return None
    # Same unified-seam consumption as run_pytest_lane_via_nix (#426).
    sandbox: ExecutionSandbox | None = nix_runner_from_env()
    if sandbox is None:
        _log.info("run_browser_evidence: TFACTORY_NIX_RUNNER_IMAGE unset; skipping")
        return None

    n_specs = _stage_browser_specs(spec_dir, project_dir)
    if n_specs == 0:
        _log.info("run_browser_evidence: no generated *.spec.ts to run; skipping")
        return {
            "ok": False,
            "output_tail": "no browser specs",
            "serve_command": None,
            "screenshots": [],
            "specs": 0,
        }
    _write_pw_config(project_dir, port=port)

    serve = detect_serve_command(project_dir, env, port=port)
    # Scope the run to the staged config (NOT the contract's generic
    # "playwright test", which would also pick up stale repo specs). Export the
    # URL env the generated specs read.
    steps = [
        f"export BASE_URL=http://127.0.0.1:{port}",
        f"export APP_URL=http://127.0.0.1:{port}",
        *build_browser_job_command(
            [f"playwright test --config {_PW_CONFIG}"],
            serve_command=serve,
            port=port,
            shots_dir=_SHOTS,
        ),
    ]
    (Path(project_dir) / _JOB_SCRIPT).write_text(
        "#!/usr/bin/env bash\nset -e\n" + "\n".join(steps) + "\n", encoding="utf-8"
    )
    job_cmd = f"nix develop path:{mount}#default --command bash {mount}/{_JOB_SCRIPT}"
    try:
        res = sandbox.run([job_cmd], workdir=str(project_dir), timeout=900)
    finally:
        for f in (_JOB_SCRIPT, _PW_CONFIG):
            (Path(project_dir) / f).unlink(missing_ok=True)
        import shutil as _sh

        _sh.rmtree(Path(project_dir) / _E2E_STAGE, ignore_errors=True)

    findings = Path(spec_dir) / "findings"
    # Per-spec pass/fail from the Job's junit — this is the REAL browser-lane
    # signal (the in-container DockerRunner path is blocked in k3d). The evaluator
    # turns it into the stability signal so a passing UI test can be ACCEPTED and
    # its acceptance criterion reach VERIFIED.
    results = parse_browser_junit(Path(project_dir) / _SHOTS / "junit.xml")
    shots = collect_screenshots(project_dir, findings)
    videos = collect_videos(project_dir, findings)
    if results:
        findings.mkdir(parents=True, exist_ok=True)
        (findings / "browser_evidence.json").write_text(json.dumps(results, indent=2))
    return {
        "ok": res.returncode == 0,
        "output_tail": (res.stdout or "")[-2000:],
        "serve_command": serve,
        "specs": n_specs,
        "screenshots": [str(p) for p in shots],
        "videos": [str(p) for p in videos],
        "results": results,
    }


def parse_browser_junit(junit_path: Path) -> dict[str, bool]:
    """Map each browser spec file -> passed (no failures/errors), from playwright
    junit (``<testsuite name="<spec>.spec.ts" failures=F errors=E>``). Pure;
    returns {} when the file is absent/unparseable."""
    import xml.etree.ElementTree as ET

    p = Path(junit_path)
    if not p.is_file():
        return {}
    try:
        root = ET.parse(p).getroot()
    except Exception:  # noqa: BLE001 - a broken report is just "no evidence"
        return {}
    out: dict[str, bool] = {}
    for suite in root.iter("testsuite"):
        name = suite.get("name") or ""
        if not name:
            continue
        failures = int(suite.get("failures") or 0)
        errors = int(suite.get("errors") or 0)
        tests = int(suite.get("tests") or 0)
        out[name] = tests > 0 and failures == 0 and errors == 0
    return out


def build_browser_job_command(
    verify_commands: list[str],
    *,
    serve_command: str | None = None,
    port: int = 8099,
    shots_dir: str = "shots",
) -> list[str]:
    """The bash steps a browser-lane Job runs inside `nix develop`.

    Optionally starts the app (``serve_command``, backgrounded) and waits for it
    before running the browser verify commands, with screenshots collected under
    ``shots_dir`` (on the co-mounted worktree, so TFactory reads them back). Pure
    string assembly — the proven live recipe, no I/O.
    """
    steps: list[str] = [f"mkdir -p {shots_dir}"]
    if serve_command:
        steps.append(f"{serve_command} >/tmp/app.log 2>&1 &")
        steps.append(
            f"for i in $(seq 1 30); do "
            f"curl -fsS http://127.0.0.1:{port}/ >/dev/null 2>&1 && break; sleep 1; "
            f"done"
        )
    steps.extend(verify_commands)
    return steps


def collect_screenshots(
    project_dir: Path, findings_dir: Path, *, shots: str = "shots"
) -> list[Path]:
    """Copy PNG/junit evidence the Job wrote into the worktree into findings/.

    Returns the destination paths. No-op (empty list) when the Job produced none.
    """
    import shutil

    src = Path(project_dir) / shots
    if not src.is_dir():
        return []
    dest = Path(findings_dir) / "screenshots"
    dest.mkdir(parents=True, exist_ok=True)
    out: list[Path] = []
    # Recurse: playwright's screenshot:'on' writes PNGs into per-test subdirs of
    # outputDir. Flatten into findings/screenshots with a path-derived name so
    # collisions across tests don't clobber.
    for f in sorted(src.rglob("*")):
        if f.suffix.lower() in (".png", ".xml") and f.is_file():
            rel = f.relative_to(src)
            name = "__".join(rel.parts) if len(rel.parts) > 1 else f.name
            target = dest / name
            shutil.copy2(f, target)
            out.append(target)
    return out


def collect_videos(
    project_dir: Path, findings_dir: Path, *, shots: str = "shots"
) -> list[Path]:
    """Copy Playwright recordings (webm) the Job wrote into findings/videos/.

    `video: 'on'` writes one ``video.webm`` per test into a per-test subdir of
    outputDir. Flatten into findings/videos with a path-derived name so recordings
    from different tests don't clobber. Returns destination paths; empty when none.
    """
    import shutil

    src = Path(project_dir) / shots
    if not src.is_dir():
        return []
    dest = Path(findings_dir) / "videos"
    out: list[Path] = []
    for f in sorted(src.rglob("*")):
        if f.suffix.lower() in (".webm", ".mp4") and f.is_file():
            dest.mkdir(parents=True, exist_ok=True)
            rel = f.relative_to(src)
            name = "__".join(rel.parts) if len(rel.parts) > 1 else f.name
            target = dest / name
            shutil.copy2(f, target)
            out.append(target)
    return out


def environment_from_contract(spec_dir: Path) -> dict | None:
    """Return the contract ``environment`` block for a spec, or None."""
    contract = read_task_contract(spec_dir)
    if not contract:
        return None
    env = contract.get("environment")
    return env if isinstance(env, dict) else None


def is_nix_environment(env: dict | None) -> bool:
    if not env:
        return False
    return (env.get("provisioning") or {}).get("method") == "nix"


def materialize_flake(
    spec_dir: Path, project_dir: Path, *, env: dict | None = None
) -> NixPlan | None:
    """Write ``flake.nix`` into ``project_dir`` from the contract environment.

    Returns a NixPlan (flake dir + commands) when the contract declares a nix
    environment, else None (caller falls back to the legacy lane runner). Does
    NOT overwrite a repo-owned flake unless the manifest is ``generated``.
    """
    env = env if env is not None else environment_from_contract(spec_dir)
    if not is_nix_environment(env):
        return None
    assert env is not None  # narrowed by is_nix_environment

    m = Manifest.from_contract(env)
    flake_path = Path(project_dir) / _FLAKE
    repo_has_flake = flake_path.exists()

    if repo_has_flake and not m.provisioning_generated:
        _log.info("nix_env: respecting repo-owned %s (manifest not generated)", _FLAKE)
    else:
        flake_path.write_text(
            generate_flake(env, project_dir=project_dir), encoding="utf-8"
        )
        _log.info("nix_env: wrote generated %s for %s", _FLAKE, spec_dir.name)

    return NixPlan(
        flake_dir=Path(project_dir),
        verify_commands=list(m.verify_commands),
        proof_verify=list(m.proof_verify),
        network=m.network or "none",
        generated=not (repo_has_flake and not m.provisioning_generated),
    )
