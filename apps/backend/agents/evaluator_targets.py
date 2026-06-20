"""Target / config resolution helpers for the Evaluator agent.

Extracted from ``agents.evaluator`` (issue #450, god-file split). These are pure
readers over the snapshotted ``.tfactory.yml`` (``context/tfactory_yml.json``)
plus the thin runtime-object constructors that branch on a resolved target's
``type``. No SDK, no status side-effects.

``agents.evaluator`` re-exports these names so existing import paths
(``from agents.evaluator import _resolve_target, _kube_runtime_for`` etc.) and
the runner-fn closures that still live in ``agents.evaluator`` keep working
unchanged.

The runtime imports that were lazy inside ``agents.evaluator`` (to defer heavy
deps / avoid that module's import-graph cycles) are hoisted to module scope here:
this module has no such cycle (verified) and the imports have no load-time
side-effects, so hoisting is behavior-preserving.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

from tools.runners.docker_run_runtime import DockerRunRuntime
from tools.runners.kubernetes_runtime import KubernetesRuntime
from tools.runners.sandbox_credentials import TargetCredentialSpec


def _browser_target_url(spec_dir: Path, subtask: dict) -> str | None:
    """Resolve the base_url for the subtask's target from the snapshotted
    .tfactory.yml (context/tfactory_yml.json). Falls back to the default
    target when the subtask has no target_name.

    The trailing slash is stripped: the parser normalises base_url to end in
    ``/``, but api tests build URLs as ``f"{base_url}/api/..."`` — a trailing
    slash would produce ``//api/...`` (a different path → spurious 404s). A
    bare origin is also valid for Playwright ``page.goto``.
    """
    ctx = spec_dir / "context" / "tfactory_yml.json"
    if not ctx.exists():
        return None
    try:
        cfg = json.loads(ctx.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    targets = cfg.get("targets") or []
    want = subtask.get("target_name") or cfg.get("default_target")

    def _norm(u: str) -> str:
        # Strip a single trailing slash from the origin/path, but never reduce
        # to empty (keep at least the scheme+host).
        return u[:-1] if (u.endswith("/") and not u.endswith("://")) else u

    for t in targets:
        if t.get("name") == want and t.get("base_url"):
            return _norm(t["base_url"])
    # last resort: first http target with a base_url
    for t in targets:
        if t.get("base_url"):
            return _norm(t["base_url"])
    return None


def _resolve_target(spec_dir: Path, subtask: dict) -> dict | None:
    """Resolve the subtask's *target object* from the snapshotted .tfactory.yml.

    Like :func:`_browser_target_url` but returns the whole target dict (so the
    caller can branch on ``type``), preferring the subtask's ``target_name``,
    then the config ``default_target``, then the first target.
    """
    ctx = spec_dir / "context" / "tfactory_yml.json"
    if not ctx.exists():
        return None
    try:
        cfg = json.loads(ctx.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    targets = cfg.get("targets") or []
    want = subtask.get("target_name") or cfg.get("default_target")
    for t in targets:
        if t.get("name") == want:
            return t
    return targets[0] if targets else None


def _kube_runtime_for(target: dict | None, *, runtime_cls=None):
    """A ``KubernetesRuntime`` for a kubernetes port-forward target, else None (#108).

    When the resolved target is ``type: kubernetes`` with ``port_forward: true``,
    the api/browser lane has no static ``base_url`` — the URL only exists while a
    ``kubectl port-forward`` is live. The caller uses the returned runtime as a
    context manager so the forward is up during the test run and torn down after
    (on success *and* failure). Auth rides the materialised read-only kubeconfig
    (``KUBECONFIG``). Returns None for non-k8s targets (the static-URL path).
    """
    if not target or target.get("type") != "kubernetes" or not target.get("port_forward"):
        return None
    cls = runtime_cls or KubernetesRuntime
    t = SimpleNamespace(
        name=target.get("name"),
        context=target.get("context"),
        namespace=target.get("namespace"),
        service=target.get("service"),
        port=target.get("port"),
        # KubernetesRuntime.start() reads target.port_forward — without it the
        # real runtime AttributeErrors (the mocked dispatch test never hits
        # start(), so this only surfaces live). #108.
        port_forward=target.get("port_forward"),
    )
    return cls(t, kubeconfig=os.environ.get("KUBECONFIG"))


def _docker_run_runtime_for(target: dict | None, *, runtime_cls=None):
    """A ``DockerRunRuntime`` for a ``type: docker_run`` target, else None (#233).

    Runs the (typically just-built) image for the lane lifetime, health-polls
    its ``wait_for`` URLs, and exposes ``target_url``. Caller uses it as a
    context manager so the container is removed on success *and* failure.
    """
    if not target or target.get("type") != "docker_run":
        return None
    cls = runtime_cls or DockerRunRuntime
    wait_for = [
        SimpleNamespace(
            url=wf.get("url"),
            expect_status=wf.get("expect_status", 200),
            timeout_seconds=wf.get("timeout_seconds", 60),
        )
        for wf in (target.get("wait_for") or [])
    ]
    t = SimpleNamespace(
        name=target.get("name"),
        image=target.get("image"),
        ports=target.get("ports") or [],
        env=target.get("env") or {},
        command=target.get("command"),
        wait_for=wait_for,
    )
    return cls(t)


def _test_credential_specs(spec_dir: Path, subtask: dict | None) -> list:
    """TargetCredentialSpec list for a subtask's ``ref``-auth target (#107).

    Reads the snapshotted .tfactory.yml: when the subtask's target uses
    ``auth: {type: ref}``, turn the referenced ``test_credentials`` entry into a
    resolver spec (``resolve_test_target_credentials`` injects it as login env).
    Empty list when there is no ref-auth / no matching credential.
    """
    if subtask is None:
        return []
    target = _resolve_target(spec_dir, subtask)
    auth = (target or {}).get("auth") or {}
    if auth.get("type") != "ref" or not auth.get("ref"):
        return []
    ctx = spec_dir / "context" / "tfactory_yml.json"
    try:
        cfg = json.loads(ctx.read_text())
    except (OSError, json.JSONDecodeError, ValueError):
        return []
    entry = (cfg.get("test_credentials") or {}).get(auth["ref"])
    if not entry:
        return []
    return [
        TargetCredentialSpec(
            name=auth["ref"],
            ref=entry["ref"],
            as_secret=entry["as_secret"],
            as_username=entry.get("as_username"),
            username_ref=entry.get("username_ref"),
        )
    ]
