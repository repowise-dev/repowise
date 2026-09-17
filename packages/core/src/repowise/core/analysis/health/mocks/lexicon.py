"""Per-language mock vocabulary, as data.

The mock-saturation pass (``complexity/mock_walk.py``) is language-agnostic: every
language difference lives in a :class:`MockDialect` row of :data:`MOCK_DIALECTS`.
This mirrors the per-language plugin idiom used by ``perf/dialects/`` and
``ingestion/resolvers/`` - one entry per language, registered in a dict, zero
edits to the walker to add one - but the dialect is a frozen dataclass rather
than a subclass, because every language difference here is vocabulary and none
of it is behaviour. Adding a language is one row plus, if its grammar needs
them, ``decorator_kinds`` / ``block_kinds`` on its ``LanguageNodeMap``.

A language absent from ``MOCK_DIALECTS`` produces no mock signal at all, which
is the safe default the precision-first house contract requires.

The shared identifier tokens come from Hora and Robbes, "Are Coding Agents
Generating Over-Mocked Tests? An Empirical Study", MSR '26
(doi:10.1145/3793302.3793362), whose case-insensitive identifier match reached
94% precision on 100 manually inspected commits. The taxonomy behind the token
list is Meszaros, *xUnit Test Patterns* (2007). Their heuristic answers "does
this commit introduce a mock?"; this pass asks the narrower question "is this
statement mock setup?", so the guarded-token handling below is ours, not theirs.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Test-double name tokens, matched case-insensitively as identifier
# substrings. Shared rather than per-dialect: these are the names developers
# give doubles in every language.
MOCK_IDENTIFIER_TOKENS: frozenset[str] = frozenset(
    {"dummy", "dummies", "stub", "mock", "mocks", "spy", "spies", "fake"}
)


@dataclass(frozen=True)
class MockDialect:
    """One language's mock vocabulary. Data only."""

    #: Callee names that are mock setup whatever the receiver. Names carrying
    #: a :data:`MOCK_IDENTIFIER_TOKENS` substring match on the token alone.
    setup_callees: frozenset[str] = frozenset()

    #: Names that mean mocking only in a mocking context: bare ``patch(...)``
    #: and ``mock.patch(...)`` are doubles, ``client.patch("/url")`` is HTTP.
    #: Counts with no receiver, or a receiver root in :attr:`guard_roots`.
    guarded_callees: frozenset[str] = frozenset()

    #: Receiver roots that license a :attr:`guarded_callees` match.
    guard_roots: frozenset[str] = frozenset()

    #: Receiver root -> the methods ON it that install a double, for fixtures
    #: that mix doubling with other duties. A listed root matches on these
    #: methods and nothing else.
    receiver_methods: dict[str, frozenset[str]] = field(default_factory=dict)

    #: Attributes whose assignment configures a double
    #: (``client.get.return_value = ...``), where the receiver carries no token.
    config_attributes: frozenset[str] = frozenset()

    #: Test-case name prefixes, matched case-insensitively. Empty means the
    #: language cannot tell from the name.
    test_name_prefixes: tuple[str, ...] = field(default=())


_PYTHON = MockDialect(
    # ``unittest.mock`` members with no mock token in their names.
    setup_callees=frozenset({"create_autospec", "seal"}),
    guarded_callees=frozenset({"patch"}),
    guard_roots=frozenset({"mock", "unittest"}),
    # ``monkeypatch`` doubles only through the attribute and item setters;
    # ``setenv`` / ``chdir`` arrange the process, which any test may do.
    receiver_methods={
        "monkeypatch": frozenset({"setattr", "delattr", "setitem", "delitem"}),
    },
    config_attributes=frozenset({"return_value", "side_effect"}),
    test_name_prefixes=("test",),
)


# Keyed by ``LanguageTag`` (``ingestion/models.py``); one dialect may serve
# several tags. Go stays out until it has an assertion vocabulary beyond
# testify: its ``t.Error`` tests are invisible to the assert/expect prefix
# match, which would make every ratio wrong.
MOCK_DIALECTS: dict[str, MockDialect] = {
    "python": _PYTHON,
}


def mock_dialect(language: str) -> MockDialect | None:
    """The dialect for *language*, or ``None`` when it has no mock vocabulary."""
    return MOCK_DIALECTS.get(language)
