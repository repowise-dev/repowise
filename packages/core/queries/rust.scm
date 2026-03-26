; =============================================================================
; repowise — Rust symbol and import queries
; tree-sitter-rust >= 0.23
; =============================================================================

; ---------------------------------------------------------------------------
; Symbols
; ---------------------------------------------------------------------------

(function_item
  name: (identifier) @symbol.name
  parameters: (parameters) @symbol.params
) @symbol.def

(struct_item
  name: (type_identifier) @symbol.name
) @symbol.def

(enum_item
  name: (type_identifier) @symbol.name
) @symbol.def

(trait_item
  name: (type_identifier) @symbol.name
) @symbol.def

; impl block — the "type" field identifies what is being implemented
(impl_item
  type: (type_identifier) @symbol.name
) @symbol.def

(const_item
  name: (identifier) @symbol.name
) @symbol.def

(type_item
  name: (type_identifier) @symbol.name
) @symbol.def

(mod_item
  name: (identifier) @symbol.name
) @symbol.def

; pub visibility modifier
(function_item
  (visibility_modifier) @symbol.modifiers
  name: (identifier) @symbol.name
) @symbol.def

; ---------------------------------------------------------------------------
; Imports
; ---------------------------------------------------------------------------

(use_declaration
  argument: (_) @import.module
) @import.statement
