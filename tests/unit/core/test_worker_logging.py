"""Tests for worker log quieting (issue #4482).

``quiet_worker_logs`` used to drop everything below ERROR inside spawned parse
workers, so worker-emitted warnings such as ``parser.symbol_bloat`` never
reached the user. It now keeps WARNING-and-above and prints them to stderr.
"""

from __future__ import annotations

import logging

import pytest

from repowise.core import worker_logging


@pytest.fixture()
def restore_structlog():
    import structlog

    previous = dict(structlog.get_config())
    yield
    structlog.configure(**previous)


def test_quiet_worker_logs_keeps_warnings(restore_structlog) -> None:
    import structlog

    worker_logging.quiet_worker_logs()

    log = structlog.get_logger("repowise.test.worker")
    # A warning must survive the filter; info/debug must not.
    log.warning("parser.symbol_bloat", path="a.py", symbol_count=900)
    log.info("parser.progress", done=1)
    log.debug("parser.trace", node="x")

    config = structlog.get_config()
    wrapper_class = config["wrapper_class"]
    assert wrapper_class is structlog.make_filtering_bound_logger(logging.WARNING)


def test_quiet_worker_logs_prints_kept_lines_to_stderr(restore_structlog, capsys) -> None:
    import structlog

    worker_logging.quiet_worker_logs()

    log = structlog.get_logger("repowise.test.worker")
    log.warning("parser.symbol_bloat", path="generated.py", symbol_count=1200)

    captured = capsys.readouterr()
    assert "parser.symbol_bloat" in captured.err
    assert captured.out == ""
