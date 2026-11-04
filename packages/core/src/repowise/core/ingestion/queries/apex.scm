; =============================================================================
; repowise — Apex symbol and call queries
; tree-sitter-language-pack "apex" (aheber/tree-sitter-sfapex)
; =============================================================================
; The grammar is Java-shaped and keeps Java's node and field names, so these
; patterns follow java.scm. Apex has no import statement. Inline SOQL/SOSL
; (query_expression) and DML (dml_expression) are not calls and are left out.

; ---------------------------------------------------------------------------
; Symbols
; ---------------------------------------------------------------------------

; The whole ``modifiers`` node is the modifier capture: its annotations
; (@AuraEnabled, @IsTest, @InvocableMethod, @RestResource, ...) lead it, so
; they reach Symbol.decorators exactly as Java's do.
(class_declaration
  (modifiers)? @symbol.modifiers
  name: (identifier) @symbol.name
) @symbol.def

(interface_declaration
  (modifiers)? @symbol.modifiers
  name: (identifier) @symbol.name
) @symbol.def

(enum_declaration
  (modifiers)? @symbol.modifiers
  name: (identifier) @symbol.name
) @symbol.def

; ``trigger Name on SObject (events) { ... }``: the sObject and events are in
; the signature (see extractors/signatures.py).
(trigger_declaration
  name: (identifier) @symbol.name
) @symbol.def

(method_declaration
  (modifiers)? @symbol.modifiers
  name: (identifier) @symbol.name
  parameters: (formal_parameters) @symbol.params
) @symbol.def

(constructor_declaration
  (modifiers)? @symbol.modifiers
  name: (identifier) @symbol.name
  parameters: (formal_parameters) @symbol.params
) @symbol.def

; A property is a field with an accessor list: ``public String name { get; set; }``.
; Plain fields are not symbols, as in Java.
(field_declaration
  (modifiers)? @symbol.modifiers
  declarator: (variable_declarator
    name: (identifier) @symbol.name)
  (accessor_list)
) @symbol.def

; ---------------------------------------------------------------------------
; Calls
; ---------------------------------------------------------------------------

; foo(args). ``!object`` keeps member calls out: unlike java.scm's
; unconstrained pattern, ``a.b.c()`` and ``Outer.Inner.m()`` must never
; arrive as a receiver-less ``c()``/``m()`` that the resolver would read
; as a call on the caller's own class.
(method_invocation
  !object
  name: (identifier) @call.target
  arguments: (argument_list) @call.arguments
) @call.site

; obj.method(args) / Class.staticMethod(args)
(method_invocation
  object: (identifier) @call.receiver
  name: (identifier) @call.target
  arguments: (argument_list) @call.arguments
) @call.site

; this.field.method(args): the field is the receiver, typed against the
; caller's own class (see java.scm for why only this/super qualify).
(method_invocation
  object: (field_access
    object: [(this) (super)]
    field: (identifier) @call.receiver
  )
  name: (identifier) @call.target
  arguments: (argument_list) @call.arguments
) @call.site

; Outer.Inner.staticMethod(args), a.b.c(args): the dotted
; path is the receiver (parser/calls.py keeps it only as a plain path of
; names). A path headed by this/super takes the pattern above at its first hop.
(method_invocation
  object: (field_access
    object: [(identifier) (field_access)]
  ) @call.receiver
  name: (identifier) @call.target
  arguments: (argument_list) @call.arguments
) @call.site

; this.method(args) / super.method(args)
(method_invocation
  object: [(this) (super)] @call.receiver
  name: (identifier) @call.target
  arguments: (argument_list) @call.arguments
) @call.site

; ((Foo) x).bar(args): the cast names the receiver's type.
(method_invocation
  object: (parenthesized_expression
    (cast_expression
      .
      type: (type_identifier) @call.receiver
      .
      value: (_)
    )
  )
  name: (identifier) @call.target
  arguments: (argument_list) @call.arguments
) @call.site

; new Foo().bar(args) / new Outer.Inner().bar(args): the whole type path is
; the receiver, so the member type is read in the class that qualifies it.
(method_invocation
  object: (object_creation_expression
    type: [
      (type_identifier)
      (scoped_type_identifier)
    ] @call.receiver
  )
  name: (identifier) @call.target
  arguments: (argument_list) @call.arguments
) @call.site

; obj.method1().method2(args)
(method_invocation
  object: (method_invocation) @call.receiver_call
  name: (identifier) @call.target
  arguments: (argument_list) @call.arguments
) @call.site

; new ClassName(args)
(object_creation_expression
  type: (type_identifier) @call.target
  arguments: (argument_list) @call.arguments
) @call.site

; new Outer.Inner(args): the inner class is the target, declared under its
; last segment, and the qualifier before it the receiver, so the member type
; is looked up in the class the qualifier names (``ns.Outer`` included), never
; by the bare name, which a same-named type in the caller's own file would take.
(object_creation_expression
  type: (scoped_type_identifier
    .
    (_) @call.receiver
    (type_identifier) @call.target .)
  arguments: (argument_list) @call.arguments
) @call.site

; ---------------------------------------------------------------------------
; Type references (``@param.type``), as in java.scm
; ---------------------------------------------------------------------------

(formal_parameter type: (_) @param.type)
(field_declaration type: (_) @param.type)
(method_declaration type: (_) @param.type)
(object_creation_expression type: (_) @param.type)
(local_variable_declaration type: (_) @param.type)
(enhanced_for_statement type: (_) @param.type)
(superclass (_) @param.type)
(interfaces (type_list (_) @param.type))
(extends_interfaces (type_list (_) @param.type))
(type_arguments (_) @param.type)
