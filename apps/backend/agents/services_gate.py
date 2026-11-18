"""Stop verification when a project needs services the sandbox cannot provide."""

import logging
from pathlib import Path

from agents.workspace_status import write_status_patch
from tfactory_yml import load_tfactory_yml

_log = logging.getLogger(__name__)


def halt_if_services_declared(spec_dir: Path, project_dir: Path) -> bool:
    try:
        config = load_tfactory_yml(project_dir)
        if config is not None:
            names = [service.name for service in config.services]
        else:
            from agents.evaluator_targets import (  # noqa: PLC0415 - avoids a cycle
                _load_tfactory_cfg,
            )

            snapshot = _load_tfactory_cfg(spec_dir)
            if snapshot is None and (
                (spec_dir / "context" / "tfactory_yml.json").exists()
                or (spec_dir / ".worktree" / ".tfactory.yml").exists()
            ):
                _log.warning("services gate: unable to read snapshot for %s", spec_dir)
            names = (
                [service["name"] for service in snapshot.get("services", [])]
                if snapshot
                else []
            )
    except Exception:  # noqa: BLE001 - an unreadable config must not break verify
        _log.warning(
            "services gate: unable to read config for %s", project_dir, exc_info=True
        )
        return False

    if not names:
        return False

    reason = (
        f"unverifiable: needs services ({', '.join(names)}): "
        "the verify sandbox cannot provision them (#1582)"
    )
    write_status_patch(
        spec_dir,
        "services_gate",
        status="generated_empty",
        phase="services_gate",
        tests_generated=0,
        verdict_class="infra",
        verify_skip_reason=reason,
    )
    _log.warning("services gate: %s", reason)
    return True
