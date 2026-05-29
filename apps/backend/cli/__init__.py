"""
CLI Package
===========

Exposes two CLI surfaces:

1. **Existing TFactory build CLI** (``main``) — argument-parser-based runner
   used by ``run.py``, the web-server, and direct invocations like
   ``python cli/main.py``.

2. **tfactory CLI** (``tfactory_main``) — click-based subcommand group
   with ``init`` and ``migrate`` subcommands.  Accessible via::

       python -m cli init
       python -m cli migrate v0_1_catalog

   The ``__main__.py`` module routes ``python -m cli`` to ``tfactory_main``.

Module structure:
- main.py:               Argument parsing and command routing (legacy build CLI)
- tfactory_init.py:      `init` subcommand — .tfactory.yml scaffolder
- tfactory_migrate.py:   `migrate` subcommand — v0.1 workspace migration
- batch_commands.py:     Batch build execution
- build_commands.py:     Build execution and follow-up tasks
- workspace_commands.py: Workspace management (merge, review, discard)
- qa_commands.py:        QA validation commands
- utils.py:              Shared utilities and configuration

Task 15 / #31 commit 4.
"""

from __future__ import annotations

import click

from .tfactory_init import init_command
from .tfactory_migrate import migrate_command


def _get_legacy_main():  # type: ignore[return]
    """Lazy-import the legacy build CLI main to avoid import errors
    when qa_loop / deleted modules are not installed.
    """
    from .main import main  # noqa: PLC0415

    return main


@click.group()
def tfactory_main() -> None:
    """TFactory CLI — scaffold and migrate TFactory configurations."""


tfactory_main.add_command(init_command, name="init")
tfactory_main.add_command(migrate_command, name="migrate")


def main():  # type: ignore[return]
    """Legacy build CLI — thin shim that defers the import."""
    return _get_legacy_main()()


__all__ = [
    "main",  # legacy build CLI (lazy-import)
    "tfactory_main",  # click group: init + migrate
]
