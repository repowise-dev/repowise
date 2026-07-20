"""Instrumentation middleware — measure & record per-tool MCP savings.

:func:`instrument` wraps a registered MCP tool so that every call, after the
tool produces its (already budget-trimmed) response, measures the delivered
token count, derives the counterfactual raw-exploration cost it replaced, and
records a ``mcp:<tool>`` row in the unified savings ledger. It optionally stamps
``_meta.tokens_saved`` / ``_meta.replaced_tokens`` onto the response for
transparency.

Two non-negotiables:

* **Byte-identical output.** The wrapped tool returns exactly what the tool
  returned, save for the optional additive ``_meta`` savings fields. The whole
  savings path is wrapped in a ``try`` that degrades to returning the untouched
  result on any failure.
* **Signature-preserving.** FastMCP introspects each tool's signature to build
  its input schema, so the wrapper copies ``functools.wraps`` metadata *and*
  the original ``__signature__`` — a bare ``*args, **kwargs`` wrapper would
  erase the tool's parameters from the MCP schema.

The counterfactual comes from one of two places, in order of trust:
  1. a value the tool *declared* via :func:`declare_replaced` (it held the exact
     artifact, e.g. ``get_symbol`` with the whole source file);
  2. otherwise the conservative estimator in :mod:`.counterfactual`.
"""

from __future__ import annotations

import contextlib
import functools
import inspect
import json
import logging
from collections.abc import Callable
from typing import Any

from repowise.core.distill.budget import estimate_tokens

from . import counterfactual
from .recorder import record_mcp_dead_end, record_mcp_saving

logger = logging.getLogger(__name__)

#: Coarse, non-identifying result fields worth reporting per tool call. All are
#: enums or booleans (confidence tier, retrieval quality, staleness) — never
#: query text, paths, or repo/symbol names. See the telemetry privacy contract.
_META_FLAGS = ("index_behind", "embedder_degraded")
_RESULT_ENUMS = ("confidence", "retrieval_quality", "grounding")


def _results_count_bucket(result: Any) -> str | None:
    """Bucket a search-shaped result count (never the results themselves)."""
    if not isinstance(result, dict):
        return None
    items = result.get("results")
    if not isinstance(items, list):
        return None
    n = len(items)
    if n == 0:
        return "0"
    if n <= 3:
        return "1-3"
    if n <= 10:
        return "4-10"
    return "10+"


def _telemetry_properties(tool: str, result: Any, duration_ms: int) -> dict[str, Any]:
    """Build the anonymous ``mcp_tool_call`` properties for *result*.

    Only coarse enums / booleans / bucketed counts — no user-identifying data.
    """
    is_error = isinstance(result, dict) and bool(result.get("error"))
    props: dict[str, Any] = {
        "tool": tool,
        "status": "error" if is_error else "ok",
        "duration_ms": duration_ms,
    }
    if isinstance(result, dict):
        for key in _RESULT_ENUMS:
            value = result.get(key)
            if isinstance(value, str) and value:
                props[key] = value
        meta = result.get("_meta")
        if isinstance(meta, dict):
            for key in _META_FLAGS:
                if isinstance(meta.get(key), bool):
                    props[key] = meta[key]
        bucket = _results_count_bucket(result)
        if bucket is not None:
            props["results_bucket"] = bucket
    return props


def _emit_telemetry(tool: str, result: Any, duration_ms: int) -> None:
    """Emit one anonymous ``mcp_tool_call`` event. Best-effort, never raises.

    This is the field-visibility counterpart to the local savings ledger: it
    tells us which tools agents actually reach for, at what confidence, and how
    often results come back stale/degraded — the adoption signal the local
    ledger can't aggregate across installs.
    """
    from repowise.core.platform import telemetry

    telemetry.record_event("mcp_tool_call", _telemetry_properties(tool, result, duration_ms))

#: ``_meta`` key a tool sets to declare its own counterfactual (see
#: :func:`declare_replaced`). The wrapper reads and then leaves it in place as a
#: transparency annotation.
_DECLARED_KEY = "replaced_tokens"


def declare_replaced(result: dict[str, Any], tokens: int) -> None:
    """Let a tool declare an exact counterfactual the estimator can't compute.

    Writes ``result["_meta"]["replaced_tokens"]``; the wrapper prefers this over
    the generic estimator. Used by tools that already hold the replaced artifact
    in memory (e.g. ``get_symbol`` knows the full file it sliced one symbol out
    of). Best-effort and additive — never raises, only mutates ``_meta``.
    """
    if not isinstance(result, dict) or not isinstance(tokens, int) or tokens <= 0:
        return
    meta = result.setdefault("_meta", {})
    if isinstance(meta, dict):
        meta[_DECLARED_KEY] = tokens


def _declared_tokens(result: Any) -> int | None:
    """Return a tool-declared counterfactual from ``_meta``, if present."""
    if not isinstance(result, dict):
        return None
    meta = result.get("_meta")
    if not isinstance(meta, dict):
        return None
    value = meta.get(_DECLARED_KEY)
    return value if isinstance(value, int) and value > 0 else None


def _delivered_tokens(result: Any) -> int:
    """Estimate tokens the agent actually received for *result*."""
    try:
        text = json.dumps(result, default=str)
    except Exception:
        return 0
    return estimate_tokens(text)


def _record(tool: str, result: Any) -> None:
    """Measure, derive the counterfactual, and record — all best-effort."""
    declared = _declared_tokens(result)
    replaced = (
        declared if declared is not None else counterfactual.replaced_tokens_for(tool, result)
    )
    if replaced <= 0:
        # Dead-end debit: an error response delivered tokens and replaced
        # nothing — net negative for the session, and the ledger must say so.
        if isinstance(result, dict) and result.get("error"):
            from repowise.server.mcp_server import _state

            record_mcp_dead_end(
                getattr(_state, "_repo_path", None), tool, _delivered_tokens(result)
            )
        return

    delivered = _delivered_tokens(result)

    # Resolve the repo the MCP server is scoped to. Lazy import keeps this
    # module free of package import-ordering coupling.
    from repowise.server.mcp_server import _state

    repo_root = getattr(_state, "_repo_path", None)
    if record_mcp_saving(repo_root, tool, replaced, delivered) and isinstance(result, dict):
        meta = result.setdefault("_meta", {})
        if isinstance(meta, dict):
            meta["replaced_tokens"] = replaced
            meta["tokens_saved"] = max(0, replaced - delivered)


def instrument(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap an async MCP tool *fn* to record its savings. Signature-preserving.

    Non-coroutine callables are returned unchanged — every OSS MCP tool is
    ``async``, and a sync tool has no measured-response hook here.
    """
    if not inspect.iscoroutinefunction(fn):
        return fn

    tool = getattr(fn, "__name__", "tool")

    @functools.wraps(fn)
    async def _wrapped(*args: Any, **kwargs: Any) -> Any:
        import time

        _t0 = time.perf_counter()
        result = await fn(*args, **kwargs)
        duration_ms = int((time.perf_counter() - _t0) * 1000)
        try:
            _record(tool, result)
        except Exception:  # pragma: no cover - defensive; savings never break a tool
            logger.debug("mcp savings instrumentation failed for %s", tool, exc_info=True)
        try:
            _emit_telemetry(tool, result, duration_ms)
        except Exception:  # pragma: no cover - defensive; telemetry never breaks a tool
            logger.debug("mcp telemetry emit failed for %s", tool, exc_info=True)
        return result

    # Preserve the original signature so FastMCP builds the correct tool schema.
    with contextlib.suppress(ValueError, TypeError):  # pragma: no cover - exotic callables
        _wrapped.__signature__ = inspect.signature(fn)  # type: ignore[attr-defined]
    return _wrapped
