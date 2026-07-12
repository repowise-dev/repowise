"""Coverage-backed missing-test signal.

A filename path-pattern "you changed X but didn't test it" check knows only that
a test *exists* for a file, never whether the committed change is *covered* by
it. Only a real per-test coverage map can tell the two apart. This module mines
that map (built by ``repowise coverage add``) into two PR-time sub-signals,
computed per changed file from the change's changed *line numbers*:

  1. UNTESTED CHANGE (the strong signal): the file IS in the coverage map, but
     the changed lines intersect no test's covered set. Some test exercises the
     file, yet these specific lines are uncovered. Fires ONLY when the file has
     coverage rows: a file with zero rows is "no coverage data / unknown", never
     "untested". Conflating the two is exactly the dishonesty that makes
     filename-guessing worthless, so the two are kept strictly apart here.

  2. STALE TEST CANDIDATE (the weak signal): the changed lines ARE covered, but
     none of the covering test *files* appear in the diff - covered code changed
     without its guarding test being touched. Degrades honestly when a covering
     row carries no test file path (coverage.py dynamic-context ids like
     ``mod.test_add`` have no path): such rows can neither confirm nor deny a
     stale test, so they never fire the signal on their own.

The detector is a pure classifier over ``(session, repo_id, changed-lines
map)`` - the inverse view of ``repowise impacted-tests`` over the same map. It
is split from any surface (the ``impacted_tests`` command resolver does the
same) so the diff -> lines -> signal path is testable against a seeded
``test_coverage`` table.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class UntestedChange:
    """A file whose changed lines no recorded test covers (strong signal)."""

    source_file: str
    uncovered_lines: list[int]
    changed_line_count: int


@dataclass
class StaleTestCandidate:
    """Covered changed lines whose covering test file(s) are not in the diff."""

    source_file: str
    covering_test_files: list[str]
    # Covering rows that carry no test-file path (dynamic-context ids). Reported
    # for transparency; they do not by themselves fire the signal.
    covering_test_ids_without_file: list[str] = field(default_factory=list)


@dataclass
class MissingTestReport:
    """Buckets from classifying each changed file against the coverage map."""

    #: file has coverage rows but the changed lines are covered by no test.
    untested_changes: list[UntestedChange] = field(default_factory=list)
    #: changed lines covered, but no covering test file is in the diff.
    stale_test_candidates: list[StaleTestCandidate] = field(default_factory=list)
    #: changed lines covered and a covering test file IS in the diff (or the
    #: covering rows are pathless so staleness is unknowable) - no signal.
    covered: list[str] = field(default_factory=list)
    #: file has ZERO coverage rows - unknown, NOT untested. Never a warning.
    no_data: list[str] = field(default_factory=list)
    #: true when the repo has no per-test map at all (nothing to say honestly).
    map_empty: bool = False

    def has_signal(self) -> bool:
        return bool(self.untested_changes or self.stale_test_candidates)


async def detect_missing_tests(
    session,
    repository_id: str,
    changed: dict[str, set[int]],
) -> MissingTestReport:
    """Classify each changed file into the missing-test buckets.

    *changed* is ``{source_file: {changed line numbers}}`` (the new side of a
    diff, from :func:`repowise.core.analysis.changed_lines.changed_lines`). Its
    keys are also the set of files touched by the change - used to decide
    whether a covering test file was itself edited (sub-signal 2).
    """
    from repowise.core.persistence.crud import get_test_coverage_summary, tests_covering

    report = MissingTestReport()
    if not changed:
        return report

    summary = await get_test_coverage_summary(session, repository_id)
    if summary.get("pair_count", 0) == 0:
        # No map ingested: we cannot honestly say anything about coverage.
        report.map_empty = True
        return report

    changed_files = set(changed.keys())

    for source_file, lines in sorted(changed.items()):
        # Does the file appear in the map at all? (lines=None -> every test that
        # touches the file, regardless of which lines). Empty => no data.
        all_rows = await tests_covering(session, repository_id, source_file, lines=None)
        if not all_rows:
            report.no_data.append(source_file)
            continue

        # The file IS in the map. Do any tests cover the CHANGED lines?
        hit_rows = await tests_covering(session, repository_id, source_file, lines=lines)
        if not hit_rows:
            # Covered file, uncovered change: the strong signal.
            covered_here: set[int] = set()
            for r in all_rows:
                covered_here.update(r["covered_lines"])
            uncovered = sorted(lines - covered_here)
            report.untested_changes.append(
                UntestedChange(
                    source_file=source_file,
                    uncovered_lines=uncovered,
                    changed_line_count=len(lines),
                )
            )
            continue

        # Changed lines ARE covered. Was a covering test touched in the diff?
        covering_test_files = sorted({r["test_file"] for r in hit_rows if r["test_file"]})
        pathless_ids = sorted({r["test_id"] for r in hit_rows if not r["test_file"]})
        touched = [tf for tf in covering_test_files if tf in changed_files]
        if touched:
            report.covered.append(source_file)
        elif covering_test_files:
            # Every covering test file we can name is absent from the diff.
            report.stale_test_candidates.append(
                StaleTestCandidate(
                    source_file=source_file,
                    covering_test_files=covering_test_files,
                    covering_test_ids_without_file=pathless_ids,
                )
            )
        else:
            # Only pathless covering rows: covered, but staleness is unknowable.
            # Degrade honestly - do not guess a stale test.
            report.covered.append(source_file)

    return report
