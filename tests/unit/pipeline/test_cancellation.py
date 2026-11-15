"""Unit tests for cooperative cancellation (issue #341, Ctrl-C half).

The signal-handler installation is interactive and not exercised here; these
cover the token mechanics, the BaseException contract that keeps broad
``except Exception`` guards from swallowing a cancellation, scope restoration,
and the duplication detector's polling of :func:`check_cancelled`.
"""

from __future__ import annotations

import time
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


def test_in_process_parse_stops_before_the_next_file_when_cancelled():
    from repowise.core.pipeline.parse_pool import PENDING
    from repowise.core.pipeline.phases.ingestion import _parse_in_process

    token = CancellationToken()
    token.cancel()
    set_active_token(token)

    fi = SimpleNamespace(path="a.py", abs_path="a.py")
    results = [PENDING]
    with pytest.raises(PipelineCancelled):
        _parse_in_process([(0, (fi, b"x = 1\n"), "")], results, None)
    assert results == [PENDING]


def _hang(_item: str) -> None:
    while True:
        time.sleep(1)


def test_parse_pool_stops_when_cancelled():
    from repowise.core.pipeline.parse_pool import PENDING, run_with_deadline

    token = CancellationToken()
    token.cancel()
    set_active_token(token)

    # No deadline: without the poll this would wait on the hung task forever.
    results = [PENDING, PENDING]
    with pytest.raises(PipelineCancelled):
        run_with_deadline(_hang, ["a", "b"], 1, results, timeout=None)
    assert results == [PENDING, PENDING]


def test_graph_build_stops_at_the_next_file_when_cancelled(tmp_path):
    from pathlib import Path

    from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder

    (tmp_path / "a.py").write_text("import b\n\n\ndef f():\n    return b.g()\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("def g():\n    return 1\n", encoding="utf-8")
    builder = GraphBuilder(tmp_path)
    parser = ASTParser()
    for fi in FileTraverser(tmp_path).traverse():
        builder.add_file(parser.parse_file(fi, Path(fi.abs_path).read_bytes()))

    token = CancellationToken()
    token.cancel()
    set_active_token(token)
    with pytest.raises(PipelineCancelled):
        builder.build()
