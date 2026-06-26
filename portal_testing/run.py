"""Entry point: test one (or all) Factory portal(s) end to end.

    python -m harness.run pfactory
    python -m harness.run all

For each portal: launch a real Chromium (Cloudflare-friendly UA) recording a
screencast video, drive the Keycloak MFA login, crawl every nav item / dropdown
/ dialog capturing screenshots + console errors, and write a Markdown report
under ``reports/<portal>/``.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

from . import config
from .crawler import PortalCrawler
from .keycloak_login import ensure_logged_in
from .report import write_report

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("run")


def _stamp() -> str:
    # Stamp comes from the OS clock at call time (the harness is a CLI tool, not
    # a replayable workflow), via a subprocess-free read.
    import datetime

    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


def run_portal(key: str) -> Path:
    portal = config.PORTALS[key]
    auth = config.Auth()
    out_dir = Path(config.REPORTS_DIR) / key
    shots_dir = out_dir / "screenshots"
    video_dir = out_dir / "video"
    video_dir.mkdir(parents=True, exist_ok=True)
    log.info("[%s] %s", key, portal.url)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=config.HEADLESS, args=["--no-sandbox"])
        context = browser.new_context(
            user_agent=config.USER_AGENT,
            viewport=config.VIEWPORT,
            ignore_https_errors=True,
            record_video_dir=str(video_dir),
            record_video_size=config.VIEWPORT,
        )
        console_errors: list[str] = []
        page = context.new_page()
        page.set_default_timeout(config.NAV_TIMEOUT_MS)
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: console_errors.append(str(e)))

        try:
            page.goto(portal.url, wait_until="domcontentloaded")
            page.wait_for_timeout(2500)
            login_info = ensure_logged_in(page, portal, auth)
            page.wait_for_timeout(1500)

            crawler = PortalCrawler(page, shots_dir, console_errors)
            crawler.landing()
            if login_info.get("logged_in"):
                crawler.dismiss_blocking_modals()
                crawler.crawl_navigation()
                crawler.crawl_dropdowns()
                crawler.crawl_dialogs()
            steps = crawler.steps
        finally:
            video_path = None
            try:
                video_path = page.video.path() if page.video else None
            except Exception:  # noqa: BLE001
                pass
            context.close()  # flush the video
            browser.close()

        video_rel = None
        if video_path and Path(video_path).exists():
            dest = video_dir / f"{key}.webm"
            Path(video_path).rename(dest)
            video_rel = f"video/{dest.name}"

    report = write_report(portal, login_info, steps, out_dir, video_rel, _stamp())
    log.info("[%s] report -> %s (%d steps, login=%s)", key, report, len(steps), login_info.get("logged_in"))
    return report


def main(argv: list[str]) -> int:
    target = argv[1] if len(argv) > 1 else "all"
    keys = list(config.PORTALS) if target == "all" else [target]
    for k in keys:
        if k not in config.PORTALS:
            log.error("unknown portal %r (have: %s)", k, ", ".join(config.PORTALS))
            return 2
        try:
            run_portal(k)
        except Exception:  # noqa: BLE001 - keep going to the next portal
            log.exception("[%s] run failed", k)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
