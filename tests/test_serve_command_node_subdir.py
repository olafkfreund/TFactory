"""#1609: a node app in a subdir is served from there, not by the root `npm start`."""

from agents.nix_env import detect_serve_command


def _demo(tmp_path):
    """aifactory-demo: python root app + root package.json + scoreboard/server.js."""
    (tmp_path / "src" / "app").mkdir(parents=True)
    (tmp_path / "src" / "app" / "main.py").write_text("app = 1\n")
    (tmp_path / "package.json").write_text(
        '{"scripts": {"start": "uvicorn src.app.main:app --reload"}}'
    )
    (tmp_path / "scoreboard").mkdir()
    (tmp_path / "scoreboard" / "server.js").write_text("// node:http\n")
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
    (tmp_path / "server.js").write_text("")
    cmd = detect_serve_command(tmp_path, targets=["../x/y.js", "/etc/y.js"])
    assert cmd is None


def test_directory_names_that_could_inject_shell_are_ignored(tmp_path):
    for name in ("app;echo INJECTED;#", "score board"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "server.js").write_text("")
        assert detect_serve_command(tmp_path, targets=[f"{name}/server.js"]) is None
