; =============================================================================
; repowise — C# symbol, import, and call queries
; tree-sitter-c-sharp >= 0.23
; =============================================================================

; ---------------------------------------------------------------------------
; Symbols — modifier-capturing patterns first (dedup keeps first match)
; ---------------------------------------------------------------------------

(class_declaration
  (attribute_list)? @symbol.modifiers
  (modifier) @symbol.modifiers
  name: (identifier) @symbol.name
) @symbol.def

(interface_declaration
  (attribute_list)? @symbol.modifiers
  (modifier) @symbol.modifiers
  name: (identifier) @symbol.name
) @symbol.def

(struct_declaration
  (attribute_list)? @symbol.modifiers
  (modifier) @symbol.modifiers
  name: (identifier) @symbol.name
) @symbol.def

(enum_declaration
  (attribute_list)? @symbol.modifiers
  (modifier) @symbol.modifiers
  name: (identifier) @symbol.name
) @symbol.def

(method_declaration
  (attribute_list)? @symbol.modifiers
  (modifier) @symbol.modifiers
  name: (identifier) @symbol.name
  parameters: (parameter_list) @symbol.params
) @symbol.def

(constructor_declaration
  (attribute_list)? @symbol.modifiers
  (modifier) @symbol.modifiers
  name: (identifier) @symbol.name
  parameters: (parameter_list) @symbol.params
) @symbol.def

(property_declaration
  (attribute_list)? @symbol.modifiers
  (modifier) @symbol.modifiers
  name: (identifier) @symbol.name
) @symbol.def

(record_declaration
  (attribute_list)? @symbol.modifiers
  (modifier) @symbol.modifiers
  name: (identifier) @symbol.name
) @symbol.def

(delegate_declaration
  (attribute_list)? @symbol.modifiers
  (modifier) @symbol.modifiers
  name: (identifier) @symbol.name
  parameters: (parameter_list) @symbol.params
) @symbol.def

(event_declaration
  (attribute_list)? @symbol.modifiers
  (modifier) @symbol.modifiers
  name: (identifier) @symbol.name
) @symbol.def

(event_field_declaration
  (attribute_list)? @symbol.modifiers
  (modifier) @symbol.modifiers
  (variable_declaration
    (variable_declarator
      name: (identifier) @symbol.name))
) @symbol.def

(field_declaration
  (attribute_list)? @symbol.modifiers
  (modifier) @symbol.modifiers
  (variable_declaration
    (variable_declarator
      name: (identifier) @symbol.name))
) @symbol.def

; ---------------------------------------------------------------------------
; Symbols — fallback without modifiers
; ---------------------------------------------------------------------------

(class_declaration
  (attribute_list)? @symbol.modifiers
  name: (identifier) @symbol.name
) @symbol.def

(interface_declaration
  (attribute_list)? @symbol.modifiers
  name: (identifier) @symbol.name
) @symbol.def

(struct_declaration
  (attribute_list)? @symbol.modifiers
  name: (identifier) @symbol.name
) @symbol.def

(enum_declaration
  (attribute_list)? @symbol.modifiers
  name: (identifier) @symbol.name
) @symbol.def

(method_declaration
  (attribute_list)? @symbol.modifiers
  name: (identifier) @symbol.name
  parameters: (parameter_list) @symbol.params
) @symbol.def

(constructor_declaration
  (attribute_list)? @symbol.modifiers
  name: (identifier) @symbol.name
  parameters: (parameter_list) @symbol.params
) @symbol.def

(property_declaration
  (attribute_list)? @symbol.modifiers
  name: (identifier) @symbol.name
) @symbol.def

(record_declaration
  (attribute_list)? @symbol.modifiers
  name: (identifier) @symbol.name
) @symbol.def

(delegate_declaration
  (attribute_list)? @symbol.modifiers
  name: (identifier) @symbol.name
  parameters: (parameter_list) @symbol.params
) @symbol.def

(event_declaration
  (attribute_list)? @symbol.modifiers
  name: (identifier) @symbol.name
) @symbol.def

(event_field_declaration
  (attribute_list)? @symbol.modifiers
  (variable_declaration
    (variable_declarator
      name: (identifier) @symbol.name))
) @symbol.def

(field_declaration
  (attribute_list)? @symbol.modifiers
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

; Simple call: Method(args)
(invocation_expression
  function: (identifier) @call.target
  arguments: (argument_list) @call.arguments
) @call.site

; Member call: obj.Method(args)
(invocation_expression
  function: (member_access_expression
    expression: (identifier) @call.receiver
    name: (identifier) @call.target
  )
  arguments: (argument_list) @call.arguments
) @call.site

; Constructor: new ClassName(args)
(object_creation_expression
  type: (identifier) @call.target
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
