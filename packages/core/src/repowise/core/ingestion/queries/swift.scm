; =============================================================================
; repowise — Swift symbol, import, and call queries
; tree-sitter-swift
; =============================================================================

; ---------------------------------------------------------------------------
; Symbols — with modifiers (priority: these come first so dedup keeps them)
; ---------------------------------------------------------------------------

(function_declaration
  (modifiers) @symbol.modifiers
  (simple_identifier) @symbol.name
) @symbol.def

; ---------------------------------------------------------------------------
; Symbols — without modifiers (fallback)
; ---------------------------------------------------------------------------

; class/struct/enum all use class_declaration
(class_declaration
  (type_identifier) @symbol.name
) @symbol.def

; extension Foo: Protocol — name is nested under user_type
(class_declaration
  (user_type
    (type_identifier) @symbol.name
  )
) @symbol.def

(protocol_declaration
  (type_identifier) @symbol.name
) @symbol.def

(function_declaration
  (simple_identifier) @symbol.name
) @symbol.def

(property_declaration
  (pattern
    (simple_identifier) @symbol.name
  )
) @symbol.def

; Protocol method declarations
(protocol_function_declaration
  (simple_identifier) @symbol.name
) @symbol.def

; Swift subscript (Q6) — capture the `subscript` keyword as the name
(subscript_declaration
  "subscript" @symbol.name
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
  (simple_identifier) @call.target
  (call_suffix
    (value_arguments) @call.arguments
  )
) @call.site

; Member call: obj.method(args)
(call_expression
  (navigation_expression
    (simple_identifier) @call.receiver
    (navigation_suffix
      (simple_identifier) @call.target
    )
  )
  (call_suffix
    (value_arguments) @call.arguments
  )
) @call.site
