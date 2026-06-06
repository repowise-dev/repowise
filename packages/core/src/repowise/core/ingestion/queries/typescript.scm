; =============================================================================
; repowise — TypeScript symbol and import queries
; tree-sitter-typescript >= 0.23
; =============================================================================

; ---------------------------------------------------------------------------
; Symbols
; ---------------------------------------------------------------------------

; Top-level function declaration
(function_declaration
  name: (identifier) @symbol.name
  parameters: (formal_parameters) @symbol.params
) @symbol.def

; Generator function
(generator_function_declaration
  name: (identifier) @symbol.name
  parameters: (formal_parameters) @symbol.params
) @symbol.def

; Class declaration
(class_declaration
  name: (type_identifier) @symbol.name
) @symbol.def

; Abstract class
(abstract_class_declaration
  name: (type_identifier) @symbol.name
) @symbol.def

; Interface
(interface_declaration
  name: (type_identifier) @symbol.name
) @symbol.def

; Type alias
(type_alias_declaration
  name: (type_identifier) @symbol.name
) @symbol.def

; Enum
(enum_declaration
  name: (identifier) @symbol.name
) @symbol.def

; Method inside class body
(method_definition
  name: (property_identifier) @symbol.name
  parameters: (formal_parameters) @symbol.params
) @symbol.def

; Arrow function assigned to const/let: const foo = (...) => { }
(lexical_declaration
  (variable_declarator
    name: (identifier) @symbol.name
    value: (arrow_function
      parameters: (formal_parameters) @symbol.params
    )
  )
) @symbol.def

; Public method accessor modifier capture
(method_definition
  (accessibility_modifier) @symbol.modifiers
  name: (property_identifier) @symbol.name
) @symbol.def

; ---------------------------------------------------------------------------
; Imports
; ---------------------------------------------------------------------------

; import { A, B } from "./module"
; import type { T } from "./types"
; import DefaultExport from "module"
(import_statement
  source: (string) @import.module
) @import.statement

; Re-export (barrel) statements — only those with a `source` are imports of
; another module's symbols. Captured as @import.statement so the existing
; import pipeline resolves the edge and carries the re-exported names:
;   export { A, B } from "./module"
;   export { A as B } from "./module"
;   export * from "./module"
;   export * as ns from "./module"
;   export type { T } from "./types"
(export_statement
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
; Type references (non-import positions)
;
; Mirrors the C# / Go pattern: a single ``@param.type`` capture name fans
; in every position where a user-defined type appears outside an import
; statement. The TypeScript head extractor in parser_helpers.py unwraps
; ``Foo[]`` / ``Promise<Foo>`` / ``ns.Foo`` / ``Foo | Bar`` shells and
; filters TS builtins (``string`` / ``number`` / ``Promise`` / ...). The
; result lets the dead-code analyzer see an ``interface Foo`` referenced
; as ``bar: Foo`` in another file even when no value-import binds ``Foo``.
; ---------------------------------------------------------------------------

; Function / method parameter types: (x: Qux) / (y?: Quux)
(required_parameter
  type: (type_annotation (_) @param.type))
(optional_parameter
  type: (type_annotation (_) @param.type))

; Interface field types: { bar: Baz }
(property_signature
  type: (type_annotation (_) @param.type))

; Class field types: class C { f: Field }
(public_field_definition
  type: (type_annotation (_) @param.type))

; Return types on function / method / arrow / call-signature declarations
(function_declaration
  return_type: (type_annotation (_) @param.type))
(method_definition
  return_type: (type_annotation (_) @param.type))
(method_signature
  return_type: (type_annotation (_) @param.type))
(arrow_function
  return_type: (type_annotation (_) @param.type))
(function_signature
  return_type: (type_annotation (_) @param.type))

; Generic-parameter constraints: <T extends Constraint>
(type_parameter
  (constraint (_) @param.type))

; Type alias RHS: type Alias = OtherType
(type_alias_declaration
  value: (_) @param.type)

; Class heritage — file-level type_use edges complement the symbol-level
; ``extends`` / ``implements`` edges the heritage extractor emits, so a
; concrete class importing an interface only to implement it counts as a
; consumer of the interface's file for unused-export purposes.
(extends_clause
  value: (_) @param.type)
(implements_clause
  (_) @param.type)
; Interface extends: interface A extends B
(extends_type_clause
  (_) @param.type)

; Compound type expressions — descend so the leaf type names inside
; ``A | B``, ``A & B`` and conditional types (``X extends Y ? A : B``)
; are reachable. Without these patterns ``type R = X | DefaultRenderer``
; would never surface ``DefaultRenderer`` as a type reference because
; the head extractor refuses to pick a single name out of a union.
(union_type (_) @param.type)
(intersection_type (_) @param.type)
(conditional_type (_) @param.type)
