; =============================================================================
; repowise — BYOND Dream Maker symbols and includes
; tree-sitter-dm >= 0.25
; =============================================================================

; Top-level and path-qualified proc definitions.
(proc_definition
  name: (identifier) @symbol.name
  (proc_parameters) @symbol.params
) @symbol.def

(proc_override
  name: (identifier) @symbol.name
  (proc_parameters) @symbol.params
) @symbol.def

; Procs declared inside a type body.
(type_proc_definition
  name: (identifier) @symbol.name
  (proc_parameters) @symbol.params
) @symbol.def

(type_proc_override
  name: (identifier) @symbol.name
  (proc_parameters) @symbol.params
) @symbol.def

(operator_override
  name: (identifier) @symbol.name
  (proc_parameters) @symbol.params
) @symbol.def

(type_operator_override
  name: (identifier) @symbol.name
  (proc_parameters) @symbol.params
) @symbol.def

; DM types are identified by their complete path (/mob/player, /datum/foo).
(type_definition
  (type_path) @symbol.name
) @symbol.def

; Global variables and preprocessor definitions.
(global_var_definition
  (var_definition
    name: (identifier) @symbol.name
  )
) @symbol.def

(global_var_definition
  name: (identifier) @symbol.name
) @symbol.def

(preproc_def
  name: (identifier) @symbol.name
) @symbol.def

(preproc_defproc
  name: (identifier) @symbol.name
) @symbol.def

; The .dme is the source of truth for the compilation unit and consists mostly
; of #include directives. Capturing these creates the file-level dependency
; graph even when a project does not use explicit module imports.
(preproc_include
  file: (_) @import.module
) @import.statement

; Call sites are intentionally omitted in the first support tier. DM dispatch
; depends on type paths and dynamic object state; feeding its bare call names
; to the generic resolver both invents edges and becomes prohibitively
; expensive on large legacy projects. Add calls with a DM-specific resolver.
