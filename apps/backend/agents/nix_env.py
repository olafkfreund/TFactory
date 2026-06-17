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
    for f in sorted(src.iterdir()):
        if f.suffix.lower() in (".png", ".xml") and f.is_file():
            target = dest / f.name
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
