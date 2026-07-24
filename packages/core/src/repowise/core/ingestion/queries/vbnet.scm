; =============================================================================
; repowise — VB.NET symbol, import, and call queries
; tree-sitter-vbnet 0.1.0
;
; Known grammar gaps (v0.1.0):
;   - `Inherits` / `Implements` clauses are NOT parsed here — they land in an
;     ERROR node. Heritage is extracted via a regex fallback over the class
;     body text instead (see extractors/heritage/vbnet.py).
;   - `constructor_declaration` exposes no `name:` field (VB's `Sub New` has
;     no identifier node for "New") — the patterns below borrow the enclosing
;     type's `name:` field as the constructor's `@symbol.name` instead, which
;     matches VB's own semantics (a constructor's name IS the type's name).
; =============================================================================

; ---------------------------------------------------------------------------
; Symbols — modifier-capturing patterns first (dedup keeps first match)
; ---------------------------------------------------------------------------

(class_block
  (modifiers) @symbol.modifiers
  name: (identifier) @symbol.name
) @symbol.def

(interface_block
  (modifiers) @symbol.modifiers
  name: (identifier) @symbol.name
) @symbol.def

(structure_block
  (modifiers) @symbol.modifiers
  name: (identifier) @symbol.name
) @symbol.def

(module_block
  (modifiers) @symbol.modifiers
  name: (identifier) @symbol.name
) @symbol.def

(enum_block
  (modifiers) @symbol.modifiers
  name: (identifier) @symbol.name
) @symbol.def

(method_declaration
  (modifiers) @symbol.modifiers
  name: (identifier) @symbol.name
  parameters: (parameter_list) @symbol.params
) @symbol.def

(constructor_declaration
  (modifiers) @symbol.modifiers
  parameters: (parameter_list) @symbol.params
) @symbol.def

(property_declaration
  (modifiers) @symbol.modifiers
  name: (identifier) @symbol.name
) @symbol.def

(delegate_declaration
  (modifiers) @symbol.modifiers
  name: (identifier) @symbol.name
  parameters: (parameter_list) @symbol.params
) @symbol.def

(event_declaration
  (modifiers) @symbol.modifiers
  name: (identifier) @symbol.name
) @symbol.def

; Requires an `as_clause` sibling on the declarator. This is deliberate, not
; just style-matching: the `Inherits X` / `Implements X, Y` ERROR-recovery
; (see file header) misparses those clauses as bare field_declarations with
; no as_clause — e.g. `Implements IFoo, IBar` becomes a "field_declaration"
; with two type-less variable_declarators. Requiring as_clause excludes that
; recovery debris; the cost is that fields relying on `Option Infer` (no
; explicit `As Type`) aren't captured as symbols.
(field_declaration
  (modifiers) @symbol.modifiers
  (variable_declarator
    name: (identifier) @symbol.name
    (as_clause))
) @symbol.def

; ---------------------------------------------------------------------------
; Symbols — fallback without modifiers
; ---------------------------------------------------------------------------

(class_block
  name: (identifier) @symbol.name
) @symbol.def

(interface_block
  name: (identifier) @symbol.name
) @symbol.def

(structure_block
  name: (identifier) @symbol.name
) @symbol.def

(module_block
  name: (identifier) @symbol.name
) @symbol.def

(enum_block
  name: (identifier) @symbol.name
) @symbol.def

(method_declaration
  name: (identifier) @symbol.name
  parameters: (parameter_list) @symbol.params
) @symbol.def

(constructor_declaration
  parameters: (parameter_list) @symbol.params
) @symbol.def

(property_declaration
  name: (identifier) @symbol.name
) @symbol.def

(delegate_declaration
  name: (identifier) @symbol.name
  parameters: (parameter_list) @symbol.params
) @symbol.def

(event_declaration
  name: (identifier) @symbol.name
) @symbol.def

(field_declaration
  (variable_declarator
    name: (identifier) @symbol.name
    (as_clause))
) @symbol.def

; ---------------------------------------------------------------------------
; Constructors — `Sub New` has no name node of its own; borrow the enclosing
; type's name field (matches VB's own semantics: a ctor IS named after its
; type). Modules can't declare `Sub New`, so only class/structure need this.
; ---------------------------------------------------------------------------

(class_block
  name: (identifier) @symbol.name
  (constructor_declaration
    parameters: (parameter_list) @symbol.params
  ) @symbol.def)

(structure_block
  name: (identifier) @symbol.name
  (constructor_declaration
    parameters: (parameter_list) @symbol.params
  ) @symbol.def)

; ---------------------------------------------------------------------------
; Namespaces
; ---------------------------------------------------------------------------

(namespace_block
  name: (namespace_name) @symbol.name
) @symbol.def

; ---------------------------------------------------------------------------
; Enum members
; ---------------------------------------------------------------------------

(enum_member
  name: (identifier) @symbol.name
) @symbol.def

; ---------------------------------------------------------------------------
; Imports
; ---------------------------------------------------------------------------

(imports_statement
  namespace: (namespace_name) @import.module
) @import.statement

; ---------------------------------------------------------------------------
; Calls
; ---------------------------------------------------------------------------

; Simple call: Method(args)
(invocation
  target: (identifier) @call.target
  arguments: (argument_list) @call.arguments
) @call.site

; Member call: obj.Method(args)
(invocation
  target: (member_access
    object: (expression (identifier) @call.receiver)
    member: (identifier) @call.target
  )
  arguments: (argument_list) @call.arguments
) @call.site

; Constructor: New ClassName(args)
(new_expression
  type: (type (namespace_name (identifier) @call.target))
  (argument_list) @call.arguments
) @call.site

; ---------------------------------------------------------------------------
; Parameter type references
;
; Same purpose as C#'s @param.type (see queries/csharp.scm) — backs
; type_ref_resolution.py's DI-edge resolution so ctor-injected types don't
; read as orphans. VB's parameter shape differs from C#'s: the type lives
; inside an `as_clause` child rather than directly on a `type:` field of
; `parameter` itself.
;
; Known gap: a generic `(Of T)` parameter type parses fine and its head
; (e.g. `List`) is still captured here, but the grammar's ERROR recovery
; around the `(Of ...)` clause corrupts any parameter *after* it in the
; same list — that trailing parameter's type is silently not captured.
; ---------------------------------------------------------------------------

(constructor_declaration
  parameters: (parameter_list
    (parameter
      (as_clause
        type: (type) @param.type))))

(method_declaration
  parameters: (parameter_list
    (parameter
      (as_clause
        type: (type) @param.type))))

(delegate_declaration
  parameters: (parameter_list
    (parameter
      (as_clause
        type: (type) @param.type))))
