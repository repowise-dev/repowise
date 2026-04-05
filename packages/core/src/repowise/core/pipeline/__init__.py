"""repowise pipeline — programmatic API for running the indexing pipeline.

Usage::

    import asyncio
    from pathlib import Path
    from repowise.core.pipeline import run_pipeline

    result = asyncio.run(run_pipeline(Path("/path/to/repo"), generate_docs=False))
    print(f"Indexed {result.file_count} files, {result.symbol_count} symbols")
"""

from .orchestrator import PipelineResult, run_generation, run_pipeline
from .persist import persist_pipeline_result
from .progress import LoggingProgressCallback, ProgressCallback

__all__ = [
    "LoggingProgressCallback",
    "PipelineResult",
    "ProgressCallback",
    "persist_pipeline_result",
    "run_generation",
    "run_pipeline",
]
