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

; <Component ... />
(jsx_self_closing_element
  name: (identifier) @call.target
) @call.site

; <Component ... > ... </Component>
(jsx_opening_element
  name: (identifier) @call.target
) @call.site
