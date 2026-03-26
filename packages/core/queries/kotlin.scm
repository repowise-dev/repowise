; =============================================================================
; repowise — Kotlin symbol and import queries
; tree-sitter-kotlin (install separately if needed)
; =============================================================================

(function_declaration
  (simple_identifier) @symbol.name
  (function_value_parameters) @symbol.params
) @symbol.def

(class_declaration
  (type_identifier) @symbol.name
) @symbol.def

(object_declaration
  (type_identifier) @symbol.name
) @symbol.def

(interface_declaration
  (type_identifier) @symbol.name
) @symbol.def

(import_header
  (identifier) @import.module
) @import.statement
