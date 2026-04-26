; =============================================================================
; repowise — Java symbol and import queries
; tree-sitter-java >= 0.23
; =============================================================================

; ---------------------------------------------------------------------------
; Symbols
; ---------------------------------------------------------------------------

(class_declaration
  name: (identifier) @symbol.name
) @symbol.def

(class_declaration
  (modifiers) @symbol.modifiers
  name: (identifier) @symbol.name
) @symbol.def

(interface_declaration
  name: (identifier) @symbol.name
) @symbol.def

(interface_declaration
  (modifiers) @symbol.modifiers
  name: (identifier) @symbol.name
) @symbol.def

(enum_declaration
  name: (identifier) @symbol.name
) @symbol.def

; Java 16+ records: record Point(double x, double y) {}
(record_declaration
  name: (identifier) @symbol.name
) @symbol.def

(record_declaration
  (modifiers) @symbol.modifiers
  name: (identifier) @symbol.name
) @symbol.def

(method_declaration
  name: (identifier) @symbol.name
  parameters: (formal_parameters) @symbol.params
) @symbol.def

(constructor_declaration
  name: (identifier) @symbol.name
  parameters: (formal_parameters) @symbol.params
) @symbol.def

; Public modifier capture
(method_declaration
  (modifiers) @symbol.modifiers
  name: (identifier) @symbol.name
) @symbol.def

; ---------------------------------------------------------------------------
; Imports
; ---------------------------------------------------------------------------

(import_declaration
  (scoped_identifier) @import.module
) @import.statement

; ---------------------------------------------------------------------------
; Calls
; ---------------------------------------------------------------------------

; Simple function/static method call: foo(args)
(method_invocation
  name: (identifier) @call.target
  arguments: (argument_list) @call.arguments
) @call.site

; Method call on object: obj.method(args)
(method_invocation
  object: (identifier) @call.receiver
  name: (identifier) @call.target
  arguments: (argument_list) @call.arguments
) @call.site

; Chained method call: obj.method1().method2(args)
(method_invocation
  object: (method_invocation)
  name: (identifier) @call.target
  arguments: (argument_list) @call.arguments
) @call.site

; Constructor: new ClassName(args)
(object_creation_expression
  type: (type_identifier) @call.target
  arguments: (argument_list) @call.arguments
) @call.site
