; =============================================================================
; repowise — C symbol and import queries
; Uses the tree-sitter-cpp grammar (superset of C)
; =============================================================================

; ---------------------------------------------------------------------------
; Symbols
; ---------------------------------------------------------------------------

(function_definition
  declarator: (function_declarator
    declarator: (identifier) @symbol.name
    parameters: (parameter_list) @symbol.params
  )
) @symbol.def

(struct_specifier
  name: (type_identifier) @symbol.name
) @symbol.def

(enum_specifier
  name: (type_identifier) @symbol.name
) @symbol.def

; typedef struct { ... } MyType;
(type_definition
  type: (struct_specifier)
  declarator: (type_identifier) @symbol.name
) @symbol.def

; typedef enum { ... } MyEnum;
(type_definition
  type: (enum_specifier)
  declarator: (type_identifier) @symbol.name
) @symbol.def

; #define MACRO_NAME ...
(preproc_def
  name: (identifier) @symbol.name
) @symbol.def

; #define FUNC_MACRO(x) ...
(preproc_function_def
  name: (identifier) @symbol.name
  parameters: (preproc_params) @symbol.params
) @symbol.def

; Forward declarations: void func(int x);
(declaration
  declarator: (function_declarator
    declarator: (identifier) @symbol.name
    parameters: (parameter_list) @symbol.params
  )
) @symbol.def

; ---------------------------------------------------------------------------
; Imports (#include directives)
; ---------------------------------------------------------------------------

(preproc_include
  path: (system_lib_string) @import.module
) @import.statement

(preproc_include
  path: (string_literal) @import.module
) @import.statement

; ---------------------------------------------------------------------------
; Calls
; ---------------------------------------------------------------------------

; Simple function call: foo(args)
(call_expression
  function: (identifier) @call.target
  arguments: (argument_list) @call.arguments
) @call.site

; Field call: ptr->func(args) or obj.func(args)
(call_expression
  function: (field_expression
    argument: (identifier) @call.receiver
    field: (field_identifier) @call.target
  )
  arguments: (argument_list) @call.arguments
) @call.site
