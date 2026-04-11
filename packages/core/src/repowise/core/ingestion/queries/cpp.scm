; =============================================================================
; repowise — C++ symbol and import queries
; tree-sitter-cpp >= 0.23
; (Also used for .c files — C is a subset of this grammar for our purposes)
; =============================================================================

; ---------------------------------------------------------------------------
; Symbols
; ---------------------------------------------------------------------------

; Function definition: ReturnType funcName(params) { body }
; The name is nested inside function_declarator
(function_definition
  declarator: (function_declarator
    declarator: (identifier) @symbol.name
    parameters: (parameter_list) @symbol.params
  )
) @symbol.def

; Qualified function definition: ReturnType ClassName::method(params) { }
(function_definition
  declarator: (function_declarator
    declarator: (qualified_identifier
      name: (identifier) @symbol.name
    )
    parameters: (parameter_list) @symbol.params
  )
) @symbol.def

; Class
(class_specifier
  name: (type_identifier) @symbol.name
) @symbol.def

; Struct
(struct_specifier
  name: (type_identifier) @symbol.name
) @symbol.def

; Enum (type_identifier is a direct child, not a named field in this grammar)
(enum_specifier
  (type_identifier) @symbol.name
) @symbol.def

; Namespace
(namespace_definition
  name: (namespace_identifier) @symbol.name
) @symbol.def

; Template class: template<typename T> class Foo { ... }
(template_declaration
  (class_specifier
    name: (type_identifier) @symbol.name
  )
) @symbol.def

; Template struct: template<typename T> struct Bar { ... }
(template_declaration
  (struct_specifier
    name: (type_identifier) @symbol.name
  )
) @symbol.def

; Template function: template<typename T> T func(T x) { ... }
(template_declaration
  (function_definition
    declarator: (function_declarator
      declarator: (identifier) @symbol.name
      parameters: (parameter_list) @symbol.params
    )
  )
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

; #include <header>
(preproc_include
  path: (system_lib_string) @import.module
) @import.statement

; #include "local_header"
(preproc_include
  path: (string_literal) @import.module
) @import.statement
