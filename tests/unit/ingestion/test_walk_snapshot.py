"""Equivalence tests for WalkSnapshot vs live iter_glob.

The dynamic-hint extractor fleet issues ~40 ``iter_glob`` queries per
``extract_all``; each used to walk the tree from disk. ``WalkSnapshot``
replays one walk for all of them, and these tests pin the replay to the
live walk: same files, same directories, same order, for basename
patterns, ``/``-tail patterns, directory-name matches, and query roots
below the snapshot root.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.fs_walk import PRUNED_DIRS_DERIVED, WalkSnapshot, iter_glob


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A tree exercising pruning, nesting, dir matches, and a nested repo."""
    files = [
        "main.go",
        "go.mod",
        "settings.py",
        "pkg/a.go",
        "pkg/settings/local.py",
        "pkg/sub/deep/b.go",
        "tests/conftest.py",
        "tests/test_a.py",
        "tests/sub/test_b.py",
        "node_modules/dep/index.js",          # pruned dir
        "build/gen.go",                       # pruned (derived set)
        "vendored/.git/HEAD",                 # nested git repo
        "vendored/inner.go",
        "META-INF/services/com.example.Impl",
    ]
    for rel in files:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x", encoding="utf-8")
    # A directory named "settings" (django matches directories by name).
    (tmp_path / "app" / "settings").mkdir(parents=True)
    (tmp_path / "app" / "settings" / "base.py").write_text("x", encoding="utf-8")
    return tmp_path


QUERIES = [
    "*.go",
    "*.py",
    "settings.py",
    "settings",            # directory-name match
    "conftest.py",
    "go.mod",
    "tsconfig*.json",      # no matches
    "META-INF/services",   # /-tail pattern
    ("*.go", "*.py"),      # multi-pattern, one walk
]


class TestWalkSnapshotEquivalence:
    def test_matches_live_iter_glob_at_root(self, tree: Path) -> None:
        snap = WalkSnapshot(tree, prune_dirs=PRUNED_DIRS_DERIVED)
        for pattern in QUERIES:
            live = list(iter_glob(tree, pattern, prune_dirs=PRUNED_DIRS_DERIVED))
            replay = list(snap.iter_glob(tree, pattern))
            assert replay == live, pattern

    def test_matches_live_iter_glob_at_subroot(self, tree: Path) -> None:
        snap = WalkSnapshot(tree, prune_dirs=PRUNED_DIRS_DERIVED)
        for sub in ("tests", "pkg", "pkg/sub", "app"):
            sub_root = tree / sub
            for pattern in ("*.py", "*.go", "test_*.py", "settings"):
                live = list(iter_glob(sub_root, pattern, prune_dirs=PRUNED_DIRS_DERIVED))
                replay = list(snap.iter_glob(sub_root, pattern))
                assert replay == live, (sub, pattern)

    def test_pruned_and_nested_git_excluded(self, tree: Path) -> None:
        snap = WalkSnapshot(tree, prune_dirs=PRUNED_DIRS_DERIVED)
        hits = {p.name for p in snap.iter_glob(tree, ("*.go", "*.js"))}
        assert "index.js" not in hits   # node_modules pruned
        assert "gen.go" not in hits     # build pruned (derived set)
        assert "inner.go" not in hits   # nested git repo pruned

    def test_out_of_tree_root_falls_back_to_live_walk(self, tree: Path, tmp_path_factory) -> None:
        other = tmp_path_factory.mktemp("elsewhere")
        (other / "lone.go").write_text("x", encoding="utf-8")
        snap = WalkSnapshot(tree, prune_dirs=PRUNED_DIRS_DERIVED)
        assert [p.name for p in snap.iter_glob(other, "*.go")] == ["lone.go"]

    def test_extractor_rglob_uses_attached_snapshot(self, tree: Path) -> None:
        from repowise.core.ingestion.dynamic_hints.base import DynamicHintExtractor

        class _Probe(DynamicHintExtractor):
            name = "probe"

            def extract(self, repo_root: Path):  # pragma: no cover - unused
                return []

        probe = _Probe()
        live = list(probe._rglob(tree, "*.go"))
        probe._walk_snapshot = WalkSnapshot(tree, prune_dirs=PRUNED_DIRS_DERIVED)
        try:
            assert list(probe._rglob(tree, "*.go")) == live
        finally:
            probe._walk_snapshot = None
