"""Cloud discovery + assessment for TFactory (#133).

Read-only by design: enumerate a cloud account's identity + resources into a
normalized inventory (consumed by ``agents.diagrams.render_cloud_topology`` and
the cloud assessment framework). Never mutates the account.
"""

from .assessment import (
    CloudAssessment,
    CloudFinding,
    assess,
    parse_ocsf,
    to_inventory_findings,
)
from .discovery import AccessResult, access_check, discover
from .report import assess_and_write, cloud_findings_paths, render_report_markdown
from .runner import build_prowler_command, run_cloud_assessment

__all__ = [
    "AccessResult",
    "CloudAssessment",
    "CloudFinding",
    "access_check",
    "assess",
    "assess_and_write",
    "build_prowler_command",
    "cloud_findings_paths",
    "discover",
    "parse_ocsf",
    "render_report_markdown",
    "run_cloud_assessment",
    "to_inventory_findings",
]
