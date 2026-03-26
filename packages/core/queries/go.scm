; =============================================================================
; repowise — Go symbol and import queries
; tree-sitter-go >= 0.23
; =============================================================================

; ---------------------------------------------------------------------------
; Symbols
; ---------------------------------------------------------------------------

; Top-level function
(function_declaration
  name: (identifier) @symbol.name
  parameters: (parameter_list) @symbol.params
) @symbol.def

; Method with receiver — @symbol.receiver is used to determine parent type
(method_declaration
  receiver: (parameter_list) @symbol.receiver
  name: (field_identifier) @symbol.name
  parameters: (parameter_list) @symbol.params
) @symbol.def

; Type declaration (struct, interface, alias)
; type_spec is always inside type_declaration
(type_spec
  name: (type_identifier) @symbol.name
) @symbol.def

; ---------------------------------------------------------------------------
; Imports
; ---------------------------------------------------------------------------

; Single import: import "fmt"
(import_spec
  (interpreted_string_literal) @import.module
) @import.statement
