"""Blame ownership must survive the removal of the second blame pass.

``index_file`` used to run ``git blame`` twice for files below the
function-blame commit floor: the porcelain pass (returning an empty index)
and gitpython's ``repo.blame`` object parse, just for ownership. Ownership
now derives from the single porcelain pass for every file; the commit floor
only gates whether the BlameIndex is retained. These tests pin the derived
ownership to the legacy gitpython computation and the floor semantics.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.ingestion.git_indexer.enrich import get_blame_ownership
from repowise.core.ingestion.git_indexer.file_history import index_file
from repowise.core.ingestion.git_indexer.function_blame import (
    build_blame_index,
    ownership_from_blame,
)


@pytest.fixture
def repo(tmp_path: Path):
    import git as gitpython

    r = gitpython.Repo.init(tmp_path)
    alice = gitpython.Actor("Alice", "alice@example.com")
    bob = gitpython.Actor("Bob", "bob@example.com")

    # sparse.py: 2 commits (below the 5-commit floor), alice owns 3/4 lines.
    (tmp_path / "sparse.py").write_text("a1\na2\na3\n")
    r.index.add(["sparse.py"])
    r.index.commit("feat: alice writes sparse", author=alice, committer=alice)
    (tmp_path / "sparse.py").write_text("a1\na2\na3\nb1\n")
    r.index.add(["sparse.py"])
    r.index.commit("feat: bob appends one line", author=bob, committer=bob)

    # hot.py: 5 commits (meets the floor), alternating authors.
    (tmp_path / "hot.py").write_text("l1\n")
    r.index.add(["hot.py"])
    r.index.commit("feat: line 1", author=alice, committer=alice)
    for i, author in enumerate((bob, alice, bob, alice), start=2):
        content = "".join(f"l{j}\n" for j in range(1, i + 1))
        (tmp_path / "hot.py").write_text(content)
        r.index.add(["hot.py"])
        r.index.commit(f"feat: line {i}", author=author, committer=author)

    yield r, tmp_path
    r.close()


class TestSingleBlamePassOwnership:
    def test_porcelain_ownership_matches_legacy_gitpython(self, repo) -> None:
        r, root = repo
        for path in ("sparse.py", "hot.py"):
            legacy = get_blame_ownership(r, path)
            idx = build_blame_index(r, path, repo_path=root)
            derived = ownership_from_blame(idx)
            assert derived[0] == legacy[0], path          # name
            assert derived[1] == legacy[1], path          # email
            assert derived[2] == pytest.approx(legacy[2]), path  # share

    def test_sparse_file_gets_ownership_but_no_blame_index(self, repo) -> None:
        r, root = repo
        meta = index_file(
            r, "sparse.py", repo_path=root, commit_limit=500,
            follow_renames=False, include_blame=True,
        )
        assert meta["primary_owner_name"] == "Alice"
        assert meta["primary_owner_email"] == "alice@example.com"
        assert meta["primary_owner_commit_pct"] == pytest.approx(0.75)
        assert "blame_index" not in meta

    def test_hot_file_retains_blame_index(self, repo) -> None:
        r, root = repo
        meta = index_file(
            r, "hot.py", repo_path=root, commit_limit=500,
            follow_renames=False, include_blame=True,
        )
        assert "blame_index" in meta
        assert meta["blame_index"].lines
        assert meta["primary_owner_name"] == "Alice"  # 3 of 5 lines

    def test_essential_tier_skips_blame_entirely(self, repo) -> None:
        r, root = repo
        meta = index_file(
            r, "sparse.py", repo_path=root, commit_limit=500,
            follow_renames=False, include_blame=False,
        )
        assert "blame_index" not in meta
        # Commit-author fallback ownership still present (2 commits: alice, bob).
        assert meta["primary_owner_name"] in ("Alice", "Bob")
