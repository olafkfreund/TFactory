"""Tests for the VAL-3 disposable-target mechanism (RFC-0006 #75)."""

from __future__ import annotations

import pytest
from agents import disposable_target as dt
from agents.disposable_target import (
    attempt_val3,
    disposable_target,
    select_backend,
    should_provision_val3,
)
from agents.val_block import build_verification_block

# A profile whose VAL-3 declares effectful commands.
_PROFILE = {"levels": {"VAL-3": {"commands": ["deploy --check", "smoke --real"]}}}
_LOCAL_VM = {"TFACTORY_VAL3_LOCAL_VM": "1"}


class _FakeTarget:
    def __init__(self, *, fail_on: str | None = None) -> None:
        self.name = "fake-vm"
        self.ran: list[str] = []
        self.torn_down = False
        self._fail_on = fail_on

    def run(self, command: str, *, timeout: float = 600.0):
        self.ran.append(command)
        if self._fail_on and self._fail_on in command:
            return False, f"FAILED: {command}"
        return True, f"ok: {command}"

    def teardown(self) -> None:
        self.torn_down = True


@pytest.fixture(autouse=True)
def _clean_registry():
    dt._PROVISIONERS.clear()
    yield
    dt._PROVISIONERS.clear()


# ── gating ───────────────────────────────────────────────────────────────


def test_gate_closed_without_effectful_commands() -> None:
    ok, reason = should_provision_val3({"levels": {"VAL-3": {"commands": []}}}, None, env=_LOCAL_VM)
    assert ok is False and "no effectful" in reason


def test_gate_closed_without_a_backend() -> None:
    ok, reason = should_provision_val3(_PROFILE, None, env={})
    assert ok is False and "no disposable-target backend" in reason


def test_gate_closed_against_prod() -> None:
    prof = {"levels": {"VAL-3": {"commands": ["x"], "target": "prod"}}}
    ok, reason = should_provision_val3(prof, None, env=_LOCAL_VM)
    assert ok is False and "production" in reason


def test_gate_closed_when_access_blocked() -> None:
    ok, reason = should_provision_val3(
        _PROFILE, {"val3": "not_run", "blocked": [{"resource": "mfa"}]}, env=_LOCAL_VM
    )
    assert ok is False and "RFC-0007" in reason


def test_gate_open_when_all_conditions_met() -> None:
    ok, _ = should_provision_val3(_PROFILE, {"val3": "ok"}, env=_LOCAL_VM)
    assert ok is True


def test_select_backend_prefers_local_vm_then_cloud() -> None:
    assert select_backend(env=_LOCAL_VM) == "local-vm"
    assert select_backend(env={"TFACTORY_VAL3_CLOUD": "aws-sbx"}) == "sandbox-cloud"
    assert select_backend(env={}) is None


# ── mandatory teardown ─────────────────────────────────────────────────────


def test_target_is_torn_down_even_on_exception() -> None:
    tgt = _FakeTarget()
    dt.register_provisioner("local-vm", lambda spec: tgt)
    with pytest.raises(RuntimeError):
        with disposable_target({}, env=_LOCAL_VM) as t:
            assert t is tgt
            raise RuntimeError("boom")
    assert tgt.torn_down is True  # finally tore it down despite the raise


def test_no_target_when_no_backend_registered() -> None:
    with disposable_target({}, env=_LOCAL_VM) as t:
        assert t is None  # backend selected but no provisioner → honest None


# ── attempt_val3 (gate → provision → run → teardown) ───────────────────────


def test_attempt_val3_runs_and_tears_down_on_success() -> None:
    tgt = _FakeTarget()
    dt.register_provisioner("local-vm", lambda spec: tgt)
    out = attempt_val3(_PROFILE, {"val3": "ok"}, env=_LOCAL_VM)
    assert out.ran is True and out.passed is True
    assert tgt.ran == ["deploy --check", "smoke --real"] and tgt.torn_down is True


def test_attempt_val3_records_failure_and_tears_down() -> None:
    tgt = _FakeTarget(fail_on="smoke")
    dt.register_provisioner("local-vm", lambda spec: tgt)
    out = attempt_val3(_PROFILE, {"val3": "ok"}, env=_LOCAL_VM)
    assert out.ran is True and out.passed is False and tgt.torn_down is True


def test_attempt_val3_not_run_when_gate_closed() -> None:
    out = attempt_val3(_PROFILE, None, env={})  # no backend
    assert out.ran is False and "backend" in out.reason


# ── val_block integration ──────────────────────────────────────────────────


def test_val_block_reaches_val3_on_a_real_passing_run() -> None:
    tgt = _FakeTarget()
    dt.register_provisioner("local-vm", lambda spec: tgt)
    out = attempt_val3(_PROFILE, {"val3": "ok"}, env=_LOCAL_VM)
    block = build_verification_block(
        [{"lane": "unit", "verdict": "accept"}, {"lane": "api", "verdict": "accept"}],
        val3=out,
    )
    assert block["achieved_level"] == "VAL-3"


def test_val_block_caps_when_val3_run_fails() -> None:
    tgt = _FakeTarget(fail_on="smoke")
    dt.register_provisioner("local-vm", lambda spec: tgt)
    out = attempt_val3(_PROFILE, {"val3": "ok"}, env=_LOCAL_VM)
    block = build_verification_block(
        [{"lane": "unit", "verdict": "accept"}, {"lane": "api", "verdict": "accept"}],
        val3=out,
    )
    # a failed VAL-3 caps the honest ceiling at VAL-2
    assert block["achieved_level"] == "VAL-2"
    assert "VAL-3 failed" in block["claim"]


def test_record_val3_persists_outcome_and_block_reader_picks_it_up(tmp_path) -> None:
    from agents.disposable_target import record_val3
    from agents.val_block import read_verification_block

    (tmp_path / "findings").mkdir(parents=True)
    (tmp_path / "findings" / "verdicts.json").write_text(
        '{"verdicts": [{"lane": "unit", "verdict": "accept"}, '
        '{"lane": "api", "verdict": "accept"}]}'
    )
    tgt = _FakeTarget()
    dt.register_provisioner("local-vm", lambda spec: tgt)
    out = record_val3(tmp_path, _PROFILE, {"val3": "ok"}, env=_LOCAL_VM)
    assert out.ran and out.passed and tgt.torn_down
    # the pure block reader picks up the recorded outcome → reaches VAL-3
    block = read_verification_block(tmp_path)
    assert block["achieved_level"] == "VAL-3"


def test_block_reader_is_pure_not_run_without_recorded_outcome(tmp_path) -> None:
    from agents.val_block import read_verification_block

    (tmp_path / "findings").mkdir(parents=True)
    (tmp_path / "findings" / "verdicts.json").write_text(
        '{"verdicts": [{"lane": "unit", "verdict": "accept"}]}'
    )
    block = read_verification_block(tmp_path)  # no val3_outcome.json
    assert block["achieved_level"] == "VAL-1"
