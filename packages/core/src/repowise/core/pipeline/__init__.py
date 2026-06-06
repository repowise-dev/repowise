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
)
from .phase_timing import PhaseTimingRecorder
from .progress import LoggingProgressCallback, ProgressCallback
from .upgrade import rehydrate_graph_builder

__all__ = [
    "LoggingProgressCallback",
    "PhaseTimingRecorder",
    "PipelineResult",
    "ProgressCallback",
    "persist_analysis",
    "persist_generation",
    "persist_git",
    "persist_ingestion",
    "persist_pipeline_result",
    "rehydrate_graph_builder",
    "run_generation",
    "run_pipeline",
    "sweep_stale_generated_pages",
]
