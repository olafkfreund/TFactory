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
import itertools
import json
import logging
import os
import shutil
import tempfile
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from agents.preflight_static import package_root_rel_paths, requirements_files
from agents.task_contract import read_task_contract
from tools.runners.docker_runner import DockerRunResult
from tools.runners.nix_provisioner import (
    Manifest,
    generate_flake,
    generate_lock,
    nix_develop_argv,
)

if TYPE_CHECKING:
    from agents.execution_sandbox import ExecutionSandbox
    from tools.runners.deploy_runner import DeployLaneResult
    from tools.runners.kube_sandbox import KubeJobSandbox

_log = logging.getLogger(__name__)

_FLAKE = "flake.nix"
_FLAKE_LOCK = "flake.lock"


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


def nix_in_image() -> bool:
    """True when nix Jobs should source ``/nix`` from their image, not the PVC.

    The warm ``tfactory-nix-store`` PVC is RWO ``local-path``: only ONE pod can
    mount it at a time, so the evaluator's concurrent per-test / stability-sample
    / mutation Jobs serialise on a single mount (#623). Its PV is also
    nodeAffinity-pinned to whichever node first consumed it — today that is the
    same node as ``tfactory-data``, which is luck, not design: if it ever
    re-provisions onto the other node, every Job mounting both RWO claims becomes
    unschedulable outright (no node satisfies both affinities). That is exactly
    what happened to AIFactory (Factory#253, AIFactory#830).

    Dropping the mount is not a correctness trade: ``build_job_manifest``'s seed
    initContainer copies the image's own ``/nix`` into the PVC, so the image IS
    the store the warm cache is seeded from. The cost is the closures realised
    *during* a task — re-fetched from the binary cache per Job instead of
    persisting. Speed for concurrency.

    Requires the build-user caps (#660): a cold ``/nix`` substitutes most paths
    but still builds the shell env locally, which needs SETUID/SETGID/KILL.
    Proven on the factory cluster — a provisioner-generated flake, no nix-store
    PVC, resolved from the image + cache.nixos.org and ran the lane green.
    """
    return os.environ.get("TFACTORY_NIX_IN_IMAGE", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def nix_runner_from_env() -> KubeJobSandbox | None:
    """Build a KubeJobSandbox from the deployment's TFACTORY_* env, or None when
    the Nix-lane sandbox isn't configured (so callers degrade gracefully)."""
    image = os.environ.get("TFACTORY_NIX_RUNNER_IMAGE")
    if not image:
        return None
    from tools.runners.kube_sandbox import KubeJobSandbox

    pvc = os.environ.get("TFACTORY_WORKSPACES_PVC")
    ns = os.environ.get("TFACTORY_SANDBOX_NAMESPACE", "factory")
    # RFC-0016 #197: opt-in warm /nix/store PVC so the toolchain closure persists
    # across Nix lane Jobs instead of cold-fetching each run. Absent → no mount,
    # so nothing breaks if the PVC is not provisioned.
    # #623: skipped entirely when nix comes from the image — the PVC is RWO, so
    # it is also the mutex the evaluator's concurrent Jobs serialise on. See
    # nix_in_image.
    nix_store_pvc = (
        None if nix_in_image() else (os.environ.get("TFACTORY_NIX_STORE_PVC") or None)
    )
    # #623: the data root (workspaces PVC) is NOT always mounted at the control
    # plane's default /home/nonroot/.tfactory — the verify Job mounts it at /work.
    # The sandbox derives each Job's co-mount subPath via pvc_subpath(workdir,
    # data_root); if data_root is wrong, pvc_subpath returns None and the nested
    # Nix Job co-mounts nothing (empty /work → the SUT is unimportable → every AC
    # rejects). TFACTORY_DATA_ROOT lets the enclosing context declare where the
    # PVC actually is; absent → the KubeJobSandbox default is unchanged.
    kw: dict[str, str] = {}
    data_root = os.environ.get("TFACTORY_DATA_ROOT")
    if data_root:
        kw["data_root"] = data_root
    return KubeJobSandbox(
        image, namespace=ns, repo_pvc=pvc, nix_store_pvc=nix_store_pvc, **kw
    )


_JOB_SCRIPT = "_tf_nix_job.sh"
_E2E_STAGE = ".tf_e2e"  # staged generated browser specs (in the worktree)
_PW_CONFIG = "_tf_pw.config.ts"
_SHOTS = "shots"
# Where the Job pip-installs the SUT's deps (#764). S108 is about host temp
# files; this is a path INSIDE the Job container, chosen because /tmp is the one
# location its non-root uid can write — the co-mounted /work is read-only to it.
_DEPS_TARGET = "/tmp/tf_sut_deps"  # noqa: S108
_PYTEST_STAGE = ".tf_pytest"  # staged junit/coverage the Nix Job writes back
_GOTEST_STAGE = ".tf_gotest"  # staged junit/coverage the Go Nix Job writes back


def _in_job_pythonpath(scratch: Path, mount: str) -> str:
    """PYTHONPATH for the in-Job pytest run, as a colon-joined string.

    `<mount>/src` + `<mount>` cover flat and src-layout repos and miss a
    monorepo entirely, whose packages sit at e.g. `apps/web-server/server`.
    Collection then fails before a single assertion runs and every acceptance
    criterion reports an import error against correct code (#756).

    Roots are discovered from the scratch copy the Job co-mounts, so a repo-
    relative root maps 1:1 onto its in-Job path. The two historical entries stay,
    last, so existing layouts behave exactly as before.
    """
    roots = [
        f"{mount}/{rel}" if rel != "." else mount
        for rel in package_root_rel_paths(scratch)
    ]
    roots += [p for p in (f"{mount}/src", mount) if p not in roots]
    return ":".join(roots)


_DEPLOY_STAGE = ".tf_deploy"  # generated deploy flake dir (co-mounted in the Job)
_NIX_MOUNT = "/work"  # where KubeJobSandbox co-mounts the worktree in the Job

# Emitted by the api-lane Job script when the self-served SUT never accepts a
# connection — so a boot failure reads as infra, not a silent endpoint-test bug.
_APP_NOT_HEALTHY_MARKER = "__TF_APP_NOT_HEALTHY__"

# The deploy-lane tools we can run hermetically inside a per-task Nix Job (#597,
# #603): ``tfsec`` + ``trivy`` are pure Go binaries (no insecure transitive deps),
# ``opentofu`` is the free Terraform (``tofu``) — all evaluate cleanly in the flake
# and run offline with no cluster. ``tfsec`` = terraform report-only scan, ``trivy
# config`` = multi-framework misconfig gate (``--skip-check-update`` = embedded
# rego checks), ``opentofu`` = the ``tofu init``/``validate``/``plan`` rung
# (init -backend=false installs any declared providers via the Job's network).
# config`` = multi-framework misconfig gate (``--skip-check-update`` = embedded
# rego checks), ``opentofu`` = the ``tofu init``/``validate``/``plan`` rung
# (init -backend=false installs any declared providers via the Job's network).
# ``checkov`` was dropped (#603): in the pinned nixpkgs it pulls
# ``python3.13-ecdsa`` marked insecure (CVE-2024-23342), so ``pkgs.checkov`` refuses
# to evaluate and aborts the whole ``nix develop`` (proven live 2026-06-30). Unfree
# ``terraform`` stays an honest ``not_run``. ``kubectl`` (for the VAL-2
# ``apply --dry-run=server`` rung, #603) is included but GATED at dispatch on the
# deploy dry-run SA being configured (:func:`run_deploy_lane_via_nix`): without
# the SA + RBAC it is dropped so the step stays an honest ``not_run`` rather than
# a dry-run that fails for want of cluster auth. A tool absent from the runnable
# set is never a silent pass — ``run_deploy_lane``'s ``tool_available`` records
# it as ``not_run``.
_DEPLOY_NIX_TOOLS: tuple[str, ...] = ("tfsec", "trivy", "opentofu", "kubectl")

# Env naming the ServiceAccount that grants the deploy Job in-cluster RBAC for
# ``kubectl apply --dry-run=server`` (#603). Unset → the kubectl rung stays an
# honest not_run (no SA is attached and kubectl is dropped from the runnable set).
_DEPLOY_DRYRUN_SA_ENV = "TFACTORY_DEPLOY_DRYRUN_SA"

# Gates Nix-lane Job dispatch within a verify process. run() offloads to threads
# (#620), so a threading primitive is the right choice — but WHICH one depends on
# the store regime:
#   * shared warm-store PVC: it is RWO, so only one Job can co-mount it at a time
#     (#623) — a strict Lock serialises them.
#   * nix-in-image (nix_in_image()): each Job's /nix is image-local, there is NO
#     shared mount to contend for, and that mode exists precisely to buy "speed
#     for concurrency" (see nix_in_image.__doc__). A strict Lock here re-serialises
#     the very fan-out (S x (3 + mutants) Jobs) in-image was meant to parallelise —
#     the single biggest reason a real verify run drags on. Use a BoundedSemaphore
#     so the Jobs run concurrently under a ceiling, rather than one at a time.
_NIX_JOB_LOCK = threading.Lock()


def _nix_job_concurrency() -> int:
    """Max concurrent nix Jobs when the store is image-local (no shared PVC).

    ponytail: fixed ceiling (env-overridable), not autoscaled — a single-node k3d
    can't absorb an unbounded fan-out of 500 MB-image Job pods at once. Raise
    TFACTORY_NIX_JOB_CONCURRENCY (or wire it to node count) if throughput matters.
    """
    try:
        return max(1, int(os.getenv("TFACTORY_NIX_JOB_CONCURRENCY", "4")))
    except ValueError:
        return 4


# Concurrency gate for the image-local regime. Sized once at import; the strict
# _NIX_JOB_LOCK still guards the shared-PVC path.
_NIX_JOB_SEM = threading.BoundedSemaphore(_nix_job_concurrency())


def _nix_dispatch_gate() -> contextlib.AbstractContextManager[bool]:
    """The right dispatch gate for the current store regime (see _NIX_JOB_LOCK).

    Both a Lock and a BoundedSemaphore are ``with``-usable context managers, so
    the call site treats them uniformly.
    """
    return _NIX_JOB_SEM if nix_in_image() else _NIX_JOB_LOCK


def _build_pytest_cmd(mount: str, name: str, reruns: int) -> str:
    """The in-shell pytest command for the Job script.

    ``reruns<=1`` is the byte-identical single run. ``reruns>1`` (#776) repeats the
    SAME pytest in the ONE dev shell, each pass wrapped in a ``__PYTEST_RUN=<i>`` /
    ``__PYTEST_EXIT=<code>`` pair so ``parse_pytest_exits`` recovers per-run codes —
    paying the per-Job setup (re-lock, pip, ``nix develop`` entry) once, not N times.
    """
    one = (
        f"python -m pytest tests/{name} -p no:cacheprovider -q "
        f"--junitxml={mount}/{_PYTEST_STAGE}/junit.xml "
        f"--cov-report=xml:{mount}/{_PYTEST_STAGE}/coverage.xml --cov=. 2>&1; "
        "echo __PYTEST_EXIT=$?"
    )
    if reruns <= 1:
        return f"cd {mount} && {one}"
    loop = "".join(f"echo __PYTEST_RUN={i}\n{one}\n" for i in range(1, reruns + 1))
    return f"cd {mount}\n{loop}"


def _stage_mutants(mutant_files: list[Path] | None, tests_dir: Path) -> list[str]:
    """Copy each mutation-candidate test into the Job's ``tests/`` dir; return the
    basenames that staged successfully (#776 Stage 1b). Best-effort per file so a
    single unwritable candidate never takes the whole batch down.
    """
    names: list[str] = []
    for mf in mutant_files or []:
        mn = Path(mf).name
        with contextlib.suppress(OSError):
            shutil.copy2(mf, tests_dir / mn)
            names.append(mn)
    return names


def _build_mutants_cmd(mutant_names: list[str]) -> str:
    """#776 Stage 1b: run each staged mutant test ONCE in the same dev shell as
    the stability batch, wrapping each in a ``__MUT_RUN=<k>`` / ``__MUT_EXIT=<code>``
    pair so ``parse_mut_exits`` recovers per-mutant codes.

    A SEPARATE exit marker (``__MUT_EXIT``, not ``__PYTEST_EXIT``) keeps
    ``parse_pytest_exits`` — which reads the stability samples — from mistaking a
    mutant's exit code for a stability run. Mutants only need the exit code
    (KILLED = non-zero), so no junit/coverage. Assumes the shell already ``cd``'d
    into the mount (the stability command does).
    """
    parts = [
        f"echo __MUT_RUN={k}\n"
        f"python -m pytest tests/{mn} -p no:cacheprovider -q 2>&1; "
        "echo __MUT_EXIT=$?\n"
        for k, mn in enumerate(mutant_names, start=1)
    ]
    return "".join(parts)


def run_pytest_lane_via_nix(  # noqa: PLR0913, PLR0915 - api-lane self-serve knobs + one linear staging flow
    spec_dir: Path,
    project_dir: Path,
    test_file: Path,
    *,
    extra_env: dict[str, str] | None = None,
    timeout: int = 900,
    serve_command: str | None = None,
    serve_port: int | None = None,
    reruns: int = 1,
    mutant_files: list[Path] | None = None,
) -> DockerRunResult | None:
    """Run ONE pytest file inside the per-task Nix dev shell as a k8s Job.

    ``reruns`` (>1, #776) runs the SAME pytest ``reruns`` times inside the ONE
    dev shell, emitting a ``__PYTEST_RUN=<i>`` / ``__PYTEST_EXIT=<code>`` pair per
    pass. This is the stability batch: the ~minutes of per-Job cost (re-lock, pip
    install the SUT, ``nix develop`` entry) is paid ONCE instead of once per
    stability sample. ``reruns=1`` is byte-identical to the pre-#776 single run.
    Callers that need the per-run codes parse them with ``parse_pytest_exits``.

    When ``serve_command`` is given (the api lane with no external target, #612),
    the Job first boots the SUT in the SAME pod at ``127.0.0.1:serve_port``,
    waits for it to accept a connection, and exports ``TFACTORY_TARGET_URL`` /
    ``APP_URL`` before pytest — so an endpoint test reaches the running app
    instead of raising ``KeyError`` on an unset URL. Mirrors the browser lane's
    in-Job serve (``build_browser_job_command``). If the app never comes up, a
    ``_APP_NOT_HEALTHY_MARKER`` line is emitted and logged so the failure reads
    as infra, not an AC failure.

    The toolchain (python + pytest + pytest-cov) comes from the materialized flake
    (declared in the contract ``environment``), not the image — so the verify env
    matches the build env with no drift. To keep concurrent runs from clobbering
    the shared project checkout, the SUT is copied into a per-run scratch dir under
    the workspaces PVC (#623); the flake + the specific test are materialized there,
    the scratch is co-mounted at ``_NIX_MOUNT`` so ``from <module> import ...``
    resolves like the DockerRunner path, pytest writes junit + coverage into a
    staging dir on the scratch, and we copy those back (off the scratch) as a
    DockerRunResult-shaped result before removing the scratch.

    Returns None when there's no nix environment or the sandbox isn't configured,
    so the caller falls back to the host/docker runner. Mirrors the staging +
    collection pattern of ``run_browser_evidence``.
    """
    mount = _NIX_MOUNT
    env = environment_from_contract(spec_dir)
    if not is_nix_environment(env):
        return None
    # Consume the engine purely through the unified seam (#426): this lane works
    # with any ExecutionSandbox the factory returns, not just KubeJobSandbox.
    sandbox: ExecutionSandbox | None = nix_runner_from_env()
    if sandbox is None:
        _log.info("run_pytest_lane_via_nix: TFACTORY_NIX_RUNNER_IMAGE unset; skipping")
        return None

    # #623: isolate every run in a per-run scratch COPY of the checkout, instead of
    # mutating the shared project checkout in place. All specs for a project share
    # ONE checkout, and each run writes flake.nix + a job script + a staging dir
    # into it; overlapping specs/mutation runs clobber each other and flake a
    # passing test to consistent_fail (proven: the same test passes in isolation
    # but rejects in-pipeline). The scratch is a sibling under the workspaces PVC
    # (so the Job can co-mount it by subPath) and is removed after the run — the
    # same isolation the docker path gets from _stage_sut_into_scratch.
    src = Path(project_dir)
    scratch = src.parent / f"_nixrun-{uuid.uuid4().hex[:12]}"
    name = Path(test_file).name
    try:
        # Copy the SUT into the scratch (skip .git + any prior lane artifacts).
        shutil.copytree(
            src,
            scratch,
            ignore=shutil.ignore_patterns(
                ".git",
                _PYTEST_STAGE,
                _GOTEST_STAGE,
                _E2E_STAGE,
                _DEPLOY_STAGE,
                _JOB_SCRIPT,
            ),
            dirs_exist_ok=True,
        )
        plan = materialize_flake(spec_dir, scratch, env=env)
        if plan is None:
            return None
        pd = scratch
        tests_dir = pd / "tests"
        tests_dir.mkdir(exist_ok=True)
        staged_test = tests_dir / name
        # Stage the specific (generated or mutated) test into the scratch's tests/
        # dir so the co-mounted Job runs THAT file.
        if Path(test_file).resolve() != staged_test.resolve():
            shutil.copy2(test_file, staged_test)
        # #776 Stage 1b: also stage the subtask's mutation-candidate test variants
        # into tests/ so the SAME Job that runs the stability batch also runs each
        # mutant once — folding the ``M`` per-candidate mutation Jobs into this one.
        # The stability run below targets ``tests/<name>`` explicitly, so these
        # extra files are never collected by it.
        mutant_names = _stage_mutants(mutant_files, tests_dir)
        stage = pd / _PYTEST_STAGE
        stage.mkdir(parents=True, exist_ok=True)
        # The Job runs as a non-root uid against the co-mounted scratch; make the
        # staging dir writable so pytest can drop junit/coverage there.
        with contextlib.suppress(OSError):
            stage.chmod(0o777)

        # Inject seed/credentials the host/docker path would set, exported in-shell
        # so the test process inherits them (PYTHONHASHSEED, TFACTORY_TARGET_URL...).
        exports = "".join(
            f"export {k}={_shquote(str(v))}\n" for k, v in (extra_env or {}).items()
        )
        # src-layout: make ``<work>/src`` (and ``<work>``) importable so
        # ``from <pkg> import ...`` resolves inside the hermetic Nix Job — the host
        # runner does the same (evaluator._run_pytest_on_host). Without it a
        # ``src/<pkg>/`` package fails at collection and every AC shows as an error
        # rather than its real pass/fail. Prepended to any PYTHONPATH above (#615).
        # Only prepend the pip target when there is something to install, so a
        # repo with no requirements.txt gets a byte-identical export.
        _reqs = requirements_files(scratch)
        _prefix = f"{_DEPS_TARGET}:" if _reqs else ""
        srcpath = (
            f'export PYTHONPATH="{_prefix}{_in_job_pythonpath(scratch, mount)}'
            f'${{PYTHONPATH:+:$PYTHONPATH}}"\n'
        )
        # #764: install the SUT's own requirements into the Job. The flake's
        # curated PyPI->nixpkgs allowlist can never be complete, and a repo that
        # declares its deps only in requirements.txt gets nothing from it, so a
        # real app is unimportable and every AC comes back a collection error
        # against correct code — the same failure #759 fixed on the host path.
        # --target avoids ensurepip/venv questions in the nix interpreter, and
        # /tmp is writable by the Job's non-root uid while the co-mounted /work
        # is not. Best-effort per file (the script runs under `set +e`): one
        # unresolvable pin must not take the rest down, and the roots on
        # PYTHONPATH remain the fallback.
        deps_prelude = "".join(
            f"pip install -q --target {_DEPS_TARGET} -r "
            f"{mount}/{req.relative_to(scratch).as_posix()} 2>&1 | tail -2\n"
            for req in _reqs
        )
        # api lane self-serve (#612): boot the SUT in-Job at 127.0.0.1:port and
        # export TFACTORY_TARGET_URL before pytest, so the endpoint test reaches
        # the running app. Readiness = "accepts a connection" (curl WITHOUT -f):
        # a bare FastAPI has no `/` route, so requiring a 2xx would hang a healthy
        # app on its 404. ponytail: fixed 30s poll; raise only if slow apps show up.
        serve_prelude = ""
        if serve_command:
            url = f"http://127.0.0.1:{serve_port}"
            serve_prelude = (
                f"export TFACTORY_TARGET_URL={url}\n"
                f"export APP_URL={url}\n"
                f"cd {mount}\n"
                f"{serve_command} >/tmp/tf_app.log 2>&1 &\n"
                f"for i in $(seq 1 30); do "
                f"curl -sS -o /dev/null {url}/ >/dev/null 2>&1 && break; sleep 1; "
                f"done\n"
                f"curl -sS -o /dev/null {url}/ >/dev/null 2>&1 || "
                f'echo "{_APP_NOT_HEALTHY_MARKER} SUT did not accept a connection '
                f'on {url}; see /tmp/tf_app.log"\n'
            )
        pytest_cmd = _build_pytest_cmd(mount, name, reruns)
        if mutant_names:
            pytest_cmd = pytest_cmd + "\n" + _build_mutants_cmd(mutant_names)
        (pd / _JOB_SCRIPT).write_text(
            "#!/usr/bin/env bash\nset +e\n"
            + exports
            + srcpath
            + deps_prelude
            + serve_prelude
            + pytest_cmd
            + "\n",
            encoding="utf-8",
        )
        job_cmd = (
            f"nix develop path:{mount}#default --command bash {mount}/{_JOB_SCRIPT}"
        )
        # A per-task Nix build can fail transiently (the nixpkgs fetch can flake).
        # Retry ONLY when pytest never emitted its exit marker (a build/setup
        # failure before the test ran, not a genuine test failure) — so a real
        # fail (e.g. a caught hardcode) is never masked.
        attempts = 2
        # Gate dispatch by store regime: a strict lock for the RWO shared PVC
        # (co-mount contention, #623), a bounded semaphore for image-local /nix
        # so the fan-out actually runs concurrently (see _NIX_JOB_LOCK).
        with _nix_dispatch_gate():
            res = sandbox.run([job_cmd], workdir=str(pd), timeout=timeout)
            for attempt in range(1, attempts):
                if "__PYTEST_EXIT=" in (res.stdout or ""):
                    break
                _log.warning(
                    "run_pytest_lane_via_nix: no pytest exit marker (nix build "
                    "likely failed transiently); retry %d/%d. tail=%r",
                    attempt + 1,
                    attempts,
                    (res.stdout or "")[-300:],
                )
                res = sandbox.run([job_cmd], workdir=str(pd), timeout=timeout)

        stdout = res.stdout or ""
        app_unhealthy = bool(serve_command) and _APP_NOT_HEALTHY_MARKER in stdout
        if app_unhealthy:
            _log.warning(
                "run_pytest_lane_via_nix: SUT never became healthy for %s — the "
                "api test ran against a down app (infra, not an AC failure). "
                "tail=%r",
                Path(spec_dir).name,
                stdout[-300:],
            )
            # The marker is echoed BEFORE pytest, so a long pytest traceback can
            # push it out of check_stability's 500-char stdout_tail. Re-append it
            # at the very end so the failure_kind classifier still sees it and
            # buckets this as an infra not_run rather than an AC failure.
            stdout += f"\n{_APP_NOT_HEALTHY_MARKER} (SUT boot failed)\n"

        # Copy the small junit/coverage OFF the PVC scratch so the returned paths
        # survive the scratch cleanup below (the caller reads them after we return).
        out_dir = Path(tempfile.mkdtemp(prefix="tf-nixjunit-"))
        junit = out_dir / "junit.xml"
        cov = out_dir / "coverage.xml"
        for produced, dest in (
            (stage / "junit.xml", junit),
            (stage / "coverage.xml", cov),
        ):
            if produced.is_file():
                shutil.copy2(produced, dest)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    code = _parse_pytest_exit(stdout)
    return DockerRunResult(
        returncode=code,
        stdout=stdout,
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


def parse_pytest_exits(output: str | None) -> list[tuple[int, str]]:
    """Recover per-run ``(exit_code, stdout_segment)`` pairs from a batched run.

    The batched job script (#776) emits, per pass, a ``__PYTEST_RUN=<i>`` line,
    the pytest output, then ``__PYTEST_EXIT=<code>``. Split on the RUN markers and
    read each pass's own EXIT marker plus the text up to it. A pass whose EXIT
    marker is missing (e.g. the shell died mid-run) counts as a failure (code 1),
    mirroring ``_parse_exit_marker`` — never a false pass. With no RUN markers
    (the ``reruns=1`` legacy shape) returns a single pair from the last EXIT
    marker, so a plain single run round-trips unchanged.
    """
    lines = (output or "").splitlines()
    run_idxs = [i for i, ln in enumerate(lines) if ln.startswith("__PYTEST_RUN=")]
    if not run_idxs:
        return [(_parse_pytest_exit(output), output or "")]
    bounds = [*run_idxs, len(lines)]
    runs: list[tuple[int, str]] = []
    for start, stop in itertools.pairwise(bounds):
        code = 1
        body: list[str] = []
        for ln in lines[start + 1 : stop]:
            if ln.startswith("__PYTEST_EXIT="):
                with contextlib.suppress(ValueError):
                    code = int(ln.split("=", 1)[1])
            else:
                body.append(ln)
        runs.append((code, "\n".join(body)))
    return runs


def parse_mut_exits(output: str | None) -> list[int]:
    """Recover per-mutant exit codes from a batched run's ``__MUT_RUN=<k>`` /
    ``__MUT_EXIT=<code>`` markers (#776 Stage 1b), in run order.

    A mutant whose EXIT marker is missing (the shell died mid-run) counts as
    code 1 — a failure, mirroring ``_parse_exit_marker``. The caller compares the
    recovered count to the number of candidates and falls back to the
    per-candidate mutation path when they don't match, so an incomplete batch
    never yields a wrong mutation verdict. Empty list when no mutant ran.
    """
    lines = (output or "").splitlines()
    run_idxs = [i for i, ln in enumerate(lines) if ln.startswith("__MUT_RUN=")]
    if not run_idxs:
        return []
    bounds = [*run_idxs, len(lines)]
    codes: list[int] = []
    for start, stop in itertools.pairwise(bounds):
        code = 1
        for ln in lines[start + 1 : stop]:
            if ln.startswith("__MUT_EXIT="):
                with contextlib.suppress(ValueError):
                    code = int(ln.split("=", 1)[1])
        codes.append(code)
    return codes


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
    # kubectl apply --dry-run=server needs the cluster API + a scoped SA token.
    # Without the SA configured (RBAC not deployed) keep the hermetic posture and
    # drop kubectl so the rung stays an honest not_run — never a dry-run that
    # fails for lack of cluster auth.
    deploy_sa = os.environ.get(_DEPLOY_DRYRUN_SA_ENV)
    if not deploy_sa:
        available.discard("kubectl")
    planned = plan_deploy_steps(files, required_scans=required_scans)
    runnable = [s for s in planned if s.tool in available]
    if not runnable:
        # No deploy step is runnable in a hermetic Job; let the caller fall back
        # to the local runner (which produces the identical honest not_run block).
        return None

    # When a kubectl server-dry-run will run, give THIS deploy Job the scoped SA
    # token + API network (verify lanes stay token-less). The other scanners
    # (tfsec/trivy/tofu) are unaffected — they don't touch the cluster. getattr:
    # only the KubeJobSandbox carries with_manifest_kw; other ExecutionSandbox
    # impls fall through unchanged.
    _with_kw = getattr(sandbox, "with_manifest_kw", None)
    if (
        deploy_sa
        and _with_kw is not None
        and any(s.tool == "kubectl" for s in runnable)
    ):
        sandbox = _with_kw(service_account=deploy_sa, network_none=False)

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
        # #778: ship the lock too so each ephemeral verify Job reuses it instead of
        # re-locking nixpkgs on every run. None for a non-default rev → nix locks it.
        lock = generate_lock()
        if lock is not None:
            (Path(project_dir) / _FLAKE_LOCK).write_text(lock, encoding="utf-8")
        _log.info("nix_env: wrote generated %s for %s", _FLAKE, spec_dir.name)

    return NixPlan(
        flake_dir=Path(project_dir),
        verify_commands=list(m.verify_commands),
        proof_verify=list(m.proof_verify),
        network=m.network or "none",
        generated=not (repo_has_flake and not m.provisioning_generated),
    )
