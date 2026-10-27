"""The pure partition is the only grouping algorithm.

The index-backed entry point must be collection plus this function, never a
second notion of "independent". So every case here is asserted twice: once over
a seeded wiki.db, and once over evidence handed in directly.
"""

from __future__ import annotations

from repowise.core.analysis.independent_changes import (
    IndependentChangeEvidence,
    collect_independent_change_evidence,
    independent_changes,
    partition_independent_changes,
)

from .test_independent_changes import _REPO_ID, _seed


def _evidence(**kwargs) -> IndependentChangeEvidence:
    base = {
        "paths": (),
        "groupable": frozenset(),
        "pairs": frozenset(),
        "linked": frozenset(),
        "commit_sets": (),
    }
    base.update(kwargs)
    return IndependentChangeEvidence(**base)


# ---------------------------------------------------------------------------
# Index-backed and supplied evidence agree
# ---------------------------------------------------------------------------


async def _both(tmp_path, *, files, edges=(), symbols=(), changed=None, commit_sets=()):
    """Run the index path and the pure path over the same seeded index."""
    factory = await _seed(tmp_path, files=files, edges=edges, symbols=symbols)
    changed = changed or files
    async with factory() as session:
        from_index = await independent_changes(
            session, _REPO_ID, changed, commit_sets=commit_sets
        )
    async with factory() as session:
        evidence = await collect_independent_change_evidence(
            session, _REPO_ID, changed, commit_sets=commit_sets
        )
    return from_index, partition_independent_changes(evidence)


async def test_two_unconnected_groups_agree_across_paths(tmp_path):
    files = ["a/one.py", "a/two.py", "b/one.py", "b/two.py"]
    edges = (
        ("a/one.py", "a/two.py", "imports"),
        ("b/one.py", "b/two.py", "imports"),
    )
    from_index, from_evidence = await _both(tmp_path, files=files, edges=edges)

    assert from_index is not None
    assert from_evidence is not None
    assert from_index.groups == from_evidence.groups
    assert from_index.ungrouped_files == from_evidence.ungrouped_files
    assert from_index.commits_known == from_evidence.commits_known
    assert len(from_evidence.groups) == 2


async def test_one_connected_change_agrees_on_no_report(tmp_path):
    files = ["a/one.py", "a/two.py"]
    edges = (("a/one.py", "a/two.py", "imports"),)
    from_index, from_evidence = await _both(tmp_path, files=files, edges=edges)
    assert from_index is None
    assert from_evidence is None


async def test_a_shared_commit_merges_groups_on_both_paths(tmp_path):
    files = ["a/one.py", "a/two.py", "b/one.py", "b/two.py"]
    edges = (
        ("a/one.py", "a/two.py", "imports"),
        ("b/one.py", "b/two.py", "imports"),
    )
    # One commit touching one file from each group links them.
    commits = [["a/one.py", "b/one.py"]]
    from_index, from_evidence = await _both(
        tmp_path, files=files, edges=edges, commit_sets=commits
    )
    assert from_index == from_evidence


# ---------------------------------------------------------------------------
# The pure function on its own
# ---------------------------------------------------------------------------


def test_fewer_than_two_paths_is_no_report():
    assert partition_independent_changes(_evidence(paths=("a.py",))) is None


def test_fewer_than_two_groupable_files_is_no_report():
    evidence = _evidence(paths=("a.py", "README.md"), groupable=frozenset({"a.py"}))
    assert partition_independent_changes(evidence) is None


def test_two_islands_split():
    result = partition_independent_changes(
        _evidence(
            paths=("a1.py", "a2.py", "b1.py", "b2.py"),
            groupable=frozenset({"a1.py", "a2.py", "b1.py", "b2.py"}),
            pairs=frozenset({("a1.py", "a2.py"), ("b1.py", "b2.py")}),
            linked=frozenset({"a1.py", "a2.py", "b1.py", "b2.py"}),
        )
    )
    assert result is not None
    assert [g.files for g in result.groups] == [("a1.py", "a2.py"), ("b1.py", "b2.py")]


def test_an_unlinked_component_is_reported_not_claimed_as_a_change():
    """Absent edges only mean independence for files the index has ever linked."""
    result = partition_independent_changes(
        _evidence(
            paths=("a1.py", "a2.py", "b1.py", "b2.py", "orphan.py"),
            groupable=frozenset({"a1.py", "a2.py", "b1.py", "b2.py", "orphan.py"}),
            pairs=frozenset({("a1.py", "a2.py"), ("b1.py", "b2.py")}),
            linked=frozenset({"a1.py", "a2.py", "b1.py", "b2.py"}),
        )
    )
    assert result is not None
    assert "orphan.py" in result.ungrouped_files
    assert all("orphan.py" not in g.files for g in result.groups)


def test_ungroupable_paths_survive_into_the_report():
    result = partition_independent_changes(
        _evidence(
            paths=("a1.py", "a2.py", "b1.py", "b2.py", "docs/guide.md"),
            groupable=frozenset({"a1.py", "a2.py", "b1.py", "b2.py"}),
            pairs=frozenset({("a1.py", "a2.py"), ("b1.py", "b2.py")}),
            linked=frozenset({"a1.py", "a2.py", "b1.py", "b2.py"}),
        )
    )
    assert result is not None
    assert "docs/guide.md" in result.ungrouped_files


def test_a_commit_covering_every_file_says_nothing():
    """A commit set equal to the whole diff is the diff restated, so it is skipped."""
    groupable = frozenset({"a1.py", "a2.py", "b1.py", "b2.py"})
    result = partition_independent_changes(
        _evidence(
            paths=tuple(sorted(groupable)),
            groupable=groupable,
            pairs=frozenset({("a1.py", "a2.py"), ("b1.py", "b2.py")}),
            linked=frozenset(groupable),
            commit_sets=(tuple(sorted(groupable)),),
        )
    )
    assert result is not None
    assert len(result.groups) == 2


def test_commits_known_reflects_whether_there_was_anything_to_check():
    groupable = frozenset({"a1.py", "a2.py", "b1.py", "b2.py"})
    args = {
        "paths": tuple(sorted(groupable)),
        "groupable": groupable,
        "pairs": frozenset({("a1.py", "a2.py"), ("b1.py", "b2.py")}),
        "linked": frozenset(groupable),
    }
    assert partition_independent_changes(_evidence(**args)).commits_known is False
    with_commits = partition_independent_changes(
        _evidence(**args, commit_sets=(("a1.py",),))
    )
    assert with_commits.commits_known is True


def test_an_edge_through_a_non_groupable_file_cannot_bridge_groups():
    result = partition_independent_changes(
        _evidence(
            paths=("a1.py", "a2.py", "b1.py", "b2.py", "conf.yaml"),
            groupable=frozenset({"a1.py", "a2.py", "b1.py", "b2.py"}),
            pairs=frozenset(
                {
                    ("a1.py", "a2.py"),
                    ("b1.py", "b2.py"),
                    ("a1.py", "conf.yaml"),
                    ("b1.py", "conf.yaml"),
                }
            ),
            linked=frozenset({"a1.py", "a2.py", "b1.py", "b2.py"}),
        )
    )
    assert result is not None
    assert len(result.groups) == 2
