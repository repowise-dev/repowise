"""primitive_obsession leaves test material alone: its wide signatures are fixture builders."""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.biomarkers import FileContext
from repowise.core.analysis.health.biomarkers.primitive_obsession import (
    PrimitiveObsessionDetector,
)
from repowise.core.analysis.health.complexity import FunctionComplexity

_HELPER = FunctionComplexity(
    "_add_edge", 1, 12, ccn=1, max_nesting=0, cognitive=0, nloc=10, param_count=6
)
_FILLER = FunctionComplexity("filler", 13, 90, ccn=1, max_nesting=0, cognitive=0, nloc=70)


def _ctx(path: str, is_test: bool | None) -> FileContext:
    return FileContext(
        file_path=path, language="python", nloc=80, has_test_file=False, module=None,
        is_test=is_test, all_functions=(_HELPER, _FILLER),
    )


@pytest.mark.parametrize(
    ("path", "is_test"),
    [("tests/unit/test_graph.py", True), ("tests/unit/test_graph.py", None), ("src/conftest.py", True)],
)
def test_a_helper_in_test_material_is_not_flagged(path: str, is_test: bool | None) -> None:
    assert PrimitiveObsessionDetector().detect(_ctx(path, is_test)) == []


def test_the_same_signature_in_production_code_is_flagged() -> None:
    [finding] = PrimitiveObsessionDetector().detect(_ctx("src/graph.py", False))
    assert finding.function_name == "_add_edge"
