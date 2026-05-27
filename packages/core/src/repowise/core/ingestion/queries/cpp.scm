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

; Inline method definition inside a class body: void method(args) { ... }
; The name is a ``field_identifier`` in this case, not a plain identifier.
(function_definition
  declarator: (function_declarator
    declarator: (field_identifier) @symbol.name
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

; Two-level qualified function: ReturnType NS::ClassName::method(params) { }
; The grammar nests the qualified_identifier left-recursively, so we
; need a separate pattern for each depth. Parser walks the captured
; name's qualified-identifier parent to extract the class name, so the
; deeper namespace prefix doesn't need to be captured here.
(function_definition
  declarator: (function_declarator
    declarator: (qualified_identifier
      name: (qualified_identifier
        name: (identifier) @symbol.name
      )
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

; ---------------------------------------------------------------------------
; Calls
; ---------------------------------------------------------------------------

; Simple function call: foo(args)
(call_expression
  function: (identifier) @call.target
  arguments: (argument_list) @call.arguments
) @call.site

; Method call: obj.method(args) or obj->method(args)
(call_expression
  function: (field_expression
    argument: (identifier) @call.receiver
    field: (field_identifier) @call.target
  )
  arguments: (argument_list) @call.arguments
) @call.site

; Scoped call: ClassName::method(args) or namespace::function(args)
(call_expression
  function: (qualified_identifier
    name: (identifier) @call.target
  )
  arguments: (argument_list) @call.arguments
) @call.site

; Chained call: obj.method1().method2(args)
(call_expression
  function: (field_expression
    argument: (call_expression)
    field: (field_identifier) @call.target
  )
  arguments: (argument_list) @call.arguments
) @call.site

; ---------------------------------------------------------------------------
; Type references — drive file-level ``type_use`` edges
; ---------------------------------------------------------------------------
; A struct / class / typedef declared in a header and used as a field /
; parameter / return type in a translation unit that ``#include``s it
; carries no import statement naming the type — only the ``#include``.
; Without these captures every header type reads as an unused export. The
; shared ``@param.type`` capture name routes through the C head extractor
; (see parser_helpers.TYPE_HEAD_EXTRACTORS); pointer/array declarator
; wrapping lives on the declarator side, and primitive builtins are filtered.

; Parameter types: void f(Widget *w)
(parameter_declaration
  type: (_) @param.type)

; Struct / class field types
(field_declaration
  type: (_) @param.type)

; Function return type: Widget * make(...)
(function_definition
  type: (_) @param.type)
