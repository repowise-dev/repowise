"""``go test -json`` output (test2json events): test timings, not a profile.

Each top-level test's ``pass``, ``fail`` or ``skip`` event carries its
``Elapsed`` seconds; it becomes one ``test_timing`` entry with that duration
and its outcome. Subtests (``TestX/case``) are left out: their time is inside
their parent's, and a parent with many cases would count them twice. Events
without a test (a package's own pass or fail, build output) are not read.

A test is linked to its function by the package's directory, from the
checkout's ``go.mod`` files, and the ``_test.go`` file there that declares it
(the locator :mod:`.go_bench` uses for benchmarks). One that cannot be placed
keeps its timing under ``<package>::<name>``. The first test of each package
is marked ``first_in_class``: a package's ``TestMain`` and shared setup run
inside it.

The same events carry a ``go test -json -bench`` run's benchmark results. A
file whose first bytes show a benchmark run is left to :mod:`.go_bench`; one
whose results come later (tests ran first) is handed to it here. Either way
it is read as benchmark results, as before this reader existed, and its tests
are not timed.

The file carries no commit; ``evidence add`` needs ``--commit``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..format_v1 import EVIDENCE_FORMAT, EvidenceFormatError
from . import go_bench

_OUTCOMES = {"pass": "passed", "fail": "failed", "skip": "skipped"}
#: Functions ``go test`` runs as tests.
_DECLARED = re.compile(r"(?m)^func\s+((?:Test|Example|Fuzz)\w*)\s*\(")


#: What a benchmark run prints first (``goos: linux``) or names (``"Test":"BenchmarkX"``).
_BENCHMARK = re.compile(r'"Output"\s*:\s*"goos: |"Test"\s*:\s*"Benchmark')


def sniff(head: bytes) -> bool:
    """test2json events, unless the head already shows a benchmark run (:mod:`.go_bench` reads those)."""
    text = go_bench._text(head).lstrip()
    return (
        bool(go_bench._TEST2JSON.match(text))
        and '"Package"' in text
        and not _BENCHMARK.search(text)
    )


def to_v1(data: bytes, root: Path | None = None) -> dict[str, Any]:
    """The whole output as a v1 ``test_timing`` document, or ``benchmark`` when it holds results."""
    try:
        return go_bench.to_v1(data, root)
    except EvidenceFormatError:
        pass
    locate = go_bench._Locator(root, _DECLARED, lambda _path, _name: True)
    seen: set[str | None] = set()
    tests: list[dict[str, Any]] = []
    for event in _events(go_bench._text(data)):
        outcome = _OUTCOMES.get(event.get("Action"))  # type: ignore[arg-type]
        name = event.get("Test")
        if outcome is None or not isinstance(name, str) or not name or "/" in name:
            continue
        package = event.get("Package") if isinstance(event.get("Package"), str) else None
        tests.append(
            _entry(package, name, outcome, event.get("Elapsed"), locate, package not in seen)
        )
        seen.add(package)
    if not tests:
        raise EvidenceFormatError(
            "the file holds no go test results",
            "save the output of go test -json ./...; a build failure runs no test",
        )
    return {
        "format": EVIDENCE_FORMAT,
        "kind": "test_timing",
        "tool": {"name": "go"},
        "tests": tests,
    }


def _events(text: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            out.append(event)
    return out


def _entry(
    package: str | None, name: str, outcome: str, elapsed: Any, locate: Any, first: bool
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "duration_s": elapsed if isinstance(elapsed, (int, float)) and elapsed >= 0 else None,
        "outcome": outcome,
        "first_in_class": first,
    }
    where = locate(package, name)
    if where is None:
        entry["id"] = f"{package}::{name}" if package else name
    else:
        entry.update(
            id=f"{where[0]}::{name}", path=where[0], function=name, line=where[2], exact=True
        )
    return entry


__all__ = ["sniff", "to_v1"]
