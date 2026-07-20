"""``fix_events`` row building, on real temporary repositories.

The things that go wrong here (a hunk header that predates the file, a rename,
an empty diff) are git behaviours rather than ours, and they have no useful
mock, so these build small repos and run the real thing.
"""

from __future__ import annotations

import json

import pytest

from repowise.core.ingestion.git_indexer import GitIndexer, GitIndexTier
from repowise.core.ingestion.git_indexer.fix_events import build_fix_events
from repowise.core.ingestion.git_indexer.prior_defects import collect_fix_commits


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


def _walk(tmp_path, paths):
    import git as gitpython

    return collect_fix_commits(gitpython.Repo(tmp_path), set(paths), as_of_ts=None)


def _file_diff(fix_sha: str, tmp_path, path: str):
    import git as gitpython

    from repowise.core.analysis.changed_lines import parse_unified_diff

    raw = gitpython.Repo(tmp_path).git.show("-U0", "--no-color", "--format=", fix_sha)
    return parse_unified_diff(raw)[path]


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
        rows = build_fix_events(walk)

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
        rows = build_fix_events(walk)

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

        rows = build_fix_events(_walk(tmp_path, ["a.py", "b.py", "c.py"]))
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
    # The fix replaced the return line, so the row carries the range it replaced.
    assert json.loads(row["old_ranges_json"]) == [[2, 2]]
    assert summary.fix_oldest_ts > 0
    assert summary.fix_events_built is True


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

    everything, oldest, tracked = indexer.capture_new_fix_events()
    assert {r["fix_sha"] for r in everything} == {first, second}
    assert oldest > 0
    assert tracked == {"a.py"}

    new_only, oldest_again, _ = indexer.capture_new_fix_events(known_shas={first})
    assert {r["fix_sha"] for r in new_only} == {second}
    # The cutoff is taken before the skip, so narrowing the trace must not move it.
    assert oldest_again == oldest


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
    seeded, _, _ = indexer.capture_new_fix_events()

    _write(repo, tmp_path, "a.py", "x = 3\n", "fix: second")
    incremental, _, _ = indexer.capture_new_fix_events(known_shas={r["fix_sha"] for r in seeded})
    merged = sorted(seeded + incremental, key=lambda r: (r["fix_sha"], r["file_path"]))

    summary, _ = await GitIndexer(tmp_path, tier=GitIndexTier.FULL).index_repo("repo1")
    assert merged == summary.fix_event_rows
