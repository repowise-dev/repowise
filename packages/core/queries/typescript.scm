; =============================================================================
; repowise — TypeScript symbol and import queries
; tree-sitter-typescript >= 0.23
; =============================================================================

; ---------------------------------------------------------------------------
; Symbols
; ---------------------------------------------------------------------------

; Top-level function declaration
(function_declaration
  name: (identifier) @symbol.name
  parameters: (formal_parameters) @symbol.params
) @symbol.def

; Generator function
(generator_function_declaration
  name: (identifier) @symbol.name
  parameters: (formal_parameters) @symbol.params
) @symbol.def

; Class declaration
(class_declaration
  name: (type_identifier) @symbol.name
) @symbol.def

; Abstract class
(abstract_class_declaration
  name: (type_identifier) @symbol.name
) @symbol.def

; Interface
(interface_declaration
  name: (type_identifier) @symbol.name
) @symbol.def

; Type alias
(type_alias_declaration
  name: (type_identifier) @symbol.name
) @symbol.def

; Enum
(enum_declaration
  name: (identifier) @symbol.name
) @symbol.def

; Method inside class body
(method_definition
  name: (property_identifier) @symbol.name
  parameters: (formal_parameters) @symbol.params
) @symbol.def

; Arrow function assigned to const/let: const foo = (...) => { }
(lexical_declaration
  (variable_declarator
    name: (identifier) @symbol.name
    value: (arrow_function
      parameters: (formal_parameters) @symbol.params
    )
  )
) @symbol.def

; Public method accessor modifier capture
(method_definition
  (accessibility_modifier) @symbol.modifiers
  name: (property_identifier) @symbol.name
) @symbol.def

; ---------------------------------------------------------------------------
; Imports
; ---------------------------------------------------------------------------

; import { A, B } from "./module"
; import type { T } from "./types"
; import DefaultExport from "module"
(import_statement
  source: (string) @import.module
) @import.statement
