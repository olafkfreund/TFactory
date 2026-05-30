"""
``localfile`` backend — read a secret from a local file.

This foundation issue handles **plaintext** files (``file:`` scheme): read the
file, optionally selecting a ``#field`` from a simple ``key: value`` / ``key=value``
file. Encrypted formats (``sops:`` / ``age:`` / ``agenix:``) route here too but
their decryption is implemented in the dedicated child issue #64 — until then
they raise ``NotImplementedError``.

Data stays on the local machine, so the egress class is LOCAL.
"""

from __future__ import annotations

from pathlib import Path

from tfactory_secrets import (
    BackendUnavailableError,
    EgressClass,
    SecretNotFoundError,
    SecretRef,
    SecretsBackend,
    SecretValue,
)

_ENCRYPTED_FORMATS = {"sops", "age", "agenix"}


class LocalFileBackend(SecretsBackend):
    name = "localfile"

    def available(self) -> bool:
        return True

    def egress_class(self) -> EgressClass:
        return EgressClass.LOCAL

    def resolve(self, ref: SecretRef) -> SecretValue:
        fmt = ref.extra.get("format", "file")
        if fmt in _ENCRYPTED_FORMATS:
            raise NotImplementedError(
                f"Encrypted local-file format {fmt!r} lands in child issue #64 "
                "(sops/age/agenix backend); use 'file:' for plaintext for now."
            )

        path = Path(ref.locator).expanduser()
        if not path.is_file():
            raise SecretNotFoundError(f"Secret file not found: {path}")
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise BackendUnavailableError(f"Cannot read {path}: {exc}") from exc

        value = _select(raw, ref.field)
        if value is None:
            raise SecretNotFoundError(
                f"Field {ref.field!r} not found in {path}"
            )
        return SecretValue(
            value=value, backend=self.name, ref=ref.raw, source=f"file:{path}"
        )


def _select(raw: str, field: str | None) -> str | None:
    """Return the whole file content (stripped) or a single ``key`` from a
    simple ``key: value`` / ``key=value`` file."""
    if field is None:
        return raw.strip()
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        for sep in (":", "="):
            if sep in line:
                key, _, val = line.partition(sep)
                if key.strip() == field:
                    return val.strip().strip("'\"")
                break
    return None


__all__ = ["LocalFileBackend"]
