"""Assertion Free Test: a test case that checks nothing.

A test that runs the code under test and then asserts nothing passes whatever
that code does. It reports coverage, it goes green on a broken build, and it
never fails the refactor it was written to protect. Meszaros names the smell
**Unknown Test**; TestLint and its descendants call it **Assertionless Test**.

It is the one marker in this family that is objectively decidable. The others
ask a question of degree and need a threshold nobody has calibrated; this one
asks whether a count is zero.

**A mock verification counts as an assertion here, and this is deliberately the
opposite of ``mock_saturated_test``.** Zhu et al. (FSE 2025,
doi:10.1145/3715741) measured that mock assertions catch faults state assertions
miss -- 782 of 1,557, at a Jaccard similarity of 32% -- and that verification
concentrates where state assertion is impossible, on external resources, state
mutators and callbacks. So a test whose only oracle is ``verify(...)`` does
check something, and calling it assertion-free would be wrong. The neighbouring
marker measures verification itself and so must exclude it from its denominator.
Same call, opposite treatment, two different questions. The tiers that keep them
apart are in ``complexity/assertions.py``.

**This marker is advisory and never deducts.** See ``ADVISORY_DIMENSION`` in
``scoring.py``, and LANGUAGE_SUPPORT.md#code-health-coverage for the measured
per-language precision and the false positives it does not separate.
"""

from __future__ import annotations

from ..coverage import is_test_file
from ..models import Severity
from .base import BiomarkerResult, FileContext

#: Languages the marker reports on. Go and Java are classified by
#: ``complexity/test_case.py`` and counted like any other, and are deliberately
#: absent here: both conventionally hand the oracle to a helper the test calls,
#: and the assertion count is per function, so the helper's assertions are
#: invisible from its caller. On Go that is not a false-positive family the
#: marker could declare and live with, it is nearly the whole population.
#: Figures and the reasoning: LANGUAGE_SUPPORT.md#code-health-coverage.
SHIPPING_LANGUAGES = frozenset({"javascript", "python", "tsx", "typescript"})


class AssertionFreeTestDetector:
    name = "assertion_free_test"
    category = "test_quality"

    def detect(self, ctx: FileContext) -> list[BiomarkerResult]:
        if ctx.language not in SHIPPING_LANGUAGES:
            return []
        if not is_test_file(ctx.file_path):
            return []
        out: list[BiomarkerResult] = []
        # ``all_functions``, not ``function_metrics``: name-keying collapses a
        # file's anonymous ``it`` callbacks into one row. See ``FileContext``.
        for fn in ctx.all_functions:
            # ``is_test_case`` is the whole gate. Every other test-quality
            # marker can lean on "it has assertions, so it is a test"; this one
            # is asking precisely the functions that have none.
            if not fn.is_test_case:
                continue
            if fn.assertion_count or fn.verification_count:
                continue
            out.append(
                BiomarkerResult(
                    biomarker_type=self.name,
                    severity=Severity.LOW,
                    function_name=fn.name,
                    line_start=fn.start_line,
                    line_end=fn.end_line,
                    details={"function": fn.name, "nloc": fn.nloc},
                    reason=f"{fn.name} is a test case that asserts nothing",
                )
            )
        return out


BIOMARKER = AssertionFreeTestDetector()
