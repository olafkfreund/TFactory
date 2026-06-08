"""Tests for staging visual baselines into a browser run (#109).

The pure staging logic (stage_baselines) is covered by test_visual_baseline.py;
this pins the evaluator's call-site helper that resolves the target + stages
into the run scratch.
"""

from __future__ import annotations

from agents.evaluator import _stage_visual_baselines
from agents.evidence.visual_baseline import accept_baseline


def _seed_baseline(spec_dir, target, snapshot="home.png"):
    (spec_dir / "findings").mkdir(parents=True, exist_ok=True)
    cap = spec_dir / "cap.png"
    cap.write_bytes(b"\x89PNG\r\n\x1a\n fake")
    accept_baseline(spec_dir, target, snapshot, cap)


def test_stages_baselines_for_default_target(tmp_path):
    spec = tmp_path / "spec"
    spec.mkdir()
    _seed_baseline(spec, "default")
    dest = tmp_path / "scratch"
    dest.mkdir()
    n = _stage_visual_baselines(spec, {"target_name": "default"}, dest)
    assert n == 1
    staged = dest / "findings" / "visual_baselines" / "default" / "home.png"
    assert staged.is_file()


def test_resolves_named_target(tmp_path):
    spec = tmp_path / "spec"
    spec.mkdir()
    _seed_baseline(spec, "web")
    dest = tmp_path / "scratch"
    dest.mkdir()
    assert _stage_visual_baselines(spec, {"target_name": "web"}, dest) == 1


def test_zero_when_no_baselines(tmp_path):
    spec = tmp_path / "spec"
    (spec / "findings").mkdir(parents=True)
    dest = tmp_path / "scratch"
    dest.mkdir()
    assert _stage_visual_baselines(spec, {"target_name": "default"}, dest) == 0


def test_none_spec_dir_is_zero(tmp_path):
    assert _stage_visual_baselines(None, {}, tmp_path) == 0


def test_missing_target_name_uses_default(tmp_path):
    spec = tmp_path / "spec"
    spec.mkdir()
    _seed_baseline(spec, "default")
    dest = tmp_path / "scratch"
    dest.mkdir()
    assert _stage_visual_baselines(spec, {}, dest) == 1  # falls back to "default"
