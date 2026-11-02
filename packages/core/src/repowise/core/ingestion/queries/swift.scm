; =============================================================================
; repowise — Swift symbol, import, and call queries
; tree-sitter-swift
; =============================================================================

; ---------------------------------------------------------------------------
; Symbols — with modifiers (priority: these come first so dedup keeps them)
; ---------------------------------------------------------------------------

(function_declaration
  (modifiers) @symbol.modifiers
  (simple_identifier) @symbol.name
) @symbol.def

; ---------------------------------------------------------------------------
; Symbols — without modifiers (fallback)
; ---------------------------------------------------------------------------

; class/struct/enum all use class_declaration
(class_declaration
  (type_identifier) @symbol.name
) @symbol.def

; extension Foo: Protocol: name is nested under user_type.
; The `.` anchor is load-bearing. A qualified `extension Ns.Type: P {}`
; parses as one flat user_type holding two type_identifier children, and an
; unanchored capture matches both, so the qualifier is recorded as a symbol
; and as a conformance parent of its own. Anchor to the last child so only
; the trailing identifier is the name. Single-identifier user_types are
; unaffected.
(class_declaration
  (user_type
    (type_identifier) @symbol.name
    .
  )
) @symbol.def

(protocol_declaration
  (type_identifier) @symbol.name
) @symbol.def

(function_declaration
  (simple_identifier) @symbol.name
) @symbol.def

(property_declaration
  (pattern
    (simple_identifier) @symbol.name
  )
) @symbol.def

; Protocol method declarations
(protocol_function_declaration
  (simple_identifier) @symbol.name
) @symbol.def

; Swift subscript (Q6) — capture the `subscript` keyword as the name
(subscript_declaration
  "subscript" @symbol.name
) @symbol.def

; ---------------------------------------------------------------------------
; Imports
; ---------------------------------------------------------------------------

(import_declaration
  (identifier) @import.module
) @import.statement

; ---------------------------------------------------------------------------
; Calls
; ---------------------------------------------------------------------------

; Simple call: foo(args)
; The "(" child is load-bearing: a subscript read ``items[0]`` parses as a
; call_expression too, with "[" in its value_arguments, and is not a call.
(call_expression
  (simple_identifier) @call.target
  (call_suffix
    (value_arguments "(") @call.arguments
  )
) @call.site

; Member call: obj.method(args) — and self-dispatch: self.method(args).
; Swift spells it ``self``, which the resolver treats identically to ``this``.
; See typescript.scm for why this is an alternation rather than a second pattern.
; ``self.field.method()`` captures the field, as java.scm does.
(call_expression
  (navigation_expression
    [
      (simple_identifier) @call.receiver
      (self_expression) @call.receiver
      (navigation_expression
        (self_expression)
        (navigation_suffix (simple_identifier) @call.receiver))
    ]
    (navigation_suffix
      (simple_identifier) @call.target
    )
  )
  (call_suffix
    (value_arguments "(") @call.arguments
  )
) @call.site

; Member call on a variable named ``copy``, ``consume`` or ``unsafe``: the
; grammar reads ``copy.remove(at: i)`` as the ``copy`` operator applied to an
; implicit member call ``.remove(at: i)``, the same tree as ``copy .remove(at: i)``.
; Read as the member call, the common case. The second branch is the first hop
; of a chain (``copy.remove(at: i).foo()``); like ``x.remove(at: i).foo()``, only
; that hop is a call site.
(_
  ["copy" "consume" "unsafe"] @call.receiver
  [
    (call_expression
      (prefix_expression "." (simple_identifier) @call.target)
      (call_suffix (value_arguments "(") @call.arguments)) @call.site
    (call_expression
      (navigation_expression
        (call_expression
          (prefix_expression "." (simple_identifier) @call.target)
          (call_suffix (value_arguments "(") @call.arguments)) @call.site))
  ]
)
