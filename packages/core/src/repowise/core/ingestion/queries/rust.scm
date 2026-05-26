; =============================================================================
; repowise — Rust symbol and import queries
; tree-sitter-rust >= 0.23
; =============================================================================

; ---------------------------------------------------------------------------
; Symbols — with visibility modifier (must come before without)
; ---------------------------------------------------------------------------

; function_item WITH visibility
(function_item
  (visibility_modifier) @symbol.modifiers
  name: (identifier) @symbol.name
  parameters: (parameters) @symbol.params
) @symbol.def

; function_item WITHOUT visibility
(function_item
  name: (identifier) @symbol.name
  parameters: (parameters) @symbol.params
) @symbol.def

; struct_item WITH visibility
(struct_item
  (visibility_modifier) @symbol.modifiers
  name: (type_identifier) @symbol.name
) @symbol.def

; struct_item WITHOUT visibility
(struct_item
  name: (type_identifier) @symbol.name
) @symbol.def

; enum_item WITH visibility
(enum_item
  (visibility_modifier) @symbol.modifiers
  name: (type_identifier) @symbol.name
) @symbol.def

; enum_item WITHOUT visibility
(enum_item
  name: (type_identifier) @symbol.name
) @symbol.def

; trait_item WITH visibility
(trait_item
  (visibility_modifier) @symbol.modifiers
  name: (type_identifier) @symbol.name
) @symbol.def

; trait_item WITHOUT visibility
(trait_item
  name: (type_identifier) @symbol.name
) @symbol.def

; impl block — the "type" field identifies what is being implemented
; impl does NOT need visibility capture (inherits from type/trait)
(impl_item
  type: (type_identifier) @symbol.name
) @symbol.def

; const_item WITH visibility
(const_item
  (visibility_modifier) @symbol.modifiers
  name: (identifier) @symbol.name
) @symbol.def

; const_item WITHOUT visibility
(const_item
  name: (identifier) @symbol.name
) @symbol.def

; type_item WITH visibility
(type_item
  (visibility_modifier) @symbol.modifiers
  name: (type_identifier) @symbol.name
) @symbol.def

; type_item WITHOUT visibility
(type_item
  name: (type_identifier) @symbol.name
) @symbol.def

; mod_item WITH visibility (symbol definition)
(mod_item
  (visibility_modifier) @symbol.modifiers
  name: (identifier) @symbol.name
) @symbol.def

; mod_item WITHOUT visibility (symbol definition)
(mod_item
  name: (identifier) @symbol.name
) @symbol.def

; macro_rules! my_macro { ... }
; Note: macro_definition does not support visibility_modifier in tree-sitter-rust.
; Visibility for macros is handled via #[macro_export] attribute instead.
(macro_definition
  name: (identifier) @symbol.name
) @symbol.def

; ---------------------------------------------------------------------------
; Imports
; ---------------------------------------------------------------------------

(use_declaration
  argument: (_) @import.module
) @import.statement

;; mod foo; declarations (without body block) act as imports in Rust
(mod_item
  name: (identifier) @import.module
  !body
) @import.statement

; ---------------------------------------------------------------------------
; Calls
; ---------------------------------------------------------------------------

; Simple function call: foo(arg1, arg2)
(call_expression
  function: (identifier) @call.target
  arguments: (arguments) @call.arguments
) @call.site

; Method call: obj.method(args)
(call_expression
  function: (field_expression
    value: (identifier) @call.receiver
    field: (field_identifier) @call.target
  )
  arguments: (arguments) @call.arguments
) @call.site

; Scoped function call: module::function(args)
(call_expression
  function: (scoped_identifier
    name: (identifier) @call.target
  )
  arguments: (arguments) @call.arguments
) @call.site

; Chained call: obj.method1().method2(args)
(call_expression
  function: (field_expression
    value: (call_expression)
    field: (field_identifier) @call.target
  )
  arguments: (arguments) @call.arguments
) @call.site

; Macro invocation: println!(...), vec![...]
(macro_invocation
  macro: (identifier) @call.target
) @call.site
