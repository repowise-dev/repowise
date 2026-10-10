"""The one effort bucket every surface sizes work with."""

from __future__ import annotations

import pytest

from repowise.core.analysis.health import effort
from repowise.core.analysis.health.refactoring import registry


@pytest.mark.parametrize(
    ("nloc", "bucket"),
    [(0, "S"), (40, "S"), (41, "M"), (150, "M"), (151, "L"), (400, "L"), (401, "XL")],
)
def test_effort_bucket_ceilings_are_inclusive(nloc: int, bucket: str) -> None:
    assert effort.effort_bucket(nloc) == bucket


def test_every_bucket_has_a_weight_in_size_order() -> None:
    assert list(effort.EFFORT_WEIGHT) == ["S", "M", "L", "XL"]
    assert sorted(effort.EFFORT_WEIGHT.values()) == list(effort.EFFORT_WEIGHT.values())


def test_detectors_size_with_the_same_function() -> None:
    assert registry.effort_bucket is effort.effort_bucket
