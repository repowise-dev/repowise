"""SZZ tracing and ``fix_events`` row building, on real temporary repositories.

Blame is the whole point of this pass, and blame has no useful mock: the things
that go wrong (``-M`` following a move, ``--ignore-rev`` walking through a
refactor, a range that predates the file) are git behaviours, not ours. So these
build small repos and run the real thing.
"""

from __future__ import annotations

import json

import pytest

from repowise.core.ingestion.git_indexer import GitIndexer, GitIndexTier
from repowise.core.ingestion.git_indexer.fix_events import build_fix_events
from repowise.core.ingestion.git_indexer.prior_defects import collect_fix_commits
from repowise.core.ingestion.git_indexer.szz import SzzTracer, rank_candidates


def _write(repo, tmp_path, name, content, message):
    (tmp_path / name).write_text(content)
    repo.index.add([name])
    return repo.index.commit(message).hexsha


def _helpers(count: int = 6) -> str:
    """A block big enough for git's copy detection to recognise when it moves.

    Copy detection matches on content, so a two-line function is below its
    threshold and a move of it is indistinguishable from authorship. Real
    refactors move real blocks; the fixtures have to as well.
    """
    return "".join(
        f"def helper_{i}(alpha, beta):\n    return alpha + beta + {i}\n\n" for i in range(count)
    )


def _repo(tmp_path):
    import git as gitpython

    return gitpython.Repo.init(tmp_path)


def _tracer(tmp_path, *, refactor_aware: bool = True) -> SzzTracer:
    import git as gitpython

    return SzzTracer(
        lambda: gitpython.Repo(tmp_path), refactor_aware=refactor_aware
    )


def _walk(tmp_path, paths):
    import git as gitpython

    return collect_fix_commits(gitpython.Repo(tmp_path), set(paths), as_of_ts=None)


def _file_diff(fix_sha: str, tmp_path, path: str):
    import git as gitpython

    from repowise.core.analysis.changed_lines import parse_unified_diff

    raw = gitpython.Repo(tmp_path).git.show("-U0", "--no-color", "--format=", fix_sha)
    return parse_unified_diff(raw)[path]


# ---------------------------------------------------------------------------
# The blame itself
# ---------------------------------------------------------------------------


class TestSzzTracer:
    def test_blames_the_commit_that_wrote_the_fixed_line(self, tmp_path) -> None:
        repo = _repo(tmp_path)
        _write(repo, tmp_path, "a.py", "def f():\n    return 1\n", "feat: add f")
        culprit = _write(
            repo, tmp_path, "a.py", "def f():\n    return None\n", "feat: return nothing"
        )
        fix = _write(repo, tmp_path, "a.py", "def f():\n    return 0\n", "fix: bad return")

        cands = _tracer(tmp_path).trace_file(fix, "a.py", _file_diff(fix, tmp_path, "a.py"))

        assert cands, "expected an inducing candidate"
        assert cands[0].sha == culprit
        assert cands[0].lines == 1
        assert cands[0].ts > 0

    def test_pure_insertion_is_blamed_at_its_anchor(self, tmp_path) -> None:
        """A fix that only ADDS lines has no deleted line, so blame the anchor.

        A guard or an early return deletes nothing, which on squash-merge repos
        is a fifth of all fixes. The code it was inserted next to is still the
        evidence available, and the row stays distinguishable downstream by its
        empty ``old_ranges``.
        """
        repo = _repo(tmp_path)
        _write(repo, tmp_path, "a.py", "def f():\n    return 1\n", "feat: add f")
        anchor_author = _write(
            repo, tmp_path, "a.py", "def f():\n    x = 2\n    return 1\n", "feat: add x"
        )
        fix = _write(
            repo, tmp_path, "a.py", "def f():\n    x = 2\n    y = 3\n    return 1\n", "fix: add y"
        )

        diff = _file_diff(fix, tmp_path, "a.py")
        assert diff.old_ranges == []
        assert diff.insert_anchors == [2]
        cands = _tracer(tmp_path).trace_file(fix, "a.py", diff)
        assert cands and cands[0].sha == anchor_author

    def test_a_brand_new_file_has_nothing_to_blame(self, tmp_path) -> None:
        """The anchor fallback cannot invent history that does not exist yet."""
        repo = _repo(tmp_path)
        _write(repo, tmp_path, "a.py", "x = 1\n", "feat: add a")
        fix = _write(repo, tmp_path, "b.py", "y = 2\n", "fix: add b")

        assert _tracer(tmp_path).trace_file(fix, "b.py", _file_diff(fix, tmp_path, "b.py")) == []

    def test_comment_only_deletions_are_skipped(self, tmp_path) -> None:
        repo = _repo(tmp_path)
        _write(repo, tmp_path, "a.py", "# note\nx = 1\n", "feat: add a")
        fix = _write(repo, tmp_path, "a.py", "# fixed note\nx = 1\n", "fix: comment")

        assert _tracer(tmp_path).trace_file(fix, "a.py", _file_diff(fix, tmp_path, "a.py")) == []

    def test_oversized_file_diff_is_skipped(self, tmp_path) -> None:
        """A mass rewrite blames as 'everyone, everywhere' and is left untraced."""
        repo = _repo(tmp_path)
        _write(repo, tmp_path, "a.py", "".join(f"x{i} = {i}\n" for i in range(400)), "feat: add a")
        fix = _write(
            repo, tmp_path, "a.py", "".join(f"y{i} = {i}\n" for i in range(400)), "fix: rewrite"
        )

        assert _tracer(tmp_path).trace_file(fix, "a.py", _file_diff(fix, tmp_path, "a.py")) == []

    def test_refactor_aware_blame_walks_through_a_cross_file_move(self, tmp_path) -> None:
        """A commit that only relocated the line must not be named as its author.

        This is the failure mode the frozen judgment set was made of: every one
        of its 14 wrong calls was a behaviour-preserving refactor that inherited
        the buggy line by moving it. ``-M`` covers moves inside one file; lifting
        a block from one file into another is what it cannot follow, so that is
        what this builds.
        """
        block = _helpers()
        repo = _repo(tmp_path)
        _write(repo, tmp_path, "old.py", "keep = 1\n", "feat: add old")
        culprit = _write(repo, tmp_path, "old.py", "keep = 1\n\n\n" + block, "feat: add helpers")
        # The block moves to new.py verbatim. Across the commit, nothing is written.
        (tmp_path / "old.py").write_text("keep = 1\n")
        (tmp_path / "new.py").write_text(block)
        repo.index.add(["old.py", "new.py"])
        mover = repo.index.commit("refactor: split out the helpers").hexsha
        fix = _write(
            repo,
            tmp_path,
            "new.py",
            block.replace("alpha + beta + 3\n", "alpha + beta + 33\n"),
            "fix: off by one",
        )

        diff = _file_diff(fix, tmp_path, "new.py")
        naive = _tracer(tmp_path, refactor_aware=False).trace_file(fix, "new.py", diff)
        aware = _tracer(tmp_path, refactor_aware=True).trace_file(fix, "new.py", diff)

        assert naive[0].sha == mover, "baseline should blame the refactor"
        assert aware[0].sha == culprit, "refactor-aware blame should reach the real author"
        assert all(c.sha != mover for c in aware)

    def test_a_commit_that_edits_while_moving_keeps_the_blame(self, tmp_path) -> None:
        """The multiset test is the guard: change a line and you own it."""
        block = _helpers()
        repo = _repo(tmp_path)
        _write(repo, tmp_path, "old.py", "keep = 1\n", "feat: add old")
        _write(repo, tmp_path, "old.py", "keep = 1\n\n\n" + block, "feat: add helpers")
        (tmp_path / "old.py").write_text("keep = 1\n")
        (tmp_path / "new.py").write_text(block.replace("beta + 3\n", "beta + 99\n"))
        repo.index.add(["old.py", "new.py"])
        editor = repo.index.commit("refactor: split out the helpers, and tweak one").hexsha
        fix = _write(
            repo, tmp_path, "new.py", block.replace("beta + 3\n", "beta + 4\n"), "fix: off by one"
        )

        aware = _tracer(tmp_path).trace_file(fix, "new.py", _file_diff(fix, tmp_path, "new.py"))
        assert aware[0].sha == editor

    def test_missing_file_at_parent_yields_no_candidates(self, tmp_path) -> None:
        """A fix commit that also creates the file has nothing to blame at ``fix^``."""
        repo = _repo(tmp_path)
        _write(repo, tmp_path, "a.py", "x = 1\n", "feat: add a")
        fix = _write(repo, tmp_path, "b.py", "y = 2\n", "fix: add b")

        import git as gitpython

        from repowise.core.analysis.changed_lines import FileDiff

        gitpython.Repo(tmp_path)  # sanity: repo is readable
        fabricated = FileDiff(path="b.py", old_ranges=[(1, 1)], removed=["y = 1"])
        assert _tracer(tmp_path).trace_file(fix, "b.py", fabricated) == []


class TestRankCandidates:
    def test_overlap_ranking_prefers_the_most_blamed_lines(self) -> None:
        ranked = rank_candidates({"a" * 40: (1, 100), "b" * 40: (5, 200)})
        assert [c.sha[0] for c in ranked] == ["b", "a"]

    def test_earliest_ranking_prefers_the_oldest_commit(self) -> None:
        ranked = rank_candidates({"a" * 40: (1, 100), "b" * 40: (5, 200)}, by="earliest")
        assert [c.sha[0] for c in ranked] == ["a", "b"]

    def test_ranking_is_total_and_deterministic(self) -> None:
        """Equal overlap and equal time still order the same way every run."""
        totals = {"b" * 40: (2, 50), "a" * 40: (2, 50)}
        assert rank_candidates(totals) == rank_candidates(dict(reversed(list(totals.items()))))


# ---------------------------------------------------------------------------
# Row building
# ---------------------------------------------------------------------------


class TestBuildFixEvents:
    def test_one_row_per_fix_commit_and_file(self, tmp_path) -> None:
        import git as gitpython

        repo = _repo(tmp_path)
        _write(repo, tmp_path, "a.py", "x = 1\n", "feat: add a")
        (tmp_path / "b.py").write_text("y = 1\n")
        (tmp_path / "a.py").write_text("x = 2\n")
        repo.index.add(["a.py", "b.py"])
        repo.index.commit("fix: correct both")

        walk = _walk(tmp_path, ["a.py", "b.py"])
        rows = build_fix_events(walk, lambda: gitpython.Repo(tmp_path))

        assert [r["file_path"] for r in rows] == ["a.py", "b.py"]
        assert {r["shape_kind"] for r in rows} == {"code_fix"}
        assert all(r["committed_at"] is not None for r in rows)
        # a.py replaced a line, so it carries an old-side range; b.py is new.
        by_path = {r["file_path"]: r for r in rows}
        assert json.loads(by_path["a.py"]["old_ranges_json"]) == [[1, 1]]
        assert json.loads(by_path["b.py"]["old_ranges_json"]) == []
        assert by_path["a.py"]["changed_loc"] == 2

    def test_non_code_fixes_keep_their_row_but_are_never_blamed(self, tmp_path) -> None:
        import git as gitpython

        repo = _repo(tmp_path)
        _write(repo, tmp_path, "README.md", "hello\n", "docs: add readme")
        _write(repo, tmp_path, "README.md", "hello there\n", "fix: wrong wording")

        walk = _walk(tmp_path, ["README.md"])
        rows = build_fix_events(walk, lambda: gitpython.Repo(tmp_path))

        assert len(rows) == 1
        assert rows[0]["shape_kind"] == "doc_only"
        assert rows[0]["inducing_shas_json"] == "[]"

    def test_rows_are_ordered_deterministically(self, tmp_path) -> None:
        import git as gitpython

        repo = _repo(tmp_path)
        for name in ("c.py", "b.py", "a.py"):
            _write(repo, tmp_path, name, "x = 1\n", f"feat: add {name}")
        for name in ("c.py", "b.py", "a.py"):
            (tmp_path / name).write_text("x = 2\n")
        repo.index.add(["a.py", "b.py", "c.py"])
        repo.index.commit("fix: bump all three")

        rows = build_fix_events(_walk(tmp_path, ["a.py", "b.py", "c.py"]), lambda: gitpython.Repo(tmp_path))
        assert rows == sorted(rows, key=lambda r: (r["fix_sha"], r["file_path"]))


# ---------------------------------------------------------------------------
# Full index + incremental capture
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_index_repo_emits_fix_events_and_window_boundary(tmp_path) -> None:
    repo = _repo(tmp_path)
    _write(repo, tmp_path, "a.py", "def f():\n    return None\n", "feat: add f")
    _write(repo, tmp_path, "a.py", "def f():\n    return 0\n", "fix: bad return")

    summary, _ = await GitIndexer(tmp_path, tier=GitIndexTier.FULL).index_repo("repo1")

    assert len(summary.fix_event_rows) == 1
    row = summary.fix_event_rows[0]
    assert row["file_path"] == "a.py"
    assert json.loads(row["inducing_shas_json"])[0]["lines"] == 1
    assert summary.fix_window_start_ts > 0


@pytest.mark.asyncio
async def test_capture_new_fix_events_skips_known_commits(tmp_path) -> None:
    """The update path traces only the fix commits it has not already stored.

    Skipping by sha rather than by timestamp is what makes an update converge on
    a full index: this asserts the known commit is gone and the new one arrives,
    with the window boundary still reported so the caller can prune.
    """
    repo = _repo(tmp_path)
    _write(repo, tmp_path, "a.py", "x = 1\n", "feat: add a")
    first = _write(repo, tmp_path, "a.py", "x = 2\n", "fix: first")
    second = _write(repo, tmp_path, "a.py", "x = 3\n", "fix: second")

    indexer = GitIndexer(tmp_path, tier=GitIndexTier.FULL)

    everything, boundary = indexer.capture_new_fix_events()
    assert {r["fix_sha"] for r in everything} == {first, second}
    assert boundary > 0

    new_only, boundary_again = indexer.capture_new_fix_events(known_shas={first})
    assert {r["fix_sha"] for r in new_only} == {second}
    assert boundary_again == boundary


@pytest.mark.asyncio
async def test_update_capture_matches_a_full_index(tmp_path) -> None:
    """Seed at an old commit, step forward, and land on the full-index answer.

    The miniature of ``validate_p2_incremental.py``: whatever route the rows
    take, the set has to be the one a fresh index at this HEAD would produce.
    """
    repo = _repo(tmp_path)
    _write(repo, tmp_path, "a.py", "x = 1\n", "feat: add a")
    _write(repo, tmp_path, "a.py", "x = 2\n", "fix: first")
    indexer = GitIndexer(tmp_path, tier=GitIndexTier.FULL)
    seeded, _ = indexer.capture_new_fix_events()

    _write(repo, tmp_path, "a.py", "x = 3\n", "fix: second")
    incremental, _ = indexer.capture_new_fix_events(known_shas={r["fix_sha"] for r in seeded})
    merged = sorted(seeded + incremental, key=lambda r: (r["fix_sha"], r["file_path"]))

    summary, _ = await GitIndexer(tmp_path, tier=GitIndexTier.FULL).index_repo("repo1")
    assert merged == summary.fix_event_rows
