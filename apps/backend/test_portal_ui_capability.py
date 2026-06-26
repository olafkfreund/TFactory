"""The portal-ui capability is registered and its config is well-formed (#553)."""

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def test_portal_ui_framework_registered():
    from framework_registry.loader import load_registry

    reg = load_registry()
    items = reg.values() if hasattr(reg, "values") else reg
    names = {str(getattr(f, "name", f)) for f in items}
    assert "portal-ui" in names


def test_four_portals_configured():
    from portal_testing import config

    assert set(config.PORTALS) == {"pfactory", "aifactory", "tfactory", "cfactory"}
    assert config.PORTALS["cfactory"].oauth2_proxy is True
    assert all(p.url.startswith("https://") for p in config.PORTALS.values())
