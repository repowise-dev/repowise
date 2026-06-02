"""Unit tests for cooperative cancellation (issue #341, Ctrl-C half).

The signal-handler installation is interactive and not exercised here; these
cover the token mechanics, the BaseException contract that keeps broad
``except Exception`` guards from swallowing a cancellation, scope restoration,
and the duplication detector's polling of :func:`check_cancelled`.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from repowise.core.cancellation import (
    CancellationToken,
    PipelineCancelled,
    cancellation_scope,
    check_cancelled,
    get_active_token,
    set_active_token,
)


@pytest.fixture(autouse=True)
def _reset_token():
    """Never let a test leak the global token into the next one."""
    set_active_token(None)
    yield
    set_active_token(None)


def test_check_cancelled_noop_without_token():
    assert get_active_token() is None
    check_cancelled()  # must not raise


def test_check_cancelled_noop_when_not_cancelled():
    token = CancellationToken()
    set_active_token(token)
    check_cancelled()  # armed but not flipped → no-op
    assert token.cancelled is False


def test_check_cancelled_raises_when_cancelled():
    token = CancellationToken()
    token.cancel()
    set_active_token(token)
    with pytest.raises(PipelineCancelled):
        check_cancelled()


def test_pipeline_cancelled_is_base_exception_not_exception():
    """Broad ``except Exception`` must not swallow a cancellation."""
    assert issubclass(PipelineCancelled, BaseException)
    assert not issubclass(PipelineCancelled, Exception)

    token = CancellationToken()
    token.cancel()
    set_active_token(token)
    caught_by_exception = False
    try:
        try:
            check_cancelled()
        except Exception:
            caught_by_exception = True
    except PipelineCancelled:
        pass
    assert caught_by_exception is False


def test_cancellation_scope_arms_and_restores():
    assert get_active_token() is None
    with cancellation_scope() as token:
        assert get_active_token() is token
        assert token.cancelled is False
    # Restored to the prior (absent) token on exit.
    assert get_active_token() is None


def test_cancellation_scope_restores_even_on_error():
    with pytest.raises(ValueError), cancellation_scope():
        raise ValueError("boom")
    assert get_active_token() is None


def test_detect_clones_bails_when_cancelled():
    from repowise.core.analysis.health.duplication.detector import detect_clones

    token = CancellationToken()
    token.cancel()
    set_active_token(token)

    files = [SimpleNamespace(file_info=SimpleNamespace(path="a.py", abs_path="a.py", language="python"), symbols=[])]
    with pytest.raises(PipelineCancelled):
        detect_clones(files)
