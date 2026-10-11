; =============================================================================
; repowise — Dart symbol, import, and call queries
; tree-sitter-dart (UserNobody14 grammar, PyPI tree-sitter-dart >= 0.1)
;
; Grammar quirk this file works around: a Dart function is a
; ``function_signature`` node whose body is a SIBLING ``function_body`` node
; (methods additionally wrap the signature in ``method_signature``). The
; signature node is captured as @symbol.def; the parser's Dart branch extends
; the symbol's end_line to the trailing body sibling.
; =============================================================================

; ---------------------------------------------------------------------------
; Symbols
; ---------------------------------------------------------------------------

(class_definition
  name: (identifier) @symbol.name
) @symbol.def

; mixin_declaration exposes no ``name`` field — the identifier child is the name.
(mixin_declaration
  (identifier) @symbol.name
) @symbol.def

(enum_declaration
  name: (identifier) @symbol.name
) @symbol.def

(extension_declaration
  name: (identifier) @symbol.name
) @symbol.def

; Top-level functions and methods (methods arrive wrapped in method_signature;
; both shapes share the function_signature node).
(function_signature
  name: (identifier) @symbol.name
  (formal_parameter_list) @symbol.params
) @symbol.def

(getter_signature
  (identifier) @symbol.name
) @symbol.def

(setter_signature
  (identifier) @symbol.name
) @symbol.def

; typedef Callback = void Function(int);
(type_alias
  (type_identifier) @symbol.name
) @symbol.def

; ---------------------------------------------------------------------------
; Imports — import / export / part / part of directives
; ---------------------------------------------------------------------------

; import 'package:http/http.dart' as http;  /  import 'src/util.dart' show a;
(import_specification
  (configurable_uri) @import.module
) @import.statement

; export 'src/api.dart';  — a barrel re-export (parser Dart branch sets
; is_reexport).
(library_export
  (configurable_uri) @import.module
) @import.statement

; part 'foo.g.dart';
(part_directive
  (uri) @import.module
) @import.statement

; part of 'lib.dart';
(part_of_directive
  (uri) @import.module
) @import.statement

; part of dotted.library.name;  — resolved through the library-name index
; (parser Dart branch prefixes ``library:``).
(part_of_directive
  (dotted_identifier_list) @import.module
) @import.statement

; ---------------------------------------------------------------------------
; Calls — Dart calls are selector chains: ``foo(x)`` is an identifier followed
; by a sibling (selector (argument_part)); ``obj.method(x)`` inserts a
; ``.method`` selector between them. Only the first link of a chain is
; captured (chained calls are best-effort, like Kotlin/Swift).
; ---------------------------------------------------------------------------

; Bare call / constructor call: foo(args) / HttpClient()
(_
  (identifier) @call.target
  .
  (selector
    (argument_part
      (arguments) @call.arguments
    )
  ) @call.site
)

; Method call: obj.method(args)
(_
  (identifier) @call.receiver
  .
  (selector
    (unconditional_assignable_selector
      (identifier) @call.target
    )
  )
  .
  (selector
    (argument_part
      (arguments) @call.arguments
    )
  ) @call.site
)

; Method call on this: this.method(args)
(_
  (this) @call.receiver
  .
  (selector
    (unconditional_assignable_selector
      (identifier) @call.target
    )
  )
  .
  (selector
    (argument_part
      (arguments) @call.arguments
    )
  ) @call.site
)
