"""repowise pipeline — programmatic API for running the indexing pipeline.

Usage::

    import asyncio
    from pathlib import Path
    from repowise.core.pipeline import run_pipeline

    result = asyncio.run(run_pipeline(Path("/path/to/repo"), generate_docs=False))
    print(f"Indexed {result.file_count} files, {result.symbol_count} symbols")
"""

from .orchestrator import PipelineResult, run_generation, run_pipeline
from .persist import (
    _sweep_stale_generated_pages as sweep_stale_generated_pages,
)
from .persist import (
    persist_analysis,
    persist_generation,
    persist_git,
    persist_ingestion,
    persist_pipeline_result,
    tombstone_absent_file_pages,
)
from .phase_timing import PhaseTimingRecorder, PhaseTimings, timed
from .progress import LoggingProgressCallback, ProgressCallback
from .reparse import reparse_repo
from .upgrade import rehydrate_graph_builder

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
