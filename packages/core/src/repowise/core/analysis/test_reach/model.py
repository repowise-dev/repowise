"""The rows a reach walk answers with, the constants it walks by, and its test order."""

from __future__ import annotations

import sys
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import PurePath
from typing import Literal, NamedTuple

from repowise.core.analysis.execution_graph import UNRELIABLE_EXECUTION_ORIGINS
from repowise.core.ingestion.symbol_identity import file_of_symbol
from repowise.core.test_paths import (
    SiblingNames,
    names_test_for,
    paired_test_names,
    qualified_name_leads,
    qualified_name_prefixes,
)

# How many call hops a test may take and still be said to reach a file. The walk
# saturates here; see the Depth section of the package docstring.
DEFAULT_CALL_DEPTH = 3

# How many import hops the weaker fallback tier may take. Still the measured
# choice; a blanket second hop costs more than it buys.
DEFAULT_MAX_DEPTH = 1

# Call edges whose resolver only matched a name. See the Confidence section.
UNRELIABLE_CALL_ORIGINS = UNRELIABLE_EXECUTION_ORIGINS

# Cap on how many test files one target reports. The consumers all cut their
# own lists shorter; the cap exists so a helper called from every test in the
# suite cannot produce an unbounded intermediate.
MAX_TESTS_PER_TARGET = 50

# A call-tier test this many hops out, from one test function, through a hub
# (:func:`~.hubs.call_hub_files`), reaches the target the way every test does: by
# calling into the hub. Such a row is tagged ``transitive``.
TRANSITIVE_HOPS = 3

# Which tier answered. The CLI prints this so a reader can tell "this test runs
# into the file" from the weaker "this test imports it".
# ``name-match`` is weaker still: a test named for the file, with no edge.
# ``changed-test`` is a test the change adds or edits that names or imports the
# file; ``inline`` is a file carrying its own tests (Rust ``#[cfg(test)]``, a Go
# ``_test.go`` beside it).
ReachedVia = Literal["call-graph", "import-graph", "name-match", "changed-test", "inline"]

# What can be said about one file's tests:
# ``reached``    some test reaches it (the row says which, and how);
# ``unreached``  the call walk, the helper or barrel hop and the whole import
#                closure all miss it, so "no test reaches this" is a claim;
# ``new``        the index has never seen the file;
# ``no_signal``  the index holds no tests, so silence proves nothing;
# ``type_only``  it declares only types: there is nothing for a test to run;
# ``mocked_only`` every test that reaches it replaces it with a double, so
#                none is shown to run it, and nothing says none could.
ReachState = Literal[
    "reached", "unreached", "new", "no_signal", "type_only", "mocked_only", "not_applicable"
]


class ReachDistance(NamedTuple):
    """How a test reached a seed: the fewest call hops, and how many of the
    test's symbols made a call at that depth."""

    hops: int
    callers: int


@dataclass(frozen=True)
class ReachedBy:
    """Tests reaching one target, and which tier found them.

    ``total`` is how many the walk actually found, before
    ``MAX_TESTS_PER_TARGET`` trimmed ``tests``. A surface that prints
    ``len(tests)`` as the answer is stating a cap as a measurement. The cut keeps
    the closest tests (:func:`rank_reaching`). Carry the total and say the bound.
    """

    tests: list[str]
    via: ReachedVia
    total: int = 0
    # Internal uncapped identities let downstream aggregators de-duplicate one
    # test that reaches several targets while public lists remain bounded.
    all_tests: tuple[str, ...] | None = None
    # How close each test came, for the call tier only: the import and name
    # tiers have no hop count to report.
    reach: Mapping[str, ReachDistance] | None = None
    # Import tier only: the files each test was reached through, i.e. the
    # dependencies of that test on the route to the target.
    entries: Mapping[str, frozenset[str]] | None = None
    # Call tier, when the walk was told the hubs: tests that reach the target
    # only through a hub, at ``TRANSITIVE_HOPS`` or more from one test function.
    # Listed after every other test; still reaching, so never dropped.
    transitive: frozenset[str] = frozenset()


@dataclass(frozen=True)
class ReachRow:
    """One file's :data:`ReachState`, with the evidence when it is ``reached`` or ``mocked_only``."""

    state: ReachState
    reached: ReachedBy | None = None
    # ``mocked_only``: the tests that reach the file and replace it.
    mocked_by: tuple[str, ...] = ()


def capped(
    target: str,
    tests: Iterable[str],
    via: ReachedVia,
    *,
    reach: Mapping[str, ReachDistance] | None = None,
    direct: Collection[str] = frozenset(),
    changed: Collection[str] = frozenset(),
    edited: Collection[str] | None = None,
    entries: Mapping[str, frozenset[str]] | None = None,
    transitive: Collection[str] = frozenset(),
    rank: bool = True,
) -> ReachedBy:
    """*tests* ranked by :func:`rank_reaching`, then cut to ``MAX_TESTS_PER_TARGET``."""
    if rank:
        ordered = tuple(
            rank_reaching(
                target,
                tests,
                reach=reach,
                direct=direct,
                changed=changed,
                edited=edited,
                transitive=transitive,
            )
        )
    else:
        ordered = tuple(sorted(tests))
    return ReachedBy(
        list(ordered[:MAX_TESTS_PER_TARGET]),
        via,
        len(ordered),
        ordered,
        reach,
        entries,
        frozenset(transitive),
    )


def rank_reaching(
    target: str,
    tests: Iterable[str],
    *,
    reach: Mapping[str, ReachDistance] | None = None,
    direct: Collection[str] = frozenset(),
    changed: Collection[str] = frozenset(),
    edited: Collection[str] | None = None,
    transitive: Collection[str] = frozenset(),
) -> list[str]:
    """*tests* reaching *target*, the likeliest to exercise it first. Cap after this.

    In turn: a test in *changed* (the change edits it), a test not in
    *transitive* (one reaching only through a hub), fewer call hops
    (*reach*; a test without one sorts after every test with one), a test in
    *direct* (it imports or calls *target* itself), more of the test's
    functions at that distance, then by name: the test named for *target*,
    the one mirroring more of its directories, and the path. Targets, tests
    and *changed* may be ``path::name`` ids; every key reads the file part.
    """
    path_of = file_of_symbol(target)
    names = paired_test_names(path_of)
    dirs = set(path_of.split("/")[:-1])
    reach = reach or {}
    if edited is None:
        edited = {file_of_symbol(c) for c in changed}

    def key(test: str) -> tuple[bool, bool, int, bool, int, bool, int, str]:
        path = file_of_symbol(test)
        distance = reach.get(test) or reach.get(path)
        return (
            path not in edited,
            test in transitive or path in transitive,
            distance.hops if distance else sys.maxsize,
            test not in direct and path not in direct,
            -distance.callers if distance else 0,
            path.rsplit("/", 1)[-1] not in names,
            -len(dirs & set(path.split("/")[:-1])),
            test,
        )

    return sorted(tests, key=key)


@cache
def _js_family_suffixes() -> frozenset[str]:
    """The extensions the language registry gives JavaScript and TypeScript.

    Resolved on first use: the registry lives under ``ingestion/``, which imports
    :mod:`repowise.core.test_paths` as it loads.
    """
    from repowise.core.ingestion.languages.registry import REGISTRY

    return REGISTRY.extensions_for(("javascript", "typescript"))


def _language_compatible(target: str, test: str) -> bool:
    """Whether *test* uses a test tail for *target*'s own language.

    A JS/TS test file (``x.test.ts``) is no evidence for a ``.py`` or ``.go`` file
    of the same stem; it counts only for a JS/TS target.
    """
    js_family = _js_family_suffixes()
    if PurePath(test).suffix.lower() in js_family:
        return PurePath(target).suffix.lower() in js_family
    return True


def _directory_overlap(target: str, test: str) -> int:
    """How many trailing directory components *target* and *test* share."""
    target_dirs = target.split("/")[:-1]
    test_dirs = test.split("/")[:-1]
    overlap = 0
    for target_dir, test_dir in zip(reversed(target_dirs), reversed(test_dirs), strict=False):
        if target_dir != test_dir:
            break
        overlap += 1
    return overlap


def tests_matching_by_name(
    targets: list[str], test_files: Collection[str], indexed_files: Collection[str] = ()
) -> dict[str, ReachedBy]:
    """Tests named for each target by convention: the fallback when no edge answered.

    An unresolved graph (dynamic client, unindexed framework) leaves
    ``tests/test_x.py`` with no edge to ``x.py``; "unknown" there argues
    against a change a test guards.  A test beside a source file it names is
    owned by that sibling, though: ``a/index.test.ts`` is not evidence for
    every other ``index.ts`` in the repository.  Tests outside their subject
    directory retain the cross-directory fallback.

    When several tests match, the ones that mirror more of the target's
    trailing directories win, so ``tests/dead_code/test_analyzer_golden.py``
    is preferred over ``tests/doc_drift/test_analyzer.py`` for
    ``pkg/dead_code/analyzer.py``.  A qualified name that
    :func:`~repowise.core.test_paths.names_test_for` accepts is also eligible,
    and a test tail for another language (a ``.test.ts`` for a ``.py``) is not.
    """
    all_files = set(targets) | set(test_files) | set(indexed_files)
    files_by_dir: dict[str, list[str]] = {}
    for path in all_files:
        files_by_dir.setdefault(path.rpartition("/")[0], []).append(path)
    siblings_by_dir = {directory: SiblingNames(files) for directory, files in files_by_dir.items()}

    test_list = list(test_files)
    tests_by_dir_and_name: dict[tuple[str, str], list[str]] = {}
    # Positions in *test_list* by file name, and by each prefix of the name a
    # qualifier could follow, so a target reads its candidates off two lookups.
    positions_by_name: dict[str, list[int]] = {}
    positions_by_prefix: dict[str, list[int]] = {}
    for position, path in enumerate(test_list):
        dir_path, _, name = path.rpartition("/")
        tests_by_dir_and_name.setdefault((dir_path, name), []).append(path)
        file_name = PurePath(path).name
        positions_by_name.setdefault(file_name, []).append(position)
        for prefix in qualified_name_prefixes(file_name):
            positions_by_prefix.setdefault(prefix, []).append(position)

    owned_by: dict[str, set[str]] = {}
    for source in all_files:
        dir_path, _, _ = source.rpartition("/")
        for name in paired_test_names(source):
            for test in tests_by_dir_and_name.get((dir_path, name), ()):
                if source != test:
                    owned_by.setdefault(test, set()).add(source)

    out: dict[str, ReachedBy] = {}
    for target in targets:
        target_stem = PurePath(target).stem.lower()
        stem_without_unit_prefix = target_stem[1:] if target_stem.startswith("u") else target_stem
        # Same-directory files; the target itself never claims a name from itself.
        siblings = siblings_by_dir[target.rpartition("/")[0]]

        positions: set[int] = set()
        for name in paired_test_names(target):
            positions.update(positions_by_name.get(name, ()))
        for lead in qualified_name_leads(target):
            positions.update(positions_by_prefix.get(lead, ()))

        found: list[str] = []
        for position in sorted(positions):
            test = test_list[position]
            test_stem = PurePath(test).stem.lower()
            if target_stem not in test_stem and stem_without_unit_prefix not in test_stem:
                continue
            if not _language_compatible(target, test):
                continue
            if not names_test_for(test, target, siblings):
                continue
            if test in owned_by and target not in owned_by[test]:
                continue
            found.append(test)

        if not found:
            continue

        overlaps = [(_directory_overlap(target, test), test) for test in found]
        best = max(overlap for overlap, _ in overlaps)
        if best > 0:
            found = [test for overlap, test in overlaps if overlap == best]

        out[target] = capped(target, found, "name-match")
    return out
