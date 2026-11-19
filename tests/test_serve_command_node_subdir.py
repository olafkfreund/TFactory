"""#1609: a node app in a subdir is served from there, not by the root `npm start`."""

import json

import pytest
from agents.nix_env import _browser_targets, _spec_text_targets, detect_serve_command


def _demo(tmp_path):
    """aifactory-demo: python root app + root package.json + scoreboard/server.js."""
    (tmp_path / "src" / "app").mkdir(parents=True)
    (tmp_path / "src" / "app" / "main.py").write_text("app = 1\n")
    (tmp_path / "package.json").write_text(
        '{"scripts": {"start": "uvicorn src.app.main:app --reload"}}'
    )
    (tmp_path / "scoreboard").mkdir()
    (tmp_path / "scoreboard" / "server.js").write_text(
        "http.createServer().listen(3100)\n"
    )
    return tmp_path


def test_subdir_node_app_is_served_with_the_port(tmp_path):
    cmd = detect_serve_command(
        _demo(tmp_path), port=8099, targets=["scoreboard/server.js"]
    )
    assert cmd == "PORT=8099 node scoreboard/server.js"


def test_spec_file_under_the_subdir_finds_the_nearest_app(tmp_path):
    cmd = detect_serve_command(
        _demo(tmp_path), port=8099, targets=["scoreboard/public/app.js"]
    )
    assert cmd == "PORT=8099 node scoreboard/server.js"


def test_python_targets_keep_the_python_app(tmp_path):
    cmd = detect_serve_command(_demo(tmp_path), port=8099, targets=["src/app/x.py"])
    assert cmd is not None and "uvicorn app.main:app" in cmd


def test_traversal_targets_are_ignored(tmp_path):
    (tmp_path / "server.js").write_text("http.createServer().listen(3100)\n")
    cmd = detect_serve_command(tmp_path, targets=["../x/y.js", "/etc/y.js"])
    assert cmd is None


def test_directory_names_that_could_inject_shell_are_ignored(tmp_path):
    for name in ("app;echo INJECTED;#", "score board"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "server.js").write_text("http.createServer().listen(3100)\n")
        assert detect_serve_command(tmp_path, targets=[f"{name}/server.js"]) is None


def test_browser_entry_point_is_not_a_server(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "index.js").write_text("ReactDOM.render(App)\n")
    assert detect_serve_command(tmp_path, targets=["src/App.js"]) is None


@pytest.mark.parametrize("source", [None, {"target_paths": []}])
def test_browser_targets_infer_only_scripts_and_serve_node(tmp_path, source):
    project = _demo(tmp_path)
    (project / "scoreboard" / "index.html").write_text("<html></html>")
    spec = tmp_path / "spec"
    (spec / "context").mkdir(parents=True)
    (spec / "context" / "aifactory_spec.md").write_text(
        "Serve `scoreboard/server.js` with scoreboard/index.html."
    )
    if source is not None:
        (spec / "context" / "source.json").write_text(json.dumps(source))
    targets = _browser_targets(spec, project)
    assert targets == ["scoreboard/server.js"]
    assert detect_serve_command(project, port=8099, targets=targets) == (
        "PORT=8099 node scoreboard/server.js"
    )


def test_browser_targets_prefer_explicit_source_targets(tmp_path):
    project = _demo(tmp_path)
    spec = tmp_path / "spec"
    (spec / "context").mkdir(parents=True)
    (spec / "context" / "aifactory_spec.md").write_text("scoreboard/server.js")
    (spec / "context" / "source.json").write_text(
        json.dumps({"target_paths": ["src/app/main.py"]})
    )
    assert _browser_targets(spec, project) == ["src/app/main.py"]


@pytest.mark.parametrize(
    "text",
    [
        "No paths here",
        "scoreboard/missing.js",
        "../scoreboard/server.js",
        "/scoreboard/server.js",
        "scoreboard/server.json",
    ],
)
def test_browser_targets_ignore_invalid_spec_paths(tmp_path, text):
    project = _demo(tmp_path)
    spec = tmp_path / "spec"
    (spec / "context").mkdir(parents=True)
    (spec / "context" / "aifactory_spec.md").write_text(text)
    assert _browser_targets(spec, project) is None


def test_browser_targets_without_context(tmp_path):
    assert _browser_targets(tmp_path / "spec", tmp_path) is None


def test_spec_text_targets_reject_symlinks_outside_project(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "server.js"
    outside.write_text("http.createServer().listen(3100)\n")
    (project / "server.js").symlink_to(outside)
    spec = tmp_path / "spec"
    (spec / "context").mkdir(parents=True)
    (spec / "context" / "aifactory_spec.md").write_text("server.js")
    assert _spec_text_targets(spec, project) == []


def test_spec_text_targets_dedupe_and_cap_at_twenty(tmp_path):
    targets = [
        f"script{i}.{suffix}" for i in range(6) for suffix in ("js", "mjs", "cjs", "ts")
    ]
    for target in targets:
        (tmp_path / target).write_text("listen")
    spec = tmp_path / "spec"
    (spec / "context").mkdir(parents=True)
    (spec / "context" / "aifactory_spec.md").write_text(
        " ".join(target for target in targets for _ in range(2))
    )
    assert _spec_text_targets(spec, tmp_path) == targets[:20]
