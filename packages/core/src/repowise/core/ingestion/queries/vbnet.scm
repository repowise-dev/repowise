; =============================================================================
; repowise VB.NET symbol, import, and call queries
; tree-sitter-vb-dotnet 0.2
; =============================================================================

; ---------------------------------------------------------------------------
; Symbols: modifier-capturing patterns first (dedup keeps first match)
; ---------------------------------------------------------------------------

; Class / module / structure / interface declarations. VB.NET nests all of
; these inside namespace_block. The container name comes from the first
; identifier after the keyword token; the block node itself carries the type.
(class_block
  (modifiers) @symbol.modifiers
  name: (identifier) @symbol.name
) @symbol.def

(module_block
  (modifiers) @symbol.modifiers
  name: (identifier) @symbol.name
) @symbol.def

(structure_block
  (modifiers) @symbol.modifiers
  name: (identifier) @symbol.name
) @symbol.def

(interface_block
  (modifiers) @symbol.modifiers
  name: (identifier) @symbol.name
) @symbol.def

(enum_block
  (modifiers) @symbol.modifiers
  name: (identifier) @symbol.name
) @symbol.def

; Methods: Sub / Function. The grammar names the node method_declaration and
; gives it name/parameters/return_type fields.
(method_declaration
  (modifiers) @symbol.modifiers
  name: (identifier) @symbol.name
  parameters: (parameter_list) @symbol.params
) @symbol.def

; Properties: Property Name As Type
(property_declaration
  (modifiers) @symbol.modifiers
  name: (identifier) @symbol.name
) @symbol.def

; Events
(event_declaration
  (modifiers) @symbol.modifiers
  name: (identifier) @symbol.name
) @symbol.def

; Fields: Private _foo As Integer. field_declaration has no name field; the
; name sits one level down in the variable_declarator.
(field_declaration
  (modifiers) @symbol.modifiers
  (variable_declarator
    (identifier) @symbol.name)
) @symbol.def

; Namespaces: the block is named by a dotted namespace_name, not an identifier.
(namespace_block
  (namespace_name) @symbol.name
) @symbol.def

; Constructors: Public Sub New(...). The grammar drops the New token entirely
; (constructor_declaration has no name field), so no name-based pattern is
; possible here. Parameter types still feed the DI backbone below.

; ---------------------------------------------------------------------------
; Symbols: fallback without modifiers
; ---------------------------------------------------------------------------

(class_block
  name: (identifier) @symbol.name
) @symbol.def

(module_block
  name: (identifier) @symbol.name
) @symbol.def

(structure_block
  name: (identifier) @symbol.name
) @symbol.def

(interface_block
  name: (identifier) @symbol.name
) @symbol.def

(enum_block
  name: (identifier) @symbol.name
) @symbol.def

(method_declaration
  name: (identifier) @symbol.name
  parameters: (parameter_list) @symbol.params
) @symbol.def

(property_declaration
  name: (identifier) @symbol.name
) @symbol.def

(event_declaration
  name: (identifier) @symbol.name
) @symbol.def

; Dim baz As Long, with no access modifier.
(field_declaration
  (variable_declarator
    (identifier) @symbol.name)
) @symbol.def

; ---------------------------------------------------------------------------
; Imports (module-level, mirrors C# using directives). The namespace field is
; the target in both the plain and the Imports Alias = Target form.
; ---------------------------------------------------------------------------
(imports_statement
  namespace: (_) @import.module
) @import.statement

; ---------------------------------------------------------------------------
; Calls: invocation has target/arguments fields; member_access has
; object/member fields. The grammar names the callee "target".
; ---------------------------------------------------------------------------

; Simple call: Method(args)
(invocation
  target: (identifier) @call.target
  arguments: (argument_list) @call.arguments
) @call.site

; Member call: obj.Method(args), where the receiver carries the type. The
; object field is always an expression wrapper, so a bare (identifier) there
; cannot match; resolution reads the identifier text out of the wrapper.
(invocation
  target: (member_access
    object: (_) @call.receiver
    member: (identifier) @call.target)
  arguments: (argument_list) @call.arguments
) @call.site

; No @call.receiver_call pattern: the shared reader of that capture looks for
; a name/function field on the inner call, and this grammar's invocation has
; neither, so Factory().Method(args) stays on the receiver-name path above.

; Constructor: New ClassName(args); argument_list is an unnamed child.
(new_expression
  type: (_) @call.target
  (argument_list) @call.arguments
) @call.site

; ---------------------------------------------------------------------------
; Parameter type references (DI backbone, mirrors C#). In this grammar the
; type lives one level down inside an as_clause.
; ---------------------------------------------------------------------------

(parameter_list
  (parameter
    (as_clause
      type: (_) @param.type)))
