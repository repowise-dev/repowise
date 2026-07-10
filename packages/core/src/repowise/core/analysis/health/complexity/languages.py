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

Control-flow maps cover all eleven full-tier languages (Python, TypeScript,
JavaScript, Go, Java, Kotlin, Rust, C++, C#, Scala, Ruby) plus their aliases
and Dart; class-level maps cover all of those except Go (no class-grouping
node). Adding a language, at either tier, is purely additive here.

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

    # ------------------------------------------------------------------
    # Dataflow def/use pass (intra-procedural CFG + reaching definitions).
    # All three default to empty, making the read-vs-write distinction
    # OPT-IN per language: a language that maps none produces no def/use
    # signal (and so no reaching definitions), the safe "no signal" default.
    # The per-language ``DefUseDialect`` (``dataflow/dialects/``) consumes
    # these to recognise the assignment shapes whose targets are writes.
    #
    #   * ``assignment_kinds`` — node type(s) for a plain assignment whose
    #     ``left`` field is the write target (Python ``assignment``; TS/JS
    #     ``assignment_expression``).
    #   * ``augmented_assign_kinds`` — compound-assignment node type(s)
    #     (``x += 1``) whose target is BOTH read and written.
    #   * ``local_decl_kinds`` — node type(s) that introduce a fresh local
    #     binding distinct from assignment (Go ``short_var_declaration``,
    #     Rust ``let_declaration``). Empty for languages such as Python where
    #     assignment is the only binding form; the dialect handles the
    #     remaining Python def sites (``for`` target, ``with ... as``, walrus,
    #     comprehension target) structurally.
    assignment_kinds: frozenset[str] = frozenset()
    augmented_assign_kinds: frozenset[str] = frozenset()
    local_decl_kinds: frozenset[str] = frozenset()

    # ------------------------------------------------------------------
    # Dataflow control-flow grammar (CFG builder + Extract Method slicer).
    # These name the structured-statement node kinds the language-agnostic
    # CFG builder (``dataflow/cfg.py``) and the slicer (``dataflow/slice.py``)
    # branch on, so the structural pass needs no per-language ``if`` chain.
    # All default to empty: a language that maps none builds a degenerate
    # (straight-line) CFG and so produces no Extract Method signal -- the same
    # precision-first "no signal" default the def/use dialect uses. They sit on
    # the node-map (next to ``branch_kinds`` / ``loop_kinds`` / ``try_kinds``,
    # which already drive the CFG) rather than on the ``DefUseDialect`` so the
    # CFG builder keeps taking only an ``lmap`` -- additive and registry-
    # dispatched exactly like the control-flow kinds above it.
    #
    #   * ``if_kinds`` -- the *statement-level* conditional node(s) that open a
    #     CFG branch (Python ``if_statement``; Rust ``if_expression``). Narrower
    #     than ``branch_kinds``, which also carries expression-level conditionals
    #     (ternary / ``conditional_expression``) that are NOT block boundaries.
    #   * ``block_kinds`` -- statement-container node(s) whose named children are
    #     a flat statement sequence (Python/Go/Java ``block``; TS
    #     ``statement_block``; Go additionally nests a ``statement_list`` inside
    #     its ``block``, so it maps both and the builder unwraps the wrapper).
    #   * ``return_kinds`` / ``raise_kinds`` / ``break_kinds`` /
    #     ``continue_kinds`` -- the jump statements. ``return`` / ``raise``
    #     (``throw``) edge to the function exit; ``break`` / ``continue`` edge to
    #     the enclosing loop's exit / header. A language that omits one simply
    #     records that statement as a plain straight-line node (no special edge).
    if_kinds: frozenset[str] = frozenset()
    block_kinds: frozenset[str] = frozenset()
    return_kinds: frozenset[str] = frozenset()
    raise_kinds: frozenset[str] = frozenset()
    break_kinds: frozenset[str] = frozenset()
    continue_kinds: frozenset[str] = frozenset()
    #   * ``statement_wrapper_kinds`` -- statement node(s) that merely wrap the
    #     node the CFG builder should classify, as their last named child.
    #     Expression-oriented grammars need this: tree-sitter-rust parses every
    #     statement-position control-flow expression (``for`` / ``if`` /
    #     ``return`` / ``break``) inside an ``expression_statement``, so without
    #     unwrapping the builder would see one opaque statement and produce a
    #     straight-line CFG. A nonempty set is therefore also the slicer's
    #     expression-oriented-language marker: it then refuses spans ending on a
    #     block's tail expression (the implicit value an extraction would
    #     silently drop), so only truly expression-oriented grammars may map it.
    statement_wrapper_kinds: frozenset[str] = frozenset()


_PY = LanguageNodeMap(
    function_kinds=frozenset({"function_definition", "async_function_definition"}),
    lambda_kinds=frozenset({"lambda"}),
    # ``if_clause`` is a comprehension filter (``[x for x in xs if a if b]``);
    # each filter is an independent branch point, so it belongs with the other
    # branch kinds. The comprehension ``for`` (``for_in_clause``) is a generator,
    # not a decision, so it is intentionally left out.
    branch_kinds=frozenset({"if_statement", "elif_clause", "conditional_expression", "if_clause"}),
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
    assignment_kinds=frozenset({"assignment"}),
    augmented_assign_kinds=frozenset({"augmented_assignment"}),
    if_kinds=frozenset({"if_statement"}),
    block_kinds=frozenset({"block"}),
    return_kinds=frozenset({"return_statement"}),
    raise_kinds=frozenset({"raise_statement"}),
    break_kinds=frozenset({"break_statement"}),
    continue_kinds=frozenset({"continue_statement"}),
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
    # ``x = ...`` is an ``assignment_expression``; ``x += ...`` a dedicated
    # ``augmented_assignment_expression``. ``let`` / ``const`` declare via
    # ``lexical_declaration``, ``var`` via ``variable_declaration`` (both nest a
    # ``variable_declarator``); the dialect reads their ``name`` binding.
    assignment_kinds=frozenset({"assignment_expression"}),
    augmented_assign_kinds=frozenset({"augmented_assignment_expression"}),
    local_decl_kinds=frozenset({"lexical_declaration", "variable_declaration"}),
    if_kinds=frozenset({"if_statement"}),
    block_kinds=frozenset({"statement_block"}),
    return_kinds=frozenset({"return_statement"}),
    raise_kinds=frozenset({"throw_statement"}),
    break_kinds=frozenset({"break_statement"}),
    continue_kinds=frozenset({"continue_statement"}),
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
    # ``x = ...`` and ``x += ...`` are both ``assignment_statement`` (the dialect
    # tells them apart by the operator token), so the augmented set stays empty.
    # ``:=`` is a ``short_var_declaration``; ``var x = ...`` a ``var_declaration``
    # (nesting ``var_spec``) -- both introduce fresh locals.
    assignment_kinds=frozenset({"assignment_statement"}),
    local_decl_kinds=frozenset({"short_var_declaration", "var_declaration"}),
    if_kinds=frozenset({"if_statement"}),
    # A Go ``block`` wraps its statements in a ``statement_list`` child; both are
    # mapped so the CFG builder unwraps the wrapper to reach the statements.
    block_kinds=frozenset({"block", "statement_list"}),
    return_kinds=frozenset({"return_statement"}),
    # Go has no exceptions (``panic`` is an ordinary call), so no raise kind.
    break_kinds=frozenset({"break_statement"}),
    continue_kinds=frozenset({"continue_statement"}),
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
    # ``x = ...`` and ``x += ...`` are both ``assignment_expression`` (the
    # dialect tells them apart by the operator token, as in Go), so the
    # augmented set stays empty. ``int x = 1, y = 2;`` is a
    # ``local_variable_declaration`` nesting one ``variable_declarator`` per
    # bound name.
    assignment_kinds=frozenset({"assignment_expression"}),
    local_decl_kinds=frozenset({"local_variable_declaration"}),
    if_kinds=frozenset({"if_statement"}),
    block_kinds=frozenset({"block"}),
    return_kinds=frozenset({"return_statement"}),
    raise_kinds=frozenset({"throw_statement"}),
    break_kinds=frozenset({"break_statement"}),
    continue_kinds=frozenset({"continue_statement"}),
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
    # The perf pass: both ``foo()`` and method/scoped calls (``x.fetch_all()`` /
    # ``std::fs::read()``) parse as ``call_expression``. Rust has no dedicated
    # async function node type — ``async fn`` is a ``function_item`` carrying a
    # ``function_modifiers`` child — so ``async_function_kinds`` stays empty and
    # ``RustPerfDialect.is_async_fn`` sniffs the modifier instead.
    call_kinds=frozenset({"call_expression"}),
    # ``x = ...`` is an ``assignment_expression``; ``x += ...`` a dedicated
    # ``compound_assignment_expr``. ``let`` (with any pattern) is the fresh-
    # binding form. ``if_let_expression`` / ``while_let_expression`` only exist
    # in older tree-sitter-rust grammars (current ones parse ``if let`` as an
    # ``if_expression`` with a ``let_condition``); listing them is harmless.
    assignment_kinds=frozenset({"assignment_expression"}),
    augmented_assign_kinds=frozenset({"compound_assignment_expr"}),
    local_decl_kinds=frozenset({"let_declaration"}),
    if_kinds=frozenset({"if_expression", "if_let_expression"}),
    block_kinds=frozenset({"block"}),
    return_kinds=frozenset({"return_expression"}),
    # ``?`` (``try_expression``) propagates an error out of the function -- an
    # early exit the CFG treats as a terminator and the Extract Method slicer
    # treats as a jump, so no span containing one is ever offered.
    raise_kinds=frozenset({"try_expression"}),
    break_kinds=frozenset({"break_expression"}),
    continue_kinds=frozenset({"continue_expression"}),
    # Rust parses every statement-position control-flow expression inside an
    # ``expression_statement``; the CFG builder unwraps it to classify the real
    # node, and the slicer uses this as the expression-oriented marker for
    # tail-expression suppression.
    statement_wrapper_kinds=frozenset({"expression_statement"}),
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

_DART = LanguageNodeMap(
    # Dart splits a function into a ``function_signature`` node whose body is
    # a SIBLING ``function_body`` node (members wrap the signature in
    # ``method_signature``). Keying ``function_kinds`` on the body measures
    # complexity/NLOC exactly right with the shared walker; the entry name
    # and parameter count come from the preceding signature sibling (see the
    # ``function_body`` handling in ``ast_utils`` / ``perf_walk``).
    function_kinds=frozenset({"function_body"}),
    lambda_kinds=frozenset({"function_expression"}),
    # ``if_element`` is the collection-literal ``if`` (``[if (x) y]``);
    # ``conditional_expression`` is the ternary.
    branch_kinds=frozenset({"if_statement", "conditional_expression", "if_element"}),
    loop_kinds=frozenset({"for_statement", "while_statement", "do_statement", "for_element"}),
    try_kinds=frozenset({"try_statement"}),
    # A bare ``on FormatException {}`` arm without ``catch`` has no
    # catch_clause node (the block hangs off try_statement directly), so it
    # is undercounted — the safe direction.
    catch_kinds=frozenset({"catch_clause"}),
    switch_kinds=frozenset({"switch_statement", "switch_expression"}),
    case_kinds=frozenset(
        {"switch_statement_case", "switch_statement_default", "switch_expression_case"}
    ),
    # The grammar exposes dedicated operator nodes inside
    # logical_and_expression / logical_or_expression — one node per operator
    # occurrence, so no binary-node text sniffing is needed.
    boolean_operator_kinds=frozenset({"logical_and_operator", "logical_or_operator"}),
    # Method/size/CCN class facts only. Dart has no wrapper node for
    # ``this.member`` (receiver and ``.member`` selector are flat siblings)
    # and idiomatic Dart omits ``this.`` anyway, so member access stays
    # unmapped and LCOM4 sits at its "no signal" safety valve rather than
    # mis-firing on every class.
    class_kinds=frozenset({"class_definition", "mixin_declaration"}),
    self_identifiers=frozenset({"this"}),
    # ``assert(...)`` is a real statement in Dart. package:test ``expect()``
    # calls have no call-expression node type to key on (calls are selector
    # chains), so assertion-call runs are not counted — under-signal, safe.
    assert_kinds=frozenset({"assert_statement"}),
    # Calls are ``selector`` nodes carrying an ``argument_part``; the Dart
    # perf dialect's callee extraction filters out the non-call selectors.
    call_kinds=frozenset({"selector"}),
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


_SCALA = LanguageNodeMap(
    # ``function_definition`` is a ``def`` with a body (expression or block);
    # abstract ``def``s parse as ``function_declaration`` (no body, nothing to
    # measure). ``given_definition`` is deliberately NOT a function kind: a
    # ``given ... with {}`` instance nests real ``function_definition`` members,
    # and function collection stops descending at a function boundary, so
    # mapping ``given`` would swallow its methods; leaving it unmapped lets the
    # traversal find them individually (alias givens are values, not bodies).
    function_kinds=frozenset({"function_definition"}),
    # A partial-function literal (``{ case n => ... }``) is a ``case_block``,
    # shared with ``match`` / ``catch``, so it cannot be a lambda kind without
    # every match arm block becoming a module-level entry; its cases still
    # count via ``case_kinds`` and its body rolls up into the enclosing def.
    lambda_kinds=frozenset({"lambda_expression"}),
    # Scala ``if`` is an expression; ``guard`` covers both for-comprehension
    # filters (``for (i <- xs if i > 0)``) and match-case guards
    # (``case n if n > 0``), each an inline decision point (flat, see
    # ``_FLAT_BRANCH_KINDS``).
    branch_kinds=frozenset({"if_expression", "guard"}),
    # A for-comprehension is a loop for nesting/CCN purposes even though it
    # desugars to flatMap: this matches developer intuition.
    loop_kinds=frozenset({"for_expression", "while_expression", "do_while_expression"}),
    try_kinds=frozenset({"try_expression"}),
    # ``catch`` takes a ``case_block``; each handler is a ``case_clause`` that
    # already counts via ``case_kinds`` (per-handler parity with Python's
    # per-``except`` counting). Mapping ``catch_clause`` too would double-count
    # every single-case catch.
    catch_kinds=frozenset(),
    switch_kinds=frozenset({"match_expression"}),
    case_kinds=frozenset({"case_clause"}),
    boolean_operator_kinds=frozenset(),
    # ``&&`` / ``||`` are ``operator_identifier`` tokens inside a generic
    # ``infix_expression``, the text sniff. The sniffer also matches ``and`` /
    # ``or`` operator text (ScalaTest matcher DSL combinators), which are
    # genuine boolean combinators when used infix, so the shared behavior is
    # acceptable here.
    boolean_operator_text_kinds=frozenset({"infix_expression"}),
    # ``object`` (singletons / companions), ``trait``, and Scala 3 ``enum``
    # bodies all group methods the same way a class body does; case classes
    # are plain ``class_definition``s.
    class_kinds=frozenset(
        {"class_definition", "object_definition", "trait_definition", "enum_definition"}
    ),
    # Self-type aliases (``self =>``) are skipped: bare and aliased implicit
    # receivers degrade to the LCOM4 "no signal" valve, same documented limit
    # as Kotlin.
    self_identifiers=frozenset({"this"}),
    # ``field_expression`` (fields ``value`` / ``field``) covers both
    # ``this.field`` and ``this.method()`` (the latter nests one inside a
    # ``call_expression``).
    member_access_kinds=frozenset({"field_expression"}),
    # Plain ``assert(...)`` and munit/JUnit-style ``assertEquals(...)`` are
    # calls; ScalaTest's infix DSL (``x shouldBe y``) has no assert-prefixed
    # callee and is a documented gap (under-signal, safe).
    assert_call_kinds=frozenset({"call_expression"}),
    # ``instance_expression`` is ``new Foo(...)``, needed so a constructor at
    # an I/O boundary inside a loop is caught (JVM interop, as in Java).
    call_kinds=frozenset({"call_expression", "instance_expression"}),
    # Scala has no async syntax; the perf dialect's ``is_async_fn`` sniffs a
    # declared ``Future[...]`` return type instead.
)


_RUBY = LanguageNodeMap(
    # NB (grammar-wide): tree-sitter-ruby names keyword tokens after their
    # parent node (an ``if`` node contains an unnamed ``if`` token of type
    # ``"if"``); the walkers' ``is_named`` guards keep those tokens from
    # double-counting.
    function_kinds=frozenset({"method", "singleton_method"}),
    # Only the ``->`` literal is a closure-shaped entry. ``block`` /
    # ``do_block`` are deliberately NOT lambda kinds: Ruby code is block-heavy
    # and every ``items.each do … end`` would otherwise read as a nested
    # closure; block bodies roll up into the enclosing method instead.
    # (``lambda { }`` / ``proc { }`` parse as plain calls with a block and
    # likewise roll up.)
    lambda_kinds=frozenset({"lambda"}),
    # ``conditional`` is the ternary; the one-line modifier forms (``return x
    # if cond``) are flat decision points (see ``_FLAT_BRANCH_KINDS``), as is
    # the inline ``x rescue nil`` (``rescue_modifier`` — a catch shape, but an
    # inline one, so it lives here with the other flat branches).
    branch_kinds=frozenset(
        {
            "if",
            "unless",
            "elsif",
            "if_modifier",
            "unless_modifier",
            "conditional",
            "rescue_modifier",
        }
    ),
    # ``loop do … end`` and ``.each``/``.map`` blocks are method calls, not
    # loop nodes — the perf dialect recognises them via ``block_loop_body``;
    # for CCN/nesting they roll up like any other block (documented choice:
    # block-heavy Ruby would otherwise nest everything).
    loop_kinds=frozenset({"while", "until", "for", "while_modifier", "until_modifier"}),
    try_kinds=frozenset({"begin"}),
    # A method-level ``rescue`` (no ``begin``) is the same ``rescue`` node
    # nested directly in the method body — counted identically.
    catch_kinds=frozenset({"rescue"}),
    switch_kinds=frozenset({"case", "case_match"}),
    case_kinds=frozenset({"when", "in_clause"}),
    boolean_operator_kinds=frozenset(),
    # ``&&`` / ``||`` / ``and`` / ``or`` are operator tokens inside a generic
    # ``binary`` node — the text sniff.
    boolean_operator_text_kinds=frozenset({"binary"}),
    class_kinds=frozenset({"class", "module", "singleton_class"}),
    self_identifiers=frozenset({"self"}),
    # LCOM4 stays at its "no signal" valve: idiomatic Ruby reaches state via
    # receiver-less ``@ivar`` reads and bare sibling-method calls, so the only
    # mappable shape (an explicit ``self.member`` call) is too sparse to build
    # an honest cohesion graph on — partial evidence would inflate LCOM4 into
    # false ``low_cohesion`` hits. ``@ivar`` text grouping is the follow-up.
    # Class size / method-count / max-CCN facts still work off ``class_kinds``.
    member_access_kinds=frozenset(),
    # minitest ``assert_equal`` / bare ``assert`` and RSpec ``expect(...)``
    # are all ``call`` nodes and match the shared assert/expect prefix rule;
    # minitest's ``refute_*`` family does not (under-signal, safe).
    assert_call_kinds=frozenset({"call"}),
    # ``subshell`` is a backtick command — its own node type, mapped so the
    # perf dialect can classify it as a subprocess sink.
    call_kinds=frozenset({"call", "subshell"}),
    # Ruby has no async syntax; ``blocking_sync_in_async`` is n/a.
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
    "dart": _DART,
    "scala": _SCALA,
    "ruby": _RUBY,
}


def get_language_map(language: str) -> LanguageNodeMap | None:
    """Return the node-type map for *language* or None when unsupported."""
    return LANGUAGE_MAPS.get(language)
