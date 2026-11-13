"""A dropped connection during persist is retried, and nothing else is (#5338).

Slice 1 left every domain committed, so a replay costs the unfinished work
rather than the run. The classifier is the part that has to be narrow: retrying
a constraint violation or a bug in an ``update_fn`` only moves the failure to a
second stack trace, and it makes a broken run look transient.
"""

from __future__ import annotations

import pytest

from repowise.cli.commands.init_cmd.persistence import _is_connection_loss


class ConnectionDoesNotExistError(Exception):
    pass


class ConnectionFailureError(Exception):
    pass


class AdminShutdownError(Exception):
    pass


class InterfaceError(Exception):
    pass


class IntegrityError(Exception):
    pass


class DataError(Exception):
    pass


class OperationalError(Exception):
    def __init__(self, message: str, orig: BaseException | None = None):
        super().__init__(message)
        self.orig = orig


@pytest.mark.parametrize(
    "exc",
    [
        ConnectionDoesNotExistError("server closed the connection"),
        ConnectionFailureError("connection reset"),
        AdminShutdownError("terminating connection due to administrator command"),
    ],
)
def test_driver_connection_loss_is_retryable(exc):
    assert _is_connection_loss(exc) is True


def test_wrapped_connection_loss_is_retryable_through_orig():
    # SQLAlchemy raises OperationalError with the driver error on `.orig`; the
    # wrapper alone says nothing about whether the connection survived.
    wrapped = OperationalError(
        "(asyncpg.exceptions.ConnectionDoesNotExistError) connection was closed",
        orig=ConnectionDoesNotExistError("connection was closed"),
    )
    assert _is_connection_loss(wrapped) is True


@pytest.mark.parametrize(
    "exc",
    [
        IntegrityError("duplicate key value violates unique constraint"),
        DataError("value too long for type character varying(64)"),
        RuntimeError("update_fn blew up"),
        ValueError("unexpected shape"),
        OperationalError("syntax error at or near 'SELCT'"),  # a bug, not a drop
        # A bare wrapper says nothing about the connection: PEP 249's
        # InterfaceError is as often a misuse as a drop, so only the
        # driver classes (directly or on `.orig`) count.
        InterfaceError("connection is closed"),
    ],
)
def test_everything_else_is_not_retryable(exc):
    assert _is_connection_loss(exc) is False


def test_a_bare_operational_error_is_not_treated_as_a_drop():
    # The wrapper is in the retryable set only so `.orig` can be reached; on its
    # own it is as likely to be a statement bug as a dropped connection.
    assert _is_connection_loss(OperationalError("some other operational failure")) is False


def test_chained_cause_is_followed():
    try:
        try:
            raise ConnectionDoesNotExistError("connection was closed")
        except ConnectionDoesNotExistError as inner:
            raise RuntimeError("persist failed") from inner
    except RuntimeError as outer:
        assert _is_connection_loss(outer) is True


def test_a_cycle_does_not_hang():
    class Loop(Exception):
        pass

    a = Loop("a")
    b = Loop("b")
    a.__cause__ = b
    b.__cause__ = a
    assert _is_connection_loss(a) is False


def test_an_implicit_context_does_not_make_a_bug_retryable():
    """The bot's catch on #5648: `__context__` is set automatically.

    Python sets `__context__` on any exception raised inside an `except` block.
    A plain bug thrown by an update_fn while a driver error is being handled
    therefore inherits that driver error as its context — and following the
    context makes the bug look like a dropped connection. Only an explicit
    `raise ... from` (``__cause__``) or SQLAlchemy's ``orig`` may be followed.
    """
    try:
        try:
            raise ConnectionDoesNotExistError("connection was closed")
        except ConnectionDoesNotExistError:
            # No `from`: this is an implicit context, not a deliberate chain.
            raise TypeError("update_fn blew up")  # noqa: B904  (the implicit context is the point)
    except TypeError as exc:
        assert isinstance(exc.__context__, ConnectionDoesNotExistError)
        assert exc.__cause__ is None
        assert _is_connection_loss(exc) is False
