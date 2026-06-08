"""Test-acceptance badge SVG (#241, epic #232).

A small, dependency-free shields-style SVG so a repo README (or Backstage
catalog annotation) can show TFactory's verdict at a glance — accept-rate
coloured by the #238/#239 commit-readiness. Pure string rendering; the
web-server route (server/routes/badges.py) reads the workspace and calls
``acceptance_badge``.
"""

from __future__ import annotations

# commit_readiness → shields colour.
_READINESS_COLOR = {
    "high": "#4c1",  # bright green
    "medium": "#dfb317",  # yellow
    "low": "#e05d44",  # red
}
_GREY = "#9f9f9f"


def _esc(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def render_badge_svg(label: str, message: str, color: str) -> str:
    """Render a flat shields-style badge as a self-contained SVG string.

    Widths are approximated at ~7px/char + padding — good enough for a README
    badge without a font-metrics dependency.
    """
    label, message = _esc(label), _esc(message)
    lw = 7 * len(label) + 10
    mw = 7 * len(message) + 10
    total = lw + mw
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{total}" height="20" '
        f'role="img" aria-label="{label}: {message}">'
        f"<title>{label}: {message}</title>"
        f'<linearGradient id="s" x2="0" y2="100%">'
        f'<stop offset="0" stop-color="#bbb" stop-opacity=".1"/>'
        f'<stop offset="1" stop-opacity=".1"/></linearGradient>'
        f'<clipPath id="r"><rect width="{total}" height="20" rx="3" fill="#fff"/></clipPath>'
        f'<g clip-path="url(#r)">'
        f'<rect width="{lw}" height="20" fill="#555"/>'
        f'<rect x="{lw}" width="{mw}" height="20" fill="{color}"/>'
        f'<rect width="{total}" height="20" fill="url(#s)"/></g>'
        f'<g fill="#fff" text-anchor="middle" '
        f'font-family="Verdana,Geneva,DejaVu Sans,sans-serif" font-size="11">'
        f'<text x="{lw / 2:.0f}" y="15" fill="#010101" fill-opacity=".3">{label}</text>'
        f'<text x="{lw / 2:.0f}" y="14">{label}</text>'
        f'<text x="{lw + mw / 2:.0f}" y="15" fill="#010101" fill-opacity=".3">{message}</text>'
        f'<text x="{lw + mw / 2:.0f}" y="14">{message}</text>'
        f"</g></svg>"
    )


def acceptance_badge(facts: dict, *, label: str = "tests") -> str:
    """Build the acceptance badge from a facts dict.

    ``facts`` = the ``build_facts`` output (or any dict with ``accept_rate`` +
    ``commit_readiness``). Message is the accept-rate as a percentage; colour is
    keyed off commit-readiness. Empty/zero runs render grey ``no data``.
    """
    verdicts = facts.get("verdicts_count") or 0
    if not verdicts:
        return render_badge_svg(label, "no data", _GREY)
    rate = facts.get("accept_rate") or 0.0
    readiness = str(facts.get("commit_readiness") or "low").lower()
    color = _READINESS_COLOR.get(readiness, _GREY)
    return render_badge_svg(label, f"{round(rate * 100)}%", color)
