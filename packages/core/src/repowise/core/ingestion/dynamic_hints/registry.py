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

from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import structlog

from .alpine import AlpineDynamicHints
from .base import DynamicEdge, DynamicHintExtractor
from .c import CDynamicHints
from .cpp import CppDynamicHints
from .django import DjangoDynamicHints
from .dotnet import DotNetDynamicHints
from .go import GoDynamicHints
from .jvm import JvmDynamicHints
from .luau import LuauDynamicHints
from .node import NodeDynamicHints
from .php import PhpDynamicHints
from .pytest_hints import PytestDynamicHints
from .python_imports import PythonDynamicHints
from .ruby import RubyDynamicHints
from .rust import RustDynamicHints
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
            JvmDynamicHints(),
            RubyDynamicHints(),
            PhpDynamicHints(),
            ScalaDynamicHints(),
            SwiftDynamicHints(),
            CDynamicHints(),
            CppDynamicHints(),
            LuauDynamicHints(),
            GoDynamicHints(),
            RustDynamicHints(),
        ]
        self._max_workers = max_workers or min(_DEFAULT_MAX_WORKERS, len(self._extractors))

    def extract_all(
        self,
        repo_root: Path,
        *,
        dotnet_index: object | None = None,
        file_paths: Iterable[str] | None = None,
    ) -> list[DynamicEdge]:
        """Run every registered extractor and merge their edges.

        Extractors run in a thread pool — their work is I/O-bound and
        releases the GIL during filesystem reads, so threads are the
        right tool (no per-process startup overhead, no pickling).
        The returned list is deterministically sorted.

        *dotnet_index* is an optional prebuilt
        :class:`~repowise.core.ingestion.resolvers.dotnet.DotNetProjectIndex`.
        The XAML extractor needs the repo's type map and otherwise rebuilds
        the index from scratch (a full .cs walk + read + scan); passing the
        one the graph resolvers already built skips that duplicate work.

        *file_paths* is the ingestion traverser's repo-relative file list
        (``FileInfo.path`` values). When provided, the shared snapshot is
        built from it directly — no second filesystem walk — and hint
        queries see exactly the indexed file set: gitignored files (built
        ``bin``/``obj`` trees, ``local_settings.py``) and generated files
        (``*.Designer.cs``) can no longer produce edges to files the index
        does not contain. Omitted (tests, standalone use), the registry
        falls back to its own pruned walk.
        """
        edges: list[DynamicEdge] = []
        if not self._extractors:
            return edges

        # One pruned walk shared by every extractor: the fleet issues ~40
        # _rglob queries, and each used to re-walk the tree. Snapshot
        # construction failure falls back to per-extractor live walks.
        snapshot = None
        try:
            from repowise.core.fs_walk import WalkSnapshot

            if file_paths is not None:
                snapshot = WalkSnapshot.from_files(repo_root, file_paths)
            else:
                from ._walk import PRUNED_DIRS

                snapshot = WalkSnapshot(repo_root, prune_dirs=PRUNED_DIRS)
        except Exception as e:  # pragma: no cover - snapshot is best-effort
            log.warning("dynamic_hints_snapshot_failed", error=str(e))
        for ex in self._extractors:
            ex._walk_snapshot = snapshot
            ex._dotnet_index = dotnet_index

        try:
            with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
                futures = {
                    pool.submit(self._run_one, ex, repo_root): ex for ex in self._extractors
                }
                for future in as_completed(futures):
                    ex = futures[future]
                    try:
                        got = future.result()
                    except Exception as e:
                        log.warning("dynamic_hints_failed", extractor=ex.name, error=str(e))
                        continue
                    edges.extend(got)
                    log.debug("dynamic_hints", extractor=ex.name, count=len(got))
        finally:
            for ex in self._extractors:
                ex._walk_snapshot = None
                ex._dotnet_index = None
        # Thread-completion order varies run-to-run; sort so downstream graph
        # construction (and therefore the exported KG) stays deterministic.
        edges.sort(key=lambda e: (e.source, e.target, e.edge_type, e.hint_source, e.weight))
        return edges

    @staticmethod
    def _run_one(extractor: DynamicHintExtractor, repo_root: Path) -> list[DynamicEdge]:
        return extractor.extract(repo_root)
