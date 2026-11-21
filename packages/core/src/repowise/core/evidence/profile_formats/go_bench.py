"""``go test -bench -benchmem`` output: benchmark results, not a profile.

Read as ``go test`` prints it (redirected to a file), or as ``go test -json``
(test2json events, whose ``Output`` pieces are joined per package first).
Each result line is ``BenchmarkName-P  N  t ns/op  b B/op  a allocs/op``:
the series is the package import path and the benchmark's name. Go
leaves ``-P`` (GOMAXPROCS) off when it is 1, and a sub-benchmark's own name
may end in ``-64``, so the suffix is dropped only when every result of the
package ends in the same one and something shows it is GOMAXPROCS: a
top-level result (a Go identifier holds no ``-``), or the ``=== RUN`` line
``-v`` and ``-json`` print without it. Otherwise (``-cpu 1,4``, or only
sub-benchmarks with no ``=== RUN`` lines) names stay whole. Repeats (``-count=N``) fold into one value with
``rounds``: the time statistics over the repeats, and the fewest
allocations and bytes, since a stray allocation (a pool refill, a
background sweep) only ever adds.

Time per operation is not exact. Allocations and bytes per operation are:
the same code allocates the same, which is what lets a check gate on them.

A benchmark is linked to its function by the package's directory, from the
checkout's ``go.mod`` files (:func:`~repowise.core.ingestion.resolvers.go.read_go_modules`),
and the ``_test.go`` file there that declares it
(:func:`~repowise.core.code_origin.is_benchmark_function`). One that cannot
be placed keeps its series without a function. Machine lines (``goos``,
``cpu``) and custom metrics (``MB/s``, ``b.ReportMetric``) are not read.
"""

from __future__ import annotations

import json
import re
import statistics
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from repowise.core.code_origin import is_benchmark_function

from ..format_v1 import EVIDENCE_FORMAT, EvidenceFormatError

_NUMBERS = r"(\d+)\s+(\d+(?:\.\d+)?(?:e[+-]?\d+)?) ns/op\b(.*)$"
_RESULT = re.compile(r"^(Benchmark\S*)\s+" + _NUMBERS)
# What a benchmark prints while it runs lands between its name and its numbers.
_NAME_ONLY = re.compile(r"^(Benchmark\S*)(?:\s|$)")
_NUMBERS_ONLY = re.compile(r"^" + _NUMBERS)
_PROCS = re.compile(r"^(.*)-(\d+)$")
_TEST2JSON = re.compile(r'^\{\s*(?:"Time"\s*:\s*"[^"]*"\s*,\s*)?"Action"\s*:')
_FAILED = re.compile(r"^\s*--- FAIL: (Benchmark\S*)")
_RAN = re.compile(r"^=== RUN\s+(Benchmark\S*)")
_DECLARED = re.compile(r"(?m)^func\s+(Benchmark\w*)\s*\(")


def _text(data: bytes) -> str:
    """UTF-8, or the UTF-16 PowerShell 5's ``>`` writes."""
    from repowise.core.analysis.health.coverage.contexts import decode_report

    return decode_report(data, errors="replace").removeprefix("\ufeff")


def sniff(head: bytes) -> bool:
    text = _text(head).lstrip()
    if _TEST2JSON.match(text):
        return '"Package"' in text
    # The header opens the output; a result line anywhere near the top also counts
    # (tests that ran first may print before it).
    return text.startswith("goos: ") or any(
        _RESULT.match(line.strip()) for line in text.splitlines()[:50]
    )


#: One result line: package, name as printed, iterations, ns/op, B/op and allocs/op.
_Result = tuple[str | None, str, int, float, dict[str, float]]


def to_v1(data: bytes, root: Path | None = None) -> dict[str, Any]:
    """The whole output as a v1 ``benchmark`` document; *root* is the checkout the run used."""
    text = _text(data)
    packages = _test2json(text) if _TEST2JSON.match(text.lstrip()) else [(None, text)]
    found, failed, ran = _results(packages)
    if not found:
        raise EvidenceFormatError(
            "the file holds no go test benchmark results"
            + (" (a benchmark failed)" if failed else ""),
            "save the output of go test -run '^$' -bench . -benchmem; "
            "a build or test failure prints no results",
        )
    locate = _Locator(root)
    out: dict[str, Any] = {
        "format": EVIDENCE_FORMAT,
        "kind": "benchmark",
        "tool": {"name": "go"},
        "benchmarks": [
            _entry(pkg, name, runs, locate) for (pkg, name), runs in _series(found, ran).items()
        ],
    }
    if failed:
        out["skipped"] = {"failed": failed}
    return out


def _results(
    packages: list[tuple[str | None, str]],
) -> tuple[list[_Result], int, set[tuple[str | None, str]]]:
    """Every result line of every package's output, the failed benchmarks counted
    (top-level only: a failed sub-benchmark fails its parent too), and the
    ``(package, name)`` pairs ``=== RUN`` names."""
    found: list[_Result] = []
    failed = 0
    ran: set[tuple[str | None, str]] = set()
    for package, output in packages:
        pkg = package
        for line in _joined(output):
            if line.startswith("pkg: "):
                pkg = line[5:].strip() or package
            elif fail := _FAILED.match(line):
                failed += "/" not in fail.group(1)
            elif run := _RAN.match(line):
                ran.add((pkg, run.group(1)))
            elif match := _RESULT.match(line):
                found.append((
                    pkg, match.group(1), int(match.group(2)), float(match.group(3)),
                    _metrics(match.group(4)),
                ))
    return found, failed, ran


def _joined(output: str) -> Iterator[str]:
    """*output*'s stripped lines, a result split by printed output joined back.

    A benchmark that prints while it runs (``fmt.Println``) leaves
    ``BenchmarkX-8  <its output>`` and the numbers on a later line.
    """
    pending: str | None = None
    for raw in output.splitlines():
        line = raw.strip()
        if pending is not None and _NUMBERS_ONLY.match(line):
            yield f"{pending} {line}"
            pending = None
            continue
        if not _RESULT.match(line) and (name := _NAME_ONLY.match(line)):
            pending = name.group(1)
        yield line


def _series(
    found: list[_Result], ran: set[tuple[str | None, str]]
) -> dict[tuple[str | None, str], list[tuple[int, float, dict[str, float]]]]:
    """Results by (package, series name), repeats together; a GOMAXPROCS suffix dropped."""
    names: dict[str | None, set[str]] = {}
    for pkg, name, *_ in found:
        names.setdefault(pkg, set()).add(name)
    procs = {pkg: _procs_suffixed(pkg, printed, ran) for pkg, printed in names.items()}
    out: dict[tuple[str | None, str], list[tuple[int, float, dict[str, float]]]] = {}
    for pkg, name, n, ns, metrics in found:
        if procs[pkg]:
            name = name.rsplit("-", 1)[0]
        out.setdefault((pkg, name), []).append((n, ns, metrics))
    return out


def _procs_suffixed(pkg: str | None, printed: set[str], ran: set[tuple[str | None, str]]) -> bool:
    """Whether every name *pkg* printed ends in one ``-P`` known to be GOMAXPROCS."""
    suffixes = {m.group(2) if (m := _PROCS.match(name)) else None for name in printed}
    if None in suffixes or len(suffixes) != 1:
        return False
    return any("/" not in name or (pkg, name.rsplit("-", 1)[0]) in ran for name in printed)


def _test2json(text: str) -> list[tuple[str | None, str]]:
    """Each package's printed output, joined from its ``output`` events in order."""
    by_package: dict[str | None, list[str]] = {}
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("Action") == "output":
            package = event.get("Package") if isinstance(event.get("Package"), str) else None
            by_package.setdefault(package, []).append(str(event.get("Output", "")))
    return [(package, "".join(parts)) for package, parts in by_package.items()]


def _metrics(rest: str) -> dict[str, float]:
    """``B/op`` and ``allocs/op`` from what follows ``ns/op``; other units are not read."""
    words = rest.split()
    out: dict[str, float] = {}
    for value, unit in zip(words[::2], words[1::2], strict=False):
        if unit in ("B/op", "allocs/op"):
            try:
                out[unit] = float(value)
            except ValueError:
                continue
    return out


def _entry(
    pkg: str | None,
    name: str,
    runs: list[tuple[int, float, dict[str, float]]],
    locate: _Locator,
) -> dict[str, Any]:
    times = [ns for _, ns, _ in runs]
    iterations = {n for n, _, _ in runs}

    def fewest(unit: str) -> float | None:
        values = [m[unit] for _, _, m in runs if unit in m]
        return min(values) if values else None

    entry: dict[str, Any] = {
        "name": f"{pkg}.{name}" if pkg else name,
        "unit": "ns",
        "lower_is_better": True,
        "median": statistics.median(times),
        "mean": statistics.fmean(times),
        "min": min(times),
        "max": max(times),
        "stddev": statistics.stdev(times) if len(times) > 1 else None,
        "rounds": len(runs),
        "iterations": next(iter(iterations)) if len(iterations) == 1 else None,
        "allocs_per_op": fewest("allocs/op"),
        "bytes_per_op": fewest("B/op"),
    }
    # The function is the name's first part, without a ``-P`` a series kept
    # (``-cpu 1,4``): a Go identifier holds no ``-``.
    where = locate(pkg, name.split("/", 1)[0].split("-", 1)[0])
    if where is not None:
        entry.update(path=where[0], function=where[1], line=where[2], exact=True)
    return entry


class _Locator:
    """``(path, function, line)`` of a package's benchmark function, read from the checkout."""

    def __init__(self, root: Path | None) -> None:
        self._root = root
        self._modules: tuple[tuple[str, str], ...] | None = None
        self._declared: dict[str, dict[str, tuple[str, int]]] = {}

    def __call__(self, pkg: str | None, function: str) -> tuple[str, str, int] | None:
        if self._root is None or pkg is None:
            return None
        rel_dir = self._package_dir(pkg)
        if rel_dir is None:
            return None
        if rel_dir not in self._declared:
            self._declared[rel_dir] = self._scan(rel_dir)
        found = self._declared[rel_dir].get(function)
        return (found[0], function, found[1]) if found else None

    def _package_dir(self, pkg: str) -> str | None:
        """The repo-relative directory of import path *pkg*: the longest module path wins."""
        from repowise.core.ingestion.resolvers.go import read_go_modules

        if self._modules is None:
            self._modules = read_go_modules(self._root)
        for module_dir, module_path in self._modules:
            if pkg == module_path or pkg.startswith(module_path + "/"):
                suffix = pkg[len(module_path) :].strip("/")
                return "/".join(p for p in (module_dir, suffix) if p)
        return None

    def _scan(self, rel_dir: str) -> dict[str, tuple[str, int]]:
        """Benchmark function -> (file, start line) for the package's ``_test.go`` files."""
        assert self._root is not None
        directory = self._root / rel_dir if rel_dir else self._root
        out: dict[str, tuple[str, int]] = {}
        try:
            files = sorted(p for p in directory.iterdir() if p.name.endswith("_test.go"))
        except OSError:
            return out
        for file in files:
            path = f"{rel_dir}/{file.name}" if rel_dir else file.name
            try:
                source = file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for match in _DECLARED.finditer(source):
                if is_benchmark_function(path, match.group(1)):
                    line = source.count("\n", 0, match.start()) + 1
                    out.setdefault(match.group(1), (path, line))
        return out


__all__ = ["sniff", "to_v1"]
