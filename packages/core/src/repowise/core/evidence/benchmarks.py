"""Benchmarks speak as benchmarks: each result, the function it measures, its numbers.

A benchmark-class profile is no measure of what code costs the product: a run
gives each benchmark about the same time, so a function's share of it tracks
how many benchmarks target the function, not how much it costs. Such profiles
never rank, lead or mint work (``evidence.hotness``); what a benchmark says is
its own result: time per operation and, exact where the tool reports them,
allocations and bytes per operation.

:func:`benchmark_view` folds the newest benchmark run of each workload into
that view, one row per result. :func:`measured_function` names the
production function a benchmark measures, from the functions its body calls
directly. Pure, so a host that keeps evidence elsewhere builds the same rows.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from repowise.core.ingestion.symbol_identity import file_of_symbol, local_symbol_id

from .hotness import newest_runs

#: Words a benchmark's name carries for its runner, not for what it measures.
_RUNNER_WORDS = frozenset({"benchmark", "bench", "test"})
# camelCase, PascalCase, snake_case and a run of capitals (``JSON``) alike.
# Not ``refactoring.naming.split_words``: that keeps ``HTTPStatus`` one word,
# and importing it loads the whole refactoring package.
_WORD = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")


def _words(name: str) -> set[str]:
    # ``BenchmarkGetX``: Go's prefix is glued to the first word.
    if name.startswith("Benchmark"):
        name = name[len("Benchmark") :]
    return {w.lower() for w in _WORD.findall(name)} - _RUNNER_WORDS


def _qualified_words(symbol_id: str) -> set[str]:
    return set().union(*(_words(part) for part in local_symbol_id(symbol_id).split("::")))


def measured_function(benchmark: str | None, callees: Iterable[str]) -> str | None:
    """The production function *benchmark* measures, or ``None`` when unsure.

    *benchmark* is the benchmark function's symbol id, *callees* the
    production functions its body calls directly. A callee measures it when
    every word of the callee's name is in the benchmark's name and covers at
    least half of it (``BenchmarkGetMapFromFormData`` calls
    ``getMapFromFormData``; ``BenchmarkPathCleanLong`` calls ``cleanPath``);
    a one-word callee that covers only part of it must sit in the benchmark's
    own directory or have its class named too (``BenchmarkGetUser`` calling
    another package's ``Get`` names none; ``TestCache::test_get`` calling
    ``Cache::get`` names it);
    the callee whose name covers the most words wins, and a tie is no answer.
    A benchmark that only calls setup and a shared request helper
    (``New``, ``Use``, ``runRequest``) names none.
    Ceiling: a benchmark named for something other than the function it
    drives, or one that reaches it through a helper, names none; reading
    which function its own profile spends the time in needs a profile per
    benchmark.
    """
    if not benchmark:
        return None
    wanted = _qualified_words(benchmark)
    if not wanted:
        return None
    best: list[tuple[int, str]] = []
    home = posixpath.dirname(file_of_symbol(benchmark))
    for callee in dict.fromkeys(callees):
        words = _words(local_symbol_id(callee).split("::")[-1])
        if not words or not words <= wanted or 2 * len(words) < len(wanted):
            continue
        # A one-word name (``Get``, ``New``) from elsewhere is too generic to
        # claim, unless its class is named too (``TestCache::test_get``, ``Cache::get``).
        named = _qualified_words(callee) - words
        if (
            len(words) == 1 < len(wanted)
            and posixpath.dirname(file_of_symbol(callee)) != home
            and not (named and named <= wanted)
        ):
            continue
        best.append((len(words), callee))
    if not best:
        return None
    best.sort(reverse=True)
    if len(best) > 1 and best[0][0] == best[1][0]:
        return None
    return best[0][1]


def _benchmark_name(key: str, symbol_id: str | None) -> str:
    """*key* from the benchmark's own name on (``BenchmarkX/case``), dropping
    the package path a Go key starts with; the whole key when its symbol is
    unknown."""
    if symbol_id:
        name = local_symbol_id(symbol_id)
        # Where the name starts, after the package (``pkg.``) or file (``::``),
        # never a package path that happens to hold the name.
        for at in (m.start() for m in re.finditer(re.escape(name), key)) if name else ():
            if at == 0 or key[at - 1] in ".:":
                return key[at:]
    return key


def _per_op(result: Any) -> dict[str, Any]:
    # Time per operation is the median the tool gave, in its unit.
    return {
        "time": result.median,
        "unit": result.unit,
        "allocs_per_op": result.allocs_per_op,
        "bytes_per_op": result.bytes_per_op,
    }


def benchmark_view(
    runs: Iterable[Any],
    results: Iterable[Any],
    measures: Mapping[str, str],
    *,
    limit: int,
) -> dict[str, Any] | None:
    """Each workload's newest benchmark run and its results, or ``None`` with none.

    *runs* are the repository's ``benchmark`` kind runs (``id``,
    ``workload_name``, ``environment``, ``measured_at``, ``ingested_at``,
    ``commit_sha``); *results* their ``evidence_benchmark_results`` rows (any
    run's: only the newest run of each workload is read); *measures* maps a
    benchmark function's symbol id to the function it measures
    (:func:`measured_function`). Rows that name a function come first, then by
    key; each run lists at most *limit* (0 keeps the runs and the total).
    """
    keep = newest_runs(list(runs))
    by_run: dict[str, list[Any]] = {}
    for result in results:
        if result.run_id in keep:
            by_run.setdefault(result.run_id, []).append(result)
    if not by_run:
        return None
    rows: list[dict[str, Any]] = []
    total = 0
    for run_id in sorted(by_run):
        found = sorted(
            by_run[run_id],
            key=lambda r: (measures.get(r.symbol_id or "") is None, r.benchmark_key),
        )
        total += len(found)
        rows.extend(
            {
                "run_id": run_id,
                "benchmark": _benchmark_name(r.benchmark_key, r.symbol_id),
                "benchmark_symbol_id": r.symbol_id,
                "measures": measures.get(r.symbol_id or ""),
                **_per_op(r),
            }
            for r in found[: max(limit, 0)]
        )
    return {"run_ids": sorted(by_run), "rows": rows, "total": total}


def measured_functions(
    benchmarks: Iterable[str | None], callees: Mapping[str, Sequence[str]]
) -> dict[str, str]:
    """:func:`measured_function` for each of *benchmarks* that names one."""
    out: dict[str, str] = {}
    for benchmark in benchmarks:
        if benchmark and (found := measured_function(benchmark, callees.get(benchmark, ()))):
            out[benchmark] = found
    return out


__all__ = ["benchmark_view", "measured_function", "measured_functions"]
