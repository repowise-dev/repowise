; =============================================================================
; repowise: Elixir symbol, import and call queries
; tree-sitter-elixir (elixir-lang/tree-sitter-elixir) >= 0.3.5
; =============================================================================
;
; Node-shape reference (verified against the loaded grammar, which ships no
; node-types.json; the field list is `key left operand operator quoted_end
; quoted_start right target value`, and nothing else):
;
;   Every definition and every directive is an ordinary `call` node whose
;   `target` field is an `identifier` holding the keyword text itself:
;   `defmodule`, `def`, `defp`, `alias`, `import`, `use`, `@doc`'s `doc`.
;   There is no `defmodule` / `def` / `alias` node kind, and no `name`,
;   `arguments` or `body` FIELD; `arguments` and `do_block` are unlabeled
;   children found by node kind.
;
;   def add(a, b), do: a + b
;     call target:(identifier "def") arguments{ call{target:(identifier "add")
;       arguments(a, b)}, keywords{do: …} }
;   defp helper(x) when is_integer(x) do … end
;     call target:(identifier "defp") arguments{ binary_operator{
;       left: call helper(x), operator: "when", right: call is_integer(x)} }
;     do_block{…}                          ; sibling of arguments, not inside it
;   def no_args, do: :ok
;     call target:(identifier "def") arguments{ identifier "no_args", keywords }
;   alias Foo.{Bar, Baz}
;     call target:(identifier "alias") arguments{ dot{left:(alias "Foo"),
;       right:(tuple (alias "Bar") (alias "Baz"))} }
;   Baz.run(x)   call target:(dot left:(alias "Baz") right:(identifier "run"))
;   Foo.Bar      a single (alias) leaf; the grammar does not split a dotted
;                module name into a dot chain
;
; Because a definition head (`add(a, b)`) is itself a `call`, and because a
; module attribute (`@doc "…"`, `@spec f(t) :: t`) is a `call` under a `@`
; unary_operator, the call patterns below would mint an edge for each one.
; Those two shapes are dropped by `_elixir_call_is_definitional` in
; parser_helpers.py, which is structural, a predicate cannot see a parent.
; Reserved keyword targets (`def`, `if`, `case`, …) are Kernel macros and are
; dropped by name through the spec's `builtin_calls`.

; ---------------------------------------------------------------
; Modules, protocols and protocol implementations. @symbol.def is the
; whole `call`, so the symbol's line range covers the do_block body.
;
; @symbol.name is the captured alias for the first two. A `defimpl` is
; renamed in the parser to the module the compiler generates for it
; (`defimpl Jason.Encoder, for: Tuple` -> `Jason.Encoder.Tuple`): the
; protocol alias alone would give two implementations in one file the
; same id, and would leave the block's own definitions attributed to
; whatever module encloses it.
; ---------------------------------------------------------------

((call
   target: (identifier) @symbol.modifiers
   (arguments . (alias) @symbol.name)) @symbol.def
 (#any-of? @symbol.modifiers "defmodule" "defprotocol" "defimpl"))

; ---------------------------------------------------------------
; Functions, macros and guards. Three head shapes:
;   def add(a, b)                    -> nested call
;   def helper(x) when is_integer(x) -> `when` binary_operator
;   def no_args                      -> bare identifier, no parens
; The keyword lands on @symbol.modifiers, which is what tells
; `defp`/`defmacrop`/`defguardp` apart from their public forms
; (extractors/visibility.py) and what names the kind
; (refine_elixir_call_kind).
; ---------------------------------------------------------------

((call
   target: (identifier) @symbol.modifiers
   (arguments . (call
     target: (identifier) @symbol.name
     (arguments) @symbol.params))) @symbol.def
 (#any-of? @symbol.modifiers
   "def" "defp" "defmacro" "defmacrop" "defguard" "defguardp" "defdelegate"))

((call
   target: (identifier) @symbol.modifiers
   (arguments . (binary_operator
     left: (call
       target: (identifier) @symbol.name
       (arguments) @symbol.params)
     operator: "when"))) @symbol.def
 (#any-of? @symbol.modifiers
   "def" "defp" "defmacro" "defmacrop" "defguard" "defguardp" "defdelegate"))

((call
   target: (identifier) @symbol.modifiers
   (arguments . (identifier) @symbol.name)) @symbol.def
 (#any-of? @symbol.modifiers
   "def" "defp" "defmacro" "defmacrop" "defguard" "defguardp" "defdelegate"))

; ---------------------------------------------------------------
; Imports -- alias / import / require / use all bind a compile-time
; module reference, which is the edge the resolver wants; `use` also
; injects code, which makes the dependency stronger, not weaker.
;
; One pattern covers both `alias Foo.Bar` (an `alias` leaf) and
; `alias Foo.{Bar, Baz}` (a `dot` over a `tuple`); the brace group is
; expanded to one Import per member in extractors/bindings/elixir.py,
; because a single @import.module capture cannot name two modules.
; Erlang-atom modules (`import :math`) match neither branch and are
; skipped, the same as in the regex tier.
; ---------------------------------------------------------------

((call
   target: (identifier) @_directive
   (arguments . [(alias) (dot)] @import.module)) @import.statement
 (#any-of? @_directive "alias" "import" "require" "use"))

; ---------------------------------------------------------------
; Call graph. `arguments` is optional so a parenless call
; (`Logger.info "x"`) is captured too; a bare identifier is not a
; call node at all and is correctly never captured, including as a
; pipe target (`x |> baz`).
; ---------------------------------------------------------------

(call
  target: (identifier) @call.target
  (arguments)? @call.arguments) @call.site

(call
  target: (dot
    left: (_) @call.receiver
    right: (identifier) @call.target)
  (arguments)? @call.arguments) @call.site

; ---------------------------------------------------------------
; Type references -- a module named in a position that is neither a
; call nor a directive. Two shapes are unambiguous:
;   %Foo.Bar{a: 1}     a struct literal names its defining module
;   @behaviour GenServer
; Typespec bodies (`@spec f(Foo.t()) :: :ok`) are deliberately left
; out: every type there is a `call` node, so capturing them would
; need the same structural filter the call patterns need, for a
; reference kind Elixir has no type-ref strategy for yet.
; ---------------------------------------------------------------

(map (struct (alias) @param.type))

((unary_operator
   operator: "@"
   operand: (call
     target: (identifier) @_attribute
     (arguments . (alias) @param.type)))
 (#any-of? @_attribute "behaviour" "behavior"))
