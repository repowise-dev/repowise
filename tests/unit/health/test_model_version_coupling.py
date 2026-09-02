"""The identity models and the analyzer stamp have to move together.

A stored opportunity id names the model that minted it. Nothing re-mints those
ids except the re-score, and the re-score is triggered by
``HEALTH_ANALYZER_VERSION`` alone - neither identity model version is an input
to that gate. So a model bump that lands without an analyzer bump leaves every
stored id describing a model that no longer exists, on a store that will never
be told to recompute.

``REFACTORING_MODEL_VERSION`` went 1 -> 2 with no matching analyzer bump. It was
harmless only because an unrelated change had already moved the analyzer in the
same release. This pins the three so that cannot happen quietly again.
"""

from __future__ import annotations

from repowise.core.analysis.health.engine import HEALTH_ANALYZER_VERSION
from repowise.core.analysis.health.perf.causal import PERFORMANCE_MODEL_VERSION
from repowise.core.analysis.health.refactoring.identity import (
    REFACTORING_MODEL_VERSION,
)

# The three stamps as they stand together. Moving either identity model means
# moving the analyzer with it, and updating this tuple in the same commit.
_STAMPS = (8, 2, 2)


def test_the_identity_models_and_the_analyzer_stamp_are_pinned_together() -> None:
    """If you changed an identity kernel: bump the analyzer too, then ``_STAMPS``."""
    assert (
        HEALTH_ANALYZER_VERSION,
        PERFORMANCE_MODEL_VERSION,
        REFACTORING_MODEL_VERSION,
    ) == _STAMPS
