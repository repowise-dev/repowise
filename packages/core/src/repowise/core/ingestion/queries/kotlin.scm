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
  (identifier) @symbol.name
) @symbol.def

(object_declaration
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

; Simple call: foo(args)
(call_expression
  (identifier) @call.target
  (value_arguments) @call.arguments
) @call.site

; Member call: obj.method(args)
(call_expression
  (navigation_expression
    (identifier) @call.receiver
    (identifier) @call.target
  )
  (value_arguments) @call.arguments
) @call.site

; ---------------------------------------------------------------------------
; Symbols — companion objects and top-level functions
; ---------------------------------------------------------------------------

; Companion objects: `companion object { ... }` or `companion object Foo { ... }`
(companion_object
  (identifier)? @symbol.name
) @symbol.def

; ---------------------------------------------------------------------------
; Type references — drive file-level ``type_use`` edges
; ---------------------------------------------------------------------------
; Kotlin types appear in primary-ctor positions (``class Foo(val x: Bar)``),
; function parameter / return positions, and property declarations — none
; of which carry an import statement of their own. The single
; ``@param.type`` capture is reused across languages
; (see parser._extract_type_refs); the Kotlin head extractor in
; parser_helpers.py unwraps ``Foo?`` / ``List<Foo>`` / dotted ``ns.Foo`` and
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
