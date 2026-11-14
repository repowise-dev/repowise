; =============================================================================
; repowise — Perl 5 symbol, import, and call queries
; tree-sitter-perl (from tree-sitter-language-pack)
;
; Scope: named subs and Perl 5.38 `method`s as symbols, `use` / `require` as
; import edges, and sub / method invocations as call targets. Builtins are
; filtered via `builtin_calls` on the spec.
; =============================================================================

; ---------------------------------------------------------------------------
; Symbols
; ---------------------------------------------------------------------------
(subroutine_declaration_statement
  name: (bareword) @symbol.name
) @symbol.def

(method_declaration_statement
  name: (bareword) @symbol.name
) @symbol.def

; ---------------------------------------------------------------------------
; Imports — `use Foo::Bar;`, `require Foo::Bar;`, `require "lib/x.pl";`.
; Pragmas (`use strict`) also match; resolvers/perl.py drops them.
; ---------------------------------------------------------------------------
(use_statement
  module: (package) @import.module
) @import.statement

(require_expression
  (bareword) @import.module
) @import.statement

(require_expression
  (string_literal) @import.module
) @import.statement

(require_expression
  (interpolated_string_literal) @import.module
) @import.statement

; ---------------------------------------------------------------------------
; Calls
; ---------------------------------------------------------------------------
; foo(...), Pkg::foo(...)
(function_call_expression
  function: (function) @call.target
) @call.site

; foo $x  (no parentheses)
(ambiguous_function_call_expression
  function: (function) @call.target
) @call.site

; $obj->method(...), Pkg->method(...)
(method_call_expression
  invocant: (_) @call.receiver
  method: (method) @call.target
) @call.site
