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

(interface_declaration
  name: (identifier) @symbol.name
) @symbol.def

(enum_declaration
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
