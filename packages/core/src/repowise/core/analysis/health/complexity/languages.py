"""Per-language tree-sitter control-flow node-type maps.

The walker is language-agnostic — language differences live entirely in
the dicts below. Each entry maps that language's tree-sitter node-type
names to the walker's abstract categories:

- ``BRANCH``    — adds 1 to CCN, 1 to nesting, 1 + nesting to cognitive.
- ``LOOP``      — same as BRANCH.
- ``TRY``       — adds 1 to CCN per ``catch``/``except`` clause; the
                  ``TRY`` block itself only opens a nesting level.
- ``CATCH``     — adds 1 to CCN, 1 to nesting; the catch clause.
- ``SWITCH``    — case dispatch root; counts each case as a branch.
- ``CASE``      — adds 1 to CCN, 1 to nesting per case.
- ``BOOLEAN_OP``— adds 1 to CCN for each ``&&``/``||`` operator. Does
                  not affect nesting.
- ``FUNCTION``  — function/method definition (walker entry point).
- ``LAMBDA``    — anonymous function. Treated as ``FUNCTION`` for
                  nested-walker recursion but does not emit its own
                  ``FunctionComplexity``.
- ``CLASS``     — (optional) node type(s) that group methods for
                  class-level metrics (LCOM4 / god-class). Opt-in per
                  language via ``class_kinds`` / ``self_identifiers`` /
                  ``member_access_kinds`` — see the dataclass below.
- ``ASSERT``    — (optional) statement / call node type(s) used to detect
                  test-assertion runs (test-quality smells). Opt-in per
                  language via ``assert_kinds`` / ``assert_call_kinds``.

Control-flow maps cover all nine full-tier languages — Python, TypeScript,
JavaScript, Go, Java, Kotlin, Rust, C++, C# — plus their aliases; class-level
maps cover all of those except Go (no class-grouping node). Adding a language —
either tier — is purely additive here.

Two cross-language heuristic limits worth noting (both degrade to "no signal",
never a false positive): (1) instance members accessed without an explicit
receiver (idiomatic Kotlin/C++/C#/Java bare ``field`` rather than
``this.field``) are not counted toward LCOM4 cohesion, so ``low_cohesion``
stays silent on receiver-less code; (2) flat ``switch``/``when``/``match``
arms count once for the dispatch, not per arm.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LanguageNodeMap:
    function_kinds: frozenset[str]
    lambda_kinds: frozenset[str]
    branch_kinds: frozenset[str]
    loop_kinds: frozenset[str]
    try_kinds: frozenset[str]
    catch_kinds: frozenset[str]
    switch_kinds: frozenset[str]
    case_kinds: frozenset[str]
    boolean_operator_kinds: frozenset[str]
    # Some languages place && / || as the *type* of the binary operator
    # node itself (Rust: ``&&`` is the operator text inside a
    # ``binary_expression``). For those, ``boolean_operator_kinds`` is
    # empty and we sniff operator text via this set of node types whose
    # text content equals ``&&`` or ``||``.
    boolean_operator_text_kinds: frozenset[str] = frozenset()

    # ------------------------------------------------------------------
    # Class-level analysis (LCOM4 / god-class). All three fields default
    # to empty, which makes class-level metrics OPT-IN per language:
    #
    #   * ``class_kinds`` empty  → no classes are emitted for this
    #     language at all (e.g. Go, where methods attach to types via an
    #     external receiver rather than nesting in a class body).
    #   * ``self_identifiers`` / ``member_access_kinds`` empty or wrong →
    #     no self/this member references are detected, so the LCOM4
    #     computation falls back to the "no signal" value (``lcom4 = 1``)
    #     rather than guessing. This is the safety valve that keeps the
    #     ``low_cohesion`` biomarker from false-firing on a language whose
    #     member-access node type we have not yet mapped.
    #
    # To add class-level support for a new language: set ``class_kinds``
    # to the node type(s) that group methods (a class body, or Rust's
    # ``impl`` block), ``self_identifiers`` to the receiver token(s) that
    # denote the instance (``self`` / ``this`` / ``$this`` / ``cls``), and
    # ``member_access_kinds`` to the node type(s) for ``receiver.member``
    # access. The receiver and member-name children are pulled out by the
    # generic field-name probe in ``walker._self_member_name`` (it tries
    # the ``object``/``value`` and ``property``/``attribute``/``field``/
    # ``name`` fields, then falls back to positional children), so most
    # tree-sitter grammars need only the node-type names below.

    # Node types that group methods into a cohesive unit for LCOM4.
    class_kinds: frozenset[str] = frozenset()
    # Receiver tokens denoting "this instance" (text-matched).
    self_identifiers: frozenset[str] = frozenset()
    # Node types representing ``receiver.member`` / ``receiver->member``
    # access (both field reads and method calls — both count as a member
    # reference for cohesion).
    member_access_kinds: frozenset[str] = frozenset()

    # ------------------------------------------------------------------
    # Assertion detection (test-quality smells). Both fields default to
    # empty, making assertion-block detection OPT-IN per language:
    #
    #   * ``assert_kinds`` — statement node types that ARE assertions on
    #     their own (Python/Java ``assert_statement``).
    #   * ``assert_call_kinds`` — call node types to inspect for an
    #     assertion *call* (``assertEqual`` / ``expect`` / ``assert_eq!``).
    #     A statement counts as an assertion when its expression is a call
    #     of one of these kinds whose callee name starts with ``assert`` or
    #     ``expect`` (see ``walker._ASSERT_CALL_PREFIXES``).
    #
    # Consumed by ``large_assertion_block`` / ``duplicated_assertion_block``
    # (both fire only on test files). A language that maps neither field
    # simply produces no assertion blocks — never a false positive.
    assert_kinds: frozenset[str] = frozenset()
    assert_call_kinds: frozenset[str] = frozenset()

    # ------------------------------------------------------------------
    # Performance pass (io_in_loop / string_concat_in_loop /
    # blocking_sync_in_async). Both fields default to empty, making the
    # perf pass OPT-IN per language:
    #
    #   * ``call_kinds`` — node type(s) for a call expression (Python
    #     ``call``; JS/TS ``call_expression``). The perf walker needs these
    #     to find execution sinks; a language that maps none produces no
    #     perf hits (never a false positive).
    #   * ``async_function_kinds`` — function node type(s) that are
    #     *syntactically* async (Python ``async_function_definition``). Used
    #     by ``blocking_sync_in_async``. Languages whose async-ness is a
    #     modifier token on a shared node type (TS ``async`` arrow/function)
    #     are additionally sniffed for an ``async`` child token by the
    #     walker, so this set only needs the dedicated async node types.
    call_kinds: frozenset[str] = frozenset()
    async_function_kinds: frozenset[str] = frozenset()


_PY = LanguageNodeMap(
    function_kinds=frozenset({"function_definition", "async_function_definition"}),
    lambda_kinds=frozenset({"lambda"}),
    branch_kinds=frozenset({"if_statement", "elif_clause", "conditional_expression"}),
    loop_kinds=frozenset({"for_statement", "while_statement"}),
    try_kinds=frozenset({"try_statement"}),
    catch_kinds=frozenset({"except_clause"}),
    switch_kinds=frozenset({"match_statement"}),
    case_kinds=frozenset({"case_clause"}),
    boolean_operator_kinds=frozenset({"boolean_operator"}),
    class_kinds=frozenset({"class_definition"}),
    self_identifiers=frozenset({"self", "cls"}),
    member_access_kinds=frozenset({"attribute"}),
    # ``assert x == y`` is a bare statement; ``self.assertEqual(...)`` is a
    # call.
    assert_kinds=frozenset({"assert_statement"}),
    assert_call_kinds=frozenset({"call"}),
    call_kinds=frozenset({"call"}),
    async_function_kinds=frozenset({"async_function_definition"}),
)

_TS = LanguageNodeMap(
    function_kinds=frozenset(
        {
            "function_declaration",
            "method_definition",
            "function_expression",
            "generator_function_declaration",
            "generator_function",
        }
    ),
    lambda_kinds=frozenset({"arrow_function"}),
    branch_kinds=frozenset({"if_statement", "ternary_expression"}),
    loop_kinds=frozenset(
        {
            "for_statement",
            "for_in_statement",
            "for_of_statement",
            "while_statement",
            "do_statement",
        }
    ),
    try_kinds=frozenset({"try_statement"}),
    catch_kinds=frozenset({"catch_clause"}),
    switch_kinds=frozenset({"switch_statement"}),
    case_kinds=frozenset({"switch_case"}),
    boolean_operator_kinds=frozenset(),
    boolean_operator_text_kinds=frozenset({"binary_expression"}),
    class_kinds=frozenset({"class_declaration", "class", "abstract_class_declaration"}),
    self_identifiers=frozenset({"this"}),
    member_access_kinds=frozenset({"member_expression"}),
    # ``expect(x).toBe(y)`` / ``assert.equal(...)`` — best-effort: any call
    # whose callee chain mentions ``expect`` / ``assert*``.
    assert_call_kinds=frozenset({"call_expression"}),
    call_kinds=frozenset({"call_expression"}),
    # TS/JS async is a modifier token, not a distinct node type; the walker
    # sniffs the ``async`` child token instead, so this stays empty.
    async_function_kinds=frozenset(),
)

_JS = _TS  # identical control-flow nodes; tree-sitter-javascript shares shape.

_GO = LanguageNodeMap(
    function_kinds=frozenset({"function_declaration", "method_declaration"}),
    lambda_kinds=frozenset({"func_literal"}),
    branch_kinds=frozenset({"if_statement"}),
    loop_kinds=frozenset({"for_statement"}),
    try_kinds=frozenset(),
    catch_kinds=frozenset(),
    switch_kinds=frozenset({"expression_switch_statement", "type_switch_statement"}),
    case_kinds=frozenset({"expression_case", "type_case", "default_case"}),
    boolean_operator_kinds=frozenset(),
    boolean_operator_text_kinds=frozenset({"binary_expression"}),
    # No class-level fields: Go methods attach to a type via an external
    # receiver (``func (r T) m()``) rather than nesting in a class body,
    # so there is no single node that groups a type's methods. Left for a
    # future receiver-aware grouping pass; until then Go emits no classes.
    # ``assert.Equal(t, ...)`` (testify) — best-effort call detection.
    assert_call_kinds=frozenset({"call_expression"}),
    call_kinds=frozenset({"call_expression"}),
)

_JAVA = LanguageNodeMap(
    function_kinds=frozenset({"method_declaration", "constructor_declaration"}),
    lambda_kinds=frozenset({"lambda_expression"}),
    branch_kinds=frozenset({"if_statement", "ternary_expression"}),
    loop_kinds=frozenset(
        {
            "for_statement",
            "enhanced_for_statement",
            "while_statement",
            "do_statement",
        }
    ),
    try_kinds=frozenset({"try_statement", "try_with_resources_statement"}),
    catch_kinds=frozenset({"catch_clause"}),
    switch_kinds=frozenset({"switch_expression", "switch_statement"}),
    case_kinds=frozenset({"switch_block_statement_group", "switch_rule"}),
    boolean_operator_kinds=frozenset(),
    boolean_operator_text_kinds=frozenset({"binary_expression"}),
    class_kinds=frozenset({"class_declaration"}),
    self_identifiers=frozenset({"this"}),
    # ``field_access`` covers ``this.field``; ``method_invocation`` covers
    # ``this.foo()`` (its ``name`` field is the called method).
    member_access_kinds=frozenset({"field_access", "method_invocation"}),
    # ``assert x`` (JUnit ``assert`` keyword) + ``assertEquals(...)`` calls.
    assert_kinds=frozenset({"assert_statement"}),
    assert_call_kinds=frozenset({"method_invocation"}),
    # The perf pass needs both forms: ``repo.find()`` (method_invocation) and
    # ``new FileInputStream()`` (object_creation_expression).
    call_kinds=frozenset({"method_invocation", "object_creation_expression"}),
)

_RUST = LanguageNodeMap(
    function_kinds=frozenset({"function_item"}),
    lambda_kinds=frozenset({"closure_expression"}),
    branch_kinds=frozenset({"if_expression", "if_let_expression"}),
    loop_kinds=frozenset(
        {"for_expression", "while_expression", "while_let_expression", "loop_expression"}
    ),
    try_kinds=frozenset(),
    catch_kinds=frozenset(),
    switch_kinds=frozenset({"match_expression"}),
    case_kinds=frozenset({"match_arm"}),
    boolean_operator_kinds=frozenset(),
    boolean_operator_text_kinds=frozenset({"binary_expression"}),
    # Methods live in an ``impl`` block, not the ``struct`` itself; each
    # impl block is its own cohesion unit (a type with several impl blocks
    # yields several ``ClassComplexity`` rows). ``field_expression`` covers
    # both ``self.field`` and ``self.method()`` (the latter nests a
    # field_expression inside a call_expression).
    class_kinds=frozenset({"impl_item"}),
    self_identifiers=frozenset({"self"}),
    member_access_kinds=frozenset({"field_expression"}),
    # ``assert!`` / ``assert_eq!`` / ``assert_ne!`` are macro invocations.
    assert_call_kinds=frozenset({"macro_invocation"}),
)


_KOTLIN = LanguageNodeMap(
    function_kinds=frozenset({"function_declaration"}),
    lambda_kinds=frozenset({"lambda_literal", "anonymous_function"}),
    branch_kinds=frozenset({"if_expression"}),
    loop_kinds=frozenset({"for_statement", "while_statement", "do_while_statement"}),
    try_kinds=frozenset({"try_expression"}),
    catch_kinds=frozenset({"catch_block"}),
    switch_kinds=frozenset({"when_expression"}),
    case_kinds=frozenset({"when_entry"}),
    boolean_operator_kinds=frozenset(),
    boolean_operator_text_kinds=frozenset({"binary_expression"}),
    # Methods group under a ``class_body``; ``object_declaration`` (singletons
    # / companion objects) groups them the same way. Member access is
    # ``receiver.member`` via ``navigation_expression``; the instance receiver
    # is a ``this_expression`` whose text is ``this``. NOTE: idiomatic Kotlin
    # accesses members WITHOUT an explicit ``this.`` receiver — those bare
    # references are not counted (the documented implicit-receiver limit), so
    # ``low_cohesion`` stays at the "no signal" value rather than mis-firing.
    class_kinds=frozenset({"class_declaration", "object_declaration"}),
    self_identifiers=frozenset({"this"}),
    member_access_kinds=frozenset({"navigation_expression"}),
    # Kotlin has no bare ``assert`` keyword; ``assertEquals(...)`` /
    # ``assertTrue(...)`` are plain calls placed directly in the statement
    # list (no ``expression_statement`` wrapper).
    assert_call_kinds=frozenset({"call_expression"}),
)

_CPP = LanguageNodeMap(
    function_kinds=frozenset({"function_definition"}),
    lambda_kinds=frozenset({"lambda_expression"}),
    branch_kinds=frozenset({"if_statement", "conditional_expression"}),
    loop_kinds=frozenset({"for_statement", "while_statement", "do_statement", "for_range_loop"}),
    try_kinds=frozenset({"try_statement"}),
    catch_kinds=frozenset({"catch_clause"}),
    switch_kinds=frozenset({"switch_statement"}),
    case_kinds=frozenset({"case_statement"}),
    boolean_operator_kinds=frozenset(),
    boolean_operator_text_kinds=frozenset({"binary_expression"}),
    # ``class_specifier`` / ``struct_specifier`` group methods in a
    # ``field_declaration_list``. ``field_expression`` covers both
    # ``this->member`` and ``obj.member``; the instance receiver is the
    # ``this`` node. Same implicit-receiver limit as Kotlin — bare member
    # access (no ``this->``) is not counted.
    class_kinds=frozenset({"class_specifier", "struct_specifier"}),
    self_identifiers=frozenset({"this"}),
    member_access_kinds=frozenset({"field_expression"}),
    # GoogleTest / Catch2 / Boost.Test macros: ``EXPECT_EQ`` / ``ASSERT_EQ`` /
    # ``ASSERT_TRUE`` are ordinary calls (``expect``/``assert`` prefix matched
    # case-insensitively).
    assert_call_kinds=frozenset({"call_expression"}),
)

_CSHARP = LanguageNodeMap(
    function_kinds=frozenset(
        {"method_declaration", "constructor_declaration", "local_function_statement"}
    ),
    lambda_kinds=frozenset({"lambda_expression"}),
    branch_kinds=frozenset({"if_statement", "conditional_expression"}),
    loop_kinds=frozenset({"for_statement", "while_statement", "foreach_statement", "do_statement"}),
    try_kinds=frozenset({"try_statement"}),
    catch_kinds=frozenset({"catch_clause"}),
    switch_kinds=frozenset({"switch_statement", "switch_expression"}),
    case_kinds=frozenset({"switch_section", "switch_expression_arm"}),
    boolean_operator_kinds=frozenset(),
    boolean_operator_text_kinds=frozenset({"binary_expression"}),
    # ``class``/``struct``/``record`` declarations group methods in a
    # ``declaration_list``. ``member_access_expression`` covers
    # ``this.member`` (and ``obj.member``); ``this`` is the receiver token.
    class_kinds=frozenset({"class_declaration", "struct_declaration", "record_declaration"}),
    self_identifiers=frozenset({"this"}),
    member_access_kinds=frozenset({"member_access_expression"}),
    # xUnit / NUnit / MSTest: ``Assert.Equal(...)`` / ``Assert.True(...)`` are
    # invocations whose callee chain begins with ``Assert``.
    assert_call_kinds=frozenset({"invocation_expression"}),
    call_kinds=frozenset({"invocation_expression"}),
)


LANGUAGE_MAPS: dict[str, LanguageNodeMap] = {
    "python": _PY,
    "typescript": _TS,
    "tsx": _TS,
    "javascript": _JS,
    "jsx": _JS,
    "go": _GO,
    "java": _JAVA,
    "rust": _RUST,
    "kotlin": _KOTLIN,
    "cpp": _CPP,
    "csharp": _CSHARP,
}


def get_language_map(language: str) -> LanguageNodeMap | None:
    """Return the node-type map for *language* or None when unsupported."""
    return LANGUAGE_MAPS.get(language)
