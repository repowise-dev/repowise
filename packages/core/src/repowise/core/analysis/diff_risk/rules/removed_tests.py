"""``weakened-tests``: removed test cases and assertion drops.

Both report only when the total over every changed test file dropped, so
tests that moved to another changed file do not report. A drop does not
report when the lost test lines name code the change removed (the tests went
with that code), or when the change added a parametrize marker (cases folded
into one). An assertion drop reports only when the file added no assertion
and assertions are at least half of the code lines it lost; otherwise the
test was rewritten. Either drop must also show in the lines the change
removed, each read on its own, so a whole-file mask that drifted on one side
(a quote it misread) never reads as lost tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

from ...change_health.analyzer import language_of
from .. import markers as m
from ..models import FileTexts, RawFinding, Rule, TestClassifier, TestFile
from ..paths import is_prose
from ..text import FileView, added_line_indexes

RULE: Rule = "weakened-tests"


@dataclass(frozen=True, slots=True)
class _Counts:
    test: TestFile
    base_cases: int
    head_cases: int
    base_asserts: int
    head_asserts: int

    @classmethod
    def of(cls, test: TestFile) -> _Counts:
        return cls(
            test,
            _count(test.base, m.TEST_CASES),
            _count(test.head, m.TEST_CASES),
            _count(test.base, m.ASSERTIONS),
            _count(test.head, m.ASSERTIONS),
        )


def removed_tests(
    tests: list[TestFile], files: list[FileTexts], is_test: TestClassifier
) -> list[RawFinding]:
    """``removed-tests`` and ``assertion-drop`` findings over the changed test files."""
    if not tests:
        return []
    removed = RemovedNames.collect(files, is_test)
    rows = [_Counts.of(test) for test in tests]
    cases_dropped = sum(r.head_cases for r in rows) < sum(r.base_cases for r in rows)
    asserts_dropped = sum(r.head_asserts for r in rows) < sum(r.base_asserts for r in rows)
    out: list[RawFinding] = []
    for row in rows:
        if _explained(row.test, removed):
            continue
        if cases_dropped and _cases_dropped(row):
            out.append(_finding(row, "removed-tests", "test cases"))
        elif asserts_dropped and _asserts_dropped(row):
            out.append(_finding(row, "assertion-drop", "assertions"))
    return out


def _explained(test: TestFile, removed: RemovedNames) -> bool:
    return test.base is None or removed.explain(test.base, test.head) or _parametrized(test)


def _cases_dropped(row: _Counts) -> bool:
    return row.head_cases < row.base_cases and _lost(row.test, m.TEST_CASES) > 0


def _asserts_dropped(row: _Counts) -> bool:
    return (
        row.head_cases >= row.base_cases
        and row.head_asserts < row.base_asserts
        and _assertions_led(row.test)
    )


def _finding(row: _Counts, kind: str, what: str) -> RawFinding:
    if kind == "removed-tests":
        before, after = row.base_cases, row.head_cases
    else:
        before, after = row.base_asserts, row.head_asserts
    evidence = f"{what}: {before} -> {after}"
    message = f"The change removes {what} ({evidence})"
    return RawFinding(RULE, kind, row.test.path, None, kind, message, evidence)


def _count(view: FileView | None, markers: tuple[m.Marker, ...]) -> int:
    if view is None:
        return 0
    return view.count(k for k in markers if k.applies_to(view.language, is_prose=False))


def _parametrized(test: TestFile) -> bool:
    """Whether the change added a parametrize marker: cases folded into one."""
    if test.head is None:
        return False
    return any(m.PARAMETRIZE.search(test.head.text_view[i]) for i in _added(test))


def _lost(test: TestFile, markers: tuple[m.Marker, ...]) -> int:
    """Markers on the test's removed lines minus those on its added lines."""
    return _on_removed_lines(test, markers) - _on_added_lines(test, markers)


def _applying(view: FileView, markers: tuple[m.Marker, ...]) -> list[m.Marker]:
    return [k for k in markers if k.applies_to(view.language, is_prose=False)]


def _on_removed_lines(test: TestFile, markers: tuple[m.Marker, ...]) -> int:
    if test.base is None:
        return 0
    return test.base.local_count(_applying(test.base, markers), _removed(test.base, test.head))


def _on_added_lines(test: TestFile, markers: tuple[m.Marker, ...]) -> int:
    if test.head is None:
        return 0
    return test.head.local_count(_applying(test.head, markers), _added(test))


def _added(test: TestFile) -> list[int]:
    """0-based head lines the change added to the test."""
    base_lines = test.base.lines if test.base else []
    return added_line_indexes(base_lines, test.head.lines) if test.head else []


def _removed(base: FileView, head: FileView | None) -> list[int]:
    """0-based base lines the change removed (base lines head does not hold)."""
    return added_line_indexes(head.lines if head else [], base.lines)


def _assertions_led(test: TestFile) -> bool:
    """Whether the file only lost assertions: it added none, and assertions
    are at least half of the code lines it removed.

    An added assertion means the checks were rewritten (two expectations
    folded into one), which a count cannot tell from a weaker test.
    """
    base = test.base
    if base is None or _on_added_lines(test, m.ASSERTIONS):
        return False
    markers = _applying(base, m.ASSERTIONS)
    code_lines = [i for i in _removed(base, test.head) if base.code_view[i].strip()]
    if not code_lines:
        return False
    asserting = sum(1 for i in code_lines if base.local_count(markers, [i]))
    return asserting * 2 >= len(code_lines)


@dataclass(frozen=True, slots=True)
class RemovedNames:
    """Names the change took out of the project's non-test source.

    Identifiers a changed source file held in base and no longer holds in
    head; identifiers of a deleted source file that no changed file still
    holds; and the stems and gone directories of deleted files.
    """

    identifiers: frozenset[str]
    segments: frozenset[str]

    @classmethod
    def collect(cls, files: list[FileTexts], is_test: TestClassifier) -> RemovedNames:
        live_dirs = {
            str(parent)
            for f in files
            if f.head_path and f.head is not None
            for parent in PurePosixPath(f.head_path).parents
        }
        segments: set[str] = set()
        for f in files:
            if f.head is None and f.base_path:
                segments |= _gone_segments(f.base_path, live_dirs)
        sources = [(f, lang) for f in files if (lang := _source_language(f, is_test))]
        return cls(frozenset(_removed_identifiers(sources)), frozenset(segments))

    def explain(self, base: FileView, head: FileView | None) -> bool:
        """Whether the lines a test file lost name removed code its head no longer names."""
        removed = _removed(base, head)
        if not removed:
            return False
        head_ids = head.identifiers() if head else set()
        if any(n in self.identifiers and n not in head_ids for n in base.identifiers(removed)):
            return True
        head_text = head.text if head else ""
        tokens = set().union(*(_path_tokens(base.lines[i]) for i in removed))
        return any(s in tokens and s not in head_text for s in self.segments)


def _source_language(f: FileTexts, is_test: TestClassifier) -> str | None:
    """The language of a changed non-test source file, else ``None``."""
    language = language_of(f.path)
    if language is None or is_prose(f.path) or is_test(f.path, language):
        return None
    return language


def _removed_identifiers(sources: list[tuple[FileTexts, str]]) -> set[str]:
    """Identifiers that left the changed source files.

    A name of a deleted file that a changed file still holds moved; it did not
    leave. Names the deleted file only imported count too: the ceiling is
    abstaining on a few more removals, never a false alarm.
    """
    identifiers: set[str] = set()
    deleted: set[str] = set()
    head_union: set[str] = set()
    for f, language in sources:
        head_ids = FileView(f.head, f.path, language).identifiers() if f.head else set()
        head_union |= head_ids
        if f.base is None:
            continue
        base_view = FileView(f.base, f.base_path or f.path, language)
        base_ids = {n for n in base_view.identifiers() if _candidate(n)}
        if f.head is None:
            deleted |= base_ids
        else:
            identifiers |= base_ids - head_ids
    return identifiers | (deleted - head_union)


def _candidate(name: str) -> bool:
    return len(name) >= m.MIN_REMOVED_NAME_LEN and name not in m.NOT_REMOVED_NAMES


def _gone_segments(path: str, live_dirs: set[str]) -> set[str]:
    """The stem of a deleted file and each directory no changed head file lives in.

    Without a head tree listing a directory that still holds unchanged files
    reads as gone; that only makes the rule abstain more often.
    """
    p = PurePosixPath(path)
    out = {p.stem} if len(p.stem) >= m.MIN_REMOVED_NAME_LEN else set()
    for parent in p.parents:
        if str(parent) not in live_dirs and len(parent.name) >= m.MIN_REMOVED_NAME_LEN:
            out.add(parent.name)
    return out


def _path_tokens(line: str) -> set[str]:
    for sep in "/.'\"`:\\":
        line = line.replace(sep, " ")
    return set(line.split())
