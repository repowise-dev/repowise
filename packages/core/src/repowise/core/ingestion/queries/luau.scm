; =============================================================================
; repowise — Luau symbol and import queries
; tree-sitter-luau (install separately if needed)
;
; Luau is a gradually-typed superset of Lua 5.1 used by the Roblox engine.
; Rojo maps filesystem layout to Roblox instance paths via default.project.json;
; `require()` accepts instance paths such as `script.Parent.Foo`,
; `script.Foo`, or `game.ReplicatedStorage.Shared.Foo`.
;
; Full Rojo-aware import resolution lives in resolvers/luau.py; this file
; only emits symbol defs and the raw require-argument text as @import.module.
; =============================================================================

; Global function: function foo.bar.baz() end
(function_declaration
  name: (_) @symbol.name
) @symbol.def

; Type alias: type Foo = ...   (Luau-specific; Lua 5.1 has no `type`)
(type_definition
  (identifier) @symbol.name
) @symbol.def

; ---------------------------------------------------------------------------
; Imports — captured as raw argument text for the resolver to parse.
;
; Matches:
;   require("some/string/path")
;   require(script.Parent.Foo)
;   require(game.ReplicatedStorage.Shared.Foo)
;
; The resolver is responsible for splitting out the instance path, consulting
; Rojo's default.project.json tree, and producing a filesystem path.
; ---------------------------------------------------------------------------
(function_call
  (identifier) @_require_name
  (arguments (_) @import.module)
  (#eq? @_require_name "require")
) @import.statement

; ---------------------------------------------------------------------------
; Calls
; ---------------------------------------------------------------------------
(function_call
  (identifier) @call.target
  (arguments) @call.arguments
) @call.site
