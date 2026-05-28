"""Centralized path helpers for TFactory data directory."""
import shutil
from pathlib import Path

AI_FACTORY_DIR = Path.home() / ".tfactory"


def migrate_legacy_data():
    """Safely migrate legacy TFactory data folder to TFactory."""
    legacy_dir = Path.home() / ".tfactory"
    if legacy_dir.exists() and not AI_FACTORY_DIR.exists():
        try:
            shutil.copytree(legacy_dir, AI_FACTORY_DIR, dirs_exist_ok=True)
            print(f"TFactory - Successfully migrated legacy data from {legacy_dir} to {AI_FACTORY_DIR}")
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
