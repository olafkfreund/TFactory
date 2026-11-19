"""Stop verification when a project needs services the sandbox cannot provide."""

import logging
import os
import re
from pathlib import Path

from agents.workspace_status import write_status_patch
from tfactory_yml import load_tfactory_yml

_log = logging.getLogger(__name__)


_COMPOSE_IMAGE = re.compile(r"image:\s*['\"]?(?:[\w./-]*/)?(postgres|redis)\b", re.I)
_DRIVERS = re.compile(
    r"(?<![\w.-])(psycopg2-binary|psycopg2|psycopg|asyncpg|pg8000|aioredis|redis)(?![\w.-])",
    re.I,
)


def _declared_services(
    spec_dir: Path, project_dir: Path
) -> list[tuple[str, str | None]]:
    try:
        config = load_tfactory_yml(project_dir)
        if config is not None:
            return [(service.name, service.version) for service in config.services]
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
            return (
                [
                    (service["name"], service.get("version"))
                    for service in snapshot.get("services", [])
                ]
                if snapshot
                else []
            )
    except Exception:  # noqa: BLE001 - an unreadable config must not break verify
        _log.warning(
            "services gate: unable to read config for %s", project_dir, exc_info=True
        )
        return []


def required_services(
    spec_dir: Path, project_dir: Path
) -> list[tuple[str, str | None]]:
    """Declared services plus shallow dependency hints, in stable order."""
    found = dict(_declared_services(spec_dir, project_dir))
    for pattern in (
        "docker-compose*.yml",
        "docker-compose*.yaml",
        "compose.yml",
        "compose.yaml",
    ):
        for path in project_dir.glob(pattern):
            try:
                for name in _COMPOSE_IMAGE.findall(path.read_text()):
                    found.setdefault(name.lower(), None)
            except OSError:
                _log.warning("services gate: unable to read %s", path)
    for root, dirs, files in os.walk(project_dir):
        depth = len(Path(root).relative_to(project_dir).parts)
        dirs[:] = (
            []
            if depth >= 2
            else [
                name
                for name in dirs
                if name not in {".git", "node_modules", ".venv", "venv"}
            ]
        )
        for filename in files:
            if not (
                filename == "pyproject.toml"
                or (filename.startswith("requirements") and filename.endswith(".txt"))
            ):
                continue
            path = Path(root) / filename
            try:
                drivers = _DRIVERS.findall(path.read_text())
            except OSError:
                _log.warning("services gate: unable to read %s", path)
                continue
            for driver in drivers:
                found.setdefault(
                    "redis" if driver.lower() in ("redis", "aioredis") else "postgres",
                    None,
                )
    return [(name, found[name]) for name in ("postgres", "redis") if name in found]


def halt_if_services_declared(spec_dir: Path, project_dir: Path) -> bool:
    names = [name for name, _ in _declared_services(spec_dir, project_dir)]
    if os.environ.get("TFACTORY_NIX_RUNNER_IMAGE") and os.environ.get(
        "TFACTORY_VERIFY_BACKEND", ""
    ).strip().lower() not in ("host", "docker"):
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
