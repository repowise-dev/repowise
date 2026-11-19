; =============================================================================
; repowise — Tcl symbol, import, and call queries
; tree-sitter-tcl (from tree-sitter-language-pack)
;
; Scope: `proc` definitions as symbols (namespaced names reduced to their last
; segment by the language config), `source` and `package require` as import
; edges, and command invocations as call targets. Core commands are filtered
; via `builtin_calls` on the spec; EDA tool commands drop during resolution.
; =============================================================================

; ---------------------------------------------------------------------------
; Symbols
; ---------------------------------------------------------------------------
(procedure
  name: (_) @symbol.name
) @symbol.def

; ---------------------------------------------------------------------------
; Imports — `source <file>` and `package require <name>`.
; A `$dir/x.tcl` argument is several sibling words, so the whole argument
; list is captured; resolvers/tcl.py drops its options (`-encoding name`, and
; EDA shells' `-echo -verbose` flags).
; ---------------------------------------------------------------------------
(command
  name: (simple_word) @_src_cmd
  arguments: (word_list) @import.module
  (#eq? @_src_cmd "source")
) @import.statement

(command
  name: (simple_word) @_pkg_cmd
  arguments: (word_list
    .
    (simple_word) @_pkg_sub
    .
    (simple_word) @import.module)
  (#eq? @_pkg_cmd "package")
  (#eq? @_pkg_sub "require")
  (#not-eq? @import.module "-exact")
) @import.statement

; `package require -exact name version`
(command
  name: (simple_word) @_pkg_cmd
  arguments: (word_list
    .
    (simple_word) @_pkg_sub
    .
    (simple_word) @_pkg_opt
    .
    (simple_word) @import.module)
  (#eq? @_pkg_cmd "package")
  (#eq? @_pkg_sub "require")
  (#eq? @_pkg_opt "-exact")
) @import.statement

; ---------------------------------------------------------------------------
; Calls — every command invocation's literal name.
; ---------------------------------------------------------------------------
(command
  name: (simple_word) @call.target
) @call.site
