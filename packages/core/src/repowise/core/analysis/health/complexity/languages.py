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

Control-flow maps cover Python, TypeScript, JavaScript, Go, Java, Rust;
class-level maps cover all of those except Go (no class-grouping node).
Adding a language — either tier — is purely additive here.
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


LANGUAGE_MAPS: dict[str, LanguageNodeMap] = {
    "python": _PY,
    "typescript": _TS,
    "tsx": _TS,
    "javascript": _JS,
    "jsx": _JS,
    "go": _GO,
    "java": _JAVA,
    "rust": _RUST,
}


def get_language_map(language: str) -> LanguageNodeMap | None:
    """Return the node-type map for *language* or None when unsupported."""
    return LANGUAGE_MAPS.get(language)
