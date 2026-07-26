"""`.mts`/`.cts` test files must be classified as tests across health logic (#288).

The original version of this file asserted on four implementations, one test
function each. Two further copies of the same predicate existed at the time and
were not listed, so they never received the fix: ``coverage_gradient`` and
``move_method`` still read ``foo.test.mts`` as production code. The matrix below
covers every reachable implementation instead, so a copy that is added or edited
without the `.mts`/`.cts` conventions fails here rather than in a user's repo.

New implementations still have to be added to ``_IMPLEMENTATIONS`` by hand, which
is the limit of this approach and the reason #1103 exists.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from repowise.core.analysis.health.biomarkers.coverage_gap import (
    _looks_like_test_path as _coverage_gap_looks_like_test_path,
)
from repowise.core.analysis.health.biomarkers.coverage_gradient import (
    _looks_like_test_path as _coverage_gradient_looks_like_test_path,
)
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
from repowise.core.analysis.health.refactoring.extract_helper import (
    _is_test_path as _extract_helper_is_test_path,
)
from repowise.core.analysis.health.refactoring.move_method import (
    _is_test_path as _move_method_is_test_path,
)
from repowise.core.analysis.health.refactoring.split_file import (
    _is_test_path as _split_file_is_test_path,
)

# Every implementation of "is this path a test?" reachable from health logic.
# Consolidating them is #1103; until then they all have to agree here.
_IMPLEMENTATIONS: tuple[tuple[str, Callable[[str], bool]], ...] = (
    ("engine", _engine_is_test_file),
    ("coverage_detector", is_test_file),
    ("hidden_coupling", _coupling_is_test_path),
    ("coverage_gap", _coverage_gap_looks_like_test_path),
    ("coverage_gradient", _coverage_gradient_looks_like_test_path),
    ("split_file", _split_file_is_test_path),
    ("move_method", _move_method_is_test_path),
    ("extract_helper", _extract_helper_is_test_path),
)

_MTS_CTS_TEST_PATHS = (
    "src/foo.test.mts",
    "src/foo.test.cts",
    "src/foo.spec.mts",
    "src/foo.spec.cts",
)

# Same extensions, production files: the suffix has to be the whole convention,
# not a substring, or `.mts` sources get exempted from coverage scoring wholesale.
_MTS_CTS_PRODUCTION_PATHS = (
    "src/foo.mts",
    "src/foo.cts",
    "src/testable.mts",
)


@pytest.mark.parametrize("path", _MTS_CTS_TEST_PATHS)
@pytest.mark.parametrize(("name", "is_test"), _IMPLEMENTATIONS, ids=lambda v: v)
def test_mts_cts_paths_are_tests(name: str, is_test: Callable[[str], bool], path: str) -> None:
    assert is_test(path), f"{name} does not classify {path} as a test"


@pytest.mark.parametrize("path", _MTS_CTS_PRODUCTION_PATHS)
@pytest.mark.parametrize(("name", "is_test"), _IMPLEMENTATIONS, ids=lambda v: v)
def test_mts_cts_production_paths_are_not_tests(
    name: str, is_test: Callable[[str], bool], path: str
) -> None:
    assert not is_test(path), f"{name} wrongly classifies {path} as a test"


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
