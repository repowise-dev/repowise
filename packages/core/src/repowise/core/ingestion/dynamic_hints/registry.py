"""Aggregates every dynamic-hint extractor and runs them concurrently.

Each extractor walks the repo independently looking for language-specific
runtime/dynamic call signals (Django URL routes, Spring annotations,
pytest fixtures, etc.). The walks are I/O-bound and entirely independent,
so they're fanned out across a thread pool — the Python GIL is released
during ``read_text`` / ``os.walk`` syscalls, which is where the time
actually goes.

Combined with the pruned :func:`._walk.iter_glob` that skips
``node_modules`` / ``.venv`` / ``.next`` / ``__pycache__`` etc. at the
point of traversal, the phase that used to stall multi-minute on polyrepos
now completes in seconds on most codebases regardless of language mix.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import structlog

from .alpine import AlpineDynamicHints
from .base import DynamicEdge, DynamicHintExtractor
from .c import CDynamicHints
from .django import DjangoDynamicHints
from .dotnet import DotNetDynamicHints
from .go import GoDynamicHints
from .rust import RustDynamicHints
from .luau import LuauDynamicHints
from .node import NodeDynamicHints
from .php import PhpDynamicHints
from .python_imports import PythonDynamicHints
from .pytest_hints import PytestDynamicHints
from .ruby import RubyDynamicHints
from .scala import ScalaDynamicHints
from .spring import SpringDynamicHints
from .swift import SwiftDynamicHints
from .xaml import XamlDynamicHints

log = structlog.get_logger(__name__)

# Capping thread count prevents thrash on machines with many extractors
# but few cores. 8 is enough for full parallelism across the 13 current
# extractors with headroom for the GIL release windows.
_DEFAULT_MAX_WORKERS = 8


class HintRegistry:
    def __init__(
        self,
        extractors: list[DynamicHintExtractor] | None = None,
        *,
        max_workers: int | None = None,
    ) -> None:
        self._extractors = extractors or [
            DjangoDynamicHints(),
            PytestDynamicHints(),
            PythonDynamicHints(),
            NodeDynamicHints(),
            AlpineDynamicHints(),
            DotNetDynamicHints(),
            XamlDynamicHints(),
            SpringDynamicHints(),
            RubyDynamicHints(),
            PhpDynamicHints(),
            ScalaDynamicHints(),
            SwiftDynamicHints(),
            CDynamicHints(),
            LuauDynamicHints(),
            GoDynamicHints(),
            RustDynamicHints(),
        ]
        self._max_workers = max_workers or min(_DEFAULT_MAX_WORKERS, len(self._extractors))

    def extract_all(self, repo_root: Path) -> list[DynamicEdge]:
        """Run every registered extractor and merge their edges.

        Extractors run in a thread pool — their work is I/O-bound and
        releases the GIL during filesystem reads, so threads are the
        right tool (no per-process startup overhead, no pickling).
        Order of edges in the returned list is not guaranteed.
        """
        edges: list[DynamicEdge] = []
        if not self._extractors:
            return edges

        with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
            futures = {pool.submit(self._run_one, ex, repo_root): ex for ex in self._extractors}
            for future in as_completed(futures):
                ex = futures[future]
                try:
                    got = future.result()
                except Exception as e:
                    log.warning("dynamic_hints_failed", extractor=ex.name, error=str(e))
                    continue
                edges.extend(got)
                log.debug("dynamic_hints", extractor=ex.name, count=len(got))
        return edges

    @staticmethod
    def _run_one(extractor: DynamicHintExtractor, repo_root: Path) -> list[DynamicEdge]:
        return extractor.extract(repo_root)
