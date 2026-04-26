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
