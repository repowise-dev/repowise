; =============================================================================
; repowise — C symbol and import queries
; Uses the tree-sitter-cpp grammar (superset of C)
; =============================================================================

; ---------------------------------------------------------------------------
; Symbols
; ---------------------------------------------------------------------------

(function_definition
  declarator: (function_declarator
    declarator: (identifier) @symbol.name
    parameters: (parameter_list) @symbol.params
  )
) @symbol.def

(struct_specifier
  name: (type_identifier) @symbol.name
) @symbol.def

(enum_specifier
  name: (type_identifier) @symbol.name
) @symbol.def

; ---------------------------------------------------------------------------
; Imports (#include directives)
; ---------------------------------------------------------------------------

(preproc_include
  path: (system_lib_string) @import.module
) @import.statement

(preproc_include
  path: (string_literal) @import.module
) @import.statement
