"""A supplied-content source must answer exactly like the Git one.

The point of :class:`MappingRevisionSource` is that a caller with no checkout
gets the comparison a checkout gets. That holds only if the two sources are
interchangeable at the protocol boundary, so these tests mirror one against the
other over the same fixture rather than asserting either in isolation.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.change_health import (
    ChangeHealthDeltaService,
    DeltaRequest,
    FileChange,
    GitRevisionSource,
    MappingRevisionSource,
    RevisionPair,
)

from .conftest import Repo, python_complex


def _mirror(git_source: GitRevisionSource, revspec: str | None) -> MappingRevisionSource:
    """Build a supplied source carrying exactly what the Git one would serve."""
    pair = git_source.resolve(revspec)
    base_paths = [c.base_path for c in pair.changes if c.base_path]
    head_paths = [c.head_path for c in pair.changes if c.head_path]
    base = git_source.read(pair.base_sha, base_paths)
    head = (
        git_source.read_working_tree(head_paths)
        if pair.working_tree
        else git_source.read(pair.head_sha, head_paths)
    )
    return MappingRevisionSource(pair, base, head)


def _shape(pair: RevisionPair) -> list[tuple]:
    return sorted(
        (c.base_path, c.head_path, c.status, tuple(sorted(c.added_lines)), c.diff_reliability)
        for c in pair.changes
    )


# ---------------------------------------------------------------------------
# Revision-pair equivalence
# ---------------------------------------------------------------------------


def test_supplied_source_resolves_the_same_pair(make_repo):
    repo: Repo = make_repo("parity")
    repo.commit("base", {"app/a.py": python_complex("run", 2)})
    repo.commit("head", {"app/a.py": python_complex("run", 6)})

    git_source = GitRevisionSource(str(repo.path))
    supplied = _mirror(git_source, "HEAD")

    assert _shape(supplied.resolve("HEAD")) == _shape(git_source.resolve("HEAD"))


def test_supplied_source_serves_the_same_bytes(make_repo):
    repo: Repo = make_repo("parity")
    repo.commit("base", {"app/a.py": python_complex("run", 2)})
    repo.commit("head", {"app/a.py": python_complex("run", 6)})

    git_source = GitRevisionSource(str(repo.path))
    pair = git_source.resolve("HEAD")
    supplied = _mirror(git_source, "HEAD")

    for sha in (pair.base_sha, pair.head_sha):
        assert supplied.read(sha, ["app/a.py"]) == git_source.read(sha, ["app/a.py"])


@pytest.mark.parametrize("revspec", ["HEAD", None])
def test_health_delta_is_identical_across_sources(make_repo, revspec):
    """The whole comparison, not just the protocol, must agree."""
    repo: Repo = make_repo("parity")
    repo.commit("base", {"app/a.py": python_complex("run", 2)})
    repo.commit("head", {"app/a.py": python_complex("run", 9)})

    request = DeltaRequest(repo_path=str(repo.path), revspec=revspec)
    from_git = ChangeHealthDeltaService(repo_path=str(repo.path)).compare(request)

    # Same request, same fixture, but every byte arrives pre-fetched.
    git_source = GitRevisionSource(str(repo.path))
    supplied = _mirror(git_source, revspec)
    from_supplied = ChangeHealthDeltaService(supplied, repo_path=str(repo.path)).compare(request)

    assert from_supplied.status == from_git.status
    assert [f.change_finding_id for f in from_supplied.findings] == [
        f.change_finding_id for f in from_git.findings
    ]


# ---------------------------------------------------------------------------
# Change shapes that must survive the round trip
# ---------------------------------------------------------------------------


def test_deletion_keeps_its_base_content(make_repo):
    """A deletion has base content and no head content. It is not an empty change."""
    repo: Repo = make_repo("parity")
    repo.commit("base", {"app/gone.py": python_complex("run", 3)})
    repo.commit("head", {"app/kept.py": "x = 1\n"})
    repo.remove("app/gone.py")
    repo.commit("delete")

    git_source = GitRevisionSource(str(repo.path))
    supplied = _mirror(git_source, "HEAD")
    pair = supplied.resolve("HEAD")

    deleted = next(c for c in pair.changes if c.status == "deleted")
    assert deleted.is_deleted
    assert deleted.head_path is None
    assert deleted.path == "app/gone.py"
    assert supplied.read(pair.base_sha, ["app/gone.py"])["app/gone.py"]
    # Absent from head means "did not exist there" -- not b"".
    assert supplied.read(pair.head_sha, ["app/gone.py"]) == {}


def test_rename_survives_with_both_sides(make_repo):
    repo: Repo = make_repo("parity")
    repo.commit("base", {"app/old.py": python_complex("run", 4)})
    repo.move("app/old.py", "app/new.py")
    repo.commit("rename")

    git_source = GitRevisionSource(str(repo.path))
    supplied = _mirror(git_source, "HEAD")
    pair = supplied.resolve("HEAD")

    assert _shape(pair) == _shape(git_source.resolve("HEAD"))
    assert pair.rename_map() == git_source.resolve("HEAD").rename_map()


def test_delete_and_add_pair_is_two_changes(make_repo):
    repo: Repo = make_repo("parity")
    repo.commit("base", {"app/a.py": python_complex("run", 3)})
    repo.remove("app/a.py")
    repo.commit("swap", {"app/b.py": "y = 2\n"})

    supplied = _mirror(GitRevisionSource(str(repo.path)), "HEAD")
    statuses = {c.path: c.status for c in supplied.resolve("HEAD").changes}
    assert statuses == {"app/a.py": "deleted", "app/b.py": "added"}


def test_zero_new_line_change_is_not_an_unreliable_diff(make_repo):
    """Pure deletion of lines leaves no new side. That is a fact, not a gap."""
    repo: Repo = make_repo("parity")
    repo.commit("base", {"app/a.py": "a = 1\nb = 2\nc = 3\n"})
    repo.commit("head", {"app/a.py": "a = 1\n"})

    supplied = _mirror(GitRevisionSource(str(repo.path)), "HEAD")
    change = next(c for c in supplied.resolve("HEAD").changes if c.path == "app/a.py")
    assert change.added_lines == set()
    assert change.diff_reliable is True


def test_missing_blob_reads_as_absent_not_empty(make_repo):
    repo: Repo = make_repo("parity")
    repo.commit("base", {"app/a.py": python_complex("run", 2)})
    repo.commit("head", {"app/a.py": python_complex("run", 5)})

    git_source = GitRevisionSource(str(repo.path))
    pair = git_source.resolve("HEAD")
    # A source that was handed nothing for this path reports absence, exactly
    # as git cat-file does for an object that is not there.
    starved = MappingRevisionSource(pair, {}, {})
    assert starved.read(pair.base_sha, ["app/a.py"]) == {}
    assert git_source.read(pair.base_sha, ["does/not/exist.py"]) == {}


def test_binary_and_truncated_diffs_are_named_not_guessed(make_repo):
    """A provider that knows why there is no line diff says so."""
    repo: Repo = make_repo("parity")
    repo.commit("base", {"app/a.py": "a = 1\n"})
    pair = GitRevisionSource(str(repo.path)).resolve("HEAD")

    binary = FileChange("assets/logo.png", "assets/logo.png", "modified", None, "binary")
    truncated = FileChange("app/huge.py", "app/huge.py", "modified", None, "truncated")
    silent = FileChange("app/x.py", "app/x.py", "modified", None)

    assert binary.diff_reliability == "binary"
    assert truncated.diff_reliability == "truncated"
    # No claim from the provider falls back to what the diff itself shows.
    assert silent.diff_reliability == "unavailable"
    assert not any(c.diff_reliable for c in (binary, truncated, silent))
    # And a real parsed diff is still reliable.
    assert all(c.diff_reliable for c in pair.changes)


# ---------------------------------------------------------------------------
# The supplied source refuses to answer a question it was not given
# ---------------------------------------------------------------------------


def test_resolving_a_different_revspec_raises(make_repo):
    repo: Repo = make_repo("parity")
    repo.commit("base", {"app/a.py": "a = 1\n"})
    repo.commit("head", {"app/a.py": "a = 2\n"})

    supplied = _mirror(GitRevisionSource(str(repo.path)), "HEAD")
    with pytest.raises(ValueError, match="does not name this revision pair"):
        supplied.resolve("some-other-branch")


def test_reading_an_unknown_sha_raises(make_repo):
    repo: Repo = make_repo("parity")
    repo.commit("base", {"app/a.py": "a = 1\n"})
    repo.commit("head", {"app/a.py": "a = 2\n"})

    supplied = _mirror(GitRevisionSource(str(repo.path)), "HEAD")
    with pytest.raises(ValueError, match="neither side"):
        supplied.read("0" * 40, ["app/a.py"])


def test_a_commit_pair_has_no_working_tree_to_read(make_repo):
    repo: Repo = make_repo("parity")
    repo.commit("base", {"app/a.py": "a = 1\n"})
    repo.commit("head", {"app/a.py": "a = 2\n"})

    supplied = _mirror(GitRevisionSource(str(repo.path)), "HEAD")
    with pytest.raises(ValueError, match="no working tree"):
        supplied.read_working_tree(["app/a.py"])


# ---------------------------------------------------------------------------
# The MCP directive keeps its wire shape after the policy moved to core
# ---------------------------------------------------------------------------


def _clean_delta():
    from repowise.core.analysis.change_health.models import ChangeHealthDelta

    return ChangeHealthDelta(
        status="available",
        explanation="",
        base=None,
        head=None,
        comparison_basis="commit",
        fingerprint=None,
    )


def test_directive_without_a_test_block_asks_for_nothing():
    """No test block means the lane was never consulted, not that coverage is absent.

    The pre-extraction code added no action for a falsy ``tests``; treating an
    absent block as "no coverage map" would invent an instruction out of
    silence.
    """
    from repowise.server.mcp_server._change_health import directive

    for absent in (None, {}):
        assert directive(_clean_delta(), absent)["next_actions"] == []


def test_directive_still_names_a_missing_coverage_map():
    from repowise.server.mcp_server._change_health import directive

    actions = directive(_clean_delta(), {"status": "no_map"})["next_actions"]
    assert actions == ["No measured test map; run the suite covering the changed files."]


def test_directive_renders_at_most_three_tests_in_one_run_command():
    from repowise.server.mcp_server._change_health import directive

    tests = {"tests_to_run": [f"t{i}" for i in range(9)], "basis": "measured"}
    assert directive(_clean_delta(), tests)["next_actions"] == ["Run: t0 t1 t2"]
