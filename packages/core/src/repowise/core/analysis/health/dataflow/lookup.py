"""Point lookup: the dataflow analysis for one named function in one file.

The entry point for callers outside the health pass (the MCP server serving
per-symbol facts at request time) that hold verified symbol bounds and live
source and need exactly one function's analysis -- no engine, no cache, no
persistence. Composes :class:`facts.FileDataflow` over the supplied source and
resolves the function by start line, disambiguated by name.

Same silence contract as the rest of the layer: any miss (unsupported
language, parse failure, no function at the given bounds, guard trip,
non-convergence) returns ``None``, never a raise.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .facts import FileDataflow

if TYPE_CHECKING:
    from .analyze import FunctionAnalysis


def function_analysis_at(
    abs_path: str,
    language: str,
    source: bytes,
    start_line: int,
    name: str | None = None,
) -> FunctionAnalysis | None:
    """Analyze the function starting at *start_line* (1-indexed) in *source*.

    *name*, when given, disambiguates same-line definitions and serves as the
    fallback match if no function starts exactly at *start_line*.
    """
    return FileDataflow(abs_path, language, source).analysis_at(start_line, name)
