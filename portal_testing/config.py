"""Portal + Keycloak configuration for the Factory test harness.

Every value is overridable by env so the same harness runs locally, in CI, and
inside a TFactory browser-lane Job. The defaults point at the live, Cloudflare-
fronted portals behind the Keycloak ``factory`` realm (TOTP MFA).
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Portal:
    key: str
    name: str
    url: str
    # Whether this portal sits behind oauth2-proxy (a Keycloak redirect on first
    # hit) vs its own in-app login button.
    oauth2_proxy: bool = False


PORTALS: dict[str, Portal] = {
    "pfactory": Portal(
        "pfactory",
        "PFactory (Plan)",
        os.environ.get("PFACTORY_URL", "https://pfactory.freundcloud.org.uk"),
    ),
    "aifactory": Portal(
        "aifactory",
        "AIFactory (Code)",
        os.environ.get("AIFACTORY_URL", "https://aifactory.freundcloud.org.uk"),
    ),
    "tfactory": Portal(
        "tfactory",
        "TFactory (Test)",
        os.environ.get("TFACTORY_URL", "https://tfactory.freundcloud.org.uk"),
    ),
    "cfactory": Portal(
        "cfactory",
        "CFactory (Cockpit)",
        os.environ.get("CFACTORY_URL", "https://cfactory.freundcloud.org.uk"),
        oauth2_proxy=True,
    ),
}


@dataclass(frozen=True)
class Auth:
    keycloak_url: str = os.environ.get(
        "KEYCLOAK_URL", "https://keycloak.freundcloud.org.uk"
    )
    realm: str = os.environ.get("KEYCLOAK_REALM", "factory")
    username: str = os.environ.get("TEST_USER", "")
    password: str = os.environ.get("TEST_PASSWORD", "")
    # base32 TOTP secret enrolled for TEST_USER (so the harness can mint codes).
    totp_secret: str = os.environ.get("TEST_TOTP_SECRET", "")


# Cloudflare in front of the portals rejects non-browser UAs; use a real one.
USER_AGENT = os.environ.get(
    "TEST_USER_AGENT",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36",
)

VIEWPORT = {"width": 1600, "height": 1000}
NAV_TIMEOUT_MS = int(os.environ.get("NAV_TIMEOUT_MS", "30000"))
HEADLESS = os.environ.get("HEADLESS", "1") != "0"
REPORTS_DIR = os.environ.get("REPORTS_DIR", "reports")
