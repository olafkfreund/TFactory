"""Cloud discovery + assessment for TFactory (#133).

Read-only by design: enumerate a cloud account's identity + resources into a
normalized inventory (consumed by ``agents.diagrams.render_cloud_topology`` and
the cloud assessment framework). Never mutates the account.
"""

from .discovery import AccessResult, access_check, discover

__all__ = ["AccessResult", "access_check", "discover"]
