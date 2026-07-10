; =============================================================================
; repowise — Shell (bash/sh/zsh) symbol, import, and call queries
; tree-sitter-bash (install separately if needed)
;
; Scope is deliberately small: function definitions as symbols, `source` / `.`
; as import edges, and command invocations as call targets. External binaries
; (grep, awk, …) are captured as call targets too, but resolve to nothing when
; no matching function symbol exists — the three-tier call resolver drops them.
;
; tree-sitter-bash represents both function forms with `function_definition`
; carrying a `name:` field:
;     foo() { ... }
;     function foo { ... }
; =============================================================================

; ---------------------------------------------------------------------------
; Symbols — function definitions (both `foo()` and `function foo` forms).
; ---------------------------------------------------------------------------
(function_definition
  name: (word) @symbol.name
) @symbol.def

; ---------------------------------------------------------------------------
; Imports — `source <file>` and `. <file>`.
;
; The argument is captured as raw text (a string, word, or concatenation); the
; parser strips surrounding quotes before handing it to resolvers/shell.py,
; which resolves the common `$SCRIPT_DIR/x.sh` / `$(dirname "$0")/x.sh` idioms
; against the sourcing file's directory.
; ---------------------------------------------------------------------------
(command
  name: (command_name) @_src_cmd
  argument: (_) @import.module
  (#match? @_src_cmd "^(source|\\.)$")
) @import.statement

; ---------------------------------------------------------------------------
; Calls — every command invocation's name. Only resolves for calls to
; functions defined in the same file or a sourced file; builtins are filtered
; via `builtin_calls` on the spec, external binaries drop during resolution.
; ---------------------------------------------------------------------------
(command
  name: (command_name) @call.target
) @call.site
