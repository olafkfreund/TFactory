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
    d = yaml.safe_load(
        (_ROOT / "frameworks" / "portal-ui" / "descriptor.yaml").read_text()
    )
    assert d["name"] == "portal-ui"
    assert d["lanes"] == ["browser"]
    assert d["language"] == "python"


def test_published_spec_carries_the_screencast_not_just_screenshots(tmp_path, monkeypatch):
    """The Evidence tab reads findings/videos/; the recording must land there.

    Regression for #876: the run's Playwright screencast was written to the Job
    pod's ephemeral report dir and never copied out, so the tab showed
    screenshots only and the report's "Screencast:" link always 404'd.
    """
    from portal_testing.visual_inspection_adapter import publish_as_tfactory_spec

    report_dir = tmp_path / "reports" / "tfactory"
    (report_dir / "screenshots").mkdir(parents=True)
    (report_dir / "video").mkdir(parents=True)
    (report_dir / "report.md").write_text(
        "# r\nlogged in: **True**\n| 3 | 1 | 1 | 5 | 0 |\n## Findings\n- None — clean\n## Walkthrough\n",
        encoding="utf-8",
    )
    (report_dir / "screenshots" / "01-landing.png").write_bytes(b"png")
    (report_dir / "video" / "tfactory.webm").write_bytes(b"webm")

    monkeypatch.setenv("TFACTORY_WORKSPACE_ROOT", str(tmp_path / "store"))
    spec_dir = publish_as_tfactory_spec("tfactory", report_dir, "run-1")

    assert (spec_dir / "findings" / "screenshots" / "01-landing.png").is_file()
    assert (spec_dir / "findings" / "videos" / "tfactory.webm").read_bytes() == b"webm"


def test_missing_login_form_is_a_failure_not_an_assumed_session():
    """Regression for #877: a portal that never shows the login must not grade green.

    A fresh Playwright context carries no cookies, so "no Keycloak form" can
    never mean "already authenticated" -- it means the login was never reached.
    Reporting logged_in=True there let a 502'd portal grade as `attention`.
    """
    from playwright.sync_api import TimeoutError as PWTimeout

    from portal_testing import config
    from portal_testing.keycloak_login import ensure_logged_in

    class _Empty:
        def count(self):
            return 0

        def is_visible(self):
            return False

        @property
        def first(self):
            return self

    class _ErrorPage:
        """Stands in for a Cloudflare 502 interstitial: no form, not Keycloak."""

        url = "https://tfactory.freundcloud.org.uk/"

        def locator(self, _sel):
            return _Empty()

        def wait_for_selector(self, *_a, **_k):
            raise PWTimeout("no such element")

        def wait_for_timeout(self, _ms):
            pass

    info = ensure_logged_in(
        _ErrorPage(), config.PORTALS["tfactory"], config.Auth()
    )

    assert info["logged_in"] is False, "a portal that never showed a login graded green"
    assert info["mfa_presented"] is False
    assert any("never reached the Keycloak form" in n for n in info["notes"])
