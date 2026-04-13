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
