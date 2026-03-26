; =============================================================================
; repowise — C++ symbol and import queries
; tree-sitter-cpp >= 0.23
; (Also used for .c files — C is a subset of this grammar for our purposes)
; =============================================================================

; ---------------------------------------------------------------------------
; Symbols
; ---------------------------------------------------------------------------

; Function definition: ReturnType funcName(params) { body }
; The name is nested inside function_declarator
(function_definition
  declarator: (function_declarator
    declarator: (identifier) @symbol.name
    parameters: (parameter_list) @symbol.params
  )
) @symbol.def

; Qualified function definition: ReturnType ClassName::method(params) { }
(function_definition
  declarator: (function_declarator
    declarator: (qualified_identifier
      name: (identifier) @symbol.name
    )
    parameters: (parameter_list) @symbol.params
  )
) @symbol.def

; Class
(class_specifier
  name: (type_identifier) @symbol.name
) @symbol.def

; Struct
(struct_specifier
  name: (type_identifier) @symbol.name
) @symbol.def

; Enum (type_identifier is a direct child, not a named field in this grammar)
(enum_specifier
  (type_identifier) @symbol.name
) @symbol.def

; Namespace
(namespace_definition
  name: (namespace_identifier) @symbol.name
) @symbol.def

; ---------------------------------------------------------------------------
; Imports (#include directives)
; ---------------------------------------------------------------------------

; #include <header>
(preproc_include
  path: (system_lib_string) @import.module
) @import.statement

; #include "local_header"
(preproc_include
  path: (string_literal) @import.module
) @import.statement
