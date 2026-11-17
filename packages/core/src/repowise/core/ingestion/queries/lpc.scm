; =============================================================================
; repowise — LPC (LDMud) symbol, import and call queries (tree-sitter-lpc)
; =============================================================================

; ---------------------------------------------------------------------------
; Symbols
; ---------------------------------------------------------------------------

; Function definitions with a body. Prototypes (body ";") are not symbols.
(function_definition
  (type_modifier)* @symbol.modifiers
  name: (identifier) @symbol.name
  parameters: (parameter_list) @symbol.params
  body: (block)
) @symbol.def

(global_var
  (type_modifier)* @symbol.modifiers
  name: (identifier) @symbol.name
) @symbol.def

(struct_definition
  name: (identifier) @symbol.name
) @symbol.def

(preproc_def
  name: (identifier) @symbol.name
) @symbol.def

(preproc_function_def
  name: (identifier) @symbol.name
  parameters: (preproc_params) @symbol.params
) @symbol.def

; ---------------------------------------------------------------------------
; Imports: the whole inherit statement (macros are expanded by the resolver)
; and #include operands.
; ---------------------------------------------------------------------------

(inheritance) @import.statement @import.module

(preproc_include
  path: (_) @import.module
) @import.statement

; ---------------------------------------------------------------------------
; Calls
; ---------------------------------------------------------------------------

; foo(...)
(function_call
  function: (identifier) @call.target
  arguments: (argument_list) @call.arguments
) @call.site

; Scoped inherit calls are deferred until LPC-specific dispatch is audited.

; ob->foo(...) / ob.foo(...) — call_other on a runtime object
(call_other
  object: (_) @call.receiver
  function: (identifier) @call.target
  arguments: (argument_list) @call.arguments
) @call.site

; Callbacks named by string: the driver calls the function in this object later, so it is used here.
; add_action("f", verb), call_out("f", t), input_to("f"), remove_call_out/find_call_out("f")
(function_call
  function: (identifier) @_cb
  arguments: (argument_list . (string_literal) @call.target)
  (#any-of? @_cb "add_action" "call_out" "input_to" "remove_call_out" "find_call_out")
) @call.site

; Old-style function-name arguments calling into this object: sort_array(arr, "f"), filter_array(arr, "f"),
; filter_array(arr, "f", this_object()), call_other(this_object(), "f"). With another object the receiver is unknown.
(function_call
  function: (identifier) @_cb
  arguments: (argument_list . (_) . (string_literal) @call.target .)
  (#any-of? @_cb "filter" "filter_array" "map" "map_array" "sort_array" "filter_indices" "filter_mapping"
                 "map_indices" "map_mapping" "walk_mapping")
) @call.site

(function_call
  function: (identifier) @_cb
  arguments: (argument_list . (_) . (string_literal) @call.target . (function_call function: (identifier) @_to))
  (#any-of? @_cb "filter" "filter_array" "map" "map_array" "sort_array" "filter_indices" "filter_mapping"
                 "map_indices" "map_mapping" "walk_mapping")
  (#eq? @_to "this_object")
) @call.site

(function_call
  function: (identifier) @_cb
  arguments: (argument_list . (function_call function: (identifier) @_to) . (string_literal) @call.target)
  (#any-of? @_cb "call_other" "call_direct")
  (#eq? @_to "this_object")
) @call.site

; #'f lfun closures (efun and operator closures are dropped as builtins or non-names)
(closure) @call.site @call.target
