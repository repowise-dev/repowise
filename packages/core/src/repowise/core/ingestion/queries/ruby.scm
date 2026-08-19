; =============================================================================
; repowise — Ruby symbol and import queries
; tree-sitter-ruby (install separately if needed)
; =============================================================================

(method
  name: (identifier) @symbol.name
  parameters: (method_parameters)? @symbol.params
) @symbol.def

(singleton_method
  name: (identifier) @symbol.name
) @symbol.def

(class
  name: (constant) @symbol.name
) @symbol.def

(module
  name: (constant) @symbol.name
) @symbol.def

; Top-level / class-level constant assignment (Q8): MAX = 3
(assignment
  left: (constant) @symbol.name
) @symbol.def

; require 'module' / require_relative './sibling'
(call
  method: (identifier) @_require_method
  arguments: (argument_list
    (string (string_content) @import.module)
  )
  (#match? @_require_method "^require")
) @import.statement

; ---------------------------------------------------------------------------
; Calls
; ---------------------------------------------------------------------------

; Simple call: foo(args)
(call
  method: (identifier) @call.target
  arguments: (argument_list) @call.arguments
) @call.site

; Class/module method call: ClassName.method(args)
(call
  receiver: (constant) @call.receiver
  method: (identifier) @call.target
) @call.site

; Method call on variable: obj.method(args)
(call
  receiver: (identifier) @call.receiver
  method: (identifier) @call.target
) @call.site

; Namespaced class/module method call: Foo::Bar.baz(args).
;
; The pattern above constrains the receiver to a bare ``constant``, and the
; simple-call pattern constrains nothing, so a namespaced receiver arrives with
; no receiver at all and resolves by bare name.
;
; The TRAILING constant is captured rather than the whole path, because that is
; the name the class index holds — so no receiver normalizer is needed (contrast
; ``_normalize_php_receiver``). The collapse is lossy and its cost is known and
; stated: ``A::Foo`` and ``B::Foo`` both offer ``Foo``, so a short class name
; reused across sibling gems in one repo can bind to the wrong namespace. Fixing
; that needs Ruby module nesting in the index, which this does not build.
(call
  receiver: (scope_resolution name: (constant) @call.receiver)
  method: (identifier) @call.target
) @call.site
