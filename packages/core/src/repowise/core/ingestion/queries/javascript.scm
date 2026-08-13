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

; Arrow function assigned to const/let (parenthesized)
(lexical_declaration
  (variable_declarator
    name: (identifier) @symbol.name
    value: (arrow_function
      parameters: (formal_parameters) @symbol.params
    )
  )
) @symbol.def

; Arrow function assigned to const/let (unparenthesized single parameter)
(lexical_declaration
  (variable_declarator
    name: (identifier) @symbol.name
    value: (arrow_function
      parameter: (identifier) @symbol.params
    )
  )
) @symbol.def

; Top-level const/let bindings — module constants and call-expression
; bindings. The declarator (not the lexical_declaration) is @symbol.def so
; the kind map can distinguish it from the arrow-function pattern above.
; Anchored at (program …) — directly or under an export_statement — so
; function-local declarations never match.
;
; Kept in step with typescript.scm minus the TS-only node types
; (as_expression / satisfies_expression do not exist in the JS grammar and
; would fail query compilation). require() / import() declarators match
; (call_expression) here too; the parser drops them via
; ``declarator_value_is_module_ref`` rather than a query predicate, because
; the await / paren / non-null / member-pick shells hide the callee.
(program
  (lexical_declaration
    (variable_declarator
      name: (identifier) @symbol.name
      value: [
        (string) (template_string) (number) (true) (false) (null) (undefined)
        (array) (object) (unary_expression) (binary_expression)
        (new_expression) (member_expression)
        (call_expression) (function_expression) (class)
        (await_expression) (parenthesized_expression)
      ]
    ) @symbol.def
  )
)

(program
  (export_statement
    (lexical_declaration
      (variable_declarator
        name: (identifier) @symbol.name
        value: [
          (string) (template_string) (number) (true) (false) (null) (undefined)
          (array) (object) (unary_expression) (binary_expression)
          (new_expression) (member_expression)
          (call_expression) (function_expression) (class)
          (await_expression) (parenthesized_expression)
        ]
      ) @symbol.def
    )
  )
)

; ---------------------------------------------------------------------------
; Imports
; ---------------------------------------------------------------------------

(import_statement
  source: (string) @import.module
) @import.statement

; Re-export (barrel) statements — only those with a `source` are imports of
; another module's symbols. Captured as @import.statement so the existing
; import pipeline resolves the edge and carries the re-exported names:
;   export { A, B } from "./module"
;   export { default as AppMain } from "./AppMain"
;   export * from "./module"
; Kept in step with typescript.scm, which has carried this pattern all along.
; Without it a .js barrel yielded no edge whatsoever, so every component it
; re-exported read as unreachable — 6 of the 8 residual dead-code findings on
; vue-element-admin traced back to exactly this.
(export_statement
  source: (string) @import.module
) @import.statement

; Dynamic import: import("./module") — the ESM code-splitting form, and how a
; router lazy-loads a route component:
;     component: () => import('@/views/user/profile')
; The specifier is a real module edge, so without this the target carries no
; inbound import and reads as unreachable. Kept in step with typescript.scm.
(call_expression
  function: (import)
  arguments: (arguments (string) @import.module)
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

; <Component ... /> — Capitalized React component
(jsx_self_closing_element
  name: (identifier) @call.target
  (#match? @call.target "^[A-Z]")
) @call.site

; <Component ... > ... </Component> — Capitalized React component
(jsx_opening_element
  name: (identifier) @call.target
  (#match? @call.target "^[A-Z]")
) @call.site

; <Form.Item ... /> or <Form.Item> ... </Form.Item> — Member expression component
; Casing filter prevents motion.div / styled.button from emitting fake edges.
; @call.receiver captures the object (e.g. "Form") so Form.Item and Card.Item
; resolve to distinct call sites via _extract_calls:851.
(jsx_self_closing_element
  name: (member_expression
    object: (identifier) @call.receiver
    property: (property_identifier) @call.target
  )
  (#match? @call.target "^[A-Z]")
) @call.site

(jsx_opening_element
  name: (member_expression
    object: (identifier) @call.receiver
    property: (property_identifier) @call.target
  )
  (#match? @call.target "^[A-Z]")
) @call.site

