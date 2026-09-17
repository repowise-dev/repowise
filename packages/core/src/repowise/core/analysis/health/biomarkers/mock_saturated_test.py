"""Mock Saturated Test: a test that mostly configures doubles.

A test built almost entirely of mock setup, with a single assertion at the end,
passes whatever the code under test does. It pins the shape of the collaboration
the test itself wired up, so it goes green on code that is broken and stays
green through the refactor that should have failed it.

Fires on a **ratio**, never a count: a test with twelve doubles and twelve
assertions is exercising a genuinely wide surface and is not the target. The
target is a test whose setup dwarfs what it checks.

**This marker is advisory and never deducts.** No defect corpus labels mock
density, so there is no calibration story for it and there will not be one.
See ``ADVISORY_DIMENSION`` in ``scoring.py``, and CODE_HEALTH.md for the
measured precision and the false positives it does not separate.

The vocabulary comes from Hora and Robbes, MSR '26
(doi:10.1145/3793302.3793362). The paper defines no over-mocking threshold and
names it future work, so the thresholds here are ours. Its 94% precision
answers "does this commit introduce a mock?", which is an easier question than
this one; never quote it for this marker.
"""

from __future__ import annotations

from ..coverage import is_test_file
from ..mocks.lexicon import mock_dialect
from ..models import Severity
from .base import BiomarkerResult, FileContext


class MockSaturatedTestDetector:
    name = "mock_saturated_test"
    category = "test_quality"

    # Hand-labelled over 32 findings: 44% at a floor of 4, 67% at 6. Precision
    # follows the floor, not the ratio, so the floor does the work and the ratio
    # only keeps wide integration tests out. Raise them rather than lower them.
    _MIN_MOCK_SETUP = 6
    _MIN_RATIO = 3.0
    _HIGH_RATIO = 8.0  # setup:assertion at which the test is nearly all scaffolding

    def detect(self, ctx: FileContext) -> list[BiomarkerResult]:
        if not is_test_file(ctx.file_path):
            return []
        prefixes = _name_prefixes(ctx.language)
        out: list[BiomarkerResult] = []
        for fn in ctx.function_metrics.values():
            mocks = fn.mock_setup_count
            asserts = fn.assertion_count
            # No assertion at all means a fixture or a helper, not a saturated
            # test. A mock factory with no assertions is correct code.
            if mocks < self._MIN_MOCK_SETUP or asserts < 1:
                continue
            if prefixes and not fn.name.lower().startswith(prefixes):
                continue
            ratio = mocks / asserts
            if ratio < self._MIN_RATIO:
                continue
            severity = Severity.MEDIUM if ratio >= self._HIGH_RATIO else Severity.LOW
            out.append(
                BiomarkerResult(
                    biomarker_type=self.name,
                    severity=severity,
                    function_name=fn.name,
                    line_start=fn.start_line,
                    line_end=fn.end_line,
                    details={
                        "function": fn.name,
                        "mock_setup_count": mocks,
                        "assertion_count": asserts,
                        "ratio": round(ratio, 2),
                    },
                    reason=(
                        f"{fn.name} runs {mocks} mock-setup statements "
                        f"against {asserts} assertion{'s' if asserts != 1 else ''}"
                    ),
                )
            )
        return out


def _name_prefixes(language: str) -> tuple[str, ...]:
    """The dialect's test-name prefixes, or ``()`` when it declares none.

    Empty is the honest answer for a language whose tests are anonymous
    callbacks; the detector then leans on its assertion gate alone.
    """
    dialect = mock_dialect(language)
    return dialect.test_name_prefixes if dialect is not None else ()


BIOMARKER = MockSaturatedTestDetector()
