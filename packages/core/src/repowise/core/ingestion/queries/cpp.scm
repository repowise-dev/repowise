; =============================================================================
; repowise — C++ symbol and import queries
; tree-sitter-cpp >= 0.23
; (Also used for .c files — C is a subset of this grammar for our purposes)
; =============================================================================

; ---------------------------------------------------------------------------
; Symbols
; ---------------------------------------------------------------------------

; Export-macro class/struct definitions are parsed as function definitions:
; ``struct MYLIB_EXPORT WriteOptions { ... }``. The grammar treats the macro
; as the specifier's name and the real type name as a bare declarator. Keep
; the specifier as ``@symbol.def`` so kind/signature handling stays class- or
; struct-shaped, while the outer capture identifies the aggregate body.
(function_definition
  type: (class_specifier
    name: (type_identifier) @symbol.cpp_export_macro
    !body
  ) @symbol.def
  declarator: (identifier) @symbol.name
  body: (compound_statement)
) @symbol.cpp_export_type

(function_definition
  type: (struct_specifier
    name: (type_identifier) @symbol.cpp_export_macro
    !body
  ) @symbol.def
  declarator: (identifier) @symbol.name
  body: (compound_statement)
) @symbol.cpp_export_type

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

; In-class member-function declaration: ``void Seek(const Slice&);``
; An abstract class has no out-of-line definition, so without these it reaches
; the method index empty. ``@symbol.def`` is the declarator, not the
; ``field_declaration``: that node can hold a whole ``struct Inner { ... } m_;``
; and would become a callable ancestor of the inner type's methods.
; ``declarator: (field_identifier)`` directly is what keeps out a
; function-pointer data member (``void (*cb_)(int);``).
(field_declaration
  declarator: (function_declarator
    declarator: (field_identifier) @symbol.name
    parameters: (parameter_list) @symbol.params
  ) @symbol.def
)

; ... returning a pointer: ``virtual Iterator* NewIterator(...) = 0;``
(field_declaration
  declarator: (pointer_declarator
    declarator: (function_declarator
      declarator: (field_identifier) @symbol.name
      parameters: (parameter_list) @symbol.params
    ) @symbol.def
  )
)

; ... returning a reference: ``const Slice& value() const;``
; ``reference_declarator`` does not name its declarator field, so the inner
; ``function_declarator`` is matched as a bare named child rather than by field.
(field_declaration
  declarator: (reference_declarator
    (function_declarator
      declarator: (field_identifier) @symbol.name
      parameters: (parameter_list) @symbol.params
    ) @symbol.def
  )
)

; Pure-virtual member of an EXPORT-MACRO class: ``virtual void Seek(...) = 0;``
; ``class EXPORT Foo { ... }`` is recovery-parsed into a ``compound_statement``
; of ``declaration`` nodes, which the patterns above cannot reach, and ``= 0``
; wraps the declarator in an ``init_declarator`` the one below cannot either.
; ``value: (number_literal)`` is what excludes the recovered inline definition
; and member-init constructor, which share the shape. ``@symbol.def`` must be
; the ``declaration``: it is a callable kind, so anchoring inside it makes it a
; callable ancestor and the match is dropped.
(declaration
  declarator: (init_declarator
    declarator: (function_declarator
      declarator: (identifier) @symbol.name
      parameters: (parameter_list) @symbol.params
    )
    value: (number_literal)
  )
) @symbol.def

; Destructor declaration inside a class body: ~Foo();
(declaration
  declarator: (function_declarator
    declarator: (destructor_name) @symbol.name
    parameters: (parameter_list) @symbol.params
  )
) @symbol.def

; Destructor definition out-of-class: Foo::~Foo() { ... }
(function_definition
  declarator: (function_declarator
    declarator: (qualified_identifier
      name: (destructor_name) @symbol.name
    )
    parameters: (parameter_list) @symbol.params
  )
) @symbol.def

; Operator-overload definition outside class: bool Foo::operator==(const Foo&) { }
(function_definition
  declarator: (function_declarator
    declarator: (qualified_identifier
      name: (operator_name) @symbol.name
    )
    parameters: (parameter_list) @symbol.params
  )
) @symbol.def

; using StringMap = std::map<std::string, int>;
(alias_declaration
  name: (type_identifier) @symbol.name
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

; The same call again, keeping the qualifier. A tree-sitter field is required
; once named, so `::free()` (no scope) would stop matching if the capture were
; added above; both patterns therefore run and the parser's dedup keeps the one
; that carried a scope. `DB::Open()` was resolving to a test class's `Open`
; because only the leaf name survived extraction.
(call_expression
  function: (qualified_identifier
    scope: (_) @call.scope
    name: (identifier) @call.target
  )
  arguments: (argument_list) @call.arguments
) @call.site

; Chained call: obj.method1().method2(args)
(call_expression
  function: (field_expression
    argument: (call_expression) @call.receiver_call
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

; Local / global variable declarations: Row scratch{ ... }; or Widget *w;
; A type used as the declared type of a variable in the same TU is a genuine
; reference, but the captures above only see parameters, fields, return
; types and template arguments — a ``Row scratch`` local reads as dead
; without this. ``type_identifier`` matches only the bare-name form, so a
; ``struct Row { ... }`` definition (wrapped in ``struct_specifier``) is not
; caught here; it has no cross-file edge anyway.
(declaration
  type: (type_identifier) @param.type)

; Function return type: Widget * make(...)
(function_definition
  type: (_) @param.type)

; Template type argument: std::vector<Widget> — captures ``Widget``
; (the head extractor strips ``std::*`` container wrappers before
; resolving). Without this, every header type used only as a template
; parameter reads as an unused export.
(template_argument_list
  (type_descriptor
    type: (_) @param.type))

; ---------------------------------------------------------------------------
; Bare references — drive symbol-level ``references`` edges
; ---------------------------------------------------------------------------
; Naming a function without calling it is a use, and none of the call
; patterns above can see it: a dispatch table, a callback field and a
; registration macro all mention the function as a plain identifier. The
; referenced function then carries no inbound edge and reads as a
; ``safe_to_delete`` unused export, which took out whole subsystems of
; handlers and interop shims (#1602).
;
; Only the identifier forms are captured, so ``NodeType::Add`` in a table
; (a ``qualified_identifier``) filters itself out. The parser drops any name
; that does not resolve to a function or method, which is what keeps a
; same-named local variable from minting an edge.

; Dispatch table: Entry g_table[] = { {"a", HandleAlpha} };
; The query is recursive, so nested initialiser rows match too. Captured under
; its own name because the parser additionally requires a table to sit at file
; / namespace / class scope: inside a function body the same shape is a
; constructor member-init or a local aggregate, where ``{data, size}`` names
; parameters rather than functions.
(initializer_list
  (identifier) @reference.table)

; Designated initialiser: static Ops o = { .write = MyWrite };
(initializer_pair
  value: (identifier) @reference.name)

; Callback field assignment: tool.Handler = Handle_SceneSummary;
; Restricted to a member on the left. A plain ``x = y`` is overwhelmingly
; local bookkeeping, and C++ getters are named exactly like the locals that
; feed them (``offset_ = offset`` beside an ``offset()`` accessor), so the
; unrestricted form manufactured a reference edge for half of leveldb.
(assignment_expression
  left: (field_expression)
  right: (identifier) @reference.name)

; Registration macro: REGISTER_HOOK(OnFrameStart);
; ``@reference.via`` carries the macro name so the parser can require the
; SCREAMING_CASE convention. Without that guard this would capture every
; identifier argument of every ordinary call in the codebase.
(call_expression
  function: (identifier) @reference.via
  arguments: (argument_list
    (identifier) @reference.name))
