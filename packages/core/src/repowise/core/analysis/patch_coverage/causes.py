"""Which changed files explain coverage lost outside the change.

Advice, never part of a verdict. For each file with newly uncovered lines
outside the change (:func:`~.delta.indirect_changes`), the changed files that
could have moved its coverage:

* ``test_deleted`` / ``test_modified``: a test file the change deleted or
  modified that reached the file. Measured from the per-test coverage map when
  one is stored for it (``per_test``), else inferred from the reverse test walk
  (``graph``).
* ``dependent_changed``: a changed non-test file with a call or an import
  edge into it (``graph`` covers both edge kinds).

Name pairing (``name``) adds a changed test named for the file by convention:
for a file the index names no test cause for, for every file without an
index, and always for a test the change deleted, which an index built at the
head no longer holds.
"""

from __future__ import annotations

import logging
from collections.abc import Collection, Iterable, Mapping
from dataclasses import replace
from typing import TYPE_CHECKING

from ...test_paths import is_test_related_path
from .delta import CauseBasis, IndirectCause, IndirectChange

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

#: Files whose causes the index is asked for. Ceiling: past it, the rest get
#: name pairing only (one ``IN`` list per graph read, bounded like the hints').
MAX_TARGETS = 200


def needs_causes(indirect: Iterable[IndirectChange]) -> list[str]:
    """The files a cause is looked for: those with newly uncovered lines."""
    return [c.file_path for c in indirect if c.newly_uncovered_ranges]


def _test_causes(
    tests: Collection[str],
    changed_tests: Collection[str],
    deleted: Collection[str],
    basis: CauseBasis,
) -> list[IndirectCause]:
    return [
        IndirectCause("test_deleted" if t in deleted else "test_modified", t, basis)
        for t in sorted(set(tests) & set(changed_tests))
    ]


def name_causes(
    files: Iterable[str], changed: Collection[str], deleted: Collection[str]
) -> dict[str, tuple[IndirectCause, ...]]:
    """``{file: causes}`` from git alone: changed tests named for each file."""
    from ..test_reachability import tests_matching_by_name

    tests = [p for p in changed if is_test_related_path(p)]
    files = list(files)
    matched = tests_matching_by_name(files, tests)
    return {
        f: tuple(
            _test_causes(matched[f].all_tests or matched[f].tests, tests, deleted, "name")
            if f in matched
            else ()
        )
        for f in files
    }


def apply_causes(
    indirect: Iterable[IndirectChange], causes: Mapping[str, tuple[IndirectCause, ...]] | None
) -> tuple[IndirectChange, ...]:
    """*indirect* with each row *causes* names carrying its causes; ``None`` changes nothing."""
    return tuple(
        replace(c, causes=causes[c.file_path]) if causes and c.file_path in causes else c
        for c in indirect
    )


async def read_indirect_causes(
    session: AsyncSession,
    repository_id: str,
    indirect: Iterable[IndirectChange],
    changed: Collection[str],
    deleted: Collection[str],
) -> dict[str, tuple[IndirectCause, ...]] | None:
    """``{file: causes}`` from the index, name pairing where it names no test; ``None`` on failure.

    *changed* is every path the change touched, deleted ones included (their
    old paths, as in *deleted*).
    """
    files = needs_causes(indirect)
    if not files:
        return {}
    try:
        found = await _read(session, repository_id, files[:MAX_TARGETS], changed, deleted)
    except Exception:
        # Advice never breaks the caller: any failure is logged and reads as unassessed.
        log.warning("indirect_coverage_causes_failed", exc_info=True)
        return None
    by_name = name_causes(files, changed, deleted)
    return {f: _merge(found.get(f, ()), by_name[f]) for f in files}


def _merge(
    indexed: tuple[IndirectCause, ...], by_name: tuple[IndirectCause, ...]
) -> tuple[IndirectCause, ...]:
    """The index's causes plus name pairing: deleted tests always, the rest when the
    index named no test. Tests first, then dependents."""
    tests = [c for c in indexed if c.kind != "dependent_changed"]
    named = {c.path for c in tests}
    extra = [
        c for c in by_name if c.path not in named and (c.kind == "test_deleted" or not tests)
    ]
    return (*tests, *extra, *(c for c in indexed if c.kind == "dependent_changed"))


async def _read(
    session: AsyncSession,
    repository_id: str,
    files: list[str],
    changed: Collection[str],
    deleted: Collection[str],
) -> dict[str, tuple[IndirectCause, ...]]:
    from repowise.core.persistence.crud.analysis.coverage_map import tests_covering_files

    from ..test_reachability import direct_dependents, tests_reaching_by_tier

    changed_tests = {p for p in changed if is_test_related_path(p)}
    changed_code = set(changed) - changed_tests
    per_test = await tests_covering_files(session, repository_id, set(files))
    reached = await tests_reaching_by_tier(session, repository_id, files)
    dependents = await direct_dependents(session, repository_id, files)
    out: dict[str, tuple[IndirectCause, ...]] = {}
    for f in files:
        if rows := per_test.get(f):
            # A test id without a file is named by its id's path part, as the hints read it.
            tests = {
                r.get("test_file") or str(r.get("test_id", "")).split("::", 1)[0] for r in rows
            }
            causes = _test_causes(tests, changed_tests, deleted, "per_test")
        elif (by := reached.get(f)) is not None:
            causes = _test_causes(by.all_tests or by.tests, changed_tests, deleted, "graph")
        else:
            causes = []
        causes += [
            IndirectCause("dependent_changed", p, "graph")
            for p in sorted((dependents.get(f, set()) & changed_code) - {f})
        ]
        out[f] = tuple(causes)
    return out
