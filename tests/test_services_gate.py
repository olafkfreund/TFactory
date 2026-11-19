"""Declared services stop the verify pipeline before agent sessions start."""

import asyncio
import json

import pytest
from agents.services_gate import halt_if_services_declared
from tfactory_yml import TFactoryYmlError, load_tfactory_yml_text

_BASE = "version: 1\ntargets:\n  - name: api\n    type: http\n    base_url: https://example.test\n"


@pytest.fixture(autouse=True)
def _no_notifications(monkeypatch):
    monkeypatch.setattr("agents.workspace_status.notify_terminal", lambda *_: None)


def test_declared_project_services_write_infra_status(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / ".tfactory.yml").write_text(
        _BASE + "services:\n  - name: postgres\n  - name: redis\n    version: '7'\n"
    )
    spec = tmp_path / "spec"
    spec.mkdir()

    assert halt_if_services_declared(spec, project) is True
    status = json.loads((spec / "status.json").read_text())
    assert status["status"] == "generated_empty"
    assert status["phase"] == "services_gate"
    assert status["tests_generated"] == 0
    assert status["verdict_class"] == "infra"
    assert "needs services (postgres, redis)" in status["verify_skip_reason"]


def test_no_services_leaves_status_untouched(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / ".tfactory.yml").write_text(_BASE)
    spec = tmp_path / "spec"
    spec.mkdir()
    status = spec / "status.json"
    status.write_text('{"status": "planned"}')

    assert halt_if_services_declared(spec, project) is False
    assert status.read_text() == '{"status": "planned"}'


def test_malformed_project_config_does_not_gate(tmp_path, caplog):
    project = tmp_path / "project"
    project.mkdir()
    (project / ".tfactory.yml").write_text("version: [")
    spec = tmp_path / "spec"
    spec.mkdir()

    assert halt_if_services_declared(spec, project) is False
    assert not (spec / "status.json").exists()
    assert "unable to read config" in caplog.text


def test_snapshot_services_gate_without_project_config(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    spec = tmp_path / "spec"
    context = spec / "context"
    context.mkdir(parents=True)
    (context / "tfactory_yml.json").write_text(
        json.dumps({"services": [{"name": "redis"}]})
    )

    assert halt_if_services_declared(spec, project) is True
    assert (
        "needs services (redis)"
        in json.loads((spec / "status.json").read_text())["verify_skip_reason"]
    )


def test_unreadable_snapshot_does_not_gate(tmp_path, caplog):
    project = tmp_path / "project"
    project.mkdir()
    spec = tmp_path / "spec"
    context = spec / "context"
    context.mkdir(parents=True)
    (context / "tfactory_yml.json").write_text("{")

    assert halt_if_services_declared(spec, project) is False
    assert not (spec / "status.json").exists()
    assert "unable to read snapshot" in caplog.text


def test_schema_accepts_supported_services_only():
    config = load_tfactory_yml_text(
        _BASE + "services:\n  - name: postgres\n  - name: redis\n    version: '7'\n"
    )
    assert [service.name for service in config.services] == ["postgres", "redis"]
    assert config.services[1].version == "7"
    with pytest.raises(TFactoryYmlError):
        load_tfactory_yml_text(_BASE + "services:\n  - name: mysql\n")


@pytest.mark.parametrize(
    ("module", "function", "client_seam"),
    [
        ("agents.planner", "run_planner", "_resolve_planner_client"),
        ("agents.gen_functional", "run_gen_functional", "_resolve_client"),
        ("agents.evaluator", "run_evaluator", "_resolve_evaluator_client"),
    ],
)
def test_agent_stops_before_sdk_client(
    tmp_path, monkeypatch, module, function, client_seam
):
    import importlib

    project = tmp_path / "project"
    project.mkdir()
    (project / ".tfactory.yml").write_text(_BASE + "services:\n  - name: postgres\n")
    spec = tmp_path / "spec"
    spec.mkdir()
    (spec / "status.json").write_text('{"status": "planned"}')
    agent = importlib.import_module(module)

    def unexpected_client(*_args, **_kwargs):
        pytest.fail("SDK client was requested")

    monkeypatch.setattr(agent, client_seam, unexpected_client)
    assert asyncio.run(getattr(agent, function)(spec, project)) is True
    assert json.loads((spec / "status.json").read_text())["status"] == "generated_empty"
