"""The one effort bucket every surface sizes work with."""

from __future__ import annotations

import pytest

from repowise.core.analysis.health import effort


@pytest.mark.parametrize(
    ("nloc", "bucket"),
    [(0, "S"), (40, "S"), (41, "M"), (150, "M"), (151, "L"), (400, "L"), (401, "XL")],
)
def test_effort_bucket_ceilings_are_inclusive(nloc: int, bucket: str) -> None:
    assert effort.effort_bucket(nloc) == bucket


def test_every_bucket_has_a_weight_in_size_order() -> None:
    assert effort.EFFORT_ORDER == ("S", "M", "L", "XL")
    assert tuple(effort.EFFORT_WEIGHT) == effort.EFFORT_ORDER
    assert sorted(effort.EFFORT_WEIGHT.values()) == list(effort.EFFORT_WEIGHT.values())
