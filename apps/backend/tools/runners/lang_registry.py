"""Per-language, per-lane tool registry — Task 4 (#5).

Lookup table that maps ``(language, lane)`` → the tool the runner should
invoke. Used by per-lane Generators (Tasks 6+) and the Executor (Task 8)
to pick the right tooling per project at plan-time.

At MVP only ``("python", "functional")`` has a real entry (pytest); the
remaining 19 cells are intentionally placeholder or ``None`` — the
generators in phases 2-5 fill them in as those lanes light up.

Single source of truth so the design-plan tooling table doesn't drift
from the code that actually invokes it:

    Lane         | Python              | TypeScript (phase 4)
    -------------+---------------------+---------------------
    functional   | pytest              | vitest
    sast         | semgrep + bandit    | semgrep + eslint-plugin-security
    deps (sast)  | pip-audit           | npm audit
    secrets      | gitleaks            | gitleaks
    dast (p5)    | owasp zap           | owasp zap
    fuzz (p5)    | atheris             | jsfuzz / fast-check
    mutation (p2)| mutmut              | stryker
"""

from __future__ import annotations

from dataclasses import dataclass


class UnsupportedLanguageError(KeyError):
    """Raised when a language has no registry row at all."""


@dataclass(frozen=True)
class ToolSpec:
    """A single (language, lane) entry."""

    binary: str
    description: str
    available_at_mvp: bool = False
    phase: str = "n/a"  # phase 1/2/3/4/5/6 — when this lane lights up


# Lane keys mirror test_plan.enums.Lane but stay as strings so this module
# stays import-cheap (no SDK / dataclass machinery at module-load time).
_LANE_KEYS = ("functional", "sast", "deps", "secrets", "dast", "fuzz", "mutation")


_REGISTRY: dict[str, dict[str, ToolSpec | None]] = {
    # ── Python (MVP target language) ────────────────────────────────────
    "python": {
        "functional": ToolSpec("pytest", "pytest + pytest-cov", True, "1"),
        "sast":       ToolSpec("semgrep", "semgrep + bandit", False, "3"),
        "deps":       ToolSpec("pip-audit", "pip-audit + OSV cross-check", False, "3"),
        "secrets":    ToolSpec("gitleaks", "gitleaks", False, "3"),
        "dast":       ToolSpec("zap-cli", "OWASP ZAP automation", False, "5"),
        "fuzz":       ToolSpec("atheris", "atheris (libFuzzer-Python)", False, "5"),
        "mutation":   ToolSpec("mutmut", "mutmut (default) / cosmic-ray", False, "2"),
    },
    # ── TypeScript / Node (phase 4) ─────────────────────────────────────
    "typescript": {
        "functional": ToolSpec("vitest", "vitest (preferred) / jest", False, "4"),
        "sast":       ToolSpec("semgrep", "semgrep + eslint-plugin-security", False, "4"),
        "deps":       ToolSpec("npm-audit", "npm audit + OSV", False, "4"),
        "secrets":    ToolSpec("gitleaks", "gitleaks", False, "4"),
        "dast":       ToolSpec("zap-cli", "OWASP ZAP automation", False, "5"),
        "fuzz":       ToolSpec("jsfuzz", "jsfuzz / fast-check", False, "5"),
        "mutation":   ToolSpec("stryker", "stryker", False, "4"),
    },
    # ── Go (phase 6+) ───────────────────────────────────────────────────
    "go": {lane: None for lane in _LANE_KEYS},
    # ── Rust (phase 6+) ─────────────────────────────────────────────────
    "rust": {lane: None for lane in _LANE_KEYS},
    # ── Ruby (phase 6+) ─────────────────────────────────────────────────
    "ruby": {lane: None for lane in _LANE_KEYS},
}


def get_tool_for_lane(language: str, lane: str) -> ToolSpec | None:
    """Return the ToolSpec for ``(language, lane)`` or None if unimpl.

    Raises:
        UnsupportedLanguageError: if ``language`` isn't in the registry
            at all (e.g. C++, Elixir).
    """
    lang = language.lower()
    if lang not in _REGISTRY:
        raise UnsupportedLanguageError(
            f"language {language!r} not in tfactory registry; "
            f"supported: {sorted(_REGISTRY)}"
        )
    return _REGISTRY[lang].get(lane)


def languages_supporting_lane(lane: str, *, mvp_only: bool = False) -> list[str]:
    """Return languages that have a non-None entry for ``lane``.

    When ``mvp_only=True``, also require ``available_at_mvp=True``.
    """
    out = []
    for lang, lanes in _REGISTRY.items():
        spec = lanes.get(lane)
        if spec is None:
            continue
        if mvp_only and not spec.available_at_mvp:
            continue
        out.append(lang)
    return sorted(out)
