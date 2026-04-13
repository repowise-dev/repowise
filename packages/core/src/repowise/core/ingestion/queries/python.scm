; =============================================================================
; repowise — Python symbol and import queries
; tree-sitter-python >= 0.23
;
; Capture name conventions (shared across ALL language query files):
;   @symbol.def       — the full definition node (used for line numbers, kind)
;   @symbol.name      — the name identifier node
;   @symbol.params    — parameter list node (optional)
;   @symbol.modifiers — decorator / visibility modifier nodes (optional)
;   @import.statement — the full import node
;   @import.module    — the module path being imported from
; =============================================================================

; ---------------------------------------------------------------------------
; Symbols
; ---------------------------------------------------------------------------

; Function (covers both regular and async — in tree-sitter-python >= 0.23
; async functions share the function_definition node type)
(function_definition
  name: (identifier) @symbol.name
  parameters: (parameters) @symbol.params
) @symbol.def

; Class
(class_definition
  name: (identifier) @symbol.name
) @symbol.def

; Decorated function or class — captures the decorator as a modifier
(decorated_definition
  (decorator) @symbol.modifiers
  (function_definition
    name: (identifier) @symbol.name
    parameters: (parameters) @symbol.params
  ) @symbol.def
)

(decorated_definition
  (decorator) @symbol.modifiers
  (class_definition
    name: (identifier) @symbol.name
  ) @symbol.def
)

; ---------------------------------------------------------------------------
; Imports
; ---------------------------------------------------------------------------

; from x.y import a, b
; from . import x
(import_from_statement
  module_name: (_) @import.module
) @import.statement

; import x.y.z
(import_statement
  name: (_) @import.module
) @import.statement

; ---------------------------------------------------------------------------
; Calls
; ---------------------------------------------------------------------------

; Simple function call: foo(arg1, arg2)
(call
  function: (identifier) @call.target
  arguments: (argument_list) @call.arguments
) @call.site

; Method call: obj.method(arg1, arg2)
(call
  function: (attribute
    object: (identifier) @call.receiver
    attribute: (identifier) @call.target
  )
  arguments: (argument_list) @call.arguments
) @call.site

; Chained method call: obj.method1().method2(args)
(call
  function: (attribute
    object: (call)
    attribute: (identifier) @call.target
  )
  arguments: (argument_list) @call.arguments
) @call.site

; Constructor call via class name: MyClass(args)
; (captured by the simple function call pattern above — class names are identifiers)
