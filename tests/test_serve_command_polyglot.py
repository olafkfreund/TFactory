"""A polyglot repo must not serve every spec with the one app it happens to hold.

TFactory#1174: spec 165 targeted a static `games/tictactoe/index.html` and got
`serve_command: "python -m uvicorn app.main:app"`, because the same checkout also
holds `src/app/main.py` (a FastAPI link-shortener), plus `go.mod`. The probe read
the repo root and never looked at what the spec targets.
"""

from __future__ import annotations

from agents.nix_env import detect_serve_command


def _polyglot(tmp_path):
    """The aifactory-demo shape: python app + static game + go, one checkout."""
    (tmp_path / "src" / "app").mkdir(parents=True)
    (tmp_path / "src" / "app" / "main.py").write_text("app = 1\n")
    (tmp_path / "games" / "tictactoe").mkdir(parents=True)
    (tmp_path / "games" / "tictactoe" / "index.html").write_text("<html></html>")
    return tmp_path


def test_static_target_is_not_served_by_an_unrelated_python_app(tmp_path):
    cmd = detect_serve_command(
        _polyglot(tmp_path), targets=["games/tictactoe/index.html"]
    )
    assert cmd is None, "a different app in the same repo must not serve this spec"


def test_the_python_app_is_still_served_for_its_own_targets(tmp_path):
    cmd = detect_serve_command(_polyglot(tmp_path), targets=["src/app/links_router.py"])
    assert cmd is not None and "uvicorn app.main:app" in cmd


def test_without_targets_repo_wide_behaviour_is_unchanged(tmp_path):
    """A single-app repo, and every existing caller, must not shift."""
    cmd = detect_serve_command(_polyglot(tmp_path))
    assert cmd is not None and "uvicorn app.main:app" in cmd


def test_the_contract_still_wins(tmp_path):
    """An explicit environment.serve_command is authoritative regardless."""
    cmd = detect_serve_command(
        _polyglot(tmp_path),
        {"serve_command": "python -m http.server 8099"},
        targets=["games/tictactoe/index.html"],
    )
    assert cmd == "python -m http.server 8099"
