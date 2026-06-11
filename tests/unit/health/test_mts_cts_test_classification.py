"""`.mts`/`.cts` test files must be classified as tests across health logic (#288)."""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.biomarkers.coverage_gap import _looks_like_test_path
from repowise.core.analysis.health.biomarkers.hidden_coupling import (
    _is_test_path as _coupling_is_test_path,
)
from repowise.core.analysis.health.coverage import is_test_file, paired_test_file
from repowise.core.analysis.health.engine import (
    _has_paired_test_file,
    _path_basenames,
)
from repowise.core.analysis.health.engine import (
    _is_test_file as _engine_is_test_file,
)


@pytest.mark.parametrize("path", ["src/foo.test.mts", "src/foo.test.cts", "src/foo.spec.mts", "src/foo.spec.cts"])
def test_engine_is_test_file(path: str) -> None:
    assert _engine_is_test_file(path)


@pytest.mark.parametrize("path", ["src/foo.test.mts", "src/foo.spec.cts"])
def test_coverage_is_test_file(path: str) -> None:
    assert is_test_file(path)


@pytest.mark.parametrize("path", ["src/foo.test.mts", "src/foo.spec.cts"])
def test_hidden_coupling_is_test_path(path: str) -> None:
    assert _coupling_is_test_path(path)


@pytest.mark.parametrize("path", ["src/foo.test.mts", "src/foo.spec.cts"])
def test_coverage_gap_looks_like_test_path(path: str) -> None:
    assert _looks_like_test_path(path)


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
