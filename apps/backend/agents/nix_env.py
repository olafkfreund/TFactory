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

import logging
from dataclasses import dataclass
from pathlib import Path

from agents.task_contract import read_task_contract
from tools.runners.nix_provisioner import (
    Manifest,
    generate_flake,
    nix_develop_argv,
)

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
    if (pd / "app.py").is_file() and "app" in (pd / "app.py").read_text(errors="ignore"):
        return f"python -m uvicorn app:app --host 127.0.0.1 --port {port}"
    if (pd / "src" / "app" / "main.py").is_file():
        return f"python -m uvicorn app.main:app --host 127.0.0.1 --port {port}"
    if (pd / "main.py").is_file() and "app" in (pd / "main.py").read_text(errors="ignore"):
        return f"python -m uvicorn main:app --host 127.0.0.1 --port {port}"
    pkg = pd / "package.json"
    if pkg.is_file() and '"start"' in pkg.read_text(errors="ignore"):
        return "npm start"
    return None


def nix_runner_from_env():
    """Build a KubeJobSandbox from the deployment's TFACTORY_* env, or None when
    the Nix-lane sandbox isn't configured (so callers degrade gracefully)."""
    import os

    image = os.environ.get("TFACTORY_NIX_RUNNER_IMAGE")
    if not image:
        return None
    from tools.runners.kube_sandbox import KubeJobSandbox

    pvc = os.environ.get("TFACTORY_WORKSPACES_PVC")
    ns = os.environ.get("TFACTORY_SANDBOX_NAMESPACE", "factory")
    return KubeJobSandbox(image, namespace=ns, repo_pvc=pvc)


_JOB_SCRIPT = "_tf_nix_job.sh"
_E2E_STAGE = ".tf_e2e"  # staged generated browser specs (in the worktree)
_PW_CONFIG = "_tf_pw.config.ts"
_SHOTS = "shots"


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
    sandbox = nix_runner_from_env()
    if sandbox is None:
        _log.info("run_browser_evidence: TFACTORY_NIX_RUNNER_IMAGE unset; skipping")
        return None

    n_specs = _stage_browser_specs(spec_dir, project_dir)
    if n_specs == 0:
        _log.info("run_browser_evidence: no generated *.spec.ts to run; skipping")
        return {"ok": False, "output_tail": "no browser specs", "serve_command": None,
                "screenshots": [], "specs": 0}
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
            serve_command=serve, port=port, shots_dir=_SHOTS,
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
    shots = collect_screenshots(project_dir, findings)
    return {
        "ok": res.ok,
        "output_tail": (res.output or "")[-2000:],
        "serve_command": serve,
        "specs": n_specs,
        "screenshots": [str(p) for p in shots],
    }


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


def collect_screenshots(project_dir: Path, findings_dir: Path, *, shots: str = "shots") -> list[Path]:
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
        flake_path.write_text(generate_flake(env), encoding="utf-8")
        _log.info("nix_env: wrote generated %s for %s", _FLAKE, spec_dir.name)

    return NixPlan(
        flake_dir=Path(project_dir),
        verify_commands=list(m.verify_commands),
        proof_verify=list(m.proof_verify),
        network=m.network or "none",
        generated=not (repo_has_flake and not m.provisioning_generated),
    )
