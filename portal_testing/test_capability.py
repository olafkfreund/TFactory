"""portal-ui capability: config well-formed + descriptor present/valid."""
from pathlib import Path

import yaml

from portal_testing import config

_ROOT = Path(__file__).resolve().parents[1]


def test_four_portals_configured():
    assert set(config.PORTALS) == {"pfactory", "aifactory", "tfactory", "cfactory"}
    assert config.PORTALS["cfactory"].oauth2_proxy is True
    assert all(p.url.startswith("https://") for p in config.PORTALS.values())


def test_descriptor_registered():
    d = yaml.safe_load((_ROOT / "frameworks" / "portal-ui" / "descriptor.yaml").read_text())
    assert d["name"] == "portal-ui"
    assert d["lanes"] == ["browser"]
    assert d["language"] == "python"
