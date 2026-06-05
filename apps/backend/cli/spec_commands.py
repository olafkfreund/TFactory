"""
Spec Listing Commands
=====================

Provides ``print_specs_list`` for the build CLI's ``--list`` command and
the "spec not found" help path (see ``cli/main.py``).

Reconstructed alongside ``qa_loop`` after the original module was lost as
an untracked file (#226 / #227): ``cli/main.py`` imports it at module top
level, so its absence crashed every agent CLI invocation before the spec
even ran.
"""

from __future__ import annotations

import json
from pathlib import Path


def _read_status(spec_dir: Path) -> str:
    """Best-effort status string from a spec's ``test_plan.json``."""
    plan_file = spec_dir / "test_plan.json"
    if not plan_file.exists():
        return "no plan"
    try:
        with open(plan_file) as f:
            plan = json.load(f)
    except (json.JSONDecodeError, OSError):
        return "unreadable plan"
    return plan.get("status") or plan.get("planStatus") or "unknown"


def list_specs(project_dir: Path) -> list[Path]:
    """Return spec folders (those containing ``spec.md``) under the project."""
    from .utils import get_specs_dir

    specs_dir = get_specs_dir(project_dir)
    if not specs_dir.exists():
        return []
    return sorted(
        p for p in specs_dir.iterdir() if p.is_dir() and (p / "spec.md").exists()
    )


def print_specs_list(project_dir: Path) -> None:
    """Print all specs in the project with their current status."""
    from ui import Icons, bold, icon, info, muted

    specs = list_specs(project_dir)
    if not specs:
        print(info(f"{icon(Icons.INFO)} No specs found."))
        print(muted("  Create one with: python spec_runner.py --interactive"))
        return

    print(bold(f"\nSpecs ({len(specs)}):\n"))
    for spec in specs:
        status = _read_status(spec)
        print(f"  {icon(Icons.BULLET)} {spec.name}  {muted('[' + status + ']')}")
    print()
