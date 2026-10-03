; =============================================================================
; repowise — Java symbol and import queries
; tree-sitter-java >= 0.23
; =============================================================================

; ---------------------------------------------------------------------------
; Symbols
; ---------------------------------------------------------------------------

; One pattern per declaration, modifiers optional: with a second pattern
; that captured them, the bare one matched first and a class's annotations
; (``@Component``, ``@State``) never reached its symbol.
(class_declaration
  (modifiers)? @symbol.modifiers
  name: (identifier) @symbol.name
) @symbol.def

(interface_declaration
  (modifiers)? @symbol.modifiers
  name: (identifier) @symbol.name
) @symbol.def

(enum_declaration
  (modifiers)? @symbol.modifiers
  name: (identifier) @symbol.name
) @symbol.def

; Java 16+ records: record Point(double x, double y) {}
(record_declaration
  (modifiers)? @symbol.modifiers
  name: (identifier) @symbol.name
) @symbol.def

; One pattern, modifiers optional: two overlapping patterns kept whichever
; matched first, which dropped either the modifiers or the parameters that
; name an overload.
(method_declaration
  (modifiers)? @symbol.modifiers
  name: (identifier) @symbol.name
  parameters: (formal_parameters) @symbol.params
) @symbol.def

(constructor_declaration
  name: (identifier) @symbol.name
  parameters: (formal_parameters) @symbol.params
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

; Method call on the caller's own field: this.field.method(args).
; The pattern above constrains ``object`` to a bare identifier and the bare
; pattern constrains nothing, so without this a field receiver arrives with no
; receiver at all — which the resolver reads as an implicit receiver on the
; caller's own class.
;
; ``this``/``super`` only, which is the rule every sibling grammar keeps: a
; captured receiver must name something in the *caller's* scope, because the
; receiver strategies type a field against the caller's class. Lifting the
; nearest name out of ``a.b.method()`` would offer ``b`` — which belongs to
; ``a``'s type — to be typed as a field of the caller, and bind a same-named
; one. ``Outer.this.method()`` declines on its own, since a qualified ``this``
; is a ``this`` node and not an ``identifier``, and that is right: it is an
; implicit receiver.
(method_invocation
  object: (field_access
    object: [(this) (super)]
    field: (identifier) @call.receiver
  )
  name: (identifier) @call.target
  arguments: (argument_list) @call.arguments
) @call.site

; Self-dispatch: this.method(args). The pattern above claims ``this.field.m()``
; and the bare pattern claims everything, so without this a plain ``this.m()``
; arrives receiver-less and is read as an implicit receiver — which resolves by
; bare name through the flat same-file index instead of against the caller's own
; class.
;
; ``super`` stays out: ``super.m()`` arrives bare, where the caller's own class
; answers it and the recursion refusal drops an override calling itself. No
; member strategy answers a ``super`` receiver for Java, so capturing one would
; only move the call onto its bare-name fallback, which skips the caller's class.
(method_invocation
  object: (this) @call.receiver
  name: (identifier) @call.target
  arguments: (argument_list) @call.arguments
) @call.site

; Fluent construction: new Foo().bar(args). The receiver type is written at the
; call site, so this lands on the receiver-names-a-class tier — which only binds
; when that class declares the method — instead of the bare-name pool. Same
; shape as csharp.scm's ``new Builder().Method()`` (#1680).
(method_invocation
  object: (object_creation_expression
    type: [
      (type_identifier) @call.receiver
      (generic_type (type_identifier) @call.receiver)
    ]
  )
  name: (identifier) @call.target
  arguments: (argument_list) @call.arguments
) @call.site

; Chained method call: obj.method1().method2(args)
(method_invocation
  object: (method_invocation) @call.receiver_call
  name: (identifier) @call.target
  arguments: (argument_list) @call.arguments
) @call.site

; Constructor: new ClassName(args)
(object_creation_expression
  type: (type_identifier) @call.target
  arguments: (argument_list) @call.arguments
) @call.site

; Method reference: Foo::bar — treat as a use of Foo.bar so the referenced
; method is not flagged unused. The argument list is empty (no @call.arguments).
(method_reference
  . (identifier) @call.receiver
  (identifier) @call.target
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
; lang_helpers/type_heads.py unwraps ``T[]`` / ``Foo<...>`` / ``ns.Foo`` / annotated
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
