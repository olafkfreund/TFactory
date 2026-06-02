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

__all__ = [
    "AccessResult",
    "CloudAssessment",
    "CloudFinding",
    "access_check",
    "assess",
    "discover",
    "parse_ocsf",
    "to_inventory_findings",
]
