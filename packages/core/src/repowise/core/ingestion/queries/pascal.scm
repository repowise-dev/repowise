; =============================================================================
; repowise — Object Pascal (Delphi / Free Pascal) symbol and import queries
; tree-sitter-pascal (jimmckeeth/tree-sitter-pascal) >= 0.11
; =============================================================================
;
; Node-shape reference (grammar.js / src/node-types.json, 0.11.0):
;   declUses   := kUses  (moduleName)+ ';'            -- no field name on children
;   moduleName := identifier ('.' identifier)*         -- dotted unit name
;   declType   := name:(_genericName) '=' type:(...)   -- covers class/record/
;                 interface/helper/enum/set/array/alias in one node; only
;                 declType carries a `name` field (declClass/declIntf/
;                 declHelper do not), which is why @symbol.def wraps the
;                 outer declType rather than the inner class/interface node.
;   declClass  := ('class'|'record'|'object') parent:(typeref (',' typeref)*)? ... 'end'
;   declIntf   := 'interface' parent:(typeref...)? guid:(...)? ... 'end'
;   declHelper := ('class'|'record'|'type') 'helper' parent:(typeref...)? 'for' typeref ... 'end'
;   declProc   := kind name:(_genericName) args:(declArgs)? (':' type:(typeref))? ';'
;                 -- signature only (interface decl, forward decl, or a
;                    defProc's `header` field); no body
;   defProc    := header:(declProc) local:(...)? body:(block|asm) ';'
;                 -- full implementation with body
;   declProp   := 'property' name:(identifier) args:(declPropArgs)? ...
;   _genericName is one of: identifier | genericDot{lhs,rhs} | genericTpl{entity,args}
;     so a name capture needs 4 shapes: bare, generic (Foo<T>), qualified
;     (TFoo.Bar), and qualified+generic (TFoo.Bar<T>) -- mirrors the shipped
;     queries/highlights.scm in the grammar's own repo.

; ---------------------------------------------------------------
; Imports -- `uses UnitA, Ns.UnitB;`
; Valid (and captured) in program/library/unit interface AND
; implementation sections alike; not scoped to either.
; ---------------------------------------------------------------

; No quantifier on purpose: `moduleName` siblings inside `declUses` are
; separated by literal `,` tokens, so a `+`/`*` quantifier here can't
; span them (tree-sitter quantifiers require adjacency) -- it collapses
; to a single match on the FIRST unit and silently drops the rest.
; Leaving this unquantified instead yields one match per moduleName
; (all sharing the same @import.statement span), which parser.py's
; Pascal-specific _extract_imports branch relies on to emit every unit
; in `uses A, B, C;` -- see that branch for why the match count matters.
(declUses
  (moduleName) @import.module) @import.statement

; ---------------------------------------------------------------
; Type declarations: class / record / interface / class helper /
; enum / set / array / plain alias -- all wrapped in `declType`.
; ---------------------------------------------------------------

(declType
  name: (identifier) @symbol.name) @symbol.def

(declType
  name: (genericTpl entity: (identifier) @symbol.name)) @symbol.def

; ---------------------------------------------------------------
; Procedure / function / constructor / destructor / operator --
; signature-only node (interface declaration or forward declaration).
; The grammar folds all five keyword kinds into one `declProc` node
; shape, so one set of patterns covers all of them.
; ---------------------------------------------------------------

(declProc
  name: (identifier) @symbol.name
  args: (declArgs)? @symbol.params) @symbol.def

(declProc
  name: (genericTpl entity: (identifier) @symbol.name)
  args: (declArgs)? @symbol.params) @symbol.def

; Qualified method name in an implementation header, e.g.
; `procedure TDualPanelWindow.HandleInput(...)`
(declProc
  name: (genericDot rhs: (identifier) @symbol.name)
  args: (declArgs)? @symbol.params) @symbol.def

(declProc
  name: (genericDot rhs: (genericTpl entity: (identifier) @symbol.name))
  args: (declArgs)? @symbol.params) @symbol.def

; ---------------------------------------------------------------
; Full definitions (header + body) in an implementation section.
; Re-captures the name one level down (inside `header:`) so the
; @symbol.def span covers header+body instead of just the header --
; get_symbol on a method should return the body, not just the
; prototype. This is a second, distinct physical node from the
; interface-section signature above (Pascal separates forward
; declaration from implementation), so both legitimately fire, and
; ASTParser's local-vs-top-level filter (`_has_callable_ancestor`)
; uses this defProc ancestor to correctly suppress nested local
; procedures from the top-level symbol list.
; ---------------------------------------------------------------

(defProc
  header: (declProc
    name: (identifier) @symbol.name
    args: (declArgs)? @symbol.params)) @symbol.def

(defProc
  header: (declProc
    name: (genericTpl entity: (identifier) @symbol.name)
    args: (declArgs)? @symbol.params)) @symbol.def

(defProc
  header: (declProc
    name: (genericDot rhs: (identifier) @symbol.name)
    args: (declArgs)? @symbol.params)) @symbol.def

(defProc
  header: (declProc
    name: (genericDot rhs: (genericTpl entity: (identifier) @symbol.name))
    args: (declArgs)? @symbol.params)) @symbol.def

; ---------------------------------------------------------------
; Properties -- `property Foo: Integer read FFoo write SetFoo;`
; ---------------------------------------------------------------

(declProp
  name: (identifier) @symbol.name
  args: (declPropArgs)? @symbol.params) @symbol.def

; ---------------------------------------------------------------
; Call graph -- `Foo(...)`, `Foo.Bar(...)`, `Foo<T>(...)`,
; `Foo.Bar<T>(...)`. Capture names match ASTParser._extract_calls:
; call.site / call.target / call.receiver / call.arguments.
; ---------------------------------------------------------------

(exprCall
  entity: (identifier) @call.target
  args: (exprArgs)? @call.arguments) @call.site

(exprCall
  entity: (exprTpl entity: (identifier) @call.target)
  args: (exprArgs)? @call.arguments) @call.site

(exprCall
  entity: (exprDot
    lhs: (_) @call.receiver
    rhs: (identifier) @call.target)
  args: (exprArgs)? @call.arguments) @call.site

(exprCall
  entity: (exprDot
    lhs: (_) @call.receiver
    rhs: (exprTpl entity: (identifier) @call.target))
  args: (exprArgs)? @call.arguments) @call.site

; ---------------------------------------------------------------
; Parenless calls -- `Foo;`, `Obj.Foo;`. A parameterless procedure or
; method call is idiomatic Pascal and drops the `()` entirely, so the
; grammar never wraps it in `exprCall` at all: a statement consisting of
; just a bare `identifier` or `exprDot` node (checked against real MTN2
; source: ~2700 bare-identifier and ~360 bare-exprDot statements, vs.
; ~6700 already-captured parenthesised calls -- this was silently
; dropping a comparable-sized share of the call graph). Anchored to
; `statement`'s bare form specifically (not just "any identifier/exprDot
; anywhere") so this doesn't also fire on the LHS of an `assignment`
; node (a sibling of `statement`, not nested in it -- see grammar note)
; or inside a `goto`/`label`/`raise`/`with`/`for` header, none of which
; wrap their identifier in `statement`.
(statement
  (identifier) @call.target) @call.site

(statement
  (exprDot
    lhs: (_) @call.receiver
    rhs: (identifier) @call.target)) @call.site

(statement
  (exprDot
    lhs: (_) @call.receiver
    rhs: (exprTpl entity: (identifier) @call.target))) @call.site

; ---------------------------------------------------------------
; `inherited Foo;` / `inherited Foo(...)` -- calls the base class's
; same-named method. The bare `inherited;` form (no explicit name) is
; NOT captured here: the target name isn't in the node's own text at
; all -- resolving it needs the *enclosing* method's name, which is
; extraction-time context this query layer doesn't have. Left as a
; known gap rather than adding that lookup in a first cut.
; ---------------------------------------------------------------

(statement
  (inherited
    (identifier) @call.target)) @call.site

(exprCall
  entity: (inherited
    (identifier) @call.target)
  args: (exprArgs)? @call.arguments) @call.site
