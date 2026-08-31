; =============================================================================
; repowise — VB.NET symbol, import, and call queries
; tree-sitter-vb-dotnet (fixed fork, c29b08e)
; =============================================================================

; ---------------------------------------------------------------------------
; Symbols — modifier-capturing patterns first (dedup keeps first match)
; ---------------------------------------------------------------------------

; Class / module / structure / interface declarations. VB.NET nests all of
; these inside namespace_block. The container *name* comes from the first
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

; Constructors: Public Sub New(...) — the grammar drops the ``New`` token
; entirely (constructor_declaration has no ``name`` field), so no name-based
; pattern is possible here. Parameter types still feed the DI backbone below.

; ---------------------------------------------------------------------------
; Symbols — fallback without modifiers
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

; ---------------------------------------------------------------------------
; Namespaces — Imports ... (module-level, mirrors C# using directives)
; ---------------------------------------------------------------------------
(imports_statement
  namespace: (_) @import.module
) @import.statement

; ---------------------------------------------------------------------------
; Calls — invocation has target/arguments fields; member_access has
; object/member fields. The grammar names the callee ``target``.
; ---------------------------------------------------------------------------

; Simple call: Method(args)
(invocation
  target: (identifier) @call.target
  arguments: (argument_list) @call.arguments
) @call.site

; Member call: obj.Method(args) — receiver carries the type. The grammar's
; ``object`` field is typed ``expression`` (a wrapper), so a bare
; ``(identifier)`` there is impossible; the wildcard captures the wrapper and
; resolution reads the identifier text out of it.
(invocation
  target: (member_access
    object: (_) @call.receiver
    member: (identifier) @call.target)
  arguments: (argument_list) @call.arguments
) @call.site

; Chained call: factory().Method(args) — object is an expression wrapper
; again, so the wildcard captures it; resolution can inspect whether it wraps
; an invocation.
(invocation
  target: (member_access
    object: (_) @call.receiver_call
    member: (identifier) @call.target)
  arguments: (argument_list) @call.arguments
) @call.site

; Constructor: New ClassName(args) — argument_list is an unnamed child
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
