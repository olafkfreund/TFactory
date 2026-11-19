"""#1582: the api lane serves the python app nearest the spec targets, with requests."""

import json
import subprocess

from agents.nix_env import (
    _GENERATED_MARKER,
    _api_targets,
    api_services_fallback_env,
    detect_serve_command,
    materialize_flake,
)


def _demo(tmp_path):
    """aifactory-demo: link shortener at src/app, notes api at services/notes-api."""
    (tmp_path / "src" / "app").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "app" / "main.py").write_text("app = 1\n")
    notes = tmp_path / "services" / "notes-api"
    (notes / "app").mkdir(parents=True, exist_ok=True)
    (notes / "app" / "main.py").write_text("app = FastAPI()\n")
    (notes / "requirements.txt").write_text("fastapi\nuvicorn\n")
    return tmp_path


def test_notes_api_is_served_from_its_subdir(tmp_path):
    want = (
        "python -m uvicorn --app-dir services/notes-api app.main:app "
        "--host 127.0.0.1 --port 8200"
    )
    project = _demo(tmp_path)
    for target in ("services/notes-api/app/main.py", "services/notes-api/tests/t.py"):
        assert detect_serve_command(project, port=8200, targets=[target]) == want


def test_without_targets_the_root_app_is_unchanged(tmp_path):
    cmd = detect_serve_command(_demo(tmp_path), port=8200)
    assert cmd is not None and "uvicorn app.main:app" in cmd


def test_unsafe_directory_names_are_ignored(tmp_path):
    (tmp_path / "a;b" / "app").mkdir(parents=True)
    (tmp_path / "a;b" / "app" / "main.py").write_text("app = 1\n")
    assert detect_serve_command(tmp_path, targets=["a;b/x.py"]) is None


def _spec(tmp_path, source=None, text=""):
    spec = tmp_path / "specs" / "1"
    (spec / "context").mkdir(parents=True, exist_ok=True)
    if source:
        (spec / "context" / "source.json").write_text(json.dumps(source))
    (spec / "context" / "aifactory_spec.md").write_text(text)
    return spec


def test_api_targets_fall_back_to_python_paths_in_the_spec_text(tmp_path):
    project = _demo(tmp_path / "p")
    spec = _spec(tmp_path, text="Edit services/notes-api/app/main.py please")
    assert _api_targets(spec, project) == ["services/notes-api/app/main.py"]
    fb = api_services_fallback_env(spec, project, "api")
    assert fb is not None


def _flake_repo(tmp_path, text):
    project = _demo(tmp_path / "p")
    (project / "flake.nix").write_text(text)
    git = ["git", "-C", str(project), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "flake.nix"], check=True)
    return project


def test_committed_generated_flake_is_regenerated_for_the_api_lane(tmp_path):
    project = _flake_repo(tmp_path, f'# {_GENERATED_MARKER}\n p."fastapi"\n')
    spec = _spec(tmp_path)
    fb = api_services_fallback_env(
        _spec(tmp_path, text="services/notes-api/app/main.py"), project, "api"
    )
    plan = materialize_flake(spec, project, fallback_env=fb, regenerate_generated=True)
    assert plan is not None and plan.generated
    assert 'p."requests"' in (project / "flake.nix").read_text()


def test_hand_written_or_browser_flake_is_left_alone(tmp_path):
    text = "{ hand = 1; }\n"
    project = _flake_repo(tmp_path / "h", text)
    fb = {"system_packages": [], "provisioning": {"method": "nix", "generated": True}}
    for regen in (True, False):
        plan = materialize_flake(
            _spec(tmp_path), project, fallback_env=fb, regenerate_generated=regen
        )
        assert plan is not None and not plan.generated
        assert (project / "flake.nix").read_text() == text
    project = _flake_repo(tmp_path / "g", f"# {_GENERATED_MARKER}\n")
    plan = materialize_flake(_spec(tmp_path), project, fallback_env=fb)
    assert plan is not None and not plan.generated  # browser lane: not opted in


def test_single_app_repo_with_test_only_targets_still_serves_its_app(tmp_path):
    # Codex review: targets that name only tests must not disable root detection.
    from agents.nix_env import detect_api_serve_command

    (tmp_path / "src" / "app").mkdir(parents=True)
    (tmp_path / "src" / "app" / "main.py").write_text("app = 1\n")
    spec = _spec(tmp_path, source={"target_paths": ["tests/test_api.py"]})
    cmd = detect_api_serve_command(spec, tmp_path, port=8200)
    assert cmd is not None and "uvicorn app.main:app" in cmd
