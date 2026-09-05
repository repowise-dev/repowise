; =============================================================================
; repowise: Objective-C symbol, import and call queries
; tree-sitter-objc (amaanq/tree-sitter-objc) >= 3.0.2, a C-grammar superset
; =============================================================================
;
; Node-shape reference, read off the installed grammar because the wheel ships
; no node-types.json:
;   class_interface      := "@interface" (identifier)                   ; class name is the FIRST
;                           (":" superclass:(identifier))?              ; named child; a category is
;                           (parameterized_arguments)?                  ; the `category:` field and an
;                           ("(" category:(identifier)? ")")? … "@end"  ; empty "()" is an extension
;   class_implementation := "@implementation" (identifier) … "@end"
;   protocol_declaration := "@protocol" (identifier) (protocol_reference_list)? … "@end"
;   method_declaration   := ("+"|"-") (method_type) (identifier) (method_parameter)* ";"
;   method_definition    := the same, with a compound_statement instead of ";"
;   message_expression   := "[" receiver:(_) method:(identifier) (":" arg)* … "]"
;
; Two things this grammar gives no single node for:
;   * a method's selector. `initWithName:age:` is a flat, unlabelled run of
;     bare `identifier` children interleaved with `method_parameter`s, so the
;     query captures the first keyword and `_objc_selector_name` joins the
;     rest from the definition node.
;   * a multi-part `@selector(a:b:)` literal. The text between the parens is
;     absent from the parse tree entirely, so there is nothing to capture and
;     no `selector_expression` pattern here.

; ---------------------------------------------------------------------------
; Symbols: classes, categories, class extensions
; ---------------------------------------------------------------------------
; Anchored on the first named child so the `superclass:` and `category:`
; identifiers, which are the same node kind, cannot be read as the name.
; A category (`Foo (Extras)`) is renamed to `Foo(Extras)` afterwards and a
; class extension (`Foo ()`) keeps the plain name and is deduped against the
; `@implementation` in its own file, see `_objc_symbol_name` and
; `_dedupe_objc_interface_symbols`.

(class_interface
  . (identifier) @symbol.name) @symbol.def

(class_implementation
  . (identifier) @symbol.name) @symbol.def

(protocol_declaration
  . (identifier) @symbol.name) @symbol.def

; ---------------------------------------------------------------------------
; Symbols: methods
; ---------------------------------------------------------------------------
; Anchored immediately after the return type, which pins the capture to the
; first keyword of the selector. An unanchored `(identifier)` child pattern
; fires once per keyword and would mint one symbol per selector part.

(method_declaration
  (method_type) . (identifier) @symbol.name) @symbol.def

(method_definition
  (method_type) . (identifier) @symbol.name) @symbol.def

; ---------------------------------------------------------------------------
; Symbols: properties
; ---------------------------------------------------------------------------
; The name sits under `declarator:` for a pointer property (`NSString *name`)
; and is a bare identifier for a scalar one (`int hidden`).

(property_declaration
  (struct_declaration
    (struct_declarator
      (pointer_declarator
        declarator: (identifier) @symbol.name)))) @symbol.def

(property_declaration
  (struct_declaration
    (struct_declarator
      (identifier) @symbol.name))) @symbol.def

; ---------------------------------------------------------------------------
; Symbols: plain C, the same shapes c.scm uses
; ---------------------------------------------------------------------------
; Objective-C files carry ordinary C freely, and a static helper function is
; as much a symbol here as it is in a .c file.

(function_definition
  declarator: (function_declarator
    declarator: (identifier) @symbol.name
    parameters: (parameter_list) @symbol.params)) @symbol.def

(declaration
  declarator: (function_declarator
    declarator: (identifier) @symbol.name
    parameters: (parameter_list) @symbol.params)) @symbol.def

(preproc_def
  name: (identifier) @symbol.name) @symbol.def

(preproc_function_def
  name: (identifier) @symbol.name
  parameters: (preproc_params) @symbol.params) @symbol.def

(enum_specifier
  name: (type_identifier) @symbol.name) @symbol.def

(struct_specifier
  name: (type_identifier) @symbol.name) @symbol.def

(type_definition
  declarator: (type_identifier) @symbol.name) @symbol.def

; ---------------------------------------------------------------------------
; Imports: `#import` and `#include`
; ---------------------------------------------------------------------------
; Both are `preproc_include` in this grammar, told apart only by the
; `directive:` field text, which these patterns deliberately do not read: an
; `#import "Foo.h"` and an `#include "foo.h"` name a file the same way and
; resolve the same way. Same pair of patterns as c.scm.

(preproc_include
  path: (system_lib_string) @import.module) @import.statement

(preproc_include
  path: (string_literal) @import.module) @import.statement

; ---------------------------------------------------------------------------
; Calls: message sends
; ---------------------------------------------------------------------------
; `[obj doThis:a that:b]` binds one `method:` child per keyword, so this
; pattern matches once per keyword part. The parser joins the whole selector
; from the site node and keeps only the first match, see
; `_objc_message_selector`. A nested send (`[[Foo alloc] init]`) sits in the
; outer send's `receiver:` field and matches on its own, so it stays a second,
; separate call site. `self` and `super` keep their literal text as receiver.

(message_expression
  receiver: (_) @call.receiver
  method: (identifier) @call.target) @call.site

; ---------------------------------------------------------------------------
; Calls: plain C, the same shapes c.scm uses
; ---------------------------------------------------------------------------

(call_expression
  function: (identifier) @call.target
  arguments: (argument_list) @call.arguments) @call.site

(call_expression
  function: (field_expression
    argument: (identifier) @call.receiver
    field: (field_identifier) @call.target)
  arguments: (argument_list) @call.arguments) @call.site

; ---------------------------------------------------------------------------
; Type references, drive file-level ``type_use`` edges
; ---------------------------------------------------------------------------
; A class named only in a parameter type, a return type, a property type or a
; `@class` forward declaration carries no import naming it: the `#import`
; names the header file, not the class; without these captures every class
; declared in a header reads as an unused export. Everything below is a type
; position, and none of it is ever a call.

; Method return types and method parameter types both wrap the type in
; `method_type`.
(method_type
  (type_name) @param.type)

; `@property (nonatomic) NSString *name;`
(property_declaration
  (struct_declaration
    (type_identifier) @param.type))

; `@class Helper;` names a class, not a file, so it is a type reference and
; never an import.
(class_declaration
  (identifier) @param.type)

; Protocol conformance lists: `<Delegate, NSCopying>` on an `@interface` is a
; `parameterized_arguments`, on an `@protocol` a `protocol_reference_list`.
(parameterized_arguments
  (type_name) @param.type)

(protocol_reference_list
  (identifier) @param.type)

; Plain C type positions, as in c.scm.
(parameter_declaration
  type: (_) @param.type)

(field_declaration
  type: (_) @param.type)
