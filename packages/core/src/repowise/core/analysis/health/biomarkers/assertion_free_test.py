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

**A hand-rolled ``throw`` counts as an oracle too.** A test that ends
``if (!ok) throw new Error(...)``, and a wait helper that gives up by throwing
rather than by asserting, both fail their test on the property the author named.
No assertion vocabulary reaches either, because a ``throw`` is a statement and
the vocabularies match callee names. ``asserts/lexicon.checks_something`` is the
predicate; it is deliberately not folded into ``assertion_count``, which is
calibrated.

**This marker is advisory and never deducts.** See ``ADVISORY_DIMENSION`` in
``scoring.py``, and LANGUAGE_SUPPORT.md#code-health-coverage for the measured
per-language precision and the false positives it does not separate.
"""

from __future__ import annotations

from ..asserts.lexicon import checks_something
from ..coverage import is_test_file
from ..models import Severity
from .base import BiomarkerResult, FileContext

#: Languages the marker reports on. Go and Java are classified by
#: ``complexity/test_case.py`` and counted like any other, and are deliberately
#: absent here: both conventionally hand the oracle to a helper the test calls.
#: ``asserts/oracle_reach.py`` now resolves that across files too, and a
#: base-class method reached through ``self`` is the shape it resolves best --
#: but a Go package-level helper is still not reached, its resolution origins
#: sitting outside that pass's admitted set. Re-admitting either language is a
#: measurement of its own, on its own hand-labelled sample, not a consequence of
#: this one.
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
        oracles = _asserting_names(ctx)
        for fn in ctx.all_functions:
            # ``is_test_case`` is the whole gate. Every other test-quality
            # marker can lean on "it has assertions, so it is a test"; this one
            # is asking precisely the functions that have none.
            if not fn.is_test_case:
                continue
            if checks_something(fn):
                continue
            if fn.called_names & oracles:
                continue
            # Resolved on a call edge by ``asserts.oracle_reach``, which the
            # engine runs before this marker. Empty without a graph.
            if fn.start_line in ctx.cross_file_oracle_lines:
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


def _asserting_names(ctx: FileContext) -> frozenset[str]:
    """Names of functions in this file that do check something, lowercased.

    A test whose own assertion count is zero has still checked something if it
    handed the job to a helper that asserts, and the count is per function, so
    the helper's assertions are otherwise invisible from the test that calls
    it. That is the largest false-positive family this marker has in every
    language it reports on.

    Names only, and the whole file is one namespace: the receiver is dropped,
    so a helper on one class suppresses a same-named helper on another, and a
    method on a test-local double suppresses against a module-level function of
    that name. Both directions hide a real finding rather than invent one,
    which is the tolerable way round for an advisory marker, but neither is
    free. A helper nested inside the test body is not collected as a function
    at all, so it suppresses nothing.

    Same file only, and by name. The cross-file half of the same question --
    a shared JS fixture, an inherited base-class helper -- is answered from the
    call graph by ``asserts.oracle_reach``, which resolves an edge rather than
    a bare name and so carries none of the collisions above. What that pass
    still cannot see it lists itself; ``super().test_x(...)`` is one of them,
    the resolver having no ``super()`` receiver handling.
    """
    return frozenset(fn.name.lower() for fn in ctx.all_functions if checks_something(fn))


BIOMARKER = AssertionFreeTestDetector()
