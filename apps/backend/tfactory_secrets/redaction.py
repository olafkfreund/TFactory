"""
Redaction for resolved secret values (epic #62, issue #8).

Two layers:
  - **value-based** (primary): given the exact secret values the broker
    resolved, replace every occurrence in a string with ``***`` — the most
    reliable redaction because we *know* the secrets.
  - **pattern-based** (backstop): reuse ``security.scan_secrets`` regexes to
    catch assignment-shaped secrets in arbitrary text.

A ``RedactingFilter`` can be attached to a logger so resolved values never
reach the logs.
"""

from __future__ import annotations

import logging
import re

_MASK = "***"
_MIN_REDACT_LEN = 4  # never redact trivially short values (avoids nuking output)


class Redactor:
    """Holds known secret values and scrubs them from text."""

    def __init__(self) -> None:
        self._values: set[str] = set()

    def register(self, value: str | None) -> None:
        if value and len(value) >= _MIN_REDACT_LEN:
            self._values.add(value)

    def redact(self, text: str) -> str:
        if not text or not self._values:
            return text
        # Replace longest values first so substrings don't pre-empt longer hits.
        for value in sorted(self._values, key=len, reverse=True):
            if value in text:
                text = text.replace(value, _MASK)
        return text


# Unquoted ``NAME=value`` — the shape logs and captured test output actually
# emit.  ``scrub_patterns`` only matches *quoted* assignments because it was
# written to scan source files, so an env dump slips straight through it.
_ASSIGNMENT_RE = re.compile(
    r"\b([A-Za-z0-9_]*"
    r"(?:SECRET|TOKEN|PASSWORD|PASSWD|APIKEY|API_KEY|API-KEY"
    r"|ACCESS_KEY|ACCESS-KEY|CREDENTIAL|PRIVATE_KEY)"
    r"[A-Za-z0-9_]*)(\s*[:=]\s*)([^\s'\"]{4,})",
    re.IGNORECASE,
)


def scrub_assignments(text: str) -> str:
    """Mask unquoted ``NAME=value`` secrets in free-form text."""
    return _ASSIGNMENT_RE.sub(lambda m: m.group(1) + m.group(2) + _MASK, text)


def scrub_log_text(text: str) -> str:
    """Scrub text bound for a log or an on-disk diagnostic artefact.

    Both layers: the source-shaped patterns *and* the unquoted assignments
    that dominate real command output.
    """
    return scrub_assignments(scrub_patterns(text))


def scrub_patterns(text: str) -> str:
    """Backstop: mask the *value* group of assignment-shaped secrets in text."""
    from security.scan_secrets import ALL_PATTERNS

    for pattern, _name in ALL_PATTERNS:
        try:
            text = re.sub(
                pattern,
                lambda m: (
                    m.group(0).replace(m.group(1), _MASK) if m.groups() else _MASK
                ),
                text,
                flags=re.IGNORECASE,
            )
        except (re.error, IndexError):  # pragma: no cover - defensive
            continue
    return text


class RedactingFilter(logging.Filter):
    """Logging filter that scrubs registered secret values from messages."""

    def __init__(self, redactor: Redactor) -> None:
        super().__init__()
        self._redactor = redactor

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = self._redactor.redact(str(record.getMessage()))
            record.args = ()
        except Exception:  # noqa: BLE001 - never break logging
            pass
        return True


__all__ = ["RedactingFilter", "Redactor", "scrub_patterns"]
