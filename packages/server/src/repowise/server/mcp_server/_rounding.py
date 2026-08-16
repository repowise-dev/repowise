"""Numeric hygiene — no raw double ever reaches the agent.

Scores, ratios and graph metrics arrive from SQL and NumPy at full double
precision (``hotspot_score: 0.791581408944753``, ``pagerank:
0.0005258389771750028``). Every digit past the fourth is noise the model pays
tokens to read and cannot act on, and a wall of them makes a response harder to
skim than the same numbers rounded.

Applied as a middleware layer around every registered tool (see
``__init__.py``) rather than at each of the ~20 producing call sites, so a tool
added later cannot regress the contract by forgetting to round.

Two deliberate choices:

* **Significant digits, not decimal places.** A blanket ``round(x, 2)`` would
  flatten ``pagerank`` (~1e-4) to ``0.0`` and destroy the ranking it exists to
  express. Rounding to four significant digits is scale-free: it shortens
  ``0.791581408944753`` to ``0.7916`` and ``0.0005258389771750028`` to
  ``0.0005258``, and leaves an already-rounded value untouched.
* **Non-finite becomes ``None``.** ``NaN``/``Infinity`` are not JSON — Python
  emits them as bare literals a strict parser rejects. ``None`` is the honest
  encoding of "no value" and every consumer already handles null.

The pass is idempotent, so re-rounding a cached payload is harmless.
"""

from __future__ import annotations

import contextlib
import functools
import inspect
import logging
import math
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

#: Significant digits kept on every emitted float.
#:
#: Four is the smallest width that preserves ordering on the values agents rank
#: by: fused ``relevance_score`` neighbours differ in the third significant
#: digit (2.9508 vs 2.9032), and percentile-derived scores cluster in the
#: high 0.9s (0.9956 vs 0.9967 vs 0.9997) where three digits would tie them.
_SIG_DIGITS = 4

#: Ceiling on decimal places, so a denormal cannot produce an absurd literal.
_MAX_DECIMALS = 12


def round_float(value: float, sig: int = _SIG_DIGITS) -> float | None:
    """Round *value* to *sig* significant digits; non-finite becomes ``None``."""
    if math.isnan(value) or math.isinf(value):
        return None
    if value == 0.0:
        return 0.0
    exponent = math.floor(math.log10(abs(value)))
    decimals = min(sig - 1 - exponent, _MAX_DECIMALS)
    if decimals <= 0:
        # Magnitudes at or above 10**sig are already coarser than the target
        # precision; rounding to 0 dp keeps them float-typed without widening.
        return float(round(value, 0))
    return round(value, decimals)


def round_numbers(obj: Any, sig: int = _SIG_DIGITS) -> Any:
    """Recursively round every float in *obj*, in place where possible.

    ``bool`` is a subclass of ``int`` and neither is touched — line numbers,
    counts and flags keep their exact type. Mutating dicts and lists in place
    avoids copying a large payload; the operation is idempotent, so a shared or
    cached structure is safe to pass through more than once.
    """
    if isinstance(obj, float):
        return round_float(obj, sig)
    if isinstance(obj, dict):
        for key, val in obj.items():
            obj[key] = round_numbers(val, sig)
        return obj
    if isinstance(obj, list):
        for i, val in enumerate(obj):
            obj[i] = round_numbers(val, sig)
        return obj
    if isinstance(obj, tuple):
        return tuple(round_numbers(v, sig) for v in obj)
    return obj


def quantize(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap an async MCP tool *fn* so its response carries no raw doubles.

    Signature-preserving, and never fatal: a payload this cannot walk is
    returned untouched rather than failing the call.
    """
    if not inspect.iscoroutinefunction(fn):
        return fn

    tool = getattr(fn, "__name__", "tool")

    @functools.wraps(fn)
    async def _wrapped(*args: Any, **kwargs: Any) -> Any:
        result = await fn(*args, **kwargs)
        try:
            return round_numbers(result)
        except Exception:  # pragma: no cover - defensive; rounding never breaks a tool
            logger.debug("mcp float rounding failed for %s", tool, exc_info=True)
            return result

    # Preserve the original signature so FastMCP builds the correct tool schema.
    with contextlib.suppress(ValueError, TypeError):  # pragma: no cover - exotic callables
        _wrapped.__signature__ = inspect.signature(fn)  # type: ignore[attr-defined]
    return _wrapped
