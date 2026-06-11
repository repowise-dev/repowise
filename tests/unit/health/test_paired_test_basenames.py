"""Equivalence tests for the precomputed test-pair basename set.

``_has_paired_test_file`` used to scan every analyzed path for every
evaluated file (O(files x paths x candidates) string suffix checks; 16s
isolated on an ~2,000-file repo). It now answers from one precomputed
``_path_basenames`` set. These tests pin the new formulation to a verbatim
copy of the old scan over a corpus of tricky paths.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.analysis.health.engine import (
    _has_paired_test_file,
    _path_basenames,
)


def _reference_has_paired_test_file(rel_path: str, all_paths: set[str]) -> bool:
    """The pre-index implementation, kept verbatim as the oracle."""
    p = Path(rel_path)
    stem = p.stem
    candidates = {
        f"test_{stem}.py",
        f"{stem}_test.py",
        f"{stem}.test.ts",
        f"{stem}.test.tsx",
        f"{stem}.test.js",
        f"{stem}.test.mts",
        f"{stem}.test.cts",
        f"{stem}.spec.ts",
        f"{stem}.spec.js",
        f"{stem}.spec.mts",
        f"{stem}.spec.cts",
        f"{stem}_test.go",
    }
    return any(
        any(other.endswith("/" + c) or other == c for c in candidates)
        for other in all_paths
    )


CORPUS: set[str] = {
    # root-level pair (the `other == c` branch)
    "test_root.py",
    "root.py",
    # nested pairs (the `endswith("/" + c)` branch)
    "pkg/sub/test_mod.py",
    "pkg/sub/mod.py",
    "src/widget.ts",
    "src/widget.spec.ts",
    "cmd/server.go",
    "cmd/server_test.go",
    # near-misses that must NOT match: prefix without separator
    "pkg/xtest_near.py",
    "near.py",
    "src/footest_other.go",
    "other.go",
    # test-suffix file with no source pair
    "orphan.test.js",
    # backslash path: the old scan only recognised "/" separators, so
    # this must NOT read as a pair for windowsy.py
    "dir\\test_windowsy.py",
    "windowsy.py",
    # deep nesting + dotted stem
    "a/b/c/d/e/data.test.tsx",
    "a/b/c/d/e/data.tsx",
    "lib/parser.config.ts",
}


class TestPairedTestBasenames:
    def test_matches_reference_over_corpus(self) -> None:
        basenames = _path_basenames(CORPUS)
        for rel_path in sorted(CORPUS):
            expected = _reference_has_paired_test_file(rel_path, CORPUS)
            assert _has_paired_test_file(rel_path, basenames) == expected, rel_path

    def test_positive_and_negative_anchors(self) -> None:
        basenames = _path_basenames(CORPUS)
        # Anchors so the corpus comparison cannot silently pass on all-False.
        assert _has_paired_test_file("root.py", basenames)
        assert _has_paired_test_file("pkg/sub/mod.py", basenames)
        assert _has_paired_test_file("src/widget.ts", basenames)
        assert _has_paired_test_file("cmd/server.go", basenames)
        assert _has_paired_test_file("a/b/c/d/e/data.tsx", basenames)
        assert not _has_paired_test_file("near.py", basenames)
        assert not _has_paired_test_file("other.go", basenames)
        assert not _has_paired_test_file("lib/parser.config.ts", basenames)
        # Backslash separator was never recognised by the old scan.
        assert not _has_paired_test_file("windowsy.py", basenames)

    def test_basenames_split_on_forward_slash_only(self) -> None:
        assert _path_basenames({"a/b.py", "c.py", "d\\e.py", "x/y/z.go"}) == {
            "b.py",
            "c.py",
            "d\\e.py",
            "z.go",
        }

    def test_empty_paths(self) -> None:
        assert _path_basenames(set()) == set()
        assert not _has_paired_test_file("anything.py", set())
