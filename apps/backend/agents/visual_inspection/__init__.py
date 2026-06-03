"""Visual Inspection Run (#170).

Record a generated Playwright browser run, capture verification + error
screenshots, and package a human inspection report + recording into
``automated-test/<run-id>/``. P1 (#171) = the packaging + deterministic report;
P2 adds the LLM correction plan + GitHub export + downloads.
"""

from .model import (
    RunMeta,
    StepResult,
    build_meta,
    new_run_id,
    slugify,
    verdict_for,
)
from .packager import PackagedRun, package_run
from .report import render_inspection_report

__all__ = [
    "StepResult",
    "RunMeta",
    "build_meta",
    "new_run_id",
    "slugify",
    "verdict_for",
    "PackagedRun",
    "package_run",
    "render_inspection_report",
]
