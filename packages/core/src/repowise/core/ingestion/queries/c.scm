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

; typedef int MyInt; / typedef struct Foo Bar; (Q10 — primitive/named alias)
(type_definition
  type: (primitive_type)
  declarator: (type_identifier) @symbol.name
) @symbol.def

(type_definition
  type: (struct_specifier
    name: (type_identifier)
  )
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

; ---------------------------------------------------------------------------
; Type references — drive file-level ``type_use`` edges
; ---------------------------------------------------------------------------
; A struct / typedef declared in a header (``parson.h``) and used as a
; field / parameter / return type in a ``.c`` that ``#include``s it carries
; no import statement naming the type — only the ``#include`` of the header.
; Without these captures every header struct reads as an unused export. The
; shared ``@param.type`` capture name routes through the C head extractor
; (see parser_helpers.TYPE_HEAD_EXTRACTORS), which drops the pointer/array
; declarator wrapping (it lives on the declarator side in C, not the type)
; and filters primitive builtins. Mirrors the Go / C# captures.

; Parameter types: void f(JSON_Object *obj)
(parameter_declaration
  type: (_) @param.type)

; Struct / union field types: struct { JSON_Value *vals; }
(field_declaration
  type: (_) @param.type)

; Function return type: JSON_Value * json_parse(...)
(function_definition
  type: (_) @param.type)
