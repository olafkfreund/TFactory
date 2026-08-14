"""The error_ref seam: a reference to the caller, the detail to the log (#1068).

``server/error_ref.py`` is step 1 of TFactory#1068 — the helper the 42 leaking
``detail=f"...{str(e)}"`` handlers will be converted onto. It lands with its own
tests first, because 21 files are about to be rewritten on top of it and a
seam nobody verified is not a seam.

Ported from AIFactory, which is where this shape was proven (AIFactory#1301).
AIFactory's own test files were NOT portable — both reach through modules that
exist only there (``server.services.gh``, ``server.project_registry``) — so
these are written against this repo.

The two properties that matter, and they pull in opposite directions:

* nothing internal reaches the caller, and
* the detail is still recoverable by support, under the id the caller quotes.

A fix that only satisfies the first is achievable by returning "error" and
deleting the log call, and it would make every production incident unreadable.

Mutation checks:
  * make ``error_reference`` return ``str(exc)`` — the leak tests go red;
  * drop ``exc_info`` or the ``logger.warning`` call — the recoverability test
    goes red;
  * drop ``sanitize_log`` from the log call — the forging test goes red.
"""

from __future__ import annotations

import logging
import re

import pytest
from server.error_ref import InputRejectedError, client_error, error_reference

REF = re.compile(r"^[0-9a-f]{12}$")

# One exception carrying every kind of internal detail these handlers leak.
CREDENTIALS_FILE = "/etc/tfactory/credentials.yaml"
INTERNAL_HOST = "tfactory-db.internal"
INTERNAL_PORT = "5432"
BOOM = ConnectionRefusedError(
    f"[Errno 111] Connection refused to {INTERNAL_HOST}:{INTERNAL_PORT} "
    f"while reading {CREDENTIALS_FILE}"
)
LEAKS = (CREDENTIALS_FILE, INTERNAL_HOST, INTERNAL_PORT, "Errno 111")


@pytest.fixture
def logger() -> logging.Logger:
    return logging.getLogger("tfactory.test.error_ref")


# --------------------------------------------------------------------------
# Nothing internal crosses the boundary
# --------------------------------------------------------------------------


def test_the_returned_message_carries_no_internal_detail(
    logger: logging.Logger,
) -> None:
    message = client_error(logger, "Failed to update the profile", BOOM)
    for fragment in LEAKS:
        assert fragment not in message, (
            f"leaked {fragment!r} to the caller: {message!r}"
        )


def test_the_returned_message_keeps_the_developer_written_context(
    logger: logging.Logger,
) -> None:
    """Redaction must not reduce the caller to a bare 500.

    The context is developer-written and names the OPERATION, not the failure,
    so it is safe and it is the only thing telling the caller what broke.
    """
    message = client_error(logger, "Failed to update the profile", BOOM)
    assert "Failed to update the profile" in message


def test_the_reference_is_twelve_hex_characters(logger: logging.Logger) -> None:
    ref = error_reference(logger, "ctx", BOOM)
    assert REF.match(ref), f"not a 12-char hex id: {ref!r}"


def test_two_failures_get_different_references(logger: logging.Logger) -> None:
    """A shared id would make the log unsearchable exactly when it matters."""
    first = error_reference(logger, "ctx", BOOM)
    second = error_reference(logger, "ctx", BOOM)
    assert first != second


# --------------------------------------------------------------------------
# ...but support can still answer "what happened"
# --------------------------------------------------------------------------


def test_the_detail_is_recoverable_from_the_log_under_that_id(
    logger: logging.Logger, caplog: pytest.LogCaptureFixture
) -> None:
    """The half a naive fix deletes.

    Returning a constant string would pass every leak assertion above and make
    production unreadable. The id the caller quotes must lead to the detail.
    """
    with caplog.at_level(logging.WARNING, logger=logger.name):
        message = client_error(logger, "Failed to update the profile", BOOM)

    ref = message.split("reference ")[1].rstrip(")")
    assert REF.match(ref)
    assert f"[ref={ref}]" in caplog.text, "the id does not appear in the log"
    assert INTERNAL_HOST in caplog.text, "the detail was not logged"
    assert CREDENTIALS_FILE in caplog.text


def test_the_traceback_reaches_the_log(
    logger: logging.Logger, caplog: pytest.LogCaptureFixture
) -> None:
    """exc_info, not just the message: the stack is what makes it diagnosable."""
    try:
        raise BOOM
    except ConnectionRefusedError as exc:
        with caplog.at_level(logging.WARNING, logger=logger.name):
            client_error(logger, "Failed to update the profile", exc)

    assert "Traceback" in caplog.text
    assert "ConnectionRefusedError" in caplog.text


# --------------------------------------------------------------------------
# CWE-209 must not be traded for CWE-117
# --------------------------------------------------------------------------


class _LineCollector(logging.Handler):
    """Collects log FILE LINES, not records.

    Python emits exactly one LogRecord however many newlines the message
    carries, so a handler that counts records cannot see a forged line at all —
    it is the rendered output that gets split. Asserting on records is how a
    sanitizer test passes against a deliberately broken sanitizer.
    """

    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.extend(self.format(record).splitlines())


def _emit(logger: logging.Logger, exc: BaseException | str) -> list[str]:
    """Run ``error_reference`` and return the rendered log FILE LINES."""
    handler = _LineCollector()
    handler.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s"))
    logger.addHandler(handler)
    try:
        error_reference(logger, "ctx", exc)
    finally:
        logger.removeHandler(handler)
    return handler.lines


FORGED = "WARNING:server.audit:profile deleted by admin"


def test_a_newline_in_the_exception_cannot_forge_a_log_line(
    logger: logging.Logger,
) -> None:
    """An exception's text is routinely attacker-supplied (a filename, a URL).

    Logging it raw would trade CWE-209 for CWE-117, so the MESSAGE goes through
    ``sanitize_log`` — escaped, not stripped, so the payload stays readable to
    whoever is reading the log.

    Scoped to a STRING failure deliberately. The exception path has a separate,
    real gap; see the xfail below rather than weakening this assertion.
    """
    lines = _emit(logger, f"bad input\n{FORGED}")

    assert not any(line == FORGED for line in lines), (
        f"a forged log line was emitted: {lines!r}"
    )
    assert any(FORGED in line for line in lines), (
        "the payload must stay readable — escaped, not stripped"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "error_reference passes exc_info for any BaseException, and the logging "
        "module renders str(exc) itself when formatting that -- never through "
        "sanitize_log. So the sanitised message is accompanied by a raw copy, "
        "and an attacker-controlled exception message still forges a log line. "
        "Present in AIFactory's shipped copy too; tracked in AIFactory#1320. "
        "Strict, so this flips to a failure the moment it is fixed and must "
        "then be promoted to a normal assertion."
    ),
)
def test_an_exception_message_cannot_forge_a_log_line_either(
    logger: logging.Logger,
) -> None:
    """The case that actually occurs: ``client_error(logger, ctx, e)``.

    Every caller passes an exception, not a string, so this is the path that
    matters -- and it is the one where ``sanitize_log`` on the message buys
    nothing, because ``exc_info`` emits the unescaped text alongside it. Note
    this needs no live traceback: passing the exception INSTANCE is enough.
    """
    lines = _emit(logger, ValueError(f"bad input\n{FORGED}"))
    assert not any(line == FORGED for line in lines), (
        f"a forged log line was emitted via exc_info: {lines!r}"
    )


# --------------------------------------------------------------------------
# The deliberate exception: a validator's own message
# --------------------------------------------------------------------------


def test_a_rejected_field_keeps_its_own_message(logger: logging.Logger) -> None:
    """Hiding a developer-written 400 behind an id turns it into a support ticket."""
    rejected = InputRejectedError("Invalid baseBranch: must be a plain git ref")
    assert client_error(logger, "unused context", rejected) == (
        "Invalid baseBranch: must be a plain git ref"
    )


def test_a_rejected_field_reads_the_attribute_not_str(logger: logging.Logger) -> None:
    """The provenance distinction, stated as a test.

    ``str(exc)`` renders ``args``, which every exception in the process writes.
    ``client_message`` has exactly one writer. Same characters here; the test
    exists so a refactor to ``str(exc)`` — which would still pass the test
    above — is caught.
    """
    rejected = InputRejectedError("Invalid baseBranch")
    rejected.args = ("SOMETHING ELSE ENTIRELY /etc/tfactory/credentials.yaml",)
    assert client_error(logger, "ctx", rejected) == "Invalid baseBranch"


def test_an_ordinary_valueerror_still_gets_a_reference(
    logger: logging.Logger,
) -> None:
    """InputRejectedError subclasses ValueError; only the subclass is exempt."""
    message = client_error(logger, "Failed to parse", ValueError(CREDENTIALS_FILE))
    assert CREDENTIALS_FILE not in message
    assert "reference " in message
