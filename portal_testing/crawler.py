"""Exercise every menu / dropdown / dialog of a portal and capture evidence.

The crawler is deliberately portal-agnostic: it discovers interactive elements
(nav links, buttons, ``aria-haspopup`` triggers) from the live DOM rather than a
hard-coded map, clicks each, and records a screenshot + any dialog/dropdown that
opens, plus console errors. A per-step record feeds the report. The whole run is
also captured as a screencast (Playwright video on the browser context).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from playwright.sync_api import Page

log = logging.getLogger("crawler")


@dataclass
class Step:
    kind: str  # nav | dropdown | dialog | page
    label: str
    url: str = ""
    screenshot: str = ""
    dialog_opened: bool = False
    console_errors: list[str] = field(default_factory=list)
    note: str = ""


def _slug(text: str) -> str:
    return (
        re.sub(r"[^a-z0-9]+", "-", (text or "step").lower()).strip("-")[:50] or "step"
    )


def _visible_texts(page: Page, selector: str, limit: int = 40) -> list[tuple[str, str]]:
    """Return (text, a stable selector) for visible matches."""
    out: list[tuple[str, str]] = []
    loc = page.locator(selector)
    for i in range(min(loc.count(), limit)):
        el = loc.nth(i)
        try:
            if not el.is_visible():
                continue
            txt = (el.inner_text(timeout=1000) or "").strip().split("\n")[0]
            if txt and len(txt) < 60:
                out.append((txt, f"{selector} >> nth={i}"))
        except Exception:  # noqa: BLE001 - DOM churns; skip flaky nodes
            continue
    return out


class PortalCrawler:
    def __init__(self, page: Page, shots_dir: Path, console_errors: list[str]):
        self.page = page
        self.shots = shots_dir
        self.shots.mkdir(parents=True, exist_ok=True)
        self.console_errors = console_errors
        self.steps: list[Step] = []
        self._n = 0

    def _shot(self, label: str) -> str:
        self._n += 1
        name = f"{self._n:02d}-{_slug(label)}.png"
        try:
            self.page.screenshot(path=str(self.shots / name), full_page=True)
        except Exception:  # noqa: BLE001
            self.page.screenshot(path=str(self.shots / name))
        return name

    def _drain_console(self) -> list[str]:
        errs = list(self.console_errors)
        self.console_errors.clear()
        return errs

    def landing(self) -> None:
        self.steps.append(
            Step(
                "page",
                "Landing",
                url=self.page.url,
                screenshot=self._shot("landing"),
                console_errors=self._drain_console(),
            )
        )

    def dismiss_blocking_modals(self) -> None:
        """Close any onboarding/blocking modal that overlays the app after login.

        Some portals greet you with a modal (e.g. PFactory's "Git Repository
        Required") whose backdrop intercepts every click, so the nav crawl would
        time out. Dismiss it via a non-committing control — "Skip"/"Cancel"/
        "Close"/"Not now"/the X — or Escape. Never click a committing action
        (Initialize/Create/Delete/Confirm).
        """
        for _ in range(3):  # a modal may reveal another beneath it
            dialog = self.page.locator(
                "[role='dialog'], .modal, [class*='dialog']"
            ).first
            if not (dialog.count() and dialog.is_visible()):
                return
            label = "modal"
            try:
                label = (
                    (dialog.inner_text(timeout=1000) or "modal")
                    .strip()
                    .split("\n")[0][:40]
                )
            except Exception:  # noqa: BLE001
                pass
            dismissed = False
            for sel in [
                "text=Skip for now",
                "text=Skip",
                "text=Not now",
                "text=Maybe later",
                "text=Cancel",
                "text=Close",
                "[aria-label='Close']",
                "button:has-text('×')",
            ]:
                b = self.page.locator(sel).first
                try:
                    if b.count() and b.is_visible():
                        b.click(timeout=2000)
                        dismissed = True
                        break
                except Exception:  # noqa: BLE001
                    continue
            if not dismissed:
                self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(700)
            self.steps.append(
                Step(
                    "dialog",
                    f"dismissed: {label}",
                    screenshot=self._shot(f"modal-{label}"),
                    dialog_opened=True,
                    note="blocking modal dismissed before crawl",
                )
            )

    def crawl_navigation(self) -> None:
        """Click each top-level nav item, screenshot the resulting view.

        Nav items may be ``<a>``, ``<button>``, or role-tagged rows in a sidebar
        — match them broadly (links, buttons, menuitems, and clickable rows in a
        nav/aside/sidebar container) so single-page-app side menus are covered.
        """
        nav_sel = (
            "nav a, nav button, header a, [role='navigation'] a, [role='navigation'] button, "
            "aside a, aside button, [class*='sidebar'] a, [class*='sidebar'] button, "
            "[role='menuitem'], a[href]"
        )
        # Collect labels up front, then re-locate each FRESH by text/role before
        # clicking. Caching nth-locators breaks on portals that do full-page
        # navigations (e.g. PFactory): the cached node detaches and every later
        # click fails. Re-querying by accessible name is navigation-proof.
        labels: list[str] = []
        seen: set[str] = set()
        for text, _ in _visible_texts(self.page, nav_sel):
            key = text.strip().lower()
            if not key or key in seen or key in {"log out", "logout", "sign out"}:
                continue
            seen.add(key)
            labels.append(text)

        for text in labels:
            loc = self._relocate_nav(text, nav_sel)
            if loc is None:
                self.steps.append(
                    Step("nav", text, note="not found on re-query (DOM changed)")
                )
                continue
            try:
                self._robust_click(loc)
                self.page.wait_for_timeout(1300)
                self.steps.append(
                    Step(
                        "nav",
                        text,
                        url=self.page.url,
                        screenshot=self._shot(f"nav-{text}"),
                        console_errors=self._drain_console(),
                    )
                )
            except Exception as exc:  # noqa: BLE001
                self.steps.append(Step("nav", text, note=f"click failed: {exc}"[:120]))

    def _robust_click(self, loc) -> None:
        """Click resiliently on a slow/throttled environment (e.g. a CPU-limited
        k8s Job pod): scroll into view, allow a generous actionability timeout,
        and fall back to a forced click if an animation keeps the node 'unstable'.
        """
        try:
            loc.scroll_into_view_if_needed(timeout=3000)
        except Exception:  # noqa: BLE001
            pass
        try:
            loc.click(timeout=10000)
        except Exception:  # noqa: BLE001 - last resort: bypass actionability checks
            loc.click(timeout=5000, force=True)

    def _relocate_nav(self, text: str, nav_sel: str):
        """Find a nav element by its accessible name, fresh from the live DOM."""
        for role in ("link", "button", "menuitem"):
            try:
                cand = self.page.get_by_role(role, name=text, exact=True)
                if cand.count() and cand.first.is_visible():
                    return cand.first
            except Exception:  # noqa: BLE001
                continue
        try:
            cand = self.page.locator(nav_sel).filter(has_text=text)
            if cand.count() and cand.first.is_visible():
                return cand.first
        except Exception:  # noqa: BLE001
            pass
        return None

    def crawl_dropdowns(self) -> None:
        """Open elements that declare a popup menu and screenshot the open state."""
        trig_sel = "[aria-haspopup='true'], [aria-haspopup='menu'], button[aria-expanded], [role='combobox'], select"
        items = _visible_texts(self.page, trig_sel)
        for text, sel in items[:15]:
            try:
                self.page.locator(sel).first.click(timeout=3000)
                self.page.wait_for_timeout(700)
                opened = (
                    self.page.locator(
                        "[role='menu'], [role='listbox'], .dropdown-menu, [class*='menu']"
                    ).first.count()
                    > 0
                )
                self.steps.append(
                    Step(
                        "dropdown",
                        text or "dropdown",
                        screenshot=self._shot(f"dropdown-{text}"),
                        dialog_opened=opened,
                        console_errors=self._drain_console(),
                    )
                )
                self.page.keyboard.press("Escape")
                self.page.wait_for_timeout(300)
            except Exception as exc:  # noqa: BLE001
                self.steps.append(
                    Step("dropdown", text or "dropdown", note=f"failed: {exc}"[:100])
                )

    def crawl_dialogs(self) -> None:
        """Click buttons that look like they open dialogs and capture the modal."""
        btns = _visible_texts(self.page, "button, [role='button'], a[class*='btn']")
        opener = re.compile(
            r"new|add|create|edit|settings|config|delete|import|connect|invite|profile|key|token",
            re.I,
        )
        for text, sel in btns:
            if not opener.search(text):
                continue
            try:
                self.page.locator(sel).first.click(timeout=3000)
                self.page.wait_for_timeout(900)
                dialog = self.page.locator(
                    "[role='dialog'], .modal, [class*='dialog']"
                ).first
                if dialog.count() and dialog.is_visible():
                    self.steps.append(
                        Step(
                            "dialog",
                            text,
                            screenshot=self._shot(f"dialog-{text}"),
                            dialog_opened=True,
                            console_errors=self._drain_console(),
                        )
                    )
                    # Close it (Escape, then any Cancel/Close) without committing.
                    self.page.keyboard.press("Escape")
                    self.page.wait_for_timeout(300)
                    for c in ["text=Cancel", "text=Close", "[aria-label='Close']"]:
                        b = self.page.locator(c).first
                        if b.count() and b.is_visible():
                            b.click()
                            break
                    self.page.wait_for_timeout(300)
            except Exception as exc:  # noqa: BLE001
                self.steps.append(Step("dialog", text, note=f"failed: {exc}"[:100]))
