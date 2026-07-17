"""Centralized path helpers for TFactory data directory."""

import contextlib
import os
import shutil
import tempfile
from pathlib import Path

AI_FACTORY_DIR = Path.home() / ".tfactory"


def migrate_legacy_data():
    """Safely migrate legacy TFactory data folder to TFactory."""
    legacy_dir = Path.home() / ".tfactory"
    if legacy_dir.exists() and not AI_FACTORY_DIR.exists():
        try:
            shutil.copytree(legacy_dir, AI_FACTORY_DIR, dirs_exist_ok=True)
            print(
                f"TFactory - Successfully migrated legacy data from {legacy_dir} to {AI_FACTORY_DIR}"
            )
        except Exception as e:
            print(f"TFactory - Warning: failed to migrate legacy data: {e}")


# Run migration automatically on module load
migrate_legacy_data()


def get_data_dir() -> Path:
    """Return the TFactory data directory, creating it if needed."""
    AI_FACTORY_DIR.mkdir(parents=True, exist_ok=True)
    return AI_FACTORY_DIR


def get_data_file(filename: str) -> Path:
    """Get a file path in the TFactory data directory."""
    return get_data_dir() / filename


def write_secret_file(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` as a 0600 file, atomically and with no
    readable window.

    ``Path.write_text`` creates the file at the umask default (usually 0644) and
    only a *subsequent* ``chmod`` narrows it — so a secret written that way is
    world-readable for the duration of the write. ``tempfile.mkstemp`` opens the
    temp file 0600 from creation regardless of umask.

    The write goes to a temp file in the same directory and is published with
    ``os.replace`` so concurrent readers always see either the old or the new
    complete content, never a truncated or interleaved file (TFactory #688).
    ``os.replace`` also swaps the inode, so a file previously left at 0644 (by
    an older build, or restored from a backup) comes out 0600.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    try:
        try:
            os.write(fd, text.encode("utf-8"))
        finally:
            os.close(fd)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
