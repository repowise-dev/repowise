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

; ---------------------------------------------------------------------------
; Bare references — drive symbol-level ``references`` edges
; ---------------------------------------------------------------------------
; The C half of #1602. Naming a function without calling it is a use that no
; call pattern above can see, and C reaches for it constantly: a dispatch
; table of handlers, a ``.callback =`` designated initialiser, a registration
; macro. The referenced function otherwise carries no inbound edge and reads
; as a ``safe_to_delete`` unused export. The parser drops any name that does
; not resolve to a function, so a same-named local cannot mint an edge.

; Dispatch table: struct cmd cmds[] = { {"add", do_add} };
; Captured under its own name because the parser additionally requires a table
; to sit at file scope: inside a function body the same shape is a local
; aggregate whose elements are parameters, not functions.
(initializer_list
  (identifier) @reference.table)

; Designated initialiser: static struct ops o = { .write = my_write };
(initializer_pair
  value: (identifier) @reference.name)

; Callback field assignment: handle->on_close = on_close_cb;
; Restricted to a member on the left: a plain ``x = y`` is overwhelmingly
; local bookkeeping, and matching it manufactured an edge wherever a local
; happened to share a function's name.
(assignment_expression
  left: (field_expression)
  right: (identifier) @reference.name)

; Registration macro: REGISTER_CMD(do_add);
; ``@reference.via`` carries the macro name so the parser can require the
; SCREAMING_CASE convention, without which this captures every identifier
; argument of every ordinary call.
(call_expression
  function: (identifier) @reference.via
  arguments: (argument_list
    (identifier) @reference.name))
