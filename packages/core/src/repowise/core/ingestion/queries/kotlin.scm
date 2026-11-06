; =============================================================================
; repowise — Kotlin symbol, import, and call queries
; tree-sitter-kotlin >= 1.0
; =============================================================================

; ---------------------------------------------------------------------------
; Symbols
; ---------------------------------------------------------------------------

(function_declaration
  (modifiers)? @symbol.modifiers
  (identifier) @symbol.name
  (function_value_parameters) @symbol.params
) @symbol.def

(class_declaration
  (modifiers)? @symbol.modifiers
  (identifier) @symbol.name
) @symbol.def

(object_declaration
  (modifiers)? @symbol.modifiers
  (identifier) @symbol.name
) @symbol.def

; typealias Foo = Bar (Q2)
(type_alias
  (identifier) @symbol.name
) @symbol.def

; Top-level / class-level val/var properties (Q3) — excludes locals inside functions
(source_file
  (property_declaration
    (variable_declaration
      (identifier) @symbol.name
    )
  ) @symbol.def
)

(class_body
  (property_declaration
    (variable_declaration
      (identifier) @symbol.name
    )
  ) @symbol.def
)

; ---------------------------------------------------------------------------
; Imports
; ---------------------------------------------------------------------------

(import
  (qualified_identifier) @import.module
) @import.statement

; ---------------------------------------------------------------------------
; Calls
; ---------------------------------------------------------------------------

; Simple call: foo(args), or foo { ... } whose only argument is a trailing
; lambda: the grammar gives that form no ``value_arguments`` at all.
; tree-sitter-kotlin binds a prefix or postfix operator before the call, so
; ``!f(x)`` arrives as ``(!f)(x)`` and ``a!!.f()`` / ``!a.f()`` as ``(!a).f()``;
; the operand is still the name called or the receiver.
(call_expression
  [
    (identifier) @call.target
    (unary_expression (identifier) @call.target)
  ]
  [
    (value_arguments)
    (annotated_lambda)
  ] @call.arguments
) @call.site

; Member call: obj.method(args) — and self-dispatch: this.method(args).
; See typescript.scm for why ``this`` rides an alternation in the receiver slot
; instead of a second pattern. ``this.field.method()`` captures the field, as
; java.scm does, so a constructor property types it. A string literal
; receiver (``"/".encodeUtf8()``) is typed ``String`` by the parser. Any other
; dotted receiver (``a.b.m()``, ``a.b!!.c.m()``) is kept as a plain field path,
; walked hop by hop; the parser drops one that is not a path of names.
(call_expression
  (navigation_expression
    [
      (identifier) @call.receiver
      (unary_expression (identifier) @call.receiver)
      (this_expression) @call.receiver
      (navigation_expression (this_expression) (identifier) @call.receiver)
      ((navigation_expression) @call.receiver
        (#not-match? @call.receiver "^this[.][A-Za-z_][A-Za-z0-9_]*$"))
      (unary_expression (navigation_expression) @call.receiver)
      (string_literal) @call.receiver
      (multiline_string_literal) @call.receiver
    ]
    (identifier) @call.target
  )
  [
    (value_arguments)
    (annotated_lambda)
  ] @call.arguments
) @call.site

; Superclass call: super.m(args), and super<I>.m(args) naming the supertype
; whose ``m`` is meant. The resolver walks the caller's ancestors, as for
; Java's ``super.m()``; ``super@Outer`` is dropped by the parser.
(call_expression
  (navigation_expression
    (super_expression (user_type)? @call.scope) @call.receiver
    (identifier) @call.target
  )
  [
    (value_arguments)
    (annotated_lambda)
  ] @call.arguments
) @call.site

; Infix call: ``a shr 4`` is ``a.shr(4)``, the right operand its one argument.
; The left operand is taken as the member pattern takes a receiver; any other
; expression there (``a shr 4 and 0xf`` for ``and``) types nothing, so that
; call is left out.
(infix_expression
  .
  [
    (identifier) @call.receiver
    (unary_expression (identifier) @call.receiver)
    (this_expression) @call.receiver
    (string_literal) @call.receiver
    (multiline_string_literal) @call.receiver
  ]
  .
  (identifier) @call.target
  .
  (_) @call.arguments
  .
) @call.site

; Call chained on another call: a.b().c(args), a?.b()?.c(args), B.Builder().c().
; The inner call is captured whole as ``@call.receiver_call``; the grammar
; names no fields on either node, so ``parser._kotlin_call_receiver`` reads its
; target, receiver and arguments by position. The ``.`` or ``?.`` between the
; two is an unnamed token, so one pattern serves both.
(call_expression
  (navigation_expression
    (call_expression) @call.receiver_call
    (identifier) @call.target
  )
  (value_arguments) @call.arguments
) @call.site

; ---------------------------------------------------------------------------
; Callable references — a function named without being called
; ---------------------------------------------------------------------------
; The operator is matched literally because the installed grammar gives
; ``Foo::bar`` and ``Foo.bar`` the same node shape, and the token is the only
; thing separating them. ``::`` also precedes a property (``Foo::name``) and
; the reserved ``::class``; neither is filtered here, because the symbol kind
; settles it at resolution and no symbol is ever named ``class``.

; Qualified: list.map(Foo::bar), register(A::process)
(navigation_expression
  (identifier) @reference.receiver
  "::"
  (identifier) @reference.name
)

; Bound to the enclosing instance: val f = this::handle
(navigation_expression
  (this_expression) @reference.receiver
  "::"
  (identifier) @reference.name
)

; Unqualified: list.map(::transform). Also catches a qualified spelling whose
; receiver the grammar parsed as a type rather than an expression, which then
; arrives receiver-less and so reaches only top-level functions. A ceiling in
; the safe direction: it costs an edge, it cannot invent one.
(callable_reference
  (identifier) @reference.name
)

; ---------------------------------------------------------------------------
; Type references — drive file-level ``type_use`` edges
; ---------------------------------------------------------------------------
; Kotlin types appear in primary-ctor positions (``class Foo(val x: Bar)``),
; function parameter / return positions, and property declarations — none
; of which carry an import statement of their own. The single
; ``@param.type`` capture is reused across languages
; (see parser._extract_type_refs); the Kotlin head extractor in
; lang_helpers/type_heads.py unwraps ``Foo?`` / ``List<Foo>`` / dotted ``ns.Foo`` and
; filters the kotlin-stdlib ubiquitous types.

; Function parameter: fun f(x: Bar) — `(user_type)` and `(nullable_type)`
; cover Foo and Foo?.
(parameter (user_type) @param.type)
(parameter (nullable_type) @param.type)

; Primary-constructor parameter: class Foo(val b: Bar, c: Baz?)
(class_parameter (user_type) @param.type)
(class_parameter (nullable_type) @param.type)

; Property type annotation: val p: Bar = TODO()
(variable_declaration (user_type) @param.type)
(variable_declaration (nullable_type) @param.type)

; Function / property return type
(function_declaration (user_type) @param.type)
(function_declaration (nullable_type) @param.type)

; Class heritage / interface implementation: class Foo : Bar, IBaz
(delegation_specifier (user_type) @param.type)

; Generic type arguments — inside ``Map<String, Foo>`` the inner
; ``user_type`` for Foo is wrapped in ``type_projection``.
(type_projection (user_type) @param.type)
(type_projection (nullable_type) @param.type)
