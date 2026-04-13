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
