"""Cloud assessment orchestrator (#133/#138) — auto-run a cloud target.

Ties the cloud stages together so a ``CloudProviderTarget`` runs end-to-end:

    discover (inventory)  →  Prowler in tfactory-runner-cloud (OCSF)  →
    assess + write report/diagram into findings/

:func:`run_cloud_assessment` is the seam the executor / portal calls. The Prowler
step shells out to Docker (read-only, creds mounted read-only); both the
discovery and Prowler steps are injectable so the orchestrator is unit-testable
without a real cloud or container.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Callable

from .discovery import discover
from .report import assess_and_write

__all__ = ["build_prowler_command", "run_cloud_assessment"]

_IMAGE = "tfactory-runner-cloud:latest"
# Where each provider's ambient creds live on the host → mounted read-only.
_CRED_MOUNTS = {
    "aws": (str(Path.home() / ".aws"), "/home/tfactory/.aws"),
    "gcp": (str(Path.home() / ".config" / "gcloud"), "/home/tfactory/.config/gcloud"),
    "azure": (str(Path.home() / ".azure"), "/home/tfactory/.azure"),
}


def build_prowler_command(
    provider: str,
    *,
    regions: list[str] | None = None,
    services: list[str] | None = None,
    output_dir: str = "/scratch",
) -> list[str]:
    """The in-container Prowler command (pure) — read-only OCSF scan."""
    cmd = ["prowler", provider]
    for s in services or []:
        cmd += ["--service", s]
    for r in regions or []:
        cmd += ["--region", r]
    cmd += ["--output-formats", "json-ocsf", "--output-directory", output_dir]
    return cmd


def _docker_argv(provider: str, profile: str | None, scratch_host: str,
                 prowler_cmd: list[str]) -> list[str]:
    """Full ``docker run`` argv wrapping the Prowler command (read-only creds)."""
    argv = ["docker", "run", "--rm", "--network=bridge"]
    mount = _CRED_MOUNTS.get(provider)
    if mount:
        argv += ["-v", f"{mount[0]}:{mount[1]}:ro"]
    argv += ["-v", f"{scratch_host}:/scratch:rw"]
    if provider == "aws" and profile:
        argv += ["-e", f"AWS_PROFILE={profile}"]
    argv += [_IMAGE, *prowler_cmd]
    return argv


def _run_prowler(target, *, timeout: int = 1800) -> str:
    """Run Prowler in the cloud runner image and return the OCSF JSON (live)."""
    provider = getattr(target, "provider", "aws")
    profile = getattr(target, "profile", None)
    scan = getattr(target, "scan", None)
    services = list(getattr(scan, "services", []) or [])
    regions = list(getattr(target, "regions", []) or [])
    scratch = tempfile.mkdtemp(prefix="tfactory-prowler-")
    Path(scratch).chmod(0o777)  # the container's non-root user writes here
    cmd = build_prowler_command(provider, regions=regions, services=services)
    argv = _docker_argv(provider, profile, scratch, cmd)
    # Prowler exits non-zero when it finds failures — that's expected, not an error.
    subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    ocsf = sorted(Path(scratch).glob("*.ocsf.json"))
    return ocsf[0].read_text(encoding="utf-8") if ocsf else "[]"


def run_cloud_assessment(
    spec_dir: Path,
    target,  # CloudProviderTarget (duck-typed)
    *,
    discover_fn: Callable | None = None,
    prowler_fn: Callable | None = None,
) -> dict:
    """Run a cloud target end-to-end and write the report into ``findings/``.

    Args:
        spec_dir: the task workspace (artifacts land under ``findings/``).
        target: a ``CloudProviderTarget`` (provider / profile / regions / scan).
        discover_fn / prowler_fn: injected in tests; default to the live
            :func:`discovery.discover` and the Docker Prowler run.

    Returns:
        ``assess_and_write``'s summary ``{verdict, fail_counts, paths}``.
    """
    provider = getattr(target, "provider", "aws")
    profile = getattr(target, "profile", None)
    regions = list(getattr(target, "regions", []) or [])
    scan = getattr(target, "scan", None)
    services = list(getattr(scan, "services", []) or [])
    fail_on = getattr(scan, "fail_on_severity", "high")

    inv = (discover_fn or discover)(
        provider, profile=profile, regions=regions, services=services
    )
    ocsf = (prowler_fn or _run_prowler)(target)
    return assess_and_write(
        spec_dir, inventory=inv, ocsf=ocsf, fail_on_severity=fail_on
    )
