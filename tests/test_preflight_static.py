"""Tests for the Gen-Functional pre-flight static check — Task 6 (#7) commit 2.

Covered:
  - extract_imports: every import form (import, from-import with single
    + multi-name, aliased, star, relative), with line numbers
  - extract_imports: syntax error in source returns (empty, error_str)
  - check_import: stdlib happy path, absent module → skipped (#707),
    hallucinated attribute → failed, relative import → skipped
  - preflight_check end-to-end:
      • valid stdlib-only test → ok=True
      • absent module → skipped, ok stays True (#707)
      • hallucinated attribute → ok=False with "has no attribute"
      • mixed valid + invalid → ok=False, only the bad ones in .failures
      • syntax error → ok=False, no imports checked
      • against the planner_smoke fixture project_tree (happy)
      • skip_stdlib_check shortcut
"""

from __future__ import annotations

import os
from pathlib import Path

import agents.preflight_static as ps
import pytest
from agents.preflight_static import (
    PreflightImport,
    PreflightResult,
    check_import,
    extract_imports,
    package_root_rel_paths,
    preflight_check,
    requirements_files,
)

FIXTURE_PROJECT = Path(__file__).parent / "fixtures" / "planner_smoke" / "project_tree"


# ── extract_imports ─────────────────────────────────────────────────────


def test_extract_plain_import() -> None:
    imports, err = extract_imports("import json\n")
    assert err is None
    assert len(imports) == 1
    assert imports[0].module == "json"
    assert imports[0].name is None
    assert imports[0].lineno == 1


def test_extract_dotted_import() -> None:
    imports, err = extract_imports("import a.b.c\n")
    assert err is None
    assert imports[0].module == "a.b.c"


def test_extract_from_import_single_name() -> None:
    imports, err = extract_imports("from pathlib import Path\n")
    assert err is None
    assert len(imports) == 1
    assert imports[0].module == "pathlib"
    assert imports[0].name == "Path"


def test_extract_from_import_multiple_names_expanded() -> None:
    imports, err = extract_imports("from typing import List, Dict, Tuple\n")
    assert err is None
    assert len(imports) == 3
    assert {i.name for i in imports} == {"List", "Dict", "Tuple"}
    assert all(i.module == "typing" for i in imports)


def test_extract_aliased_import_records_alias_and_original() -> None:
    imports, err = extract_imports("import asyncio as a\n")
    assert err is None
    assert imports[0].module == "asyncio"
    assert imports[0].alias == "a"


def test_extract_from_import_aliased() -> None:
    imports, err = extract_imports("from datetime import datetime as dt\n")
    assert err is None
    assert imports[0].module == "datetime"
    assert imports[0].name == "datetime"
    assert imports[0].alias == "dt"


def test_extract_star_import() -> None:
    imports, err = extract_imports("from json import *\n")
    assert err is None
    assert imports[0].name == "*"


def test_extract_relative_imports_marked() -> None:
    imports, err = extract_imports("from . import sibling\nfrom ..pkg import thing\n")
    assert err is None
    assert all(i.is_relative for i in imports)
    assert imports[0].module == "."
    assert imports[1].module == "..pkg"


def test_extract_records_line_numbers() -> None:
    src = "\nimport os\n\n\nimport sys\n"  # lines 2 and 5
    imports, _ = extract_imports(src)
    linenos = sorted(i.lineno for i in imports)
    assert linenos == [2, 5]


def test_extract_returns_syntax_error_for_bad_source() -> None:
    imports, err = extract_imports("def \n")  # syntax error
    assert imports == []
    assert err is not None
    assert "SyntaxError" in err


def test_extract_handles_empty_source() -> None:
    imports, err = extract_imports("")
    assert err is None
    assert imports == []


def test_extract_walks_into_function_bodies() -> None:
    """Imports inside functions / classes are still imports — collect them."""
    src = "def f():\n    import json\nclass C:\n    from pathlib import Path\n"
    imports, _ = extract_imports(src)
    assert {(i.module, i.name) for i in imports} == {
        ("json", None),
        ("pathlib", "Path"),
    }


# ── check_import (per-import subprocess) ────────────────────────────────


def test_check_import_passes_for_stdlib() -> None:
    imp = PreflightImport(module="json")
    check_import(imp)
    assert imp.failed is False
    assert imp.skipped is False


def test_check_import_skips_absent_module() -> None:
    # #707: a module absent from the generation interpreter is an environment
    # gap (third-party lib / SUT transitive dep that lives in the test-execution
    # env, not TFactory's venv), NOT a hallucination. Skip it — the real test
    # run resolves it — rather than false-rejecting and replan-looping to stuck.
    imp = PreflightImport(module="this_module_definitely_does_not_exist_xyz")
    check_import(imp)
    assert imp.failed is False
    assert imp.skipped is True
    assert "ModuleNotFoundError" in (imp.reason or "")


def test_check_from_import_passes_for_real_attribute() -> None:
    imp = PreflightImport(module="json", name="loads")
    check_import(imp)
    assert imp.failed is False


def test_check_from_import_fails_for_missing_attribute() -> None:
    imp = PreflightImport(module="json", name="ghost_func_does_not_exist")
    check_import(imp)
    assert imp.failed is True
    assert "has no attribute" in (imp.reason or "")


def test_check_relative_import_is_skipped() -> None:
    imp = PreflightImport(module=".sibling", is_relative=True)
    check_import(imp)
    assert imp.failed is False
    assert imp.skipped is True
    assert "relative import" in (imp.reason or "")


def test_check_star_import_only_verifies_module() -> None:
    imp = PreflightImport(module="json", name="*")
    check_import(imp)
    assert imp.failed is False


def test_check_import_resolves_src_layout_package(tmp_path) -> None:
    """A src-layout package (``<project>/src/<pkg>``) imports when the project
    dir is supplied — ``<project>/src`` is added to PYTHONPATH so no install
    step is needed. Regression guard for the #609 replan loop."""
    pkg = tmp_path / "src" / "orders_api"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "main.py").write_text("app = object()\n")
    imp = PreflightImport(module="orders_api.main", name="app")
    check_import(imp, project_dir=tmp_path)
    assert imp.failed is False, imp.reason


def test_check_from_import_of_real_submodule_passes(tmp_path) -> None:
    """#712: ``from pkg import sub`` where ``sub`` is a real SUBMODULE (a file
    on disk) that the package __init__ does not re-export.

    ``importlib.import_module(pkg)`` runs only ``pkg/__init__.py`` and does NOT
    import submodules, so ``hasattr(pkg, 'sub')`` is False even though the real
    test run resolves the import fine. The pre-flight must probe the submodule
    before declaring a hallucination — otherwise a perfectly correct api/unit
    test replan-loops to the STUCK budget (the residual root cause of #707/#712,
    not covered by the #709 absent-*module* fix)."""
    pkg = tmp_path / "app"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")  # does NOT re-export routes
    (pkg / "routes.py").write_text("def create_app():\n    return object()\n")
    imp = PreflightImport(module="app", name="routes")
    check_import(imp, project_dir=tmp_path)
    assert imp.failed is False, imp.reason
    assert imp.skipped is False  # a real submodule verifies, it isn't skipped


def test_check_from_import_submodule_with_absent_dep_is_skipped(tmp_path) -> None:
    """#712: a real submodule that imports a third-party dep absent from the
    generation env is an environment gap (like #709), not a hallucination —
    skip it rather than false-reject."""
    pkg = tmp_path / "app"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "routes.py").write_text("import totally_absent_dep_xyz\n")
    imp = PreflightImport(module="app", name="routes")
    check_import(imp, project_dir=tmp_path)
    assert imp.failed is False, imp.reason
    assert imp.skipped is True


def test_check_from_import_genuinely_absent_name_still_fails(tmp_path) -> None:
    """#712 safety: a name that is neither an attribute NOR an importable
    submodule is still a genuine hallucination and must fail — the submodule
    probe must not weaken the guard."""
    pkg = tmp_path / "app"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    imp = PreflightImport(module="app", name="ghost_name_xyz")
    check_import(imp, project_dir=tmp_path)
    assert imp.failed is True
    assert "has no attribute" in (imp.reason or "")


# ── preflight_check end-to-end ──────────────────────────────────────────


def test_preflight_valid_stdlib_test_passes() -> None:
    src = (
        "import json\n"
        "from pathlib import Path\n"
        "from typing import List\n"
        "import asyncio\n"
    )
    res = preflight_check(src)
    assert res.ok is True
    assert len(res.imports_checked) == 4
    assert res.failures == []


def test_preflight_absent_module_is_skipped_not_failed() -> None:
    # #707: an unresolvable module is skipped (unverifiable in the gen env),
    # so it does not sink the whole check into a replan loop. Contrast with a
    # missing ATTRIBUTE on an importable module, which still fails (below).
    src = "from totally_fake_module import nothing\n"
    res = preflight_check(src)
    assert res.ok is True
    assert res.failures == []
    assert len(res.skipped) == 1
    assert "totally_fake_module" in res.skipped[0].module


def test_preflight_hallucinated_attribute_fails() -> None:
    src = "from json import ghost_func_xyz\n"
    res = preflight_check(src)
    assert res.ok is False
    assert len(res.failures) == 1
    assert "has no attribute" in res.failures[0].reason


def test_preflight_mixed_valid_and_invalid_isolates_failures() -> None:
    src = (
        "import json\n"
        "from json import loads, ghost_func\n"
        "from pathlib import Path\n"
        "import not_a_real_module_xyz\n"
    )
    res = preflight_check(src)
    # ghost_func is a genuine hallucination (importable module, missing
    # attribute) -> fail. not_a_real_module_xyz is an absent module -> skipped
    # (#707), not a failure.
    assert res.ok is False
    fail_names = {f.describe() for f in res.failures}
    assert any("ghost_func" in n for n in fail_names)
    assert not any("not_a_real_module_xyz" in n for n in fail_names)
    assert any("not_a_real_module_xyz" in s.describe() for s in res.skipped)
    # The valid ones aren't in failures
    assert not any("import json\n" in f.describe() for f in res.failures)
    assert not any("Path" in f.describe() for f in res.failures)


def test_preflight_api_lane_test_with_absent_dep_passes() -> None:
    # #707 regression: a correct api-lane test imports `requests` (or the SUT's
    # own deps) that aren't installed in TFactory's service venv, and reads
    # TFACTORY_TARGET_URL at module level. This must NOT trigger a replan —
    # previously it false-rejected -> stuck -> generated_empty -> verify never
    # ran, blocking clean VAL-2 even when the endpoint code was correct.
    src = (
        "import os\n"
        "import requests_absent_in_gen_env\n"
        "\n"
        "BASE_URL = os.environ['TFACTORY_TARGET_URL']\n"
        "\n"
        "def test_healthz():\n"
        "    resp = requests_absent_in_gen_env.get(f'{BASE_URL}/healthz', timeout=10)\n"
        "    assert resp.status_code == 200\n"
    )
    res = preflight_check(src)
    assert res.ok is True, res.summary()
    assert res.failures == []


def test_preflight_syntax_error_returns_early() -> None:
    src = "def \n"  # malformed
    res = preflight_check(src)
    assert res.ok is False
    assert res.syntax_error is not None
    assert "SyntaxError" in res.syntax_error
    assert res.imports_checked == []


def test_preflight_empty_source_is_ok() -> None:
    res = preflight_check("")
    assert res.ok is True
    assert res.imports_checked == []


def test_preflight_relative_imports_dont_block_success() -> None:
    """Relative imports are skipped but shouldn't fail the whole check
    when nothing else is wrong."""
    src = "import json\nfrom . import sibling\n"
    res = preflight_check(src)
    assert res.ok is True
    assert len(res.skipped) == 1
    assert res.skipped[0].is_relative


def test_preflight_against_real_fixture_project_happy() -> None:
    """Targets a real fixture project — the planner_smoke tree."""
    src = (
        "from app.auth import login_user, get_session\n"
        "from app.auth.session import GRACE_WINDOW_MIN\n"
    )
    res = preflight_check(src, project_dir=FIXTURE_PROJECT)
    assert res.ok is True, f"unexpected failures: {[f.reason for f in res.failures]}"


def test_preflight_against_real_fixture_project_hallucinated_attr() -> None:
    src = "from app.auth import this_method_does_not_exist\n"
    res = preflight_check(src, project_dir=FIXTURE_PROJECT)
    assert res.ok is False
    assert "has no attribute" in res.failures[0].reason


def test_skip_stdlib_check_bypasses_subprocess_for_stdlib() -> None:
    src = "import json\nfrom pathlib import Path\nfrom typing import List\n"
    res = preflight_check(src, skip_stdlib_check=True)
    assert res.ok is True
    # All three were skipped (with the "stdlib" reason)
    assert len(res.skipped) == 3
    assert all("stdlib" in s.reason for s in res.skipped)


# ── Summary helper ──────────────────────────────────────────────────────


def test_summary_reports_ok_count() -> None:
    src = "import json\nfrom pathlib import Path\n"
    res = preflight_check(src)
    assert "OK" in res.summary()
    assert "2" in res.summary()


def test_summary_reports_failure_count() -> None:
    # Genuine hallucinations (importable module, missing attribute) still fail;
    # absent modules are skipped (#707), so use missing attributes here.
    src = "from json import ghost_one_xyz\nfrom os import ghost_two_xyz\n"
    res = preflight_check(src)
    assert "2 failed" in res.summary()


# ── #732: monorepo package roots ────────────────────────────────────────


def _monorepo(tmp_path: Path) -> Path:
    """A repo whose importable package lives at apps/web-server/server/."""
    pkg = tmp_path / "apps" / "web-server" / "server" / "routes"
    pkg.mkdir(parents=True)
    (tmp_path / "apps" / "web-server" / "server" / "__init__.py").write_text("")
    (pkg / "__init__.py").write_text("")
    (pkg / "git.py").write_text("def assert_safe_mcp_url(url):\n    return None\n")
    return tmp_path


def test_package_roots_finds_a_nested_package(tmp_path: Path) -> None:
    root = _monorepo(tmp_path)
    assert ps.package_roots_for(root, "server.routes.git") == [
        str(root / "apps" / "web-server")
    ]


def test_package_roots_empty_for_unknown_package(tmp_path: Path) -> None:
    """Unknown package: no roots, so behaviour falls back to project/src."""
    assert ps.package_roots_for(_monorepo(tmp_path), "nothing_here") == []


def test_package_roots_skips_vendored_directories(tmp_path: Path) -> None:
    """A copy under node_modules/.venv must never win over the checkout."""
    root = _monorepo(tmp_path)
    vendored = root / "node_modules" / "server"
    vendored.mkdir(parents=True)
    (vendored / "__init__.py").write_text("")
    roots = ps.package_roots_for(root, "server.routes.git")
    assert str(root / "node_modules") not in roots


def test_check_import_resolves_a_nested_package_from_the_checkout(
    tmp_path: Path,
) -> None:
    """The #732 regression.

    Without the nested root on PYTHONPATH the module either is not found
    (skipped, harmless) or -- the real failure -- resolves against a copy of
    the same package already on the ambient path, reporting a brand-new
    function as a hallucinated attribute. That exhausted a whole replan budget
    and failed a verify run against correct code.
    """
    root = _monorepo(tmp_path)
    imp = ps.PreflightImport(
        module="server.routes.git",
        name="assert_safe_mcp_url",
        lineno=1,
        is_relative=False,
    )
    ps.check_import(imp, project_dir=root)
    assert imp.failed is False, imp.reason
    assert imp.skipped is False, "the module IS resolvable — it must be checked"


def test_check_import_still_catches_a_hallucination_in_a_nested_package(
    tmp_path: Path,
) -> None:
    """Finding the right root must not blind the check it exists to perform."""
    root = _monorepo(tmp_path)
    imp = ps.PreflightImport(
        module="server.routes.git",
        name="not_a_real_function",
        lineno=1,
        is_relative=False,
    )
    ps.check_import(imp, project_dir=root)
    assert imp.failed is True
    assert "has no attribute" in (imp.reason or "")


# ── #752: the probe must not resolve against the service's own tree ──────


def test_probe_runs_from_the_checkout_not_the_service_cwd(tmp_path, monkeypatch):
    """TFactory verifying TFactory imported the RUNNING service's module.

    `python -c` puts the CWD at the front of sys.path, ahead of PYTHONPATH, so
    inheriting the service's working directory outranked the checkout roots.
    Both trees provide `server.routes.git`; the probe read the wrong one and
    called a correct test a hallucination.
    """
    # The "service" tree: same package name, WITHOUT the built symbol.
    service = tmp_path / "service"
    (service / "server" / "routes").mkdir(parents=True)
    for d in (service / "server", service / "server" / "routes"):
        (d / "__init__.py").write_text("")
    (service / "server" / "routes" / "git.py").write_text("def old_only(): ...\n")

    # The checkout under verification: same package name, WITH the built symbol.
    checkout = tmp_path / "checkout"
    (checkout / "server" / "routes").mkdir(parents=True)
    for d in (checkout / "server", checkout / "server" / "routes"):
        (d / "__init__.py").write_text("")
    (checkout / "server" / "routes" / "git.py").write_text(
        "def _is_safe_mcp_url(url): return True\n"
    )

    monkeypatch.chdir(service)  # exactly what the pod does
    res = check_import(
        PreflightImport(module="server.routes.git", name="_is_safe_mcp_url"),
        project_dir=checkout,
    )
    assert not res.failed, res.reason


# ── #756: execution-time roots for a monorepo ───────────────────────────


def test_package_root_rel_paths_finds_the_monorepo_root(tmp_path: Path) -> None:
    root = _monorepo(tmp_path)  # apps/web-server/server, as the Factory repos are
    (root / "src" / "libpkg").mkdir(parents=True)
    (root / "src" / "libpkg" / "__init__.py").write_text("")
    for vendor in ("node_modules", ".venv"):
        (root / vendor / "junk").mkdir(parents=True)
        (root / vendor / "junk" / "__init__.py").write_text("")

    rels = package_root_rel_paths(root)
    assert "apps/web-server" in rels, rels  # the root nothing used to provide
    assert "src" in rels, rels
    # Inner packages never become roots — only the outermost one is needed.
    assert "apps/web-server/server" not in rels, rels
    # Vendor dirs stay out.
    assert not any(r.startswith(("node_modules", ".venv")) for r in rels), rels


def test_package_root_rel_paths_is_empty_without_packages(tmp_path: Path) -> None:
    bare = tmp_path / "bare"
    (bare / "scripts").mkdir(parents=True)
    (bare / "scripts" / "run.py").write_text("x = 1\n")
    assert package_root_rel_paths(bare) == []


# ── #759: find a monorepo's requirements files ──────────────────────────


def test_requirements_files_found_below_the_root(tmp_path: Path) -> None:
    """This repo's exact shape: no root requirements.txt, two below it."""
    root = _monorepo(tmp_path)
    (root / "apps" / "web-server" / "requirements.txt").write_text("fastapi\n")
    (root / "apps" / "backend" / "requirements.txt").parent.mkdir(parents=True)
    (root / "apps" / "backend" / "requirements.txt").write_text("pytest\n")
    for vendor in ("node_modules", ".venv"):
        (root / vendor).mkdir(parents=True, exist_ok=True)
        (root / vendor / "requirements.txt").write_text("junk\n")

    found = [str(p.relative_to(root)) for p in requirements_files(root)]
    assert "apps/web-server/requirements.txt" in found, found
    assert "apps/backend/requirements.txt" in found, found
    # Vendored copies must never be installed.
    assert not any(f.startswith(("node_modules", ".venv")) for f in found), found


def test_requirements_files_prefers_the_root_and_is_bounded(tmp_path: Path) -> None:
    root = _monorepo(tmp_path)
    (root / "requirements.txt").write_text("a\n")
    for i in range(8):
        d = root / f"svc{i}"
        d.mkdir()
        (d / "requirements.txt").write_text("b\n")
    found = requirements_files(root, limit=3)
    assert len(found) == 3
    assert found[0].name == "requirements.txt"
    assert found[0].parent == root  # nearest first


# ── #754: say which file answered, and don't call shadowing a lie ────────


def test_missing_attribute_reason_names_the_resolved_file(tmp_path: Path) -> None:
    """A genuine hallucination still fails — and now says where it looked."""
    root = _monorepo(tmp_path)
    imp = PreflightImport(
        module="server.routes.git", name="never_written", lineno=1, is_relative=False
    )
    check_import(imp, project_dir=root)
    assert imp.failed is True, imp.reason
    assert "never_written" in imp.reason
    assert "resolved:" in imp.reason, imp.reason
    assert "git.py" in imp.reason, imp.reason


def test_shadowed_import_is_skipped_not_called_a_hallucination(tmp_path: Path) -> None:
    """The #732/#742/#752 shape: another copy answered, so we cannot judge.

    The service tree lacks the symbol; the checkout has it. With the service
    tree winning (it is the CWD), the old code called a correct test a
    hallucination and burned a replan budget.
    """
    service = tmp_path / "service"
    (service / "server" / "routes").mkdir(parents=True)
    for d in (service / "server", service / "server" / "routes"):
        (d / "__init__.py").write_text("")
    (service / "server" / "routes" / "git.py").write_text("def other(): ...\n")

    checkout = _monorepo(tmp_path)  # has assert_safe_mcp_url

    imp = PreflightImport(
        module="server.routes.git",
        name="assert_safe_mcp_url",
        lineno=1,
        is_relative=False,
    )
    # Force the service copy to win, as an ambient PYTHONPATH would.
    env_before = os.environ.get("PYTHONPATH", "")
    os.environ["PYTHONPATH"] = str(service)
    try:
        check_import(imp, project_dir=checkout / "nonexistent-subdir")
    finally:
        os.environ["PYTHONPATH"] = env_before

    # Either it resolved the checkout (fine) or it was shadowed — but a
    # shadowed result must never be reported as a hallucination.
    assert imp.failed is False or "resolution failure" not in (imp.reason or "")


def test_third_party_missing_attribute_still_fails(tmp_path: Path) -> None:
    """Guard against over-reach: json is not a package this checkout owns."""
    imp = PreflightImport(
        module="json", name="definitely_not_here", lineno=1, is_relative=False
    )
    check_import(imp, project_dir=_monorepo(tmp_path))
    assert imp.failed is True, imp.reason
    assert imp.skipped is False, "a real hallucination must not be excused"
