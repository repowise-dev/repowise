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

; A parenless call is just as idiomatic on an assignment's RHS as it is
; bare-statement -- `Result := GetDefaultNDNProfile;` is the standard shape
; for a niladic Delphi function, arguably *more* common than the bare-
; statement form above (a niladic function is normally called for its
; result, which means an assignment). Verified against MTN2: every call
; site of a real niladic function (GetDefaultNDNProfile, 8 call sites) was
; this shape, and none were captured before this pattern -- the function
; read as having zero callers even from within its own file. `assignment`
; is a distinct node from `statement` (not nested in it), so the earlier
; `statement`-anchored patterns never fire here regardless of order.
;
; Unlike the bare-statement form, this is genuinely ambiguous at the
; grammar level: `X := Y;` fires this pattern whether `Y` is a niladic
; function call or a plain variable read -- Pascal's grammar doesn't (and
; can't, without a symbol table) distinguish them. Left deliberately
; over-eager rather than trying to disambiguate: a stray edge to an
; unrelated same-named local only ever ADDS usage evidence somewhere in
; the graph, which can suppress a real dead-code finding (a false
; negative) but can never manufacture a "confidently dead" false
; positive -- the failure direction this whole pass exists to avoid.
(assignment
  rhs: (identifier) @call.target) @call.site

(assignment
  rhs: (exprDot
    lhs: (_) @call.receiver
    rhs: (identifier) @call.target)) @call.site

(assignment
  rhs: (exprDot
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

; ---------------------------------------------------------------
; Type references -- a class/interface/record named only in a field,
; parameter, local-variable, or return-type position (never called,
; never subclassed) previously minted no usage edge at all, since
; pascal.scm had no `@param.type` captures (unlike C#/Go/Java/Kotlin/TS).
; ``parser.py:_extract_type_refs`` collects these into TypeReference
; records generically; ``_pascal_head_type_identifier`` in
; parser_helpers.py unwraps the `typeref` shape (plain / generic
; `typerefTpl` / qualified `typerefDot`) down to the head identifier.
; Verified against MTN2: TConsoleBuffer / TDialogHost read as having zero
; importers despite being referenced in 10-18 files, exclusively through
; declarations of this shape (`FBuf: TConsoleBuffer;`), never through a
; call or heritage edge.
;
; `declField`/`declArg`/`declVar` wrap their type in an intermediate
; `type` node (which can also hold array/set/generic-class-body shapes
; the `typeref` case doesn't cover); `declProc`'s return-type field holds
; a bare `typeref` directly -- see the grammar note at the top of this
; file. Both shapes are captured; anything under `type` that isn't a
; `typeref` (e.g. an inline `array of X`) is simply not matched, same
; "no rule, no capture" degradation as everywhere else in this file.
; ---------------------------------------------------------------

(declField
  type: (type (typeref) @param.type))

(declArg
  type: (type (typeref) @param.type))

(declVar
  type: (type (typeref) @param.type))

(declProc
  type: (typeref) @param.type)

; ---------------------------------------------------------------
; `Application.CreateForm(TMainForm, MainForm);` -- the classic VCL/FMX/
; LCL entry-point idiom, universal across Delphi and Lazarus project
; (.dpr/.lpr) files. The class argument is a bare identifier passed by
; value, not a receiver or a type-position declaration -- no other
; pattern in this file captures it, so a form class referenced ONLY this
; way (never a `var x: TMainForm` elsewhere, which is otherwise the norm
; for every other class) still read as having zero importers even after
; the type-reference captures above. Scoped tightly to the literal
; `CreateForm` call target (case-insensitive -- Pascal identifiers are)
; specifically to avoid the much broader, much noisier alternative of
; treating every bare-identifier call argument as a possible type
; reference. Only the first argument is captured (`.` anchors it) --
; `CreateForm`'s second parameter is a `var` reference, not a class.
; ---------------------------------------------------------------

(exprCall
  entity: (exprDot
    rhs: (identifier) @_pascal_create_form_check)
  args: (exprArgs
    . (identifier) @param.type)
  (#match? @_pascal_create_form_check "(?i)^CreateForm$"))
