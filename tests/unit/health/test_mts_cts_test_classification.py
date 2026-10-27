"""A source file's *paired* test must be found for `.mts`/`.cts` sources (#288).

This is a different question from "is this path a test?" — that one now has a
single implementation and a single corpus in ``tests/unit/test_test_paths.py``.
What remains here is the pairing rule: given ``src/foo.mts``, is there a
``src/foo.test.mts`` beside it? The per-implementation matrix this file used to
carry is gone with the implementations it named (#1103).
"""

from __future__ import annotations

from repowise.core.analysis.health.engine import _has_paired_test_file, _path_basenames
from repowise.core.analysis.test_reachability import tests_matching_by_name as name_match


def test_name_match_finds_mts_cts() -> None:
    foo = name_match(["src/foo.ts"], {"src/foo.ts", "src/foo.test.mts"})
    bar = name_match(["src/bar.ts"], {"src/bar.ts", "src/bar.spec.cts"})
    assert foo["src/foo.ts"].tests == ["src/foo.test.mts"]
    assert bar["src/bar.ts"].tests == ["src/bar.spec.cts"]


def test_engine_has_paired_test_file_for_mts_source() -> None:
    assert _has_paired_test_file(
        "src/foo.mts", _path_basenames({"src/foo.mts", "src/foo.test.mts"})
    )
    assert _has_paired_test_file(
        "src/bar.cts", _path_basenames({"src/bar.cts", "src/bar.spec.cts"})
    )
