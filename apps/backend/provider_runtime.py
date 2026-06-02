"""Provider runtime version manager (#121).

Detect / update-to-latest / pin the external provider CLIs & SDKs TFactory
drives — Claude (SDK), Codex, GitHub Copilot, Gemini (and its ``antigravity``
binary). Each runtime knows how to report its installed version, where
"latest" comes from, and how to build an install/update command for a chosen
version. Pins persist to ``~/.tfactory/provider-runtimes.json`` so a
known-good version survives a bad upstream release.

Ollama / OpenAI-compatible are user-managed endpoints (detect-only / N/A).

Design: ``install_argv`` is **pure** (it only builds the command); running it
is the caller's explicit choice (never silent). ``detect_installed`` /
``latest_version`` shell out + hit the network best-effort and degrade to
``None`` rather than raising.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

__all__ = [
    "ProviderRuntime",
    "RuntimeStatus",
    "get_all_status",
    "get_runtime",
    "get_status",
    "install_argv",
    "pinned_version",
    "runtimes",
    "set_pin",
]

RuntimeKind = Literal["npm", "pip", "gh", "binary"]

_VERSION_RE = re.compile(r"(\d+\.\d+\.\d+(?:[-.][0-9A-Za-z.]+)?)")


@dataclass(frozen=True)
class ProviderRuntime:
    """How to detect + manage one provider's CLI/SDK."""

    name: str
    kind: RuntimeKind
    binaries: tuple[str, ...]  # candidate binary names, first found wins ("" for pip)
    version_args: tuple[str, ...]  # e.g. ("--version",)
    package: str | None = None  # npm / pip package name
    managed: bool = True  # False → detect-only (e.g. ollama)


# The provider runtimes TFactory cares about. Gemini lists ``antigravity``
# first since that's the post-sunset binary (see providers/gemini_agentic.py).
_REGISTRY: dict[str, ProviderRuntime] = {
    "claude": ProviderRuntime("claude", "pip", (), (), package="claude-agent-sdk"),
    "codex": ProviderRuntime(
        "codex", "npm", ("codex",), ("--version",), package="@openai/codex"
    ),
    "copilot": ProviderRuntime(
        "copilot",
        "gh",
        ("copilot", "github-copilot"),
        ("--version",),
        package="@github/copilot",
    ),
    "gemini": ProviderRuntime(
        "gemini",
        "npm",
        ("antigravity", "gemini"),
        ("--version",),
        package="@google/gemini-cli",
    ),
    "ollama": ProviderRuntime(
        "ollama", "binary", ("ollama",), ("--version",), managed=False
    ),
}


@dataclass(frozen=True)
class RuntimeStatus:
    name: str
    kind: RuntimeKind
    managed: bool
    installed: bool
    installed_version: str | None
    latest_version: str | None
    pinned_version: str | None
    update_available: bool


# ── registry access ─────────────────────────────────────────────────────────


def runtimes() -> list[ProviderRuntime]:
    return list(_REGISTRY.values())


def get_runtime(name: str) -> ProviderRuntime:
    try:
        return _REGISTRY[name]
    except KeyError as exc:
        raise KeyError(
            f"unknown provider runtime {name!r}; known: {sorted(_REGISTRY)}"
        ) from exc


def _parse_version(text: str | None) -> str | None:
    if not text:
        return None
    m = _VERSION_RE.search(text)
    return m.group(1) if m else None


# ── detection ────────────────────────────────────────────────────────────────


def _find_binary(rt: ProviderRuntime) -> str | None:
    for name in rt.binaries:
        path = shutil.which(name)
        if path:
            return path
    return None


def detect_installed(rt: ProviderRuntime) -> str | None:
    """Installed version, or ``None`` if not installed / undetectable."""
    if rt.kind == "pip":
        try:
            from importlib.metadata import version as _pkg_version

            return _parse_version(_pkg_version(rt.package))
        except Exception:  # noqa: BLE001 - not installed / metadata missing
            return None
    binary = _find_binary(rt)
    if binary is None:
        return None
    try:
        proc = subprocess.run(
            [binary, *rt.version_args],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return _parse_version((proc.stdout or "") + (proc.stderr or ""))


def latest_version(rt: ProviderRuntime) -> str | None:
    """Best-effort latest version from the package source (network); ``None``
    on any failure or for sources we don't query (gh / binary)."""
    if not rt.package:
        return None
    if rt.kind == "npm":
        npm = shutil.which("npm")
        if not npm:
            return None
        try:
            proc = subprocess.run(
                [npm, "view", rt.package, "version"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            return _parse_version(proc.stdout.strip()) if proc.returncode == 0 else None
        except (OSError, subprocess.SubprocessError):
            return None
    if rt.kind == "pip":
        try:
            import urllib.request

            url = f"https://pypi.org/pypi/{rt.package}/json"
            with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310
                data = json.loads(resp.read().decode("utf-8"))
            return _parse_version(data.get("info", {}).get("version"))
        except Exception:  # noqa: BLE001 - network/parse best-effort
            return None
    return None


# ── pins (persisted choice) ──────────────────────────────────────────────────


def _pins_path() -> Path:
    # Computed lazily so tests can monkeypatch Path.home().
    return Path.home() / ".tfactory" / "provider-runtimes.json"


def _read_pins() -> dict[str, str]:
    path = _pins_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    return (
        {k: v for k, v in data.items() if isinstance(v, str)}
        if isinstance(data, dict)
        else {}
    )


def pinned_version(name: str) -> str | None:
    return _read_pins().get(name)


def set_pin(name: str, version: str | None) -> None:
    """Pin ``name`` to ``version`` (rollback target), or clear with ``None``."""
    get_runtime(name)  # validate
    pins = _read_pins()
    if version is None:
        pins.pop(name, None)
    else:
        pins[name] = version
    path = _pins_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(pins, indent=2, sort_keys=True))


# ── status ───────────────────────────────────────────────────────────────────


def _semver_lt(a: str, b: str) -> bool:
    """True if version ``a`` is older than ``b`` (numeric-tuple compare)."""

    def parts(v: str) -> list[int]:
        out = []
        for chunk in v.split("."):
            num = re.match(r"\d+", chunk)
            out.append(int(num.group()) if num else 0)
        return out

    pa, pb = parts(a), parts(b)
    n = max(len(pa), len(pb))
    pa += [0] * (n - len(pa))
    pb += [0] * (n - len(pb))
    return pa < pb


def get_status(name: str, *, check_latest: bool = True) -> RuntimeStatus:
    rt = get_runtime(name)
    installed = detect_installed(rt)
    latest = latest_version(rt) if (check_latest and rt.managed) else None
    pinned = pinned_version(name)
    update_available = bool(installed and latest and _semver_lt(installed, latest))
    return RuntimeStatus(
        name=rt.name,
        kind=rt.kind,
        managed=rt.managed,
        installed=installed is not None,
        installed_version=installed,
        latest_version=latest,
        pinned_version=pinned,
        update_available=update_available,
    )


def get_all_status(*, check_latest: bool = True) -> list[RuntimeStatus]:
    return [get_status(name, check_latest=check_latest) for name in _REGISTRY]


# ── install / update command (pure builder) ──────────────────────────────────


def install_argv(rt: ProviderRuntime, version: str | None = None) -> list[str]:
    """Build the install/update command for ``rt`` at ``version`` (or latest).

    Pure — returns the argv; the caller decides whether to run it. Raises
    ``ValueError`` for runtimes TFactory can't install (binary / unmanaged).
    """
    if not rt.managed:
        raise ValueError(f"{rt.name} is user-managed; TFactory does not install it")
    if rt.kind == "npm":
        spec = f"{rt.package}@{version or 'latest'}"
        return ["npm", "install", "-g", spec]
    if rt.kind == "pip":
        spec = f"{rt.package}=={version}" if version else rt.package
        cmd = [sys.executable, "-m", "pip", "install", "--upgrade", spec]
        return cmd
    if rt.kind == "gh":
        # Copilot is upgraded in place; no version pin via this path.
        return ["copilot", "upgrade"]
    raise ValueError(f"cannot build an install command for kind {rt.kind!r}")
