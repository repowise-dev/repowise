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
; ``super`` is deliberately NOT in this alternation, and the reason is narrower
; than "super is unsafe". Capturing any receiver makes the bare twin
; member-shaped, which suppresses ``_enclosing_class_method``'s recursion
; refusal — the guard that emits no edge when the flat index hands a method its
; own name. The call then falls through to the same-file tier, and THAT is only
; wrong when the file declares the target name on MORE THAN ONE class: the flat
; index is last-wins, so it answers with a sibling's method.
;
; So the hazard is a property of the file, not of the keyword, and ``this`` is
; not immune to it — it is merely far less likely to sit in such a file.
; Measured on caffeine: ``super`` costs 2 wrong edges (``DelegationBenchmark``
; declares ``get`` on both ``InheritMap`` and ``DelegateMap``), while all 3
; self-recursive ``this.add()`` sites are safe because ``IntegerSum`` is the only
; class in its file declaring ``add`` and the ``callee != caller`` guard then
; refuses. ``super`` is excluded because its population sits in override-heavy
; files where sibling classes share names; that costs recall only, which is the
; right direction to err in. If a future session adds ``super``, the fix it
; needs first is a same-file index keyed by class, not a wider capture.
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
  object: (method_invocation)
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
