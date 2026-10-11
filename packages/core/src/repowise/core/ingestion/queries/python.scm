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

; Module-level assignment — constants and module config values. Anchored at
; (module …) so function-local and class-body assignments never match; the
; parser refines the kind (SCREAMING_CASE → constant, otherwise variable).
(module
  (expression_statement
    (assignment
      left: (identifier) @symbol.name
    ) @symbol.def
  )
)

; Decorated function or class — captures the decorator as a modifier
(decorated_definition
  (decorator)+ @symbol.modifiers
  (function_definition
    name: (identifier) @symbol.name
    parameters: (parameters) @symbol.params
  ) @symbol.def
)

(decorated_definition
  (decorator)+ @symbol.modifiers
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
  name: [
    (dotted_name) @import.module
    (aliased_import name: (_) @import.module)
  ]
) @import.statement

; ---------------------------------------------------------------------------
; Calls
; ---------------------------------------------------------------------------

; Simple function call: foo(arg1, arg2)
(call
  function: (identifier) @call.target
  arguments: (argument_list) @call.arguments
) @call.site

; Method call: obj.method(arg1, arg2), and obj.a.b.method() -- an attribute
; receiver is kept only when it is a plain dotted path, which the parser checks.
(call
  function: (attribute
    object: [(identifier) (attribute)] @call.receiver
    attribute: (identifier) @call.target
  )
  arguments: (argument_list) @call.arguments
) @call.site

; Chained method call: obj.method1().method2(args)
(call
  function: (attribute
    object: (call) @call.receiver_call
    attribute: (identifier) @call.target
  )
  arguments: (argument_list) @call.arguments
) @call.site

; Constructor call via class name: MyClass(args)
; (captured by the simple function call pattern above — class names are identifiers)

; ---------------------------------------------------------------------------
; Value references: a function or class handed over by name, not called
; ---------------------------------------------------------------------------
; register(fn), callback=fn, map(fn, xs), [fn], (fn,), {"k": fn}, return fn,
; x = fn. Each becomes a ``references`` edge once the name resolves to a
; function or class; one alternation per spelling keeps it to two patterns.
[
  (argument_list (identifier) @reference.name)
  (keyword_argument value: (identifier) @reference.name)
  (list (identifier) @reference.name)
  (tuple (identifier) @reference.name)
  (set (identifier) @reference.name)
  (pair value: (identifier) @reference.name)
  (return_statement (identifier) @reference.name)
  (assignment right: (identifier) @reference.name)
]

; The same positions holding mod.fn or self.handle
[
  (argument_list (attribute
    object: (identifier) @reference.receiver
    attribute: (identifier) @reference.name))
  (keyword_argument value: (attribute
    object: (identifier) @reference.receiver
    attribute: (identifier) @reference.name))
  (list (attribute
    object: (identifier) @reference.receiver
    attribute: (identifier) @reference.name))
  (tuple (attribute
    object: (identifier) @reference.receiver
    attribute: (identifier) @reference.name))
  (pair value: (attribute
    object: (identifier) @reference.receiver
    attribute: (identifier) @reference.name))
  (assignment right: (attribute
    object: (identifier) @reference.receiver
    attribute: (identifier) @reference.name))
]
