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

; Package-level const: const MaxRetries = 3
(const_spec
  name: (identifier) @symbol.name
) @symbol.def

; Package-level var: var ErrNotFound = errors.New("not found")
(var_spec
  name: (identifier) @symbol.name
) @symbol.def

; ---------------------------------------------------------------------------
; Imports
; ---------------------------------------------------------------------------

; Single import: import "fmt"
(import_spec
  (interpreted_string_literal) @import.module
) @import.statement

; ---------------------------------------------------------------------------
; Calls
; ---------------------------------------------------------------------------

; Simple function call: foo(arg1, arg2)
(call_expression
  function: (identifier) @call.target
  arguments: (argument_list) @call.arguments
) @call.site

; Method call: obj.Method(args)
(call_expression
  function: (selector_expression
    operand: (identifier) @call.receiver
    field: (field_identifier) @call.target
  )
  arguments: (argument_list) @call.arguments
) @call.site

; Package-qualified call: pkg.Function(args)
; (same pattern as method call — receiver is the package alias)
; Captured by the selector_expression pattern above.

; Chained call: obj.Method1().Method2(args)
(call_expression
  function: (selector_expression
    operand: (call_expression)
    field: (field_identifier) @call.target
  )
  arguments: (argument_list) @call.arguments
) @call.site
