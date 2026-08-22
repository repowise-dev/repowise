; repowise — COBOL 85 symbols and static call sites
; nolanlwin/yutaro-sakamoto grammar, ABI 14 (tree-sitter-language-pack)

; A program definition wraps all four divisions, giving its symbol a useful
; whole-program range while the name stays the PROGRAM-ID value.
(program_definition
  (identification_division
    (program_name) @symbol.name)) @symbol.def

; IBM course material commonly omits the terminal period after PROGRAM-ID.
; The grammar recovers the rest of the program but places the header in a
; direct ERROR node. Capture only ERROR spans that actually contain PROGRAM-ID;
; the name normalizer extracts that one identifier and ignores the remainder.
((program_definition
  (identification_division)
  (ERROR) @symbol.name) @symbol.def
  (#match? @symbol.name "PROGRAM-ID\\s*\\."))

; The grammar exposes headers as leaf nodes, including their punctuation.
; LanguageConfig normalizes those names and extends their ranges over the
; following sibling statements for caller attribution.
(section_header) @symbol.name @symbol.def
(paragraph_header) @symbol.name @symbol.def

; Every COBOL data level is useful structural context, including copybook
; contents when a .cpy file is indexed directly.
(data_description
  (entry_name) @symbol.name) @symbol.def

; Only literal CALL targets are statically knowable. Identifier/data-item
; targets are deliberately silent until data-flow support exists.
(call_statement
  x: (string) @call.target) @call.site

; PERFORM paragraph/section calls are same-file procedure edges.
(perform_statement_call_proc
  procedure: (perform_procedure
    (label
      (qualified_word
        (WORD) @call.target)))) @call.site
