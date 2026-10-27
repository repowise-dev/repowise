"""The scope a decision binds to, and the staleness question asked of it.

Two properties are load-bearing here and are asserted rather than assumed: a
record in a ``packages/`` layout must bind to the directories its files
actually live in (the old first-path-segment rule made almost every record in
such a repo claim ``packages`` or ``tests``), and staleness must be a fact
about whether those files moved rather than a formula with fitted divisors.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from repowise.core.analysis.decision_extractor import DecisionExtractor
from repowise.core.analysis.decisions.scope import derive_decision_scope, resolve_module_nodes

_BIRTH = datetime(2026, 1, 1, tzinfo=UTC)
_BEFORE = _BIRTH - timedelta(days=10)
_AFTER = _BIRTH + timedelta(days=10)


# -- node sets ---------------------------------------------------------------


def test_record_binds_to_real_directories_not_the_first_path_segment() -> None:
    """The defect this exists for: everything in this layout scoping to `packages`."""
    modules = resolve_module_nodes(
        [
            "packages/core/src/repowise/core/pipeline/persist.py",
            "packages/core/src/repowise/core/pipeline/incremental.py",
            "tests/unit/pipeline/test_persist.py",
        ]
    )

    assert modules == [
        "packages/core/src/repowise/core/pipeline",
        "tests/unit/pipeline",
    ]
    assert "packages" not in modules
    assert "tests" not in modules


def test_files_sharing_a_directory_collapse_to_one_module() -> None:
    assert resolve_module_nodes(["a/b/one.py", "a/b/two.py", "a/b/three.py"]) == ["a/b"]


def test_root_level_files_contribute_no_module() -> None:
    """A record naming README.md is scoped by that file, not by the repository."""
    assert resolve_module_nodes(["README.md", "setup.py"]) == []


def test_windows_separators_and_empties_are_normalised() -> None:
    assert resolve_module_nodes(["pkg\\mod\\x.py", "", "pkg/mod/y.py"]) == ["pkg/mod"]


def test_module_list_is_bounded() -> None:
    """An unbounded list is a second copy of the tree in a text column."""
    many = [f"dir{i}/file.py" for i in range(50)]
    assert len(resolve_module_nodes(many)) == 12


def test_granularity_agrees_with_the_module_convention() -> None:
    """Two files in sibling packages are cross-module, not one `packages` module."""
    files = ["packages/core/a.py", "packages/cli/b.py"]

    assert derive_decision_scope(files, None) == "cross-module"
    assert derive_decision_scope(["packages/core/a.py", "packages/core/b.py"], None) == "module"


# -- staleness as a fact -----------------------------------------------------


def _meta(last_commit_at: datetime) -> dict:
    return {"last_commit_at": last_commit_at, "commit_count_90d": 99}


def test_untouched_scope_reads_still_true() -> None:
    score = DecisionExtractor.compute_staleness(
        _BIRTH,
        ["a.py", "b.py"],
        {"a.py": _meta(_BEFORE), "b.py": _meta(_BEFORE)},
    )

    assert score == 0.0


def test_changed_scope_does_not_read_still_true() -> None:
    score = DecisionExtractor.compute_staleness(
        _BIRTH,
        ["a.py", "b.py"],
        {"a.py": _meta(_AFTER), "b.py": _meta(_AFTER)},
    )

    assert score == 1.0


def test_score_is_the_fraction_of_the_scope_that_moved() -> None:
    score = DecisionExtractor.compute_staleness(
        _BIRTH,
        ["a.py", "b.py", "c.py", "d.py"],
        {
            "a.py": _meta(_AFTER),
            "b.py": _meta(_BEFORE),
            "c.py": _meta(_BEFORE),
            "d.py": _meta(_BEFORE),
        },
    )

    assert score == 0.25


def test_commit_volume_no_longer_moves_the_score() -> None:
    """The old formula's divisors were fitted to one repository's history.

    Two records over untouched files must score identically no matter how busy
    those files are, which the ``commit_count / 15`` term did not manage.
    """
    quiet = DecisionExtractor.compute_staleness(
        _BIRTH,
        ["a.py"],
        {"a.py": {"last_commit_at": _BEFORE, "commit_count_90d": 0}},
    )
    busy = DecisionExtractor.compute_staleness(
        _BIRTH,
        ["a.py"],
        {"a.py": {"last_commit_at": _BEFORE, "commit_count_90d": 400}},
    )

    assert quiet == busy == 0.0


def test_age_alone_no_longer_moves_the_score() -> None:
    """A decade-old record over code nobody touched is still true."""
    ancient = datetime(2015, 1, 1, tzinfo=UTC)

    score = DecisionExtractor.compute_staleness(
        ancient,
        ["a.py"],
        {"a.py": _meta(datetime(2015, 6, 1, tzinfo=UTC) - timedelta(days=200))},
    )

    assert score == 0.0


def test_commit_prose_no_longer_boosts_the_score() -> None:
    """The keyword boost read English commit messages and inferred intent."""
    score = DecisionExtractor.compute_staleness(
        _BIRTH,
        ["a.py"],
        {
            "a.py": {
                "last_commit_at": _BEFORE,
                "significant_commits_json": (
                    '[{"date": "2026-06-01T00:00:00+00:00",'
                    ' "message": "migrate away from redis, drop the cache"}]'
                ),
            }
        },
        decision_text="use redis for the cache",
    )

    assert score == 0.0


def test_a_file_the_repo_does_not_track_cannot_be_shown_to_hold() -> None:
    score = DecisionExtractor.compute_staleness(_BIRTH, ["gone.py"], {})

    assert score == 1.0


def test_sqlite_naive_and_string_timestamps_both_compare() -> None:
    """The ORM hands back datetimes; SQLite hands back strings. Both arrive."""
    score = DecisionExtractor.compute_staleness(
        _BIRTH.replace(tzinfo=None),
        ["a.py"],
        {"a.py": {"last_commit_at": _AFTER.isoformat().replace("+00:00", "Z")}},
    )

    assert score == 1.0


def test_unscoped_record_scores_zero_and_that_is_not_freshness() -> None:
    """0.0 here means "cannot be asked", which `decision health` reports apart.

    Asserted so the pairing stays deliberate: the score alone cannot tell the
    two apart, which is exactly why the unscoped count and the read-time
    sentence both exist.
    """
    assert DecisionExtractor.compute_staleness(_BIRTH, [], {}) == 0.0
