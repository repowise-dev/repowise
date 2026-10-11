"""Rust inline ``#[cfg(test)]`` detection feeds ``has_test_file``.

Idiomatic Rust unit tests live in a ``#[cfg(test)] mod tests`` block inside
the source file, invisible to the filename/dir test-pairing heuristic. The
walker flags these so well-tested Rust files aren't reported as untested.
"""

from __future__ import annotations

from repowise.core.analysis.health.complexity.walker import _detect_inline_tests, walk_file

RUST_WITH_INLINE_TESTS = b"""
pub fn add(a: i32, b: i32) -> i32 {
    a + b
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn it_adds() {
        assert_eq!(add(2, 2), 4);
    }
}
"""

RUST_NO_TESTS = b"""
pub fn add(a: i32, b: i32) -> i32 {
    a + b
}
"""


def test_detect_inline_tests_true_for_rust_with_cfg_test() -> None:
    assert _detect_inline_tests(RUST_WITH_INLINE_TESTS, "rust") is True


def test_detect_inline_tests_false_for_rust_without_tests() -> None:
    assert _detect_inline_tests(RUST_NO_TESTS, "rust") is False


def test_detect_inline_tests_false_for_non_rust() -> None:
    # The same bytes in another language must not trip the Rust-only scan.
    assert _detect_inline_tests(RUST_WITH_INLINE_TESTS, "python") is False


def test_walk_file_sets_has_inline_tests() -> None:
    fcx = walk_file("src/lib.rs", "rust", RUST_WITH_INLINE_TESTS)
    assert fcx.has_inline_tests is True

    fcx_no = walk_file("src/lib.rs", "rust", RUST_NO_TESTS)
    assert fcx_no.has_inline_tests is False
