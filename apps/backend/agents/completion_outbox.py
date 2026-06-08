"""Transactional outbox + retrying relay for completion-event delivery (#281).

TFactory is the **Reflect** stage of the Factory PARR spine. When the Triager
reaches a terminal status it emits an RFC-0001 completion event to CFactory.
The legacy path (``triager._notify_completion``) POSTs the webhook
fire-and-forget: a crash *after* the terminal transition but *before* the POST
succeeds silently loses the event, leaving CFactory's WorkItem stale with no
replay.

This module adds an **at-least-once** delivery guarantee. Because TFactory's
pipeline is file-based (the terminal state change is a ``status.json`` write,
not a DB transaction), the outbox is a durable **directory of JSON entries**:

    $TFACTORY_WORKSPACE_ROOT/outbox/        (default ~/.tfactory/outbox)
        <id>.json          ← pending / retrying entries
        dead/<id>.json     ← dead-lettered after max attempts

Flow
----
1. On terminal status the Triager calls :func:`enqueue` — an **atomic**
   temp-file + ``os.replace`` write. Once that returns, the event is durable
   and survives a crash/restart.
2. A :func:`relay_once` pass (run inline after enqueue, by a background loop in
   the web-server, and/or via the CLI) delivers every *due* entry to the
   webhook with exponential backoff, deletes it on 2xx, and reschedules it on
   failure. After ``max_attempts`` an entry is moved to ``dead/`` so the relay
   doesn't spin forever.

Idempotency: every entry carries a stable ``id`` (the envelope ``id`` when
present — see #282 — else a generated UUID). The relay sends it as the
``Idempotency-Key`` header so the consumer can dedup. This issue changes
**transport only** — no wire-format change.

Non-breaking: the whole path is opt-in behind ``TFACTORY_COMPLETION_OUTBOX``.
With it unset the Triager keeps the legacy direct-POST behaviour unchanged.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

# A deliver function takes the envelope + its idempotency id and returns True
# on success (consumer accepted, 2xx). Anything falsy / raising = retry later.
DeliverFn = Callable[[dict, str], bool]

_ENV_OUTBOX_ENABLED = "TFACTORY_COMPLETION_OUTBOX"
_ENV_BACKOFF_BASE = "TFACTORY_COMPLETION_OUTBOX_BACKOFF_BASE"  # seconds
_ENV_BACKOFF_CAP = "TFACTORY_COMPLETION_OUTBOX_BACKOFF_CAP"  # seconds
_ENV_MAX_ATTEMPTS = "TFACTORY_COMPLETION_OUTBOX_MAX_ATTEMPTS"

_DEFAULT_BACKOFF_BASE = 5.0
_DEFAULT_BACKOFF_CAP = 3600.0
_DEFAULT_MAX_ATTEMPTS = 20


def _truthy(value: str | None) -> bool:
    return bool(value) and value.strip().lower() in ("1", "true", "yes", "on")


def outbox_enabled() -> bool:
    """True when the outbox relay path is opted in (``TFACTORY_COMPLETION_OUTBOX``)."""
    return _truthy(os.environ.get(_ENV_OUTBOX_ENABLED))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _parse_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def outbox_root(outbox_root: Path | str | None = None) -> Path:
    """Resolve the outbox directory.

    Precedence: explicit arg → ``$TFACTORY_WORKSPACE_ROOT/outbox`` →
    ``~/.tfactory/outbox``. The directory is created on demand.
    """
    if outbox_root is not None:
        root = Path(outbox_root)
    else:
        env = os.environ.get("TFACTORY_WORKSPACE_ROOT")
        base = Path(env).expanduser() if env else (Path.home() / ".tfactory")
        root = base / "outbox"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _backoff_base() -> float:
    try:
        return float(os.environ.get(_ENV_BACKOFF_BASE, _DEFAULT_BACKOFF_BASE))
    except ValueError:
        return _DEFAULT_BACKOFF_BASE


def _backoff_cap() -> float:
    try:
        return float(os.environ.get(_ENV_BACKOFF_CAP, _DEFAULT_BACKOFF_CAP))
    except ValueError:
        return _DEFAULT_BACKOFF_CAP


def _max_attempts() -> int:
    try:
        return int(os.environ.get(_ENV_MAX_ATTEMPTS, _DEFAULT_MAX_ATTEMPTS))
    except ValueError:
        return _DEFAULT_MAX_ATTEMPTS


def backoff_seconds(attempts: int) -> float:
    """Exponential backoff for the *next* attempt after ``attempts`` failures.

    attempts=1 → base, attempts=2 → 2·base, … capped at the configured ceiling.
    """
    if attempts <= 0:
        return 0.0
    delay = _backoff_base() * (2 ** (attempts - 1))
    return min(delay, _backoff_cap())


@dataclass
class OutboxEntry:
    """One durable, retrying completion event."""

    id: str
    envelope: dict
    created_at: str
    attempts: int = 0
    next_attempt_at: str | None = None
    last_error: str | None = None

    @property
    def path_name(self) -> str:
        return f"{self.id}.json"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "envelope": self.envelope,
            "created_at": self.created_at,
            "attempts": self.attempts,
            "next_attempt_at": self.next_attempt_at,
            "last_error": self.last_error,
        }

    @classmethod
    def from_dict(cls, data: dict) -> OutboxEntry:
        return cls(
            id=data["id"],
            envelope=data.get("envelope", {}),
            created_at=data.get("created_at") or _iso(_now()),
            attempts=int(data.get("attempts", 0)),
            next_attempt_at=data.get("next_attempt_at"),
            last_error=data.get("last_error"),
        )

    def is_due(self, now: datetime) -> bool:
        if not self.next_attempt_at:
            return True
        try:
            return _parse_iso(self.next_attempt_at) <= now
        except ValueError:
            return True


def _atomic_write(path: Path, data: dict) -> None:
    """Write ``data`` as JSON to ``path`` atomically (temp file + os.replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def enqueue(envelope: dict, *, root: Path | str | None = None) -> str:
    """Durably append a completion event to the outbox; return its id.

    The id is the envelope ``id`` when present (forward-compatible with the
    additive-envelope work, #282), else a generated UUID. The write is atomic,
    so once this returns the event survives a crash and will be replayed.
    """
    root_dir = outbox_root(root)
    entry_id = str(envelope.get("id") or uuid.uuid4().hex)
    now = _now()
    entry = OutboxEntry(
        id=entry_id,
        envelope=envelope,
        created_at=_iso(now),
        attempts=0,
        next_attempt_at=_iso(now),  # due immediately
        last_error=None,
    )
    _atomic_write(root_dir / entry.path_name, entry.to_dict())
    logger.debug("completion_outbox: enqueued %s", entry_id)
    return entry_id


def pending(root: Path | str | None = None) -> list[OutboxEntry]:
    """Return all undelivered entries (excludes dead-lettered), oldest first."""
    root_dir = outbox_root(root)
    entries: list[OutboxEntry] = []
    for path in sorted(root_dir.glob("*.json")):
        try:
            entries.append(OutboxEntry.from_dict(json.loads(path.read_text())))
        except (OSError, json.JSONDecodeError, KeyError):
            continue
    entries.sort(key=lambda e: e.created_at)
    return entries


@dataclass
class RelayStats:
    delivered: int = 0
    failed: int = 0
    dead_lettered: int = 0
    skipped: int = 0  # not yet due

    def as_dict(self) -> dict:
        return {
            "delivered": self.delivered,
            "failed": self.failed,
            "dead_lettered": self.dead_lettered,
            "skipped": self.skipped,
        }


def relay_once(
    deliver: DeliverFn | None = None,
    *,
    root: Path | str | None = None,
    now: datetime | None = None,
) -> RelayStats:
    """Attempt delivery of every *due* outbox entry exactly once.

    On 2xx the entry file is deleted (delivered). On failure ``attempts`` is
    incremented and the entry rescheduled with exponential backoff; after
    ``max_attempts`` it is moved to ``dead/`` so the relay never spins forever.
    Safe to call concurrently-ish and repeatedly (idempotent per entry id).
    """
    root_dir = outbox_root(root)
    deliver = deliver or _default_deliver
    now = now or _now()
    stats = RelayStats()
    max_attempts = _max_attempts()

    for path in sorted(root_dir.glob("*.json")):
        try:
            entry = OutboxEntry.from_dict(json.loads(path.read_text()))
        except (OSError, json.JSONDecodeError, KeyError):
            continue

        if not entry.is_due(now):
            stats.skipped += 1
            continue

        ok = False
        err: str | None = None
        try:
            ok = bool(deliver(entry.envelope, entry.id))
        except Exception as exc:  # noqa: BLE001 — delivery must never raise out
            err = f"{type(exc).__name__}: {str(exc)[:200]}"

        if ok:
            try:
                path.unlink()
            except OSError:
                pass
            stats.delivered += 1
            continue

        entry.attempts += 1
        entry.last_error = err or "delivery returned falsy"
        if entry.attempts >= max_attempts:
            _dead_letter(root_dir, path, entry)
            stats.dead_lettered += 1
            continue

        entry.next_attempt_at = _iso(
            now + timedelta(seconds=backoff_seconds(entry.attempts))
        )
        try:
            _atomic_write(path, entry.to_dict())
        except OSError:
            pass
        stats.failed += 1

    if stats.delivered or stats.failed or stats.dead_lettered:
        logger.info("completion_outbox relay: %s", stats.as_dict())
    return stats


def _dead_letter(root_dir: Path, path: Path, entry: OutboxEntry) -> None:
    """Move an exhausted entry into ``dead/`` for operator inspection."""
    dead_dir = root_dir / "dead"
    dead_dir.mkdir(parents=True, exist_ok=True)
    try:
        _atomic_write(dead_dir / entry.path_name, entry.to_dict())
        path.unlink()
    except OSError:
        pass
    logger.warning(
        "completion_outbox: dead-lettered %s after %d attempts (%s)",
        entry.id,
        entry.attempts,
        entry.last_error,
    )


def _default_deliver(envelope: dict, entry_id: str) -> bool:
    """POST the envelope to ``TFACTORY_COMPLETION_WEBHOOK`` with idempotency.

    Returns True only on a 2xx response. The ``Idempotency-Key`` header lets
    the consumer dedup replays (at-least-once → effectively-once downstream).
    """
    import urllib.request

    url = (os.environ.get("TFACTORY_COMPLETION_WEBHOOK") or "").strip()
    if not url:
        # No sink configured — treat as "cannot deliver yet", keep for replay.
        return False
    timeout = float(os.environ.get("TFACTORY_COMPLETION_WEBHOOK_TIMEOUT", "5"))
    req = urllib.request.Request(
        url,
        data=json.dumps(envelope).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Idempotency-Key": entry_id,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return 200 <= resp.status < 300


def relay_forever(
    *,
    interval_seconds: float = 30.0,
    root: Path | str | None = None,
    stop: Callable[[], bool] | None = None,
) -> None:
    """Drain the outbox on a loop until ``stop()`` returns True.

    Intended for a daemon/background thread (e.g. the web-server startup). Each
    pass is best-effort; exceptions are logged and the loop continues so a
    transient sink outage never kills the relay.
    """
    import time

    stop = stop or (lambda: False)
    while not stop():
        try:
            relay_once(root=root)
        except Exception:  # noqa: BLE001 — never let the relay thread die
            logger.exception("completion_outbox: relay pass failed")
        time.sleep(interval_seconds)


def _main(argv: list[str] | None = None) -> int:
    """CLI: drain the outbox once (or watch). ``python -m agents.completion_outbox``."""
    import argparse

    parser = argparse.ArgumentParser(
        description="TFactory completion-event outbox relay"
    )
    parser.add_argument(
        "--root", default=None, help="Outbox directory (default ~/.tfactory/outbox)"
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Relay continuously instead of a single pass",
    )
    parser.add_argument(
        "--interval", type=float, default=30.0, help="Watch poll interval (seconds)"
    )
    parser.add_argument(
        "--list", action="store_true", help="List pending entries and exit"
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.list:
        for entry in pending(args.root):
            print(
                f"{entry.id}  attempts={entry.attempts}  "
                f"next={entry.next_attempt_at}  err={entry.last_error}"
            )
        return 0

    if args.watch:
        relay_forever(interval_seconds=args.interval, root=args.root)
        return 0

    stats = relay_once(root=args.root)
    print(json.dumps(stats.as_dict()))
    return 0


__all__ = [
    "OutboxEntry",
    "RelayStats",
    "backoff_seconds",
    "enqueue",
    "outbox_enabled",
    "outbox_root",
    "pending",
    "relay_forever",
    "relay_once",
]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
