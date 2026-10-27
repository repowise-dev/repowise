"""Unit tests for A13: the stdio server must not log onto its protocol channel.

On stdio, stdout carries JSON-RPC. structlog's default factory prints there,
so every debug line emitted during ``get_answer`` synthesis arrived at the
client as a malformed frame. These tests assert the two sinks now point at
stderr.
"""

from __future__ import annotations

import logging
import sys

import structlog

from repowise.server.mcp_server._stdio_logging import route_logging_to_stderr


def test_a_stdout_stream_handler_is_repointed_at_stderr():
    logger = logging.getLogger("repowise.test.stdio_logging")
    handler = logging.StreamHandler(sys.stdout)
    logger.addHandler(handler)
    try:
        route_logging_to_stderr()
        assert handler.stream is sys.stderr
    finally:
        logger.removeHandler(handler)


def test_a_handler_already_on_stderr_is_left_alone():
    logger = logging.getLogger("repowise.test.stdio_logging_stderr")
    handler = logging.StreamHandler(sys.stderr)
    logger.addHandler(handler)
    try:
        route_logging_to_stderr()
        assert handler.stream is sys.stderr
    finally:
        logger.removeHandler(handler)


def test_structlog_writes_to_stderr_not_stdout(capsys):
    route_logging_to_stderr()
    # A module-level logger bound before the call must still be redirected,
    # which is what cache_logger_on_first_use=False buys.
    structlog.get_logger("repowise.test.stdio").warning("protocol channel check")
    captured = capsys.readouterr()
    assert "protocol channel check" not in captured.out
    assert "protocol channel check" in captured.err


def test_calling_twice_is_idempotent():
    route_logging_to_stderr()
    route_logging_to_stderr()
    factory = structlog.get_config()["logger_factory"]
    assert isinstance(factory, structlog.PrintLoggerFactory)
