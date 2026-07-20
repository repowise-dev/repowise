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

; Package-qualified function value passed as call argument: f(pkg.Handler, ...)
; Rescues functions used as first-class values — passed as callbacks, middleware,
; or handlers rather than called directly. Without this, pkg.Func referenced
; only in argument position (never in function: position) has no call edges
; and is incorrectly flagged as an unused export.
(call_expression
  arguments: (argument_list
    (selector_expression
      operand: (_) @call.receiver
      field: (field_identifier) @call.target
    )
  )
) @call.site

; ---------------------------------------------------------------------------
; Type references — drive file-level ``type_use`` edges
; ---------------------------------------------------------------------------
; Go places a large share of its dependency surface in type positions that
; carry no import statement: a struct field of type ``pkg.Config``, a
; parameter or return of a sibling-package type, a ``pkg.Options{}``
; composite literal. The single ``@param.type`` capture name is reused
; across languages (see parser._extract_type_refs); the Go-specific head
; extractor unwraps ``*T`` / ``[]T`` / ``map[K]V`` / ``pkg.T`` / ``T[U]``.

; Parameter and receiver types: func f(o Options), func (c *Cache) ...
(parameter_declaration
  type: (_) @param.type)

; Struct field types: struct { Inner *Partition }
(field_declaration
  type: (_) @param.type)

; Single return type: func New() *Cache  (multi-returns are parameter_lists,
; already covered by the parameter_declaration pattern above)
(function_declaration
  result: (_) @param.type)
(method_declaration
  result: (_) @param.type)

; Composite-literal type: Options{...}, pkg.Cache{...} — the key signal that
; rescues struct types used only as values, never imported by name.
(composite_literal
  type: (_) @param.type)
