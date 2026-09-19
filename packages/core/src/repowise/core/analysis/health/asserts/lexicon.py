"""Per-language assertion vocabulary, as data, in two tiers.

The assertion pass (``complexity/assertions.py``) counts two things from one
walk, and they are deliberately not the same count:

* the **narrow** tier — a callee name starting ``assert`` or ``expect``, plus
  the language's own ``assert`` statement. It is what every shipped release has
  counted and it feeds ``assertion_blocks``, which the calibrated
  ``large_assertion_block`` / ``duplicated_assertion_block`` read. It takes
  nothing from this file and nothing from user config.
* the **broad** tier — narrow, plus the rows below and any name the repository
  configures. It feeds ``assertion_count``, read by the advisory
  ``mock_saturated_test`` and ``assertion_free_test``.
* the **verification** tier — :attr:`AssertDialect.verify_names`, counted apart
  into ``verification_count`` and never added to either count above. A mock
  verification is an oracle, so it answers "does this test check anything"; it
  is also the thing ``mock_saturated_test`` measures, so it must never enter
  that marker's denominator. Same call, opposite treatment, two questions.

That split is the reason a row here can never move a score, and it is the only
reason this file is safe to edit freely.

Names are matched **exactly**, never as prefixes. SonarQube keeps a wide list
and a narrow one for the same reason we keep two, but its wide list matches
name prefixes; measured across three test corpora, the ambiguous English verbs
in that shape (``check``, ``validate``, ``approve``, ``fail``) matched
production functions under test and no assertions at all. Measurements and
reasoning in LANGUAGE_SUPPORT.md#code-health-coverage.

Both directions of error are understood. Over-counting shrinks a saturation
ratio and can only suppress a finding; under-counting inflates it. A missing
row is the dangerous side and a slightly over-inclusive one is cheap.

**Adding a language** is one row plus a key in :data:`ASSERT_DIALECTS` — but
only when its assertions are a named call, and only when its ``LanguageNodeMap``
already opts into ``assert_call_kinds``. Two things a row will not do: it cannot
express a prefix family, so an ``assert_*``-style family must be enumerated; and
it cannot see an assertion that is not a call, so an infix DSL (ScalaTest's
``x shouldBe y``) needs a grammar change in ``complexity/`` first. A language
absent from the table keeps the narrow tier alone, which is exactly today's
behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Callee-name prefixes of the narrow tier. Matched case-insensitively against
#: every identifier in a call's callee chain. Frozen: the calibrated markers
#: count with these and nothing else.
NARROW_PREFIXES: tuple[str, ...] = ("assert", "expect")


def checks_something(fn) -> bool:
    """Whether a walked function carries an oracle of any kind.

    Three counts, because three different things fail a test: a state
    assertion, a mock verification, and a ``raise`` / ``throw`` the author
    wrote by hand. The third is not in any assertion vocabulary -- it is a
    statement, not a call -- so a helper built on ``if (!ok) throw new
    Error(...)`` reads as checking nothing unless this is asked instead of
    ``assertion_count``.

    Read by ``assertion_free_test`` and by the oracle resolution behind it,
    both of which want the boolean. No calibrated marker reads it; they count
    ``assertion_count`` alone and are unaffected by the third term.
    """
    return bool(fn.assertion_count or fn.verification_count or fn.raise_count)


@dataclass(frozen=True)
class AssertVocabulary:
    """One run's configured assertion names, and the cache key they imply.

    Built once per analysis and passed down to the walk, so nothing about a
    run's vocabulary lives on the analyzer between runs. An empty vocabulary
    keys the walk cache exactly as it was keyed before this existed.
    """

    names: frozenset[str] = frozenset()
    key: str = ""

    @classmethod
    def from_analyzer_config(cls, cfg: dict) -> AssertVocabulary:
        """Read ``assert_extra_names``, already coerced by ``HealthConfig``."""
        names = frozenset(cfg.get("assert_extra_names") or ())
        return cls(names=names, key=",".join(sorted(names)))


@dataclass(frozen=True)
class AssertDialect:
    """One language's broad-tier assertion vocabulary. Data only."""

    #: Exact lowercase names that make a call an assertion, matched against the
    #: called name *or* any receiver root. Both, because a verification reads
    #: either way round: ``verify(mock)`` is the callee, and in
    #: ``verify(mock).save()`` it is the receiver of ``save``.
    assert_names: frozenset[str] = frozenset()

    #: Receiver root -> the methods ON it that assert, for a root too generic to
    #: take whole. Go's ``t`` is the test handle: ``t.Fatalf`` asserts, ``t.Run``
    #: and ``t.Log`` do not.
    receiver_methods: dict[str, frozenset[str]] = field(default_factory=dict)

    #: Exact lowercase names that make a call a *verification* — an assertion
    #: about a recorded call on a double rather than about state. Matched the
    #: same two ways as :attr:`assert_names`. Counted into ``verification_count``
    #: alone; see the tier list in the module docstring for why it is separate.
    #: Repository config never reaches this field, so it cannot be widened.
    verify_names: frozenset[str] = frozenset()


# ``testing.TB``'s failure reporters, keyed by the conventional receiver names.
# Matched by receiver name and not by type, which the walker cannot resolve; in
# a Go test file a ``t``/``b``/``tb`` receiver is the test handle. ``Skip`` /
# ``Log`` / ``Run`` / ``Helper`` are deliberately absent.
_GO_TB_FAILURES = frozenset({"error", "errorf", "fatal", "fatalf", "fail", "failnow"})

_GO = AssertDialect(
    # testify's two halves are one API, and the narrow tier already takes
    # ``assert.Equal`` on its receiver name while nothing took ``require``.
    # Taken whole rather than by method list: the cost is a domain package of
    # the same name counted in the harmless direction, the benefit is never
    # missing a testify method.
    assert_names=frozenset({"require"}),
    receiver_methods={r: _GO_TB_FAILURES for r in ("t", "b", "tb")},
)


# Java's whole gap is Mockito verification, which is why this row sets only
# ``verify_names``: its narrow tier already takes ``assertEquals`` / ``assertThat``.
# ``verifyZeroInteractions`` was removed in Mockito 4 and is still widespread.
# BDDMockito's ``then(mock).should()`` is taken on ``should``, which reaches this
# row as a receiver root.
_JAVA = AssertDialect(
    verify_names=frozenset(
        {
            "verify",
            "verifynointeractions",
            "verifynomoreinteractions",
            "verifyzerointeractions",
            "should",
        }
    ),
)


# pytest's oracles that are context managers rather than calls. Receiver-scoped
# to ``pytest`` because ``raises`` / ``warns`` are ordinary English on anything
# else. ``unittest``'s equivalents need no row: ``assertRaises`` is already
# narrow, it was only ever the ``with`` header that hid it.
_PY = AssertDialect(
    receiver_methods={"pytest": frozenset({"raises", "warns", "deprecated_call", "fail"})},
)


_JS_TS = AssertDialect(
    # should.js / chai's BDD chain, as the receiver root of the matcher call:
    # ``result.should.equal(x)``. The property form (``x.should.be.true``) is
    # not a call and stays uncounted, an undercount in the safe direction.
    assert_names=frozenset({"should"}),
)


# Keyed by ``LanguageTag`` (``ingestion/models.py``), as ``LANGUAGE_MAPS`` and
# ``MOCK_DIALECTS`` are.
ASSERT_DIALECTS: dict[str, AssertDialect] = {
    "go": _GO,
    "java": _JAVA,
    "javascript": _JS_TS,
    "jsx": _JS_TS,
    "python": _PY,
    "typescript": _JS_TS,
    "tsx": _JS_TS,
}


def assert_dialect(
    language: str, extra_names: frozenset[str] = frozenset()
) -> AssertDialect | None:
    """The vocabulary for *language*, or ``None`` when it adds nothing to narrow.

    *extra_names* is the repository's own list, for a house assertion helper the
    conventions miss. It joins the broad tier for every language, so it can
    never move a scored marker.
    """
    dialect = ASSERT_DIALECTS.get(language)
    if not extra_names:
        return dialect
    if dialect is None:
        return AssertDialect(assert_names=extra_names)
    return AssertDialect(
        assert_names=dialect.assert_names | extra_names,
        receiver_methods=dialect.receiver_methods,
        verify_names=dialect.verify_names,
    )
