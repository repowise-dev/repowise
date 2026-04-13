; =============================================================================
; repowise — PHP symbol, import, and call queries
; tree-sitter-php >= 0.23 (uses language_php())
; =============================================================================

; ---------------------------------------------------------------------------
; Symbols
; ---------------------------------------------------------------------------

(class_declaration
  name: (name) @symbol.name
) @symbol.def

(interface_declaration
  name: (name) @symbol.name
) @symbol.def

(trait_declaration
  name: (name) @symbol.name
) @symbol.def

(enum_declaration
  name: (name) @symbol.name
) @symbol.def

(method_declaration
  (visibility_modifier) @symbol.modifiers
  name: (name) @symbol.name
  parameters: (formal_parameters) @symbol.params
) @symbol.def

(function_definition
  name: (name) @symbol.name
  parameters: (formal_parameters) @symbol.params
) @symbol.def

; ---------------------------------------------------------------------------
; Imports (use declarations)
; ---------------------------------------------------------------------------

(namespace_use_declaration
  (namespace_use_clause
    (qualified_name) @import.module
  )
) @import.statement

; ---------------------------------------------------------------------------
; Calls
; ---------------------------------------------------------------------------

; Function call: func(args)
(function_call_expression
  function: (name) @call.target
  (arguments) @call.arguments
) @call.site

; Qualified function call: Namespace\func(args)
(function_call_expression
  function: (qualified_name
    (name) @call.target
  )
  (arguments) @call.arguments
) @call.site

; Method call: $obj->method(args)
(member_call_expression
  name: (name) @call.target
  (arguments) @call.arguments
) @call.site

; Static call: ClassName::method(args)
(scoped_call_expression
  name: (name) @call.target
  (arguments) @call.arguments
) @call.site

; Constructor: new ClassName(args)
(object_creation_expression
  (qualified_name
    (name) @call.target
  )
  (arguments) @call.arguments
) @call.site

; Constructor (simple name): new ClassName(args)
(object_creation_expression
  (name) @call.target
  (arguments) @call.arguments
) @call.site
