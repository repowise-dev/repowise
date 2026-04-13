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
