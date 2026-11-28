"""Per-file containment for the graph build's resolution passes.

A resolver bug on one file costs that file's edges for one pass, never the
whole index. Every per-file pass resolves a file into a complete result first
and writes edges only from that result, so a raise leaves nothing of the file
half-written. Each failure is logged and recorded on the builder, which reports
the count as a degraded line (:func:`failure_summary`).

``REPOWISE_STRICT_GRAPH=1`` re-raises instead; the test suite sets it so a
resolver bug still fails CI rather than quietly dropping edges.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any, TypeVar

import structlog

log = structlog.get_logger(__name__)

STRICT_ENV = "REPOWISE_STRICT_GRAPH"

#: Failed files a summary line names before it only counts the rest.
_NAMED_IN_SUMMARY = 3

#: ``(pass, path, exception type name)`` per file a pass skipped.
Failure = tuple[str, str, str]

T = TypeVar("T")


def resolve_contained(
    failures: list[Failure], pass_name: str, path: str, fn: Callable[..., T], *args: Any
) -> T | None:
    """``fn(*args)``, or ``None`` after recording the failure when it raises.

    Out of memory is not one file's bug, so it propagates; cancellation is a
    ``BaseException`` and is never caught.
    """
    try:
        return fn(*args)
    except MemoryError:
        raise
    except Exception as exc:
        if os.environ.get(STRICT_ENV, "").strip() in ("1", "true", "yes"):
            raise
        failures.append((pass_name, path, type(exc).__name__))
        log.warning(
            "graph_file_resolution_failed",
            graph_pass=pass_name,
            path=path,
            error_type=type(exc).__name__,
        )
        log.debug("graph_file_resolution_traceback", path=path, exc_info=True)
        return None


def failure_summary(failures: list[Failure]) -> str | None:
    """One degraded line for the files the build skipped, or ``None`` when none were."""
    if not failures:
        return None
    files = list(dict.fromkeys(path for _, path, _ in failures))
    named = ", ".join(
        f"{path} ({graph_pass}: {error})"
        for graph_pass, path, error in failures[:_NAMED_IN_SUMMARY]
    )
    more = len(failures) - _NAMED_IN_SUMMARY
    tail = f" and {more} more" if more > 0 else ""
    return (
        f"Graph build skipped some edges of {len(files)} file(s) after a resolver error: "
        f"{named}{tail}. Run with -v for the tracebacks."
    )
