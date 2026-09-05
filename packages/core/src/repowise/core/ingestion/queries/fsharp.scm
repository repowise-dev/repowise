; =============================================================================
; repowise -- F# symbol, import, call and type-reference queries
; tree-sitter-fsharp (ionide/tree-sitter-fsharp) >= 0.3.11, implementation
; grammar (`language()`); .fsi signature files use a separate grammar and are
; routed to the regex import tier instead; see parser.py.
; =============================================================================
;
; Node-shape reference (read off the installed grammar, not the docs):
;   named_module        := 'module' name:(long_identifier) decl*
;                          -- top-level `module Foo.Bar`, no '='; the rest of
;                             the file hangs off it directly
;   module_defn         := attributes? 'module' (identifier) '=' block:(_)+
;                          -- nested `module X =`; the name is a bare
;                             identifier, never a long_identifier
;   namespace           := 'namespace' name:(long_identifier) decl*
;   function_or_value_defn
;                       := 'let' 'rec'? LEFT '='? type? body:(_)
;                          ('and' LEFT '=' body:(_))*
;                          -- LEFT is function_declaration_left (has params) or
;                             value_declaration_left (plain value). ONE node
;                             holds every clause of a `let rec ... and ...` group,
;                             which is why @symbol.def sits on the LEFT node:
;                             each clause has to be its own symbol.
;   type_definition     := 'type' (record_type_defn | union_type_defn |
;                                  anon_type_defn | enum_type_defn |
;                                  delegate_type_defn | type_extension | ...)
;                          -- the name is `type_name: (identifier|long_identifier)`
;                             inside a `type_name` node, and the outer
;                             type_definition carries no name of its own, so
;                             @symbol.def sits on the inner defn node (which
;                             also lets each shape map to its own kind)
;   member_defn         := 'static'? 'abstract'? 'member' 'val'?
;                          (method_or_prop_defn | member_signature | property_or_ident)
;   application_expression
;                       := LEFT-CURRIED, one node per argument and no field
;                          names: `add 1 2` is application(application(add,1),2)
;   import_decl         := 'open' 'type'? (long_identifier)

; ---------------------------------------------------------------
; Modules and namespaces
; ---------------------------------------------------------------

(named_module
  name: (long_identifier) @symbol.name) @symbol.def

(namespace
  name: (long_identifier) @symbol.name) @symbol.def

(module_defn
  (identifier) @symbol.name) @symbol.def

; ---------------------------------------------------------------
; let bindings -- one symbol per clause.
; The def capture is the LEFT node, not the enclosing
; function_or_value_defn: a `let rec f ... and g ...` group is a single
; defn node holding both clauses, so capturing the defn would give
; two symbols the same span. parser.py extends each symbol's end
; line over its own clause (return-type annotation included).
; ---------------------------------------------------------------

(function_or_value_defn
  (function_declaration_left
    (access_modifier)? @symbol.modifiers
    (identifier) @symbol.name
    (argument_patterns)? @symbol.params) @symbol.def)

; Active pattern `let (|Even|Odd|) n = ...` defines one name per case,
; so this fires once per case name and mints one symbol for each.
(function_or_value_defn
  (function_declaration_left
    (access_modifier)? @symbol.modifiers
    (active_pattern
      (active_pattern_op_name) @symbol.name)) @symbol.def)

; Plain value binding. Only the single-name shape is captured:
; `let a, b = ...` nests both names in a repeat_pattern, where nothing
; in the grammar says which span belongs to which name.
(function_or_value_defn
  (value_declaration_left
    (access_modifier)? @symbol.modifiers
    (identifier_pattern
      (long_identifier_or_op
        (identifier) @symbol.name))) @symbol.def)

; A binding that has BOTH parameters and a return-type annotation
; (`let total (xs: int list) : int = ...`) reparses as a value binding whose
; name sits one level deeper, with the parameter patterns as siblings of the
; name inside the same identifier_pattern. That extra nesting is the only
; thing that tells this shape apart from a plain value, and parser.py reads
; the kind back off it.
(function_or_value_defn
  (value_declaration_left
    (access_modifier)? @symbol.modifiers
    (identifier_pattern
      (long_identifier_or_op
        (long_identifier (identifier) @symbol.name .)))) @symbol.def)

; ---------------------------------------------------------------
; Type definitions. One pattern pair per shape so each keeps its own
; kind; the name is either a bare identifier or a long_identifier (a
; generic head), and the last segment of the latter is the type's own
; name. `type Foo with ...` (type_extension) is deliberately absent: it
; augments a type declared elsewhere rather than declaring one, and
; capturing it minted a second symbol under the id of the type it
; augments. Its members still find `Foo` as their parent, because the
; parent walk in parser.py reads type_extension as an owner.
; ---------------------------------------------------------------

(record_type_defn
  (type_name (access_modifier)? @symbol.modifiers
    type_name: (identifier) @symbol.name)) @symbol.def
(record_type_defn
  (type_name (access_modifier)? @symbol.modifiers
    type_name: (long_identifier (identifier) @symbol.name .))) @symbol.def

(union_type_defn
  (type_name (access_modifier)? @symbol.modifiers
    type_name: (identifier) @symbol.name)) @symbol.def
(union_type_defn
  (type_name (access_modifier)? @symbol.modifiers
    type_name: (long_identifier (identifier) @symbol.name .))) @symbol.def

(enum_type_defn
  (type_name (access_modifier)? @symbol.modifiers
    type_name: (identifier) @symbol.name)) @symbol.def
(enum_type_defn
  (type_name (access_modifier)? @symbol.modifiers
    type_name: (long_identifier (identifier) @symbol.name .))) @symbol.def

(delegate_type_defn
  (type_name (access_modifier)? @symbol.modifiers
    type_name: (identifier) @symbol.name)) @symbol.def
(delegate_type_defn
  (type_name (access_modifier)? @symbol.modifiers
    type_name: (long_identifier (identifier) @symbol.name .))) @symbol.def

(type_abbrev_defn
  (type_name (access_modifier)? @symbol.modifiers
    type_name: (identifier) @symbol.name)) @symbol.def
(type_abbrev_defn
  (type_name (access_modifier)? @symbol.modifiers
    type_name: (long_identifier (identifier) @symbol.name .))) @symbol.def

; Classes, interfaces and structs all share anon_type_defn; the kind is
; refined in the parser by looking at whether every member is abstract.
(anon_type_defn
  (type_name (access_modifier)? @symbol.modifiers
    type_name: (identifier) @symbol.name)) @symbol.def
(anon_type_defn
  (type_name (access_modifier)? @symbol.modifiers
    type_name: (long_identifier (identifier) @symbol.name .))) @symbol.def

(exception_definition
  exception_name: (long_identifier (identifier) @symbol.name .)) @symbol.def

; ---------------------------------------------------------------
; Members. `this.Foo` / `_.Foo` put the receiver under `instance` and
; the name under `method`; `static member Create()` and
; `member val Data = 0` carry a bare property_or_ident instead, and an
; abstract member carries a member_signature.
; ---------------------------------------------------------------

(member_defn
  (method_or_prop_defn
    name: (property_or_ident
      method: (identifier) @symbol.name)
    args: (_)? @symbol.params)) @symbol.def

(member_defn
  (method_or_prop_defn
    name: (property_or_ident
      . (identifier) @symbol.name .)
    args: (_)? @symbol.params)) @symbol.def

(member_defn
  (property_or_ident
    . (identifier) @symbol.name .)) @symbol.def

(member_defn
  (member_signature
    (identifier) @symbol.name)) @symbol.def

; ---------------------------------------------------------------
; Imports -- `open Foo.Bar`, `open type Foo.Bar.Baz`. Both forms put the
; whole dotted path in one long_identifier; parser.py reads the `type`
; token back off the statement, because `open type` names a TYPE and the
; module the file depends on is the path that contains it.
; `#load "x.fsx"` is a script directive, not an open; the regex tier never
; emitted it either, so it stays out rather than arriving in one tier only.
; ---------------------------------------------------------------

(import_decl
  (long_identifier) @import.module) @import.statement

; ---------------------------------------------------------------
; Call graph.
; The callee must be an identifier or a dotted identifier: a literal, a
; lambda or a parenthesised expression in callee position is not a name
; anyone can resolve, so it mints nothing. `Path.Combine(a, b)` collapses
; the whole dotted path into one long_identifier (there is no dot_expression
; for a static path), so the receiver/target split happens in parser.py.
; No @call.arguments: currying means each argument is a separate
; application_expression node, so an argument count read off any single one
; would be wrong.
; ---------------------------------------------------------------

; Bare application -- `add 1 2`, `helper x`. The callee must start
; lowercase: F# spells functions camelCase and reserves an initial capital
; for union cases, types and constructors, none of which is a call to a
; function symbol. `Wrapper 5` and `add 1 2` are the same node shape, so
; the naming convention is the only thing that separates them, and minting
; the union case would bind it to whatever same-named function exists.
((application_expression
   . (long_identifier_or_op
       (identifier) @call.target)) @call.site
 (#not-match? @call.target "^[A-Z]"))

; Dotted static path -- `List.map f xs`, `Path.Combine(a, b)`.
(application_expression
  . (long_identifier_or_op
      (long_identifier) @call.target)) @call.site

; Instance method -- `sb.Append x`. The base is restricted to a plain name:
; a base that is itself an expression (`Counter(1).Increment()`) names no
; receiver the resolver could use, and a wrong receiver is worse than none.
(application_expression
  . (dot_expression
      base: (long_identifier_or_op) @call.receiver
      field: (long_identifier_or_op
        (identifier) @call.target))) @call.site

; Pipe -- `xs |> reverse`. Keyed on the operator text because every other
; infix expression has an ordinary value on its right-hand side; only `|>`
; makes that value the thing being applied.
((infix_expression
   (infix_op) @_op
   .
   (long_identifier_or_op
     (identifier) @call.target) .) @call.site
 (#eq? @_op "|>")
 (#not-match? @call.target "^[A-Z]"))

((infix_expression
   (infix_op) @_op
   .
   (long_identifier_or_op
     (long_identifier) @call.target) .) @call.site
 (#eq? @_op "|>"))

; ---------------------------------------------------------------
; Type references. Declaration positions only, and captured as
; @param.type -- never as a call. A type in an annotation is named, not
; invoked, and letting one through as a call target is how a constructor
; and its type end up sharing an edge.
; ---------------------------------------------------------------

(typed_pattern (simple_type) @param.type)
(argument_spec (simple_type) @param.type)
(record_field (simple_type) @param.type)
(union_type_field (simple_type) @param.type)
(curried_spec (simple_type) @param.type)
(function_type (simple_type) @param.type)
(postfix_type (simple_type) @param.type)
(function_or_value_defn (simple_type) @param.type)
(class_inherits_decl (simple_type) @param.type)
(interface_implementation (simple_type) @param.type)
