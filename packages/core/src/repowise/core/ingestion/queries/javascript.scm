; =============================================================================
; repowise — JavaScript symbol and import queries
; tree-sitter-javascript >= 0.23
; =============================================================================

; ---------------------------------------------------------------------------
; Symbols
; ---------------------------------------------------------------------------

(function_declaration
  name: (identifier) @symbol.name
  parameters: (formal_parameters) @symbol.params
) @symbol.def

(generator_function_declaration
  name: (identifier) @symbol.name
  parameters: (formal_parameters) @symbol.params
) @symbol.def

(class_declaration
  name: (identifier) @symbol.name
) @symbol.def

(method_definition
  name: (property_identifier) @symbol.name
  parameters: (formal_parameters) @symbol.params
) @symbol.def

; Arrow function assigned to const/let
(lexical_declaration
  (variable_declarator
    name: (identifier) @symbol.name
    value: (arrow_function
      parameters: (formal_parameters) @symbol.params
    )
  )
) @symbol.def

; ---------------------------------------------------------------------------
; Imports
; ---------------------------------------------------------------------------

(import_statement
  source: (string) @import.module
) @import.statement

; CommonJS: const svc = require('./svc')  /  const { a, b } = require('./svc')
; Tag the individual declarator so multi-declarator statements aren't deduped.
(variable_declarator
  value: (call_expression
    function: (identifier) @_require
    arguments: (arguments (string) @import.module))
  (#eq? @_require "require")
) @import.statement

; CommonJS member pick: var x = require('./svc').member — the value is a
; member_expression WRAPPING the call, so the bare-call declarator pattern
; above never matches it (express's lib/*.js are full of this shape).
(variable_declarator
  value: (member_expression
    object: (call_expression
      function: (identifier) @_require_member
      arguments: (arguments (string) @import.module)))
  (#eq? @_require_member "require")
) @import.statement

; CommonJS re-export / property assignment:
;   module.exports = require('./x')
;   exports.foo = require('./y')
;   module.exports.foo = require('./z')
; (any member-expression LHS is a genuine dependency; the parser decides
; whether the shape is a re-export from the statement context)
(assignment_expression
  left: (member_expression)
  right: (call_expression
    function: (identifier) @_require_assign
    arguments: (arguments (string) @import.module))
  (#eq? @_require_assign "require")
) @import.statement

; CommonJS hub: Object.assign(module.exports, require('./a'), require('./b'))
; The parser walks the whole statement for every require() it contains, so
; multi-require hubs survive raw-statement dedup.
(call_expression
  function: (member_expression) @_objassign_fn
  arguments: (arguments
    (call_expression
      function: (identifier) @_require_arg
      arguments: (arguments (string) @import.module)))
  (#eq? @_objassign_fn "Object.assign")
  (#eq? @_require_arg "require")
) @import.statement

; ---------------------------------------------------------------------------
; Calls
; ---------------------------------------------------------------------------

; Simple function call: foo(arg1, arg2)
(call_expression
  function: (identifier) @call.target
  arguments: (arguments) @call.arguments
) @call.site

; Method call: obj.method(args)
(call_expression
  function: (member_expression
    object: (identifier) @call.receiver
    property: (property_identifier) @call.target
  )
  arguments: (arguments) @call.arguments
) @call.site

; Chained call: obj.method1().method2(args)
(call_expression
  function: (member_expression
    object: (call_expression)
    property: (property_identifier) @call.target
  )
  arguments: (arguments) @call.arguments
) @call.site

; new expression: new Foo(args)
(new_expression
  constructor: (identifier) @call.target
  arguments: (arguments) @call.arguments
) @call.site

; ---------------------------------------------------------------------------
; JSX element usage (treated as a call to the component)
; ---------------------------------------------------------------------------

; <Component ... />
(jsx_self_closing_element
  name: (identifier) @call.target
) @call.site

; <Component ... > ... </Component>
(jsx_opening_element
  name: (identifier) @call.target
) @call.site
