; =============================================================================
; repowise — TSX-grammar-only additions to the TypeScript query
; Appended to typescript.scm by parser._load_compiled_query when the
; grammar variant is ``tsx``. The plain ``typescript`` grammar does not
; define JSX node types, so these captures live here instead of in
; typescript.scm.
; =============================================================================

; ---------------------------------------------------------------------------
; JSX element usage (treated as a call to the component)
; ---------------------------------------------------------------------------

; <Component ... /> — Capitalized React component
(jsx_self_closing_element
  name: (identifier) @call.target
  (#match? @call.target "^[A-Z]")
) @call.site

; <Component ... > ... </Component> — Capitalized React component
(jsx_opening_element
  name: (identifier) @call.target
  (#match? @call.target "^[A-Z]")
) @call.site

; <Form.Item ... /> or <Form.Item> ... </Form.Item> — Member expression component
; Casing filter prevents motion.div / styled.button from emitting fake edges.
; @call.receiver captures the object (e.g. "Form") so Form.Item and Card.Item
; resolve to distinct call sites via _extract_calls:851.
(jsx_self_closing_element
  name: (member_expression
    object: (identifier) @call.receiver
    property: (property_identifier) @call.target
  )
  (#match? @call.target "^[A-Z]")
) @call.site

(jsx_opening_element
  name: (member_expression
    object: (identifier) @call.receiver
    property: (property_identifier) @call.target
  )
  (#match? @call.target "^[A-Z]")
) @call.site

