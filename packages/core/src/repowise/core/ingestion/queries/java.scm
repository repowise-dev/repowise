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

; ---------------------------------------------------------------------------
; Type references — drive file-level ``type_use`` edges
; ---------------------------------------------------------------------------
; Java buries a large share of its dependency surface in type positions
; that carry no import statement: a constructor or method parameter of an
; injected service type, a field of a sibling-package class, the return
; type of a factory, the element type of ``new Foo()``. The single
; ``@param.type`` capture is reused across languages
; (see parser._extract_type_refs); the Java head extractor in
; parser_helpers.py unwraps ``T[]`` / ``Foo<...>`` / ``ns.Foo`` / annotated
; types and filters primitives plus the most ubiquitous ``java.lang`` /
; ``java.util`` / ``java.util.function`` builtins.

; Constructor / method / lambda formal parameters
(formal_parameter type: (_) @param.type)

; Field declarations (instance + static)
(field_declaration type: (_) @param.type)

; Method return types
(method_declaration type: (_) @param.type)

; Constructor invocation type: ``new Foo<...>(args)``
(object_creation_expression type: (_) @param.type)

; Local variable types — rescues Spring-style ``Foo foo = svc.lookup();``
(local_variable_declaration type: (_) @param.type)

; Heritage clauses — emit file-level ``type_use`` edges that complement
; the symbol-level extends/implements edges the heritage extractor
; produces. A class that imports an interface only to implement it
; counts as a consumer of the interface's file for unused-export
; purposes.
(superclass (_) @param.type)
(super_interfaces (type_list (_) @param.type))

; Generic type arguments inside any of the above — without this an
; ``Optional<UserPreferences>`` field would only register ``Optional``
; (a builtin) and never the user type ``UserPreferences``.
(type_arguments (_) @param.type)
