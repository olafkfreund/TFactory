"""Tests for the env-gated k8s-Job disposable target (issue #251).

Mirrors the style of test_disposable_target.py: no live cluster, no real k8s
API — KubeJobSandbox is replaced by a lightweight _MockSandbox stub so the
tests are hermetic and fast.

Coverage:
  * K8sJobTarget.run() delegates the command and timeout to the sandbox.
  * K8sJobTarget.teardown() is idempotent (safe to call multiple times).
  * The disposable_target() context manager tears down K8sJobTarget even when
    the body raises an exception.
  * auto_register() wires the provisioner into _PROVISIONERS iff the env var
    is set; absent the var the registry is untouched.
  * select_backend() returns "k8s-job" when TFACTORY_VAL3_K8S_JOB=1 and the
    existing local-vm / sandbox-cloud backends are unaffected.
"""

from __future__ import annotations

import pytest
from agents import disposable_target as dt
from agents.disposable_target import disposable_target, select_backend
from agents.k8s_job_target import (
    ENV_ENABLE,
    ENV_IMAGE,
    K8sJobTarget,
    auto_register,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_K8S_ENV = {ENV_ENABLE: "1"}
_K8S_ENV_WITH_IMAGE = {ENV_ENABLE: "1", ENV_IMAGE: "ghcr.io/example/runner:latest"}


class _MockResult:
    """Minimal stand-in for KubeJobSandbox's JobRunResult."""

    def __init__(self, ok: bool = True, output: str = "ok") -> None:
        self.ok = ok
        self.output = output


class _MockSandbox:
    """Stand-in for KubeJobSandbox — records calls, never touches k8s."""

    def __init__(self, ok: bool = True, output: str = "ok") -> None:
        self._ok = ok
        self._output = output
        self.calls: list[tuple[list[str], int]] = []

    def run(
        self,
        commands: list[str],
        *,
        workdir: str | None = None,
        timeout: int = 900,
    ) -> _MockResult:
        self.calls.append((commands, timeout))
        return _MockResult(ok=self._ok, output=self._output)


# ---------------------------------------------------------------------------
# Autouse fixture: keep registry clean between tests (mirrors test_disposable_target)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_registry():
    dt._PROVISIONERS.clear()
    yield
    dt._PROVISIONERS.clear()


# ---------------------------------------------------------------------------
# K8sJobTarget.run() — delegation
# ---------------------------------------------------------------------------


def test_run_delegates_command_as_single_element_list() -> None:
    """run() wraps the command string in a list before calling the sandbox."""
    sandbox = _MockSandbox(ok=True, output="deployed")
    target = K8sJobTarget(sandbox, name="test-target")

    ok, output = target.run("kubectl apply --dry-run")

    assert ok is True
    assert output == "deployed"
    assert len(sandbox.calls) == 1
    commands, _timeout = sandbox.calls[0]
    assert commands == ["kubectl apply --dry-run"]


def test_run_passes_timeout_as_int() -> None:
    """Float timeout from the protocol is converted to int for the sandbox."""
    sandbox = _MockSandbox()
    target = K8sJobTarget(sandbox)

    target.run("true", timeout=300.0)

    _, timeout = sandbox.calls[0]
    assert timeout == 300
    assert isinstance(timeout, int)


def test_run_clamps_timeout_to_minimum_one_second() -> None:
    """Fractional/zero/negative timeouts are clamped to 1 (never a 0s deadline)."""
    sandbox = _MockSandbox()
    target = K8sJobTarget(sandbox)

    target.run("true", timeout=0.4)
    target.run("true", timeout=-5.0)

    assert [t for _, t in sandbox.calls] == [1, 1]


def test_run_returns_failure_from_sandbox() -> None:
    """A non-ok sandbox result propagates as (False, output)."""
    sandbox = _MockSandbox(ok=False, output="error: image pull failed")
    target = K8sJobTarget(sandbox)

    ok, output = target.run("helm upgrade --dry-run .")

    assert ok is False
    assert "image pull" in output


def test_run_uses_full_output() -> None:
    """output from the sandbox is returned verbatim."""
    sandbox = _MockSandbox(output="plan: 2 to add, 0 to destroy")
    target = K8sJobTarget(sandbox)

    _, output = target.run("terraform plan")

    assert output == "plan: 2 to add, 0 to destroy"


# ---------------------------------------------------------------------------
# K8sJobTarget.teardown() — idempotent no-op
# ---------------------------------------------------------------------------


def test_teardown_is_idempotent() -> None:
    """teardown() can be called multiple times without error."""
    target = K8sJobTarget(_MockSandbox())

    target.teardown()
    target.teardown()  # second call must not raise

    assert target._torn_down is True


def test_teardown_does_not_call_sandbox() -> None:
    """teardown() does not issue any sandbox.run() calls — Jobs self-delete."""
    sandbox = _MockSandbox()
    target = K8sJobTarget(sandbox)

    target.teardown()

    assert sandbox.calls == []


# ---------------------------------------------------------------------------
# disposable_target() context manager — teardown on exception
# ---------------------------------------------------------------------------


def test_k8s_target_torn_down_even_on_exception() -> None:
    """The context manager guarantees teardown even when the body raises."""
    sandbox = _MockSandbox()
    target_holder: list[K8sJobTarget] = []

    # Register a provisioner that returns our pre-built target.
    the_target = K8sJobTarget(sandbox, name="exc-test")
    dt.register_provisioner("k8s-job", lambda _spec: the_target)

    with pytest.raises(RuntimeError, match="boom"):
        with disposable_target({}, env=_K8S_ENV) as t:
            assert t is the_target
            target_holder.append(t)
            raise RuntimeError("boom")

    assert the_target._torn_down is True


def test_no_k8s_target_when_image_unset() -> None:
    """Env flip without an image → lazy-registered provisioner honestly yields None."""
    # _PROVISIONERS is cleared by autouse fixture; do not register anything.
    with disposable_target({}, env=_K8S_ENV) as t:
        assert t is None


# ---------------------------------------------------------------------------
# Lazy activation — the env flip alone wires the backend (no startup import)
# ---------------------------------------------------------------------------


def test_disposable_target_lazily_registers_k8s_job_provisioner() -> None:
    """disposable_target() auto-registers the k8s-job provisioner from the env."""
    assert "k8s-job" not in dt._PROVISIONERS
    with disposable_target({}, env=_K8S_ENV):
        pass
    assert "k8s-job" in dt._PROVISIONERS


def test_disposable_target_lazy_path_yields_target(monkeypatch) -> None:
    """The env flip alone is enough for disposable_target() to yield a target."""
    the_target = K8sJobTarget(_MockSandbox(), name="lazy-wired")
    monkeypatch.setattr(
        "agents.k8s_job_target._make_provisioner",
        lambda env=None: lambda spec: the_target,
    )
    with disposable_target({}, env=_K8S_ENV_WITH_IMAGE) as t:
        assert t is the_target
    assert the_target._torn_down is True


def test_no_lazy_registration_without_env_flip() -> None:
    """Empty env → no backend, no lazy registration, registry untouched."""
    with disposable_target({}, env={}) as t:
        assert t is None
    assert dt._PROVISIONERS == {}


# ---------------------------------------------------------------------------
# auto_register() — env-gated wiring into _PROVISIONERS
# ---------------------------------------------------------------------------


def test_auto_register_skipped_when_env_var_absent() -> None:
    """auto_register() with empty env is a no-op."""
    registered = auto_register(env={})

    assert registered is False
    assert "k8s-job" not in dt._PROVISIONERS


def test_auto_register_skipped_when_env_var_false() -> None:
    """auto_register() with falsy env var is a no-op."""
    registered = auto_register(env={ENV_ENABLE: "0"})

    assert registered is False
    assert "k8s-job" not in dt._PROVISIONERS


def test_auto_register_wires_provisioner_when_enabled() -> None:
    """auto_register() with TFACTORY_VAL3_K8S_JOB=1 registers the provisioner."""
    registered = auto_register(env=_K8S_ENV)

    assert registered is True
    assert "k8s-job" in dt._PROVISIONERS


def test_auto_register_provisioner_returns_none_without_image() -> None:
    """Provisioner returns None (honest not_run) when image env var is absent."""
    auto_register(env={ENV_ENABLE: "1"})  # no ENV_IMAGE
    provisioner = dt._PROVISIONERS["k8s-job"]

    result = provisioner({})

    assert result is None


def test_auto_register_provisioner_returns_target_with_image(monkeypatch) -> None:
    """Provisioner returns a K8sJobTarget when the image env var is set."""
    import agents.k8s_job_target as kjt

    # Patch KubeJobSandbox so no cluster is needed.
    monkeypatch.setattr(
        "agents.k8s_job_target._make_provisioner",
        lambda env=None: lambda spec: K8sJobTarget(_MockSandbox(), name="wired"),
    )
    auto_register(env=_K8S_ENV_WITH_IMAGE)
    provisioner = dt._PROVISIONERS["k8s-job"]

    target = provisioner({})

    assert isinstance(target, K8sJobTarget)
    assert target.name == "wired"


# ---------------------------------------------------------------------------
# select_backend() — k8s-job backend is additive
# ---------------------------------------------------------------------------


def test_select_backend_returns_k8s_job_when_env_set() -> None:
    """TFACTORY_VAL3_K8S_JOB=1 causes select_backend() to return 'k8s-job'."""
    assert select_backend(env={ENV_ENABLE: "1"}) == "k8s-job"


def test_select_backend_local_vm_still_preferred_over_k8s_job() -> None:
    """local-vm takes priority over k8s-job when both env vars are set."""
    env = {ENV_ENABLE: "1", "TFACTORY_VAL3_LOCAL_VM": "1"}
    assert select_backend(env=env) == "local-vm"


def test_select_backend_k8s_job_preferred_over_sandbox_cloud() -> None:
    """k8s-job takes priority over sandbox-cloud when both are set."""
    env = {ENV_ENABLE: "1", "TFACTORY_VAL3_CLOUD": "aws-sbx"}
    assert select_backend(env=env) == "k8s-job"


def test_select_backend_unchanged_without_k8s_job_env() -> None:
    """Existing backends are unaffected when TFACTORY_VAL3_K8S_JOB is absent."""
    assert select_backend(env={}) is None
    assert select_backend(env={"TFACTORY_VAL3_LOCAL_VM": "1"}) == "local-vm"
    assert select_backend(env={"TFACTORY_VAL3_CLOUD": "aws-sbx"}) == "sandbox-cloud"


# ---------------------------------------------------------------------------
# K8sJobTarget.name — default uniqueness
# ---------------------------------------------------------------------------


def test_target_name_defaults_to_unique_k8s_job_prefix() -> None:
    """Default name starts with 'k8s-job-' and differs between instances."""
    t1 = K8sJobTarget(_MockSandbox())
    t2 = K8sJobTarget(_MockSandbox())

    assert t1.name.startswith("k8s-job-")
    assert t2.name.startswith("k8s-job-")
    assert t1.name != t2.name


def test_target_name_can_be_overridden() -> None:
    """Explicit name is used as-is."""
    target = K8sJobTarget(_MockSandbox(), name="my-custom-target")
    assert target.name == "my-custom-target"
