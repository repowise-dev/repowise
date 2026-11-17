"""repowise pipeline — programmatic API for running the indexing pipeline.

Usage::

    import asyncio
    from pathlib import Path
    from repowise.core.pipeline import run_pipeline

    result = asyncio.run(run_pipeline(Path("/path/to/repo"), generate_docs=False))
    print(f"Indexed {result.file_count} files, {result.symbol_count} symbols")

The phase timer is imported eagerly; everything else loads on first use, so a
command that only times its phases (every ``repowise update``, including the
no-change workspace path) does not pay for the whole generation stack.
"""

from __future__ import annotations

import importlib
from typing import Any

from .phase_timing import PhaseTimingRecorder, PhaseTimings, timed

#: Lazily exported name -> (submodule, attribute in it).
_LAZY = {
    "PipelineResult": ("orchestrator", "PipelineResult"),
    "run_generation": ("orchestrator", "run_generation"),
    "run_pipeline": ("orchestrator", "run_pipeline"),
    "sweep_stale_generated_pages": ("persist", "_sweep_stale_generated_pages"),
    "persist_analysis": ("persist", "persist_analysis"),
    "persist_generation": ("persist", "persist_generation"),
    "persist_git": ("persist", "persist_git"),
    "persist_ingestion": ("persist", "persist_ingestion"),
    "persist_pipeline_result": ("persist", "persist_pipeline_result"),
    "tombstone_absent_file_pages": ("persist", "tombstone_absent_file_pages"),
    "LoggingProgressCallback": ("progress", "LoggingProgressCallback"),
    "ProgressCallback": ("progress", "ProgressCallback"),
    "reparse_repo": ("reparse", "reparse_repo"),
    "rehydrate_graph_builder": ("upgrade", "rehydrate_graph_builder"),
}


def __getattr__(name: str) -> Any:
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(f".{target[0]}", __name__), target[1])
    globals()[name] = value
    return value


__all__ = [
    "LoggingProgressCallback",
    "PhaseTimingRecorder",
    "PhaseTimings",
    "PipelineResult",
    "ProgressCallback",
    "persist_analysis",
    "persist_generation",
    "persist_git",
    "persist_ingestion",
    "persist_pipeline_result",
    "rehydrate_graph_builder",
    "reparse_repo",
    "run_generation",
    "run_pipeline",
    "sweep_stale_generated_pages",
    "timed",
    "tombstone_absent_file_pages",
]
