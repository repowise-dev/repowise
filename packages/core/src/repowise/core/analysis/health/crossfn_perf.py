"""The performance passes that run after every file is walked and before any is scored."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import structlog

from ...code_origin import CodeOrigin
from .complexity import FileComplexity
from .dataflow import FileDataflowCache
from .perf import (
    CallGraphIndex,
    PerfRanker,
    apply_perf_promotions,
    collect_blocking_io_under_lock,
    collect_centrality_gated,
    collect_crossfn_io_in_loop,
)
from .perf.lazy_load import collect_lazy_loads
from .perf.orm_models import build_model_index
from .perf.table_scale import settle_small_table_loops
from .perf.unbounded_reduction import collect_unbounded_reductions
from .source_reader import SourceReader

log = structlog.get_logger(__name__)


class CrossFunctionPerfStage:
    """Adds the graph- and repo-dependent ``perf_hits`` to the walked files, in place.

    *origins* is the dict the walk fills, read when the stage runs.
    """

    def __init__(
        self,
        graph: Any,
        execution_graph: Callable[[], CallGraphIndex | None],
        origins: dict[str, CodeOrigin],
        parsed_files: list[Any],
        read_source: SourceReader,
    ) -> None:
        self._graph = graph
        self._execution_graph = execution_graph
        self._origins = origins
        self._parsed_files = parsed_files
        self._read_source = read_source

    def run(
        self,
        walked: list[tuple[Any, FileComplexity]],
        dataflow: FileDataflowCache,
        timings: Any | None,
    ) -> None:
        """Every post-walk perf pass, in order: promotions read the crossfn hits."""
        from repowise.core.pipeline.phase_timing import timed

        with timed(timings, "analysis.health.crossfn"):
            self.apply_crossfn(walked)
        with timed(timings, "analysis.health.promotions"):
            apply_perf_promotions(walked, dataflow=dataflow)
        with timed(timings, "analysis.health.unbounded_reduction"):
            collect_unbounded_reductions(walked, read_source=self._read_source)
        with timed(timings, "analysis.health.lazy_load"):
            index = build_model_index(self._parsed_files, self._read_source)
            collect_lazy_loads(walked, self._parsed_files, read_source=self._read_source, index=index)
        with timed(timings, "analysis.health.small_tables"):
            try:
                settle_small_table_loops(walked, index)
            except Exception as exc:  # the loops keep the magnitude the walk gave them
                log.debug("health_small_tables_failed", error=str(exc))

    def apply_crossfn(self, walked: list[tuple[Any, FileComplexity]]) -> None:
        """Append cross-function and centrality-gated perf hits to each file, in place.

        Failure-isolated. Without a graph the centrality-gated pass still runs and emits
        nothing: no marker ships without established centrality.
        """
        try:
            index = self._execution_graph()
            by_file: dict[str, list] = {}
            if self._graph is not None and index is not None:
                for src in (
                    collect_crossfn_io_in_loop(walked, self._graph, index=index),
                    collect_blocking_io_under_lock(walked, self._graph, index=index),
                ):
                    for path, hits in src.items():
                        by_file.setdefault(path, []).extend(hits)
            ranker = PerfRanker(index)
            for path, hits in collect_centrality_gated(walked, ranker, self._origins).items():
                by_file.setdefault(path, []).extend(hits)
            for _pf, fcx in walked:
                extra = by_file.get(_pf.file_info.path)
                if extra:
                    fcx.perf_hits = [*fcx.perf_hits, *extra]
        except Exception as exc:
            log.debug("health_crossfn_perf_failed", error=str(exc))
            return
