"""Assertions are written before the diff is shown (#1502).

Step 1 writes the test from the criterion alone; step 2 gets the diff for
wiring and may not change an expected value. Real ``run_gen_functional`` with
the SDK faked; each session writes the next source in turn.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from agents.gen_functional import run_gen_functional
from prompts_pkg.prompts import get_tfactory_gen_functional_prompt

_REL = "tests/test_total.py"
# The criterion says 11.76; the implementation (shop.total) returns 11.75.
_ASSERT_CRITERION = (
    "from shop import total\n\n\ndef test_total():\n    assert total() == 11.76\n"
)
_ASSERT_IMPL = (
    "from shop import total\n\n\ndef test_total():\n    assert total() == 11.75\n"
)
_WIRED_KEEPS = _ASSERT_CRITERION.replace("from shop", "import shop\nfrom shop")


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TFACTORY_AUTO_PLAN", "0")
    monkeypatch.setenv("TFACTORY_AUTO_EVALUATE", "0")
    monkeypatch.delenv("TFACTORY_GEN_TWO_STEP", raising=False)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    pd = tmp_path / "project"
    pd.mkdir()
    (pd / "shop.py").write_text("def total():\n    return 11.75\n")
    return pd


def _make_spec(tmp_path: Path, **extra) -> Path:
    d = tmp_path / "workspaces" / "demo" / "specs" / "001"
    for sub in ("context", "tests", "findings", "logs"):
        (d / sub).mkdir(parents=True)
    (d / "status.json").write_text(json.dumps({"status": "planned"}))
    subtask = {
        "id": "s0",
        # No number here, so only the lock (not #888) can catch a rewrite.
        "description": "the order total is the signed amount",
        "status": "pending",
        "lane": "unit",
        "language": "python",
        "framework": "pytest",
        "target": "shop.py::total",
        "rationale": "AC#1",
        "files_to_create": extra.pop("files", [_REL]),
    }
    (d / "test_plan.json").write_text(
        json.dumps(
            {
                "feature": "demo",
                "workflow_type": "feature",
                "phases": [{"phase": 1, "name": "AC#1", "subtasks": [subtask]}],
                "status": "in_progress",
            }
        )
    )
    return d


@pytest.fixture
def spec(tmp_path: Path) -> Path:
    return _make_spec(tmp_path)


@pytest.fixture
def sdk(monkeypatch: pytest.MonkeyPatch):
    """Each session writes the next source (``None`` = writes nothing)."""
    prompts: list[str] = []

    class _CM:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

    def _setup(*sources: str | None, status: str = "complete") -> list[str]:
        queue = list(sources)

        async def _resolve(*a, **kw):
            return _CM()

        async def _invoke(client, prompt, spec_dir, verbose):
            prompts.append(prompt)
            src = queue.pop(0) if len(queue) > 1 else queue[0]
            if src is not None:
                for line in prompt.splitlines():
                    if line.startswith("- write the file at:"):
                        path = Path(line.split("`")[1])
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_text(src)
                        break
            return status if len(prompts) > 1 else "complete", "ok", {}

        monkeypatch.setattr("agents.gen_functional._resolve_client", _resolve)
        monkeypatch.setattr("agents.gen_functional._invoke_session", _invoke)
        return prompts

    return _setup


def _replan(spec: Path) -> dict | None:
    f = spec / "context" / "replan_request.json"
    return json.loads(f.read_text()) if f.exists() else None


async def test_assertions_are_requested_before_the_diff_is_shown(
    spec: Path, project: Path, sdk
) -> None:
    prompts = sdk(_ASSERT_CRITERION, _WIRED_KEEPS)

    assert await run_gen_functional(spec, project) is True

    assert len(prompts) == 2
    first, second = prompts
    assert str(spec / "context" / "diff.patch") not in first
    assert "Do NOT Read `context/diff.patch`" in first
    assert "WIRING STEP" not in first
    assert str(spec / "context" / "diff.patch") in second
    assert "WIRING STEP" in second and "FIXED" in second


async def test_contradicting_criterion_is_asserted_and_fails_the_implementation(
    spec: Path, project: Path, sdk, monkeypatch: pytest.MonkeyPatch
) -> None:
    sdk(_ASSERT_CRITERION, _WIRED_KEEPS)
    assert await run_gen_functional(spec, project) is True

    # The kept test asserts the criterion; against shop.total() == 11.75 it fails.
    monkeypatch.syspath_prepend(str(project))
    ns: dict = {}
    exec((spec / _REL).read_text(), ns)  # noqa: S102 - generated test, fixture project
    with pytest.raises(AssertionError):
        ns["test_total"]()


async def test_wiring_that_adopts_the_implementation_value_is_rejected(
    spec: Path, project: Path, sdk
) -> None:
    sdk(_ASSERT_CRITERION, _ASSERT_IMPL)

    assert await run_gen_functional(spec, project) is False

    rr = _replan(spec)
    assert rr is not None and "11.76" in rr["reason"]


async def test_an_import_retry_that_overwrites_the_value_is_rejected(
    spec: Path, project: Path, sdk
) -> None:
    bad = _ASSERT_CRITERION + 'require("app/nothing/here")\n'  # unresolvable
    # step 1 ok, wiring keeps the value but leaves a bad import, retry swaps it.
    sdk(_ASSERT_CRITERION, bad, _ASSERT_IMPL)

    assert await run_gen_functional(spec, project) is False
    assert _replan(spec) is not None


async def test_wiring_that_writes_nothing_leaves_the_draft_to_the_guardrails(
    spec: Path, project: Path, sdk
) -> None:
    sdk(_ASSERT_CRITERION, None)

    assert await run_gen_functional(spec, project) is True
    assert (spec / _REL).read_text() == _ASSERT_CRITERION


async def test_a_wiring_session_error_fails_the_subtask(
    spec: Path, project: Path, sdk
) -> None:
    sdk(_ASSERT_CRITERION, _WIRED_KEEPS, status="error")

    await run_gen_functional(spec, project)

    plan = json.loads((spec / "test_plan.json").read_text())
    st = plan["phases"][0]["subtasks"][0]
    assert st["status"] != "completed"


async def test_kill_switch_runs_one_session(
    spec: Path, project: Path, sdk, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TFACTORY_GEN_TWO_STEP", "0")
    prompts = sdk(_ASSERT_CRITERION)

    assert await run_gen_functional(spec, project) is True
    assert len(prompts) == 1
    assert "WIRING STEP" not in prompts[0] and "diff.patch" in prompts[0]


async def test_multiple_output_files_stay_single_step(
    tmp_path: Path, project: Path, sdk
) -> None:
    spec = _make_spec(tmp_path, files=[_REL, "tests/test_other.py"])
    prompts = sdk(_ASSERT_CRITERION)

    await run_gen_functional(spec, project)

    assert len(prompts) == 1


def test_default_step_prompt_is_unchanged(spec: Path, project: Path) -> None:
    from agents.gen_functional import _resolve_framework_descriptor
    from test_plan import ImplementationPlan

    plan = ImplementationPlan.load(spec / "test_plan.json")
    st = plan.phases[0].subtasks[0]
    desc = _resolve_framework_descriptor(st)
    single = get_tfactory_gen_functional_prompt(spec, project, st, desc)
    assert "diff.patch" in single and "WIRING STEP" not in single
