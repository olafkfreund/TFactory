"""A URL rejection reaches the caller because it is MARKED safe (#1073).

``url_safety`` used to raise plain ``ValueError``, and the two handlers echoed
``str(exc)`` into a 400. That was correct only by coincidence: the messages
happened to be developer-written about the caller's own URL. Nothing enforced
it, and ``except ValueError`` catches any ValueError -- including one raised
from inside a library those functions call, whose text nobody here wrote.

Now the validator raises ``InputRejectedError`` (a ``ValueError`` subclass, so
every existing handler still catches it) and the handlers call ``client_error``,
which returns ``InputRejectedError.client_message`` verbatim and gives every
other exception a correlation id.

The interesting test is the LAST one: a foreign ``ValueError`` reaching the same
handler must now be redacted. That is what turns "safe because we happened to
write the messages" into "safe because only our validator can write this".

One raise site was NOT simply blessed. The resolve failure was
``ValueError(f"cannot resolve host {host!r}: {exc}")`` -- interpolating a
``socket.gaierror``, i.e. third-party text riding out on a repo-owned
exception. That is the shape that made CFactory's ``AdapterError`` leak an
upstream host while looking safe. The inner exception is dropped from the
message and kept on ``__cause__``.

Mutation check: make ``client_error`` return ``str(exc)`` unconditionally and
``test_a_foreign_valueerror_is_redacted`` goes red.
"""

from __future__ import annotations

import logging

import pytest
from server.error_ref import InputRejectedError, client_error
from server.services.url_safety import assert_safe_outbound_url

PRIVATE = "http://10.0.0.5:11434"


def test_a_rejected_scheme_raises_input_rejected() -> None:
    with pytest.raises(InputRejectedError) as caught:
        assert_safe_outbound_url("ftp://example.com")
    assert "unsupported URL scheme" in caught.value.client_message


def test_a_missing_host_raises_input_rejected() -> None:
    with pytest.raises(InputRejectedError):
        assert_safe_outbound_url("http://")


def test_a_private_address_raises_input_rejected() -> None:
    with pytest.raises(InputRejectedError) as caught:
        assert_safe_outbound_url(PRIVATE)
    assert "non-public address" in caught.value.client_message


def test_it_is_still_a_valueerror() -> None:
    """Backward compatibility is the reason this could be done at all.

    Six call sites catch ``ValueError``. If InputRejectedError were not a
    subclass, this change would have silently stopped every one of them from
    catching an SSRF rejection -- turning a 400 into an unhandled 500.
    """
    with pytest.raises(ValueError):
        assert_safe_outbound_url(PRIVATE)


def test_the_resolve_failure_does_not_carry_the_inner_exception() -> None:
    """Third-party text must not ride out on a repo-owned exception.

    The host is the caller's own input and stays. The ``socket.gaierror`` does
    not: it is stdlib wording, and blessing it as InputRejectedError would mark
    text nobody here wrote as safe to hand back.
    """
    bad = "http://no-such-host.invalid"
    with pytest.raises(InputRejectedError) as caught:
        assert_safe_outbound_url(bad)

    message = caught.value.client_message
    assert "no-such-host.invalid" in message, "the caller's own host should stay"
    for fragment in ("Errno", "gaierror", "Name or service not known", "Temporary failure"):
        assert fragment not in message, f"stdlib text leaked into the message: {message!r}"
    # Still recoverable for whoever debugs it.
    assert caught.value.__cause__ is not None, "the inner exception was lost entirely"


def test_a_foreign_valueerror_is_redacted() -> None:
    """The whole point of #1073.

    ``except ValueError`` in the two handlers catches anything a library
    raises, not only our validator. Before this change that text was echoed
    into the 400 verbatim. Now only an InputRejectedError passes through.
    """
    logger = logging.getLogger("tfactory.test.url_rejection")
    internal = "/etc/tfactory/credentials.yaml"

    ours = client_error(logger, "ctx", InputRejectedError("Invalid baseUrl: use http/https"))
    assert ours == "Invalid baseUrl: use http/https"

    theirs = client_error(logger, "disallowed base URL", ValueError(f"boom {internal}"))
    assert internal not in theirs, f"a foreign ValueError was echoed: {theirs!r}"
    assert "reference " in theirs, "no correlation id for the redacted case"
