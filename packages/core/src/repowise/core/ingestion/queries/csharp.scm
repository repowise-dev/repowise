; =============================================================================
; repowise — C# symbol, import, and call queries
; tree-sitter-c-sharp >= 0.23
; =============================================================================

; ---------------------------------------------------------------------------
; Symbols — modifier-capturing patterns first (dedup keeps first match)
; ---------------------------------------------------------------------------

(class_declaration
  (modifier) @symbol.modifiers
  name: (identifier) @symbol.name
) @symbol.def

(interface_declaration
  (modifier) @symbol.modifiers
  name: (identifier) @symbol.name
) @symbol.def

(struct_declaration
  (modifier) @symbol.modifiers
  name: (identifier) @symbol.name
) @symbol.def

(enum_declaration
  (modifier) @symbol.modifiers
  name: (identifier) @symbol.name
) @symbol.def

(method_declaration
  (modifier) @symbol.modifiers
  name: (identifier) @symbol.name
  parameters: (parameter_list) @symbol.params
) @symbol.def

(constructor_declaration
  (modifier) @symbol.modifiers
  name: (identifier) @symbol.name
  parameters: (parameter_list) @symbol.params
) @symbol.def

(property_declaration
  (modifier) @symbol.modifiers
  name: (identifier) @symbol.name
) @symbol.def

(record_declaration
  (modifier) @symbol.modifiers
  name: (identifier) @symbol.name
) @symbol.def

(delegate_declaration
  (modifier) @symbol.modifiers
  name: (identifier) @symbol.name
  parameters: (parameter_list) @symbol.params
) @symbol.def

(event_declaration
  (modifier) @symbol.modifiers
  name: (identifier) @symbol.name
) @symbol.def

(event_field_declaration
  (modifier) @symbol.modifiers
  (variable_declaration
    (variable_declarator
      name: (identifier) @symbol.name))
) @symbol.def

(field_declaration
  (modifier) @symbol.modifiers
  (variable_declaration
    (variable_declarator
      name: (identifier) @symbol.name))
) @symbol.def

; ---------------------------------------------------------------------------
; Symbols — fallback without modifiers
; ---------------------------------------------------------------------------

(class_declaration
  name: (identifier) @symbol.name
) @symbol.def

(interface_declaration
  name: (identifier) @symbol.name
) @symbol.def

(struct_declaration
  name: (identifier) @symbol.name
) @symbol.def

(enum_declaration
  name: (identifier) @symbol.name
) @symbol.def

(method_declaration
  name: (identifier) @symbol.name
  parameters: (parameter_list) @symbol.params
) @symbol.def

(constructor_declaration
  name: (identifier) @symbol.name
  parameters: (parameter_list) @symbol.params
) @symbol.def

(property_declaration
  name: (identifier) @symbol.name
) @symbol.def

(record_declaration
  name: (identifier) @symbol.name
) @symbol.def

(delegate_declaration
  name: (identifier) @symbol.name
  parameters: (parameter_list) @symbol.params
) @symbol.def

(event_declaration
  name: (identifier) @symbol.name
) @symbol.def

(event_field_declaration
  (variable_declaration
    (variable_declarator
      name: (identifier) @symbol.name))
) @symbol.def

(field_declaration
  (variable_declaration
    (variable_declarator
      name: (identifier) @symbol.name))
) @symbol.def

; ---------------------------------------------------------------------------
; Namespaces (block + file-scoped C# 10+)
; ---------------------------------------------------------------------------

(namespace_declaration
  name: (qualified_name) @symbol.name
) @symbol.def

(namespace_declaration
  name: (identifier) @symbol.name
) @symbol.def

(file_scoped_namespace_declaration
  name: (qualified_name) @symbol.name
) @symbol.def

(file_scoped_namespace_declaration
  name: (identifier) @symbol.name
) @symbol.def

; ---------------------------------------------------------------------------
; Enum members
; ---------------------------------------------------------------------------

(enum_member_declaration
  name: (identifier) @symbol.name
) @symbol.def

; ---------------------------------------------------------------------------
; Imports (using directives)
; ---------------------------------------------------------------------------

(using_directive
  (identifier) @import.module
) @import.statement

(using_directive
  (qualified_name) @import.module
) @import.statement

; ---------------------------------------------------------------------------
; Calls
; ---------------------------------------------------------------------------

; Simple call: Method(args) and Method<T>(args).
; A type argument list wraps the name in a `generic_name`, so matching only
; `(identifier)` drops every generic call. The capture sits inside each branch
; so the target is the bare name either way — capturing the alternation itself
; would name the target `Method<T>`, which no index holds.
(invocation_expression
  function: [
    (identifier) @call.target
    (generic_name (identifier) @call.target)
  ]
  arguments: (argument_list) @call.arguments
) @call.site

; Member call: obj.Method(args) — and self-dispatch: this.Method(args).
; ``this`` is an ANONYMOUS node in tree-sitter-c-sharp, so it is matched as the
; literal token "this"; `(this)` does not compile. See typescript.scm for why
; this is an alternation rather than a second pattern.
(invocation_expression
  function: (member_access_expression
    expression: [(identifier) "this"] @call.receiver
    name: [
      (identifier) @call.target
      (generic_name (identifier) @call.target)
    ]
  )
  arguments: (argument_list) @call.arguments
) @call.site

; Chained call: factory().Method(args). Carry the inner invocation as an AST
; node so resolution can use its declared return type without scanning text.
(invocation_expression
  function: (member_access_expression
    expression: (invocation_expression) @call.receiver_call
    name: [
      (identifier) @call.target
      (generic_name (identifier) @call.target)
    ]
  )
  arguments: (argument_list) @call.arguments
) @call.site

; Fluent construction: new Builder().Method(args).
; The receiver is captured from the constructed type rather than from a
; variable, because here the type is written at the call site. That keeps the
; site on the resolver's receiver-names-a-class path, which only binds when
; that class declares the method, instead of the bare-name pool.
(invocation_expression
  function: (member_access_expression
    expression: (object_creation_expression
      type: [
        (identifier) @call.receiver
        (generic_name (identifier) @call.receiver)
      ]
    )
    name: [
      (identifier) @call.target
      (generic_name (identifier) @call.target)
    ]
  )
  arguments: (argument_list) @call.arguments
) @call.site

; Constructor: new ClassName(args) and new ClassName<T>(args)
(object_creation_expression
  type: [
    (identifier) @call.target
    (generic_name (identifier) @call.target)
  ]
  arguments: (argument_list) @call.arguments
) @call.site

; ---------------------------------------------------------------------------
; Parameter type references
;
; Captures the type node of every parameter inside a constructor, method,
; or delegate signature so the graph builder can emit a "type_use" edge
; from the containing file to the file declaring the type. This is the
; backbone of DI-heavy resolution in C# / .NET — without it, classes that
; exist only to be injected as ctor parameters read as orphans.
; ---------------------------------------------------------------------------

(constructor_declaration
  parameters: (parameter_list
    (parameter
      type: (_) @param.type)))

(method_declaration
  parameters: (parameter_list
    (parameter
      type: (_) @param.type)))

(delegate_declaration
  parameters: (parameter_list
    (parameter
      type: (_) @param.type)))

; Primary constructors on records (C# 9+): `record Foo(IBar bar)`.
; tree-sitter-c-sharp 0.23 exposes the parameter list as an unnamed
; child of record_declaration rather than a named field, so we omit
; the `parameters:` field anchor that constructor_declaration uses.
(record_declaration
  (parameter_list
    (parameter
      type: (_) @param.type)))

; Generic type arguments are type uses even when they occur on an invocation
; rather than in a declaration, e.g. `services.AddScoped<IService, Service>()`.
; The pattern is global so nested arguments are captured recursively by their
; own `type_argument_list` node.
(type_argument_list
  (_) @param.type)

; `typeof(Service)` is an explicit type reference with an unambiguous type
; field, so it uses the same extraction and resolution path.
(typeof_expression
  type: (_) @param.type)
