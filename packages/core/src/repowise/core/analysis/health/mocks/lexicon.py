"""Per-language mock vocabulary, as data.

The mock-saturation pass (``complexity/mock_walk.py``) is language-agnostic: every
language difference lives in a :class:`MockDialect` row of :data:`MOCK_DIALECTS`.
This mirrors the per-language plugin idiom used by ``perf/dialects/`` and
``ingestion/resolvers/`` - one entry per language, registered in a dict, zero
edits to the walker to add one - but the dialect is a frozen dataclass rather
than a subclass, because every language difference here is vocabulary and none
of it is behaviour. Adding a language is one row, plus ``block_kinds`` and
``call_kinds`` on its ``LanguageNodeMap``. Two things a row alone will not tell
you: ``file_markers`` must be set or the file is skipped before the walk and
counts zero silently, and the language needs an honest ``assert_call_kinds``,
since the assertion count is this marker's denominator. That last one is why Go
and Java have no row.

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

    #: Extra lowercase substrings that mean "this file may mock", beyond
    #: :data:`MOCK_IDENTIFIER_TOKENS`, unioned into the whole-file precheck.
    #: Over-inclusive costs time; under-inclusive counts nothing at all.
    file_markers: frozenset[str] = frozenset()

    #: Callee -> how it names the binding it replaces: ``dotted`` as one
    #: ``"pkg.mod.name"`` string, ``object`` as ``(obj, "name")``,
    #: ``dotted_or_object`` either way, ``modules`` as ``sys.modules`` keys,
    #: ``factory`` as a module specifier plus a factory for its stand-in,
    #: ``restore`` as a specifier whose stand-in it removes. Callees are
    #: spelled with any :attr:`guard_roots` prefix dropped.
    binding_patches: dict[str, str] = field(default_factory=dict)

    #: Keywords that leave the original reachable through the double, or may:
    #: a patch carrying one never counts as replacing the binding.
    passthrough_keywords: frozenset[str] = frozenset()

    #: Test-runner object -> the modules that export it under that name
    #: (``vi`` from ``vitest``), for proving a receiver is the runner's.
    runner_modules: dict[str, frozenset[str]] = field(default_factory=dict)

    #: Runner objects the runner also installs as globals (``jest``).
    runner_globals: frozenset[str] = frozenset()

    #: Runner methods that return a bare double (``vi.fn()``).
    double_callees: frozenset[str] = frozenset()

    #: Double methods that configure what the double returns or runs
    #: (``vi.fn().mockReturnValue(1)``). Chained on a bare double they keep it
    #: free of the original, provided every argument is closed too.
    double_configurers: frozenset[str] = frozenset()

    #: Runner methods whose factory runs before the file's imports and hands
    #: its value to a binding (``const { f } = vi.hoisted(() => ...)``).
    hoist_callees: frozenset[str] = frozenset()


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
    file_markers=frozenset({"patch"}),
    binding_patches={
        "patch": "dotted",
        "patch.object": "object",
        "patch.dict": "modules",
        "monkeypatch.setattr": "dotted_or_object",
        "monkeypatch.setitem": "modules",
    },
    passthrough_keywords=frozenset({"wraps", "side_effect", "autospec"}),
)


_JS_TS = MockDialect(
    # Listed by receiver because each namespace mixes doubling with test-
    # environment arrangement, the same split ``monkeypatch`` forced for Python:
    # ``vi.fn`` installs a double, ``vi.useFakeTimers`` / ``vi.stubEnv`` arrange
    # the run, ``vi.clearAllMocks`` tears down.
    receiver_methods={
        "vi": frozenset(
            {
                "fn",
                "mock",
                "domock",
                "spyon",
                "mocked",
                "mockobject",
                "hoisted",
                "stubglobal",
                "importmock",
            }
        ),
        "jest": frozenset(
            {
                "fn",
                "mock",
                "domock",
                "spyon",
                "mocked",
                "createmockfrommodule",
                "genmockfrommodule",
                "requiremock",
                "replaceproperty",
            }
        ),
        # ``sinon.fake.returns(...)`` reaches here as one call with ``fake`` in
        # the receiver chain, so the configuring verbs must be listed too: an
        # exhaustive root has no inner call to fall back on.
        "sinon": frozenset(
            {
                "stub",
                "spy",
                "fake",
                "mock",
                "createstubinstance",
                "replace",
                "replacegetter",
                "replacesetter",
                "returns",
                "resolves",
                "rejects",
                "throws",
                "yields",
            }
        ),
        "jasmine": frozenset({"createspy", "createspyobj"}),
    },
    # A JS test is an anonymous callback inside ``it(...)``, so there is no
    # name to gate on. Empty degrades to "no extra gate", which is correct.
    test_name_prefixes=(),
    # ``vi.fn()`` / ``jest.fn()`` carry no test-double token anywhere in the
    # call, so without these a file that mocks only through them is skipped.
    file_markers=frozenset({"vi.fn", "jest.fn", "sinon", "jasmine"}),
    # ``doMock`` is left out: it applies only to later dynamic imports.
    binding_patches={
        "vi.mock": "factory",
        "jest.mock": "factory",
        "vi.unmock": "restore",
        "jest.unmock": "restore",
        "vi.doUnmock": "restore",
        "jest.doUnmock": "restore",
    },
    runner_modules={"vi": frozenset({"vitest"}), "jest": frozenset({"@jest/globals"})},
    runner_globals=frozenset({"jest"}),
    double_callees=frozenset({"fn"}),
    double_configurers=frozenset(
        {
            "mockReturnValue",
            "mockReturnValueOnce",
            "mockResolvedValue",
            "mockResolvedValueOnce",
            "mockRejectedValue",
            "mockRejectedValueOnce",
            "mockImplementation",
            "mockImplementationOnce",
        }
    ),
    hoist_callees=frozenset({"hoisted"}),
)


# Keyed by ``LanguageTag`` (``ingestion/models.py``); one dialect may serve
# several tags. Go and Java are absent deliberately: neither has a trustworthy
# assertion count, and that is this marker's denominator. Measurements and
# reasoning in docs/architecture/language-support.md#test-quality-markers-per-language.
MOCK_DIALECTS: dict[str, MockDialect] = {
    "python": _PYTHON,
    "javascript": _JS_TS,
    "typescript": _JS_TS,
}


def mock_dialect(language: str) -> MockDialect | None:
    """The dialect for *language*, or ``None`` when it has no mock vocabulary."""
    return MOCK_DIALECTS.get(language)
