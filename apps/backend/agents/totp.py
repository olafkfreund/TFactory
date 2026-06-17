"""RFC-6238 TOTP code generation for Class B (bootstrap-once) MFA.

RFC-0007 classifies a username/password + authenticator-app (TOTP) login as
**Class B — bootstrap-once**: a human enrols the authenticator ONCE and the
TOTP *seed* is stored in the encrypted credential vault (kind ``totp``). The
pipeline never asks a human for a code again — it derives the current 6-digit
code from the seed in-process. This is generation, not a bypass: the same math
the user's authenticator app does.

This module is the server-side generator/validator (passlib). The actual login
fill happens browser-side in the Playwright auth-setup (a matching RFC-6238
helper is embedded in ``auth.setup.steps.tmpl.ts``) so the code is generated at
the moment of fill and never expires in flight. This Python path is used to
validate a seed at curation time (does it produce a code?) and for non-browser
(api-lane) TOTP.
"""

from __future__ import annotations

from passlib.totp import TOTP


class TotpError(ValueError):
    """Raised when a TOTP seed is malformed."""


_ALGS = {"sha1", "sha256", "sha512"}


def generate_totp(
    secret: str,
    *,
    at: int | None = None,
    digits: int = 6,
    alg: str = "sha1",
    period: int = 30,
) -> str:
    """Return the current TOTP code for a base32 ``secret`` (RFC-6238).

    Defaults match the common case (SHA-1, 6 digits, 30s — Google Authenticator,
    Authy, 1Password). ``digits``/``alg``/``period`` cover enterprise IdPs that use
    SHA-256/512, 8 digits, or other windows. ``at`` (unix seconds) pins the time
    window for deterministic tests. Raises :class:`TotpError` on a malformed seed
    or unsupported parameters.
    """
    alg = (alg or "sha1").lower()
    if alg not in _ALGS:
        raise TotpError(f"unsupported TOTP alg: {alg!r} (use sha1|sha256|sha512)")
    try:
        totp = TOTP(key=_normalise(secret), digits=digits, period=period, alg=alg)
        token = totp.generate(time=at) if at is not None else totp.generate()
        return token.token
    except TotpError:
        raise
    except Exception as exc:  # noqa: BLE001 - normalise to one error type
        raise TotpError(f"invalid TOTP seed: {type(exc).__name__}") from exc


def is_valid_totp_secret(
    secret: str, *, digits: int = 6, alg: str = "sha1", period: int = 30
) -> bool:
    """True when ``secret`` (+ params) yields a usable TOTP code (curation check)."""
    try:
        generate_totp(secret, at=0, digits=digits, alg=alg, period=period)
        return True
    except TotpError:
        return False


def _normalise(secret: str) -> str:
    """Strip spaces and uppercase a base32 seed (authenticator apps print groups
    of 4 lowercase/uppercase chars with spaces)."""
    return secret.replace(" ", "").strip().upper()
