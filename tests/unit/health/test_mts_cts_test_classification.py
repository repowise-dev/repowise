"""A source file's *paired* test must be found for `.mts`/`.cts` sources (#288).

This is a different question from "is this path a test?" — that one now has a
single implementation and a single corpus in ``tests/unit/test_test_paths.py``.
What remains here is the pairing rule: given ``src/foo.mts``, is there a
``src/foo.test.mts`` beside it? The per-implementation matrix this file used to
carry is gone with the implementations it named (#1103).
"""

from __future__ import annotations

from repowise.core.analysis.health.coverage import paired_test_file
from repowise.core.analysis.health.engine import _has_paired_test_file, _path_basenames


def test_paired_test_file_finds_mts_cts() -> None:
    assert paired_test_file("src/foo.ts", {"src/foo.ts", "src/foo.test.mts"}) == "src/foo.test.mts"
    assert paired_test_file("src/bar.ts", {"src/bar.ts", "src/bar.spec.cts"}) == "src/bar.spec.cts"


def test_engine_has_paired_test_file_for_mts_source() -> None:
    assert _has_paired_test_file(
        "src/foo.mts", _path_basenames({"src/foo.mts", "src/foo.test.mts"})
    )
    assert _has_paired_test_file(
        "src/bar.cts", _path_basenames({"src/bar.cts", "src/bar.spec.cts"})
    )
