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

Phase 1 covers Python, TypeScript, JavaScript, Go, Java, Rust.
Phase 5 will add C, C++, C#, Kotlin, Ruby, PHP, Swift, Scala.
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
)

_RUST = LanguageNodeMap(
    function_kinds=frozenset({"function_item"}),
    lambda_kinds=frozenset({"closure_expression"}),
    branch_kinds=frozenset({"if_expression", "if_let_expression"}),
    loop_kinds=frozenset({"for_expression", "while_expression", "while_let_expression", "loop_expression"}),
    try_kinds=frozenset(),
    catch_kinds=frozenset(),
    switch_kinds=frozenset({"match_expression"}),
    case_kinds=frozenset({"match_arm"}),
    boolean_operator_kinds=frozenset(),
    boolean_operator_text_kinds=frozenset({"binary_expression"}),
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
