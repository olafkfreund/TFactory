"""RFC-0006 #75 — VAL-3 disposable-target provisioning with mandatory teardown.

VAL-3 verifies *effectful* behaviour against a REAL, disposable host — never
production (that is VAL-4). This module is the provisioning **mechanism** with a
hard guarantee: any target it provisions is torn down, even on failure (the
run-aws-demo pattern), via a context manager whose ``finally`` always tears
down.

It is **gated** (``should_provision_val3``): a target is provisioned only when
the plan declares effectful VAL-3 commands AND a target backend + its
credentials are actually available — and never against a prod target. When the
gate is closed, NO target is provisioned and VAL-3 stays honestly ``not_run``
(the never-overclaim default emitted by :mod:`agents.val_block` and surfaced by
RFC-0006 #74/#76). This is the point of the RFC: VAL-3 is claimed only when a
disposable target genuinely ran.

Backends, in preference order (RFC-0006 §VAL-3 / RFC-0007 access):
  - ``local-vm`` — a throwaway QEMU/libvirt VM, when the host can run one;
  - ``sandbox-cloud`` — a cost-guarded cloud sandbox with mandatory auto-teardown,
    when ``TFACTORY_VAL3_CLOUD`` is configured with credentials;
  - else ``None`` → VAL-3 not_run.

The concrete provisioners are infra-dependent and selected by env; the gating,
backend selection, and teardown guarantee here are pure + fully tested. A
backend that cannot actually provision (no infra) returns ``None`` from
``provision`` so the gate's honest default holds — never a fake "VAL-3 passed".
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Protocol

logger = logging.getLogger(__name__)

__all__ = [
    "DisposableTarget",
    "should_provision_val3",
    "select_backend",
    "disposable_target",
    "attempt_val3",
    "record_val3",
    "register_provisioner",
    "Val3Outcome",
]


class DisposableTarget(Protocol):
    """A provisioned, throwaway host VAL-3 effectful commands run against."""

    name: str

    def run(self, command: str, *, timeout: float = 600.0) -> tuple[bool, str]:
        """Run one command on the target; return ``(ok, output)``."""
        ...

    def teardown(self) -> None:
        """Destroy the target. Must be idempotent and never raise."""
        ...


@dataclass
class Val3Outcome:
    """Result of a VAL-3 run (or the honest reason it did not run)."""

    ran: bool
    passed: bool = False
    reason: str = ""
    output: str = ""


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def should_provision_val3(
    profile: dict[str, Any] | None,
    access: dict[str, Any] | None,
    *,
    env: dict[str, str] | None = None,
) -> tuple[bool, str]:
    """Decide whether to provision a VAL-3 disposable target — and why/why-not.

    Returns ``(provision, reason)``. The gate opens only when ALL hold:
      * the profile declares effectful VAL-3 commands (there is something to run);
      * a target backend is configured/available (``select_backend`` is not None);
      * access/credentials for the target are present (not blocked/uncurated);
      * the target is NOT production (VAL-3 is disposable hosts only; prod=VAL-4).
    Any closed condition keeps VAL-3 ``not_run`` honestly — never a guess.
    """
    env = os.environ if env is None else env

    val3 = ((profile or {}).get("levels") or {}).get("VAL-3") or {}
    commands = val3.get("commands") or []
    if not commands:
        return False, "profile declares no effectful VAL-3 commands"

    if val3.get("target") == "prod" or _truthy(env.get("TFACTORY_VAL3_TARGET_IS_PROD")):
        return False, "VAL-3 never runs against production (that would be VAL-4)"

    backend = select_backend(env=env)
    if backend is None:
        return False, "no disposable-target backend available (local VM / sandbox cloud)"

    # RFC-0007: a credentialed lane that couldn't be curated/reached stays not_run.
    acc = access or {}
    if acc.get("blocked") or acc.get("val3") == "not_run":
        return False, "VAL-3 access not provisioned/curated (RFC-0007)"

    return True, f"effectful VAL-3 commands + {backend} target + access provisioned"


def select_backend(*, env: dict[str, str] | None = None) -> str | None:
    """Pick the VAL-3 backend from the environment, preferring a local VM.

    ``local-vm`` when ``TFACTORY_VAL3_LOCAL_VM=1`` (host can run QEMU/libvirt);
    ``sandbox-cloud`` when ``TFACTORY_VAL3_CLOUD`` names a configured, credentialed
    cost-guarded cloud sandbox; else ``None`` (no target → VAL-3 not_run).
    """
    env = os.environ if env is None else env
    if _truthy(env.get("TFACTORY_VAL3_LOCAL_VM")):
        return "local-vm"
    if (env.get("TFACTORY_VAL3_CLOUD") or "").strip():
        return "sandbox-cloud"
    return None


# Provisioner registry: backend name -> a callable returning a DisposableTarget
# or None (None = could not provision → honest not_run, never a fake pass). The
# concrete QEMU/cloud provisioners are infra-dependent and registered by the
# deployment; absent a registration the backend yields None.
_PROVISIONERS: dict[str, Callable[[dict[str, Any]], DisposableTarget | None]] = {}


def register_provisioner(
    backend: str, fn: Callable[[dict[str, Any]], DisposableTarget | None]
) -> None:
    """Register a concrete provisioner for ``backend`` (e.g. a QEMU launcher)."""
    _PROVISIONERS[backend] = fn


@contextmanager
def disposable_target(
    spec: dict[str, Any] | None,
    *,
    env: dict[str, str] | None = None,
) -> Iterator[DisposableTarget | None]:
    """Yield a provisioned disposable target, or ``None`` when the gate is closed.

    GUARANTEE: if a target is provisioned, it is torn down on exit — even if the
    body raises (the run-aws-demo mandatory-teardown pattern). ``teardown`` is
    best-effort and never masks the body's exception. When no backend provisioner
    is registered (no infra), ``provision`` returns ``None`` and the caller keeps
    VAL-3 honestly not_run.
    """
    env = os.environ if env is None else env
    backend = select_backend(env=env)
    provisioner = _PROVISIONERS.get(backend) if backend else None
    target: DisposableTarget | None = None
    if provisioner is not None:
        try:
            target = provisioner(spec or {})
        except Exception as exc:  # noqa: BLE001 — a failed provision is not a pass
            logger.warning("VAL-3 target provision failed (%s): %s", backend, exc)
            target = None
    try:
        yield target
    finally:
        if target is not None:
            try:
                target.teardown()
            except Exception as exc:  # noqa: BLE001 — teardown must never raise out
                logger.error("VAL-3 target teardown failed for %s: %s", target.name, exc)


def attempt_val3(
    profile: dict[str, Any] | None,
    access: dict[str, Any] | None,
    *,
    spec: dict[str, Any] | None = None,
    env: dict[str, str] | None = None,
) -> Val3Outcome:
    """Gate → provision → run VAL-3 commands → guaranteed teardown → outcome.

    Returns ``Val3Outcome(ran=False, reason=...)`` whenever the gate is closed or
    no target could be provisioned (the honest default — VAL-3 stays not_run).
    Only when a real disposable target ran every VAL-3 command does it return
    ``ran=True`` with the pass/fail truth. Teardown is guaranteed by the context
    manager even if a command raises.
    """
    env = os.environ if env is None else env
    ok, reason = should_provision_val3(profile, access, env=env)
    if not ok:
        return Val3Outcome(ran=False, reason=reason)

    commands = (((profile or {}).get("levels") or {}).get("VAL-3") or {}).get("commands") or []
    with disposable_target(spec, env=env) as target:
        if target is None:
            return Val3Outcome(
                ran=False,
                reason="disposable-target backend gated open but could not provision",
            )
        outputs: list[str] = []
        for cmd in commands:
            passed, out = target.run(str(cmd))
            outputs.append(out)
            if not passed:
                return Val3Outcome(
                    ran=True, passed=False,
                    reason=f"VAL-3 command failed on {target.name}: {cmd}",
                    output="\n".join(outputs),
                )
        return Val3Outcome(ran=True, passed=True, output="\n".join(outputs))


def record_val3(
    spec_dir: Path | str,
    profile: dict[str, Any] | None,
    access: dict[str, Any] | None,
    *,
    env: dict[str, str] | None = None,
) -> Val3Outcome:
    """Run VAL-3 once (gated) and persist the outcome to findings/val3_outcome.json.

    The single VAL-3 provisioning point for a verify run: the verify path calls
    it once (best-effort), so ``agents.val_block.read_verification_block`` can
    read a pure outcome later without re-provisioning. Returns the outcome and
    writes it (sans large output) for the block reader. Never raises.
    """
    outcome = attempt_val3(profile, access, env=env)
    try:
        fdir = Path(spec_dir) / "findings"
        fdir.mkdir(parents=True, exist_ok=True)
        record = {k: v for k, v in asdict(outcome).items() if k != "output"}
        (fdir / "val3_outcome.json").write_text(json.dumps(record, indent=2))
    except OSError:
        pass
    return outcome
