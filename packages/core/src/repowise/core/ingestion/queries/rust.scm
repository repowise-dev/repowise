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

; impl with generic type target: impl<T> Foo<T> { }
(impl_item
  type: (generic_type
    type: (type_identifier) @symbol.name
  )
) @symbol.def

; impl with scoped type target: impl Trait for path::Foo
(impl_item
  type: (scoped_type_identifier
    name: (type_identifier) @symbol.name
  )
) @symbol.def

; impl with reference type target: impl Trait for &Foo
(impl_item
  type: (reference_type
    type: (type_identifier) @symbol.name
  )
) @symbol.def

; union_item WITH visibility
(union_item
  (visibility_modifier) @symbol.modifiers
  name: (type_identifier) @symbol.name
) @symbol.def

; union_item WITHOUT visibility
(union_item
  name: (type_identifier) @symbol.name
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

; static_item WITH visibility
(static_item
  (visibility_modifier) @symbol.modifiers
  name: (identifier) @symbol.name
) @symbol.def

; static_item WITHOUT visibility
(static_item
  name: (identifier) @symbol.name
) @symbol.def

; Enum variants (inherit visibility from parent enum)
(enum_variant
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
; Re-exports (pub use)
; ---------------------------------------------------------------------------

; pub use path::to::Item
(use_declaration
  (visibility_modifier) @reexport.visibility
  argument: (_) @reexport.source
) @reexport.statement

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
    path: (_) @call.receiver
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

; Method call on self: self.method(args)
(call_expression
  function: (field_expression
    value: (self) @call.receiver
    field: (field_identifier) @call.target
  )
  arguments: (arguments) @call.arguments
) @call.site

; Method call on field access: expr.field.method(args)
(call_expression
  function: (field_expression
    value: (field_expression) @call.receiver
    field: (field_identifier) @call.target
  )
  arguments: (arguments) @call.arguments
) @call.site

; Turbofish function call: foo::<T>(args)
(call_expression
  function: (generic_function
    function: (identifier) @call.target
  )
  arguments: (arguments) @call.arguments
) @call.site

; Turbofish method call: obj.method::<T>(args)
(call_expression
  function: (generic_function
    function: (field_expression
      field: (field_identifier) @call.target
    )
  )
  arguments: (arguments) @call.arguments
) @call.site

; Turbofish scoped call: module::func::<T>(args)
(call_expression
  function: (generic_function
    function: (scoped_identifier
      path: (_) @call.receiver
      name: (identifier) @call.target
    )
  )
  arguments: (arguments) @call.arguments
) @call.site

; Macro invocation: println!(...), vec![...]
(macro_invocation
  macro: (identifier) @call.target
) @call.site

; Scoped macro invocation: module::macro!(...)
(macro_invocation
  macro: (scoped_identifier
    path: (_) @call.receiver
    name: (identifier) @call.target
  )
) @call.site

; Function pointer / callback argument: register(my_handler), vec.iter().map(process)
; Captures top-level identifier arguments in call expressions as references
; to prevent false-positive dead code flags on functions passed by name.
(call_expression
  arguments: (arguments
    (identifier) @call.target
  )
) @call.site

; Type argument in turbofish: func::<MyType>(...), Channel::<MyType>::new()
; Creates a reference edge so MyType is not flagged as unused.
(generic_function
  type_arguments: (type_arguments
    (type_identifier) @call.target
  )
) @call.site

; Scoped identifier in struct field initializer: Foo { field: module::func }
; Captures the final identifier as a reference to prevent false dead code flags.
(field_initializer
  value: (scoped_identifier
    path: (_) @call.receiver
    name: (identifier) @call.target
  )
) @call.site

; Plain identifier in struct field initializer: Foo { field: my_func }
(field_initializer
  value: (identifier) @call.target
) @call.site

; ---------------------------------------------------------------------------
; Type references — track types used in signatures to prevent false dead code
; ---------------------------------------------------------------------------

; Type reference in function parameter: fn foo(x: MyType)
(parameter
  type: (type_identifier) @call.target
) @call.site

; Type reference via &dyn: fn foo(x: &dyn MyTrait)
(parameter
  type: (reference_type
    type: (dynamic_type
      (type_identifier) @call.target
    )
  )
) @call.site

; Type reference via impl Trait: fn foo(x: impl MyTrait)
(parameter
  type: (abstract_type
    (type_identifier) @call.target
  )
) @call.site

; Trait bound in generic parameter: fn foo<T: MyTrait>()
; Also matches where clauses: where T: MyTrait + OtherTrait
(trait_bounds
  (type_identifier) @call.target
) @call.site

; Return type reference: fn foo() -> MyType
(function_item
  return_type: (type_identifier) @call.target
) @call.site

; dyn Trait in type arguments: Box<dyn MyTrait>, Arc<dyn MyTrait>
(type_arguments
  (dynamic_type
    (type_identifier) @call.target
  )
) @call.site

; ---------------------------------------------------------------------------
; Fields
; ---------------------------------------------------------------------------

; Struct fields (named) WITH visibility
(field_declaration
  (visibility_modifier) @symbol.modifiers
  name: (field_identifier) @symbol.name
) @symbol.def

; Struct fields (named) WITHOUT visibility
(field_declaration
  name: (field_identifier) @symbol.name
) @symbol.def
