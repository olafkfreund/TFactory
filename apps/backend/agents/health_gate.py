"""Pre-lane health gate + target-URL resolution (#234, epic #232).

Two problems this solves:

  1. ``HttpTarget.health_check`` existed in the schema but was never invoked, so
     a *down* deployed target surfaced as opaque test timeouts. Now the Evaluator
     probes it before the browser/api/integration lane runs and records a clear
     "target unhealthy" signal — the root cause, not a 30s wall.
  2. The deployed URL had to be hand-typed. ``resolve_target_url`` centralises
     precedence (an explicit ``TFACTORY_TARGET_URL`` env override wins, then the
     target's ``base_url``) so CI can inject a freshly-deployed URL without
     editing ``.tfactory.yml``; ``discover_ingress_url`` best-effort resolves a
     Kubernetes ingress host via ``kubectl`` (seam-injected for tests).

Pure + dependency-free (stdlib ``urllib``); the network/subprocess calls sit
behind seams so tests never hit a real service or cluster. Best-effort — a probe
failure is reported, never raised, so the pipeline can't break on a gate.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class HealthResult:
    ok: bool
    url: str | None = None
    status_code: int | None = None
    detail: str = ""

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "url": self.url,
            "status_code": self.status_code,
            "detail": self.detail,
        }


def _join(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def probe(
    url: str, *, expect_status: int = 200, timeout: int = 10, opener=None
) -> HealthResult:
    """GET ``url`` and compare the status to ``expect_status``.

    ``opener(url, timeout) -> status_code`` is injectable for tests; the default
    uses stdlib urllib. Any transport error → ``ok=False`` with the reason.
    """
    if opener is None:

        def opener(u: str, t: int) -> int:  # noqa: ANN001
            import urllib.request

            req = urllib.request.Request(u, method="GET")
            with urllib.request.urlopen(req, timeout=t) as resp:  # noqa: S310
                return resp.status

    try:
        code = opener(url, timeout)
    except Exception as exc:  # noqa: BLE001 — unreachable target is a gate failure
        return HealthResult(ok=False, url=url, detail=f"unreachable: {exc}")
    ok = code == expect_status
    return HealthResult(
        ok=ok,
        url=url,
        status_code=code,
        detail="" if ok else f"expected {expect_status}, got {code}",
    )


def gate(base_url: str | None, health_cfg: dict | None, *, opener=None) -> HealthResult:
    """Probe a target's health check before its lane runs.

    Returns ``ok=True`` (a pass-through) when there is no ``base_url`` or no
    ``health_check`` configured — the gate only fails on a *configured* check
    that doesn't pass, so existing targets without a check are unaffected.
    """
    if not base_url or not health_cfg:
        return HealthResult(ok=True, url=base_url, detail="no health_check configured")
    url = _join(base_url, health_cfg.get("path", "/healthz"))
    return probe(
        url,
        expect_status=int(health_cfg.get("expect_status", 200)),
        timeout=int(health_cfg.get("timeout_seconds", 10)),
        opener=opener,
    )


def resolve_target_url(target: dict | None, *, env: dict | None = None) -> str | None:
    """Resolve a deployed target's base URL with precedence.

    ``TFACTORY_TARGET_URL`` env override (CI injects a freshly-deployed URL)
    wins, then the target's declared ``base_url``. Returns None when neither is
    available (a kube ``port_forward`` target resolves its URL at runtime via
    KubernetesRuntime, #108 — not here).
    """
    env = env if env is not None else os.environ
    override = (env.get("TFACTORY_TARGET_URL") or "").strip()
    if override:
        return override.rstrip("/")
    if target and target.get("base_url"):
        return str(target["base_url"]).rstrip("/")
    return None


def discover_ingress_url(
    namespace: str,
    name: str,
    *,
    scheme: str = "https",
    runner=None,
) -> str | None:
    """Best-effort resolve a Kubernetes Ingress host to a URL via ``kubectl``.

    ``runner(args) -> str`` (stdout) is injectable for tests. Returns
    ``<scheme>://<host>`` or None if kubectl fails / no host is set. Never
    raises — discovery is opportunistic.
    """
    if runner is None:

        def runner(args: list[str]) -> str:  # noqa: ANN001
            return subprocess.run(  # noqa: S603
                args, capture_output=True, text=True, timeout=15, check=True
            ).stdout

    args = [
        "kubectl",
        "-n",
        namespace,
        "get",
        "ingress",
        name,
        "-o",
        "jsonpath={.spec.rules[0].host}",
    ]
    try:
        host = (runner(args) or "").strip().strip('"')
    except Exception:  # noqa: BLE001 — no cluster / no ingress → just skip
        return None
    if not host:
        return None
    return f"{scheme}://{host}"


def health_to_json(result: HealthResult) -> str:
    return json.dumps(result.as_dict(), sort_keys=True)
