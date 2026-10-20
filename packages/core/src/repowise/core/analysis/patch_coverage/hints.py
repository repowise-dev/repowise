"""Where to add a test for each uncovered range of a change.

A range with no covering test is a question ("which test should grow?") the
index can answer, so every uncovered range carries a :class:`TestHint`: the
innermost indexed symbol around it and the test files best placed to extend.

Evidence is tried strongest first, and the first that names a test answers:

* ``per_test`` (measured): the per-test coverage map (``coverage add`` on a
  report with contexts) has tests that ran other lines of the same symbol, or
  lines within :data:`WINDOW` of a range outside any symbol. Used only when the
  patch coverage is ``current``: the map's line numbers are ingest-time ones.
* ``call_graph`` (inferred): tests whose calls reach that symbol
  (``tests_reaching_by_tier`` seeded with the symbol id, so a test reaching a
  neighbour in the same file does not count).
* ``import_graph`` (inferred): tests that import the file. File-level.
* ``none``: nothing names a test; the range needs a new one.

Symbol spans are line numbers at the indexed commit, and the ranges are line
numbers in the change. When the two differ, each span is moved through the
diff between them (:func:`translate_spans`); a range in code added since the
index outside every moved span (a new function) has no symbol and falls to
file-level evidence.

The assembly is pure; :func:`read_test_hints` does the batched reads.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from ..test_reachability import ReachedBy
    from .compute import FilePatchCoverage, PatchCoverage

log = logging.getLogger(__name__)

HintBasis = Literal["per_test", "call_graph", "import_graph", "none"]

#: Test files named per range, best first; ``total`` counts the rest.
TEST_LIMIT = 3

#: Lines either side of a range outside any symbol that a per-test hit may use.
WINDOW = 5

#: Uncovered ranges shown per file before "+N more", and hinted per file (the
#: first ones in line order): a hint is never computed for a range no surface
#: lists, and a file with hundreds of gaps stays bounded.
RANGE_LIMIT = 8

#: Seeds per graph walk. Each walk binds its seeds as one ``IN`` list, so a
#: change touching thousands of symbols must not reach SQLite's bound-variable
#: limit. Ceiling: past it, the later symbols and files get no graph evidence
#: (their hints read ``none`` unless per-test coverage answers).
MAX_SEEDS = 200

_REACHED_BASIS: dict[str, HintBasis] = {"call-graph": "call_graph", "import-graph": "import_graph"}


@dataclass(frozen=True)
class SymbolSpan:
    """One indexed symbol's lines."""

    symbol_id: str
    qualified_name: str
    start_line: int
    end_line: int


@dataclass(frozen=True)
class TestHint:
    """Where to extend the tests for one uncovered range."""

    __test__ = False  # not a pytest class, whatever its name says

    range: tuple[int, int]
    #: The innermost indexed symbol containing the range start; ``None`` outside any.
    symbol: str | None
    tests: tuple[str, ...]
    basis: HintBasis
    #: How many test files qualified before :data:`TEST_LIMIT` cut ``tests``.
    total: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "range": list(self.range),
            "symbol": self.symbol,
            "tests": list(self.tests),
            "basis": self.basis,
            "total": self.total,
        }


def innermost_symbol(spans: Iterable[SymbolSpan], line: int) -> SymbolSpan | None:
    """The narrowest span containing *line*: a method over its class."""
    containing = [s for s in spans if s.start_line <= line <= s.end_line]
    return min(
        containing,
        key=lambda s: (s.end_line - s.start_line, -s.start_line, s.symbol_id),
        default=None,
    )


def translate_spans(
    spans: Iterable[SymbolSpan], hunks: Iterable[tuple[int, int, int, int]]
) -> list[SymbolSpan]:
    """*spans* moved from the indexed commit to the change's line numbers.

    A symbol the diff deleted outright (its end lands above its start) is
    dropped.
    """
    from ..changed_lines import map_old_line

    hunks = list(hunks)
    out = []
    for s in spans:
        start = map_old_line(hunks, s.start_line)
        end = map_old_line(hunks, s.end_line, end=True)
        if end >= start:
            out.append(replace(s, start_line=start, end_line=end))
    return out


def hint_ranges(f: FilePatchCoverage) -> tuple[tuple[int, int], ...]:
    """The ranges of *f* a hint is computed for."""
    return f.uncovered_ranges[:RANGE_LIMIT]


def _per_test(rows: Sequence[Mapping[str, Any]], lo: int, hi: int) -> list[str]:
    """Test files whose covered lines fall in ``[lo, hi]``, most lines first."""
    hits: dict[str, int] = {}
    for row in rows:
        # A test id without a file is named by its id's path part, as the
        # workspace test impact reads it.
        test_file = row.get("test_file") or str(row.get("test_id", "")).split("::", 1)[0]
        count = sum(1 for line in row.get("covered_lines") or () if lo <= line <= hi)
        if test_file and count:
            hits[test_file] = max(hits.get(test_file, 0), count)
    return sorted(hits, key=lambda t: (-hits[t], t))


def build_hints(
    f: FilePatchCoverage,
    spans: Sequence[SymbolSpan],
    per_test: Sequence[Mapping[str, Any]],
    reached: Mapping[str, ReachedBy],
) -> tuple[TestHint, ...]:
    """One :class:`TestHint` per hinted range of *f*.

    *spans* are the file's symbols in the change's line numbers, *per_test*
    its per-test coverage rows (``tests_covering_files``; pass none when they
    may describe other code), *reached* the graph walk keyed by symbol id
    (call graph) and by file path (import graph).
    """
    hints = []
    for a, b in hint_ranges(f):
        sym = innermost_symbol(spans, a)
        tests, basis, total = _evidence(f.file_path, sym, (a, b), per_test, reached)
        hints.append(
            TestHint(
                range=(a, b),
                symbol=sym.qualified_name if sym else None,
                tests=tuple(tests[:TEST_LIMIT]),
                basis=basis,
                total=total,
            )
        )
    return tuple(hints)


def _evidence(
    path: str,
    sym: SymbolSpan | None,
    rng: tuple[int, int],
    per_test: Sequence[Mapping[str, Any]],
    reached: Mapping[str, ReachedBy],
) -> tuple[Sequence[str], HintBasis, int]:
    """``(tests, basis, total)`` for one range: per-test first, then the graph."""
    a, b = rng
    lo, hi = (sym.start_line, sym.end_line) if sym else (a - WINDOW, b + WINDOW)
    if tests := _per_test(per_test, lo, hi):
        return tests, "per_test", len(tests)
    for key in ((sym.symbol_id,) if sym else ()) + (path,):
        by = reached.get(key)
        # A tier this module does not know is no evidence at all.
        if by is not None and by.tests and by.via in _REACHED_BASIS:
            return by.tests, _REACHED_BASIS[by.via], by.total
    return (), "none", 0


def attach_hints(pc: PatchCoverage, hints: Mapping[str, tuple[TestHint, ...]]) -> PatchCoverage:
    """*pc* with every measured row's hints set; a row with no gap gets none."""
    files = tuple(
        replace(f, hints=hints.get(f.file_path, ())) if f.status == "measured" else f
        for f in pc.files
    )
    return replace(pc, files=files)


def hint_phrase(hint: TestHint) -> str:
    """How every surface words a hint, measured and inferred kept apart.

    ``"extend tests/test_auth.py (inferred: calls reach `login`)"``; a hint
    naming no test reads ``"no test reaches this; add one"``. The UI's
    ``hintReason`` mirrors this wording.
    """
    if not hint.tests:
        return "no test reaches this; add one"
    where = f"`{hint.symbol}`" if hint.symbol else None
    why = {
        "per_test": (
            f"measured: runs other lines of {where}" if where else "measured: runs nearby lines"
        ),
        "call_graph": f"inferred: calls reach {where or 'this code'}",
        "import_graph": "inferred: imports this file",
    }[hint.basis]
    return f"extend {hint.tests[0]} ({why})"


def first_hint(f: FilePatchCoverage) -> TestHint | None:
    """The file's first hint that names a test, else its first hint, else ``None``."""
    hints = f.hints or ()
    return next((h for h in hints if h.tests), hints[0] if hints else None)


async def read_test_hints(
    session: AsyncSession,
    repository_id: str,
    pc: PatchCoverage,
    *,
    repo_path: str | None = None,
    head_commit: str | None = None,
    working_tree: bool = False,
) -> dict[str, tuple[TestHint, ...]] | None:
    """``{path: hints}`` for every measured file with uncovered ranges; ``None`` on failure.

    Hints are advice, so any failure (an old index, a git error) is logged and
    reads as ``None``, never raised. *repo_path* names the checkout the ranges
    were read from, at *head_commit* or in the *working_tree*, so symbol spans
    stored at another commit can be moved there. Without *repo_path* the spans
    are taken as stored.
    """
    try:
        return await _read_test_hints(
            session, repository_id, pc, repo_path, head_commit, working_tree
        )
    except Exception:
        # Advice never breaks the caller: any failure is logged and reads as no hints.
        log.warning("patch_coverage_hints_failed", exc_info=True)
        return None


async def _read_test_hints(
    session: AsyncSession,
    repository_id: str,
    pc: PatchCoverage,
    repo_path: str | None,
    head_commit: str | None,
    working_tree: bool,
) -> dict[str, tuple[TestHint, ...]]:
    """Batched reads for the whole change: symbols, per-test rows, the reverse test walk."""
    from repowise.core.persistence.crud.analysis.coverage_map import tests_covering_files

    files = [f for f in pc.with_status("measured") if f.uncovered_ranges]
    if not files:
        return {}
    paths = sorted(f.file_path for f in files)
    spans = await _read_spans(session, repository_id, paths)
    if spans and repo_path:
        indexed = await _indexed_commit(session, repository_id)
        spans = await _spans_at_change(repo_path, indexed, head_commit, working_tree, spans)

    # Per-test line numbers are the ingest's; only coverage current for this
    # change vouches that they are the change's too.
    per_test: Mapping[str, list] = {}
    if pc.scope.freshness == "current":
        per_test = await tests_covering_files(session, repository_id, set(paths))

    placed = {
        f.file_path: [innermost_symbol(spans.get(f.file_path, ()), a) for a, _b in hint_ranges(f)]
        for f in files
    }
    reached = await _walk_tests(session, repository_id, placed)
    return {
        f.file_path: build_hints(
            f, spans.get(f.file_path, ()), per_test.get(f.file_path, ()), reached
        )
        for f in files
    }


async def _read_spans(
    session: AsyncSession, repository_id: str, paths: list[str]
) -> dict[str, list[SymbolSpan]]:
    """The stored symbols of *paths*, at the indexed commit's line numbers."""
    from sqlalchemy import select

    from repowise.core.persistence.models import WikiSymbol

    rows = await session.execute(
        select(
            WikiSymbol.file_path,
            WikiSymbol.symbol_id,
            WikiSymbol.qualified_name,
            WikiSymbol.start_line,
            WikiSymbol.end_line,
        ).where(WikiSymbol.repository_id == repository_id, WikiSymbol.file_path.in_(paths))
    )
    spans: dict[str, list[SymbolSpan]] = {}
    for path, symbol_id, name, start, end in rows:
        spans.setdefault(path, []).append(SymbolSpan(symbol_id, name, start, end))
    return spans


async def _indexed_commit(session: AsyncSession, repository_id: str) -> str | None:
    from sqlalchemy import select

    from repowise.core.persistence.models import Repository

    return await session.scalar(
        select(Repository.head_commit).where(Repository.id == repository_id)
    )


async def _walk_tests(
    session: AsyncSession,
    repository_id: str,
    placed: Mapping[str, list[SymbolSpan | None]],
) -> dict[str, ReachedBy]:
    """The reverse test walk, keyed by symbol id (call tier) and by file (import tier).

    Twice, the way the workspace test impact does: the call tier keyed by
    symbol id so each range is credited only with tests reaching its own
    symbol, then the import tier for only the files a range still needs it
    for. The test-file set is read once for both.
    """
    from ..test_reachability import load_test_files, tests_reaching_by_tier

    symbol_ids = sorted({s.symbol_id for syms in placed.values() for s in syms if s})[:MAX_SEEDS]
    test_files = await load_test_files(session, repository_id)
    reached: dict[str, ReachedBy] = {}
    if symbol_ids:
        reached.update(
            await tests_reaching_by_tier(
                session,
                repository_id,
                symbol_ids,
                import_depth=0,
                symbol_seeds={sid: {sid} for sid in symbol_ids},
                test_files=test_files,
            )
        )
    unanswered = [
        path
        for path in sorted(placed)
        if any(s is None or s.symbol_id not in reached for s in placed[path])
    ][:MAX_SEEDS]
    if unanswered:
        reached.update(
            await tests_reaching_by_tier(
                session, repository_id, unanswered, call_depth=0, test_files=test_files
            )
        )
    return reached


async def _spans_at_change(
    repo_path: str,
    indexed: str | None,
    head_commit: str | None,
    working_tree: bool,
    spans: dict[str, list[SymbolSpan]],
) -> dict[str, list[SymbolSpan]]:
    """*spans* in the change's line numbers; ``{}`` when that cannot be worked out.

    No spans is the honest fallback: a symbol placed by stale line numbers
    would send the reader to the wrong test. Runs one ``git diff`` for all the
    files, off the event loop.
    """
    import asyncio
    import subprocess

    from ..changed_lines import diff_since

    if not indexed:
        return {}
    if not working_tree and indexed == head_commit:
        return spans
    until = None if working_tree else head_commit or "HEAD"
    try:
        diffs = await asyncio.to_thread(diff_since, repo_path, indexed, until, sorted(spans))
    except (ValueError, subprocess.SubprocessError, OSError):
        return {}  # the indexed commit is gone (a rebase, a shallow clone)
    return {
        path: translate_spans(found, diffs[path].hunks) if path in diffs else found
        for path, found in spans.items()
    }
