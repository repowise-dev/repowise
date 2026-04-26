; =============================================================================
; repowise — Scala symbol, import, and call queries
; tree-sitter-scala >= 0.23
; =============================================================================

; ---------------------------------------------------------------------------
; Symbols
; ---------------------------------------------------------------------------

(class_definition
  name: (identifier) @symbol.name
) @symbol.def

(trait_definition
  name: (identifier) @symbol.name
) @symbol.def

(object_definition
  name: (identifier) @symbol.name
) @symbol.def

(function_definition
  name: (identifier) @symbol.name
  (parameters) @symbol.params
) @symbol.def

(function_declaration
  name: (identifier) @symbol.name
  (parameters) @symbol.params
) @symbol.def

(val_definition
  pattern: (identifier) @symbol.name
) @symbol.def

; ---------------------------------------------------------------------------
; Imports
; ---------------------------------------------------------------------------

(import_declaration
  (identifier) @import.module
) @import.statement

; ---------------------------------------------------------------------------
; Calls
; ---------------------------------------------------------------------------

; Simple call: foo(args)
(call_expression
  function: (identifier) @call.target
  arguments: (arguments) @call.arguments
) @call.site

; Member call: obj.method(args)  — uses select_expression in tree-sitter-scala
(call_expression
  function: (field_expression
    value: (identifier) @call.receiver
    field: (identifier) @call.target
  )
  arguments: (arguments) @call.arguments
) @call.site
