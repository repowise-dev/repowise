; =============================================================================
; repowise — GDScript (Godot) symbol, import and call queries
; tree-sitter-gdscript (PrestonKnopp/tree-sitter-gdscript) >= 6.1
; =============================================================================
;
; Node-shape reference, read off src/node-types.json + grammar.js at 6.1.0
; (not guessed -- every field name below is verified against those two files):
;
;   source                     := top-level; statements are DIRECT children
;                                 (_compound_statement is an inlined rule, so
;                                  function_definition / class_definition /
;                                  enum_definition are children of `source`
;                                  itself, not of a wrapper node)
;   class_name_statement       := name:(name) icon_path:(string)?
;                                 extends:(extends_statement)?
;   extends_statement          := "extends" (string | type)   -- NO field names
;   class_definition           := name:(name) extends:(extends_statement)?
;                                 body:(class_body)
;   function_definition        := name:(name)? parameters:(parameters)
;                                 return_type:(type)? body:(body)?
;   constructor_definition     := "func" "_init" parameters:(parameters) ...
;                                 -- carries NO `name` field; the name is the
;                                    anonymous "_init" token, captured as such
;   variable_statement         := name:(name) type:? value:? setget:? static:?
;   export_variable_statement  := GDScript 3 `export var x` (GDScript 4 spells
;                                 it `@export var x`, which parses as an
;                                 ordinary variable_statement carrying an
;                                 `annotations` child -- both are covered)
;   onready_variable_statement := GDScript 3 `onready var x`
;   const_statement            := name:(name) type:? value:(_expression)
;   signal_statement           := name:(name) parameters:(parameters)?
;   enum_definition            := name:(name)? body:(enumerator_list)
;   call                       := (_primary_expression) arguments:(arguments)
;   attribute_call             := (identifier) arguments:(arguments)
;                                 -- `obj.method()` parses as an `attribute`
;                                    holding an attribute_call, NOT as `call`
;   base_call                  := "." (identifier) arguments:(arguments)
;
; Full res:// import resolution lives in resolvers/gdscript.py; this file only
; emits the raw quoted path as @import.module. parser.py strips the quotes
; before the resolver sees it (`.strip("\"'` ")`), so `preload("res://a.gd")`
; arrives there as `res://a.gd`.

; ---------------------------------------------------------------
; Script-level class declaration -- `class_name Player`.
;
; A .gd file IS a class, but the class has a *name* only when the
; script declares one. Scripts without `class_name` therefore
; contribute no class symbol and no symbol-level heritage edge (the
; file-level import edge from `extends "res://..."` is unaffected).
; Documented as a known gap in docs/layers/LANGUAGE_SUPPORT.md
; rather than papered over with a synthesized file-stem name:
; HeritageResolver builds `child_id` as f"{path}::{child_name}"
; without checking that the symbol exists, so a synthesized name
; would emit an edge to a node that is not in the graph.
; ---------------------------------------------------------------

(source
  (class_name_statement
    name: (name) @symbol.name) @symbol.def)

; ---------------------------------------------------------------
; Inner classes -- `class Inner extends Node: ...`
; ---------------------------------------------------------------

(class_definition
  name: (name) @symbol.name) @symbol.def

; ---------------------------------------------------------------
; Functions. Not anchored to source/class_body: GDScript has no
; nested `func`, and ASTParser._has_callable_ancestor already drops
; anything that somehow sits inside another callable.
; ---------------------------------------------------------------

(function_definition
  name: (name) @symbol.name
  parameters: (parameters) @symbol.params) @symbol.def

; `func _init(...)` is its own node type with no `name` field at all --
; the grammar spells `_init` as a literal token. Anonymous nodes are
; capturable, and node_text over the token yields exactly "_init".
(constructor_definition
  "_init" @symbol.name
  parameters: (parameters) @symbol.params) @symbol.def

; ---------------------------------------------------------------
; Members. These ARE anchored to `source` / `class_body`: unlike
; `func`, a `var`/`const`/`enum` is legal inside a function body and
; inside a lambda body. The parser's callable-ancestor filter catches
; the function-body case but not the lambda one (`lambda` is not a
; symbol node type, so it is not "callable" to that check), so the
; anchor is what keeps loop-local scratch variables out of the
; top-level symbol list.
; ---------------------------------------------------------------

(source
  (variable_statement
    name: (name) @symbol.name) @symbol.def)

(class_body
  (variable_statement
    name: (name) @symbol.name) @symbol.def)

(source
  (export_variable_statement
    name: (name) @symbol.name) @symbol.def)

(class_body
  (export_variable_statement
    name: (name) @symbol.name) @symbol.def)

(source
  (onready_variable_statement
    name: (name) @symbol.name) @symbol.def)

(class_body
  (onready_variable_statement
    name: (name) @symbol.name) @symbol.def)

(source
  (const_statement
    name: (name) @symbol.name) @symbol.def)

(class_body
  (const_statement
    name: (name) @symbol.name) @symbol.def)

(source
  (signal_statement
    name: (name) @symbol.name
    parameters: (parameters)? @symbol.params) @symbol.def)

(class_body
  (signal_statement
    name: (name) @symbol.name
    parameters: (parameters)? @symbol.params) @symbol.def)

; `name` is optional on enum_definition -- an anonymous `enum {A, B}`
; declares its enumerators into the enclosing scope and has no symbol
; of its own to name, so requiring the field here skips exactly the
; declarations that have nothing to record.
(source
  (enum_definition
    name: (name) @symbol.name) @symbol.def)

(class_body
  (enum_definition
    name: (name) @symbol.name) @symbol.def)

; ---------------------------------------------------------------
; Imports -- `preload("res://a.gd")`, `load("res://b.tscn")` and
; `extends "res://base.gd"`.
;
; Two single-`#eq?` patterns rather than one `#any-of?`: no query in
; this tree uses `#any-of?` yet, and `#eq?` is the form luau.scm has
; in production.
;
; Only string-literal arguments are captured. `load(some_var)` is
; unresolvable without dataflow, and emitting the variable's *name*
; as a module path would manufacture a wrong edge.
; ---------------------------------------------------------------

(call
  (identifier) @_preload_fn
  arguments: (arguments (string) @import.module)
  (#eq? @_preload_fn "preload")) @import.statement

(call
  (identifier) @_load_fn
  arguments: (arguments (string) @import.module)
  (#eq? @_load_fn "load")) @import.statement

(extends_statement
  (string) @import.module) @import.statement

; ---------------------------------------------------------------
; Call graph
; ---------------------------------------------------------------

; Plain call -- `foo(...)`. Godot's global scope (`print`, `range`,
; `Vector2`, ...) is filtered out via the spec's builtin_calls.
(call
  (identifier) @call.target
  arguments: (arguments) @call.arguments) @call.site

; Method call -- `obj.method(...)`. The receiver is the FIRST child of
; the `attribute` node (anchored with `.`); `a.b.c()` therefore reports
; receiver `a`, which is the same simplification the other languages'
; receiver captures make.
(attribute
  . (_) @call.receiver
  (attribute_call
    (identifier) @call.target
    arguments: (arguments) @call.arguments)) @call.site

; GDScript 3 parent-method call -- `.method(...)`. The GDScript 4
; spelling `super.method(...)` parses as an ordinary attribute call
; with receiver `super` and is captured by the pattern above.
(base_call
  (identifier) @call.target
  arguments: (arguments) @call.arguments) @call.site
