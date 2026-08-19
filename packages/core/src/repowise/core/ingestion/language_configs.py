"""Per-language parser configuration data.

``LanguageConfig`` plus the declarative ``LANGUAGE_CONFIGS`` table that
drives :class:`~repowise.core.ingestion.parser.ASTParser`. Extracted from
``parser.py`` so the parser module holds behaviour and this module holds
the per-language data. The parser keeps re-exporting both names, so
``from ...parser import LANGUAGE_CONFIGS, LanguageConfig`` still works.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from .extractors.visibility import (
    csharp_visibility,
    dart_visibility,
    go_visibility,
    java_visibility,
    kotlin_visibility,
    php_visibility,
    public_by_default,
    py_visibility,
    rust_visibility,
    scala_visibility,
    swift_visibility,
    ts_visibility,
)


@dataclass
class LanguageConfig:
    """Per-language metadata used by ASTParser.

    The ASTParser itself contains no language-specific if/elif logic.
    All branching happens through these configs and the .scm query files.
    """

    # Maps tree-sitter node type → our canonical SymbolKind string
    symbol_node_types: dict[str, str]

    # tree-sitter node types that carry import information (doc purposes)
    import_node_types: list[str]

    # tree-sitter node types that export symbols (doc purposes)
    export_node_types: list[str]

    # (name: str, modifier_texts: list[str]) → "public" | "private" | ...
    visibility_fn: Callable[[str, list[str]], str]

    # How to determine a method's parent class:
    #   "nesting"  — walk up AST; parent class types in parent_class_types
    #   "receiver" — extract from @symbol.receiver capture (Go)
    #   "impl"     — look for impl_item ancestor (Rust)
    #   "none"     — no parent tracking
    parent_extraction: str = "nesting"

    # Node types that indicate a class context (used with "nesting" mode)
    parent_class_types: frozenset[str] = field(default_factory=frozenset)

    # Node types whose symbols are bodiless declarations. C/C++ headers declare
    # what a .cpp defines, so both sides land as same-named symbols; this is
    # what tells them apart downstream (see ``Symbol.is_declaration``).
    declaration_node_types: frozenset[str] = field(default_factory=frozenset)


LANGUAGE_CONFIGS: dict[str, LanguageConfig] = {
    "python": LanguageConfig(
        symbol_node_types={
            "function_definition": "function",
            "class_definition": "class",
            # Module-level assignments (the .scm pattern is module-anchored).
            # Refined in the parser: SCREAMING_CASE → constant, else variable.
            "assignment": "constant",
        },
        import_node_types=["import_statement", "import_from_statement"],
        export_node_types=[],
        visibility_fn=py_visibility,
        parent_extraction="nesting",
        parent_class_types=frozenset({"class_definition"}),
    ),
    "typescript": LanguageConfig(
        symbol_node_types={
            "function_declaration": "function",
            "generator_function_declaration": "function",
            "arrow_function": "function",
            "class_declaration": "class",
            "abstract_class_declaration": "class",
            "interface_declaration": "interface",
            "type_alias_declaration": "type_alias",
            "enum_declaration": "enum",
            "method_definition": "method",
            "lexical_declaration": "function",  # const foo = () => {}
            # Top-level const/let with a literal value (the .scm pattern is
            # program-anchored). Refined in the parser like Python assignments.
            "variable_declarator": "constant",
        },
        import_node_types=["import_statement"],
        export_node_types=["export_statement"],
        visibility_fn=ts_visibility,
        parent_extraction="nesting",
        parent_class_types=frozenset({"class_declaration", "abstract_class_declaration"}),
    ),
    "javascript": LanguageConfig(
        symbol_node_types={
            "function_declaration": "function",
            "generator_function_declaration": "function",
            "arrow_function": "function",
            "class_declaration": "class",
            "method_definition": "method",
            "lexical_declaration": "function",
            "variable_declarator": "constant",
        },
        import_node_types=["import_statement"],
        export_node_types=["export_statement"],
        visibility_fn=public_by_default,
        parent_extraction="nesting",
        parent_class_types=frozenset({"class_declaration"}),
    ),
    "go": LanguageConfig(
        symbol_node_types={
            "function_declaration": "function",
            "method_declaration": "method",
            "type_spec": "struct",  # refined in post-processing
            "const_spec": "variable",  # const MaxRetries = 3
            "var_spec": "variable",  # var ErrNotFound = errors.New(...)
        },
        import_node_types=["import_declaration"],
        export_node_types=[],
        visibility_fn=go_visibility,
        parent_extraction="receiver",
        parent_class_types=frozenset(),
    ),
    "rust": LanguageConfig(
        symbol_node_types={
            "function_item": "function",
            "struct_item": "struct",
            "enum_item": "enum",
            "trait_item": "trait",
            "impl_item": "impl",
            "const_item": "constant",
            "type_item": "type_alias",
            "mod_item": "module",
            "macro_definition": "function",
            "static_item": "constant",
            "enum_variant": "variable",
            "field_declaration": "property",
            "union_item": "struct",
        },
        import_node_types=["use_declaration"],
        export_node_types=[],
        visibility_fn=rust_visibility,
        parent_extraction="impl",
        parent_class_types=frozenset({"impl_item", "mod_item"}),
    ),
    "java": LanguageConfig(
        symbol_node_types={
            "class_declaration": "class",
            "interface_declaration": "interface",
            "enum_declaration": "enum",
            "record_declaration": "class",  # Java 16+ records
            "method_declaration": "method",
            "constructor_declaration": "function",
        },
        import_node_types=["import_declaration"],
        export_node_types=[],
        visibility_fn=java_visibility,
        parent_extraction="nesting",
        parent_class_types=frozenset(
            {"class_declaration", "interface_declaration", "enum_declaration", "record_declaration"}
        ),
    ),
    "cpp": LanguageConfig(
        symbol_node_types={
            "function_definition": "function",
            "class_specifier": "class",
            "struct_specifier": "struct",
            "enum_specifier": "enum",
            "namespace_definition": "module",
            "template_declaration": "class",  # template<> class/struct/function
            "type_definition": "struct",  # typedef struct { ... } Name;
            "preproc_def": "variable",  # #define MACRO value
            "preproc_function_def": "function",  # #define MACRO(x) ...
            "declaration": "function",  # forward declarations + dtor decls
            "alias_declaration": "type_alias",  # using X = Y;
        },
        import_node_types=["preproc_include"],
        export_node_types=[],
        visibility_fn=public_by_default,
        parent_extraction="nesting",
        parent_class_types=frozenset({"class_specifier", "struct_specifier"}),
        declaration_node_types=frozenset({"declaration"}),
    ),
    "c": LanguageConfig(
        symbol_node_types={
            "function_definition": "function",
            "struct_specifier": "struct",
            "enum_specifier": "enum",
            "type_definition": "struct",  # typedef struct { ... } Name;
            "preproc_def": "variable",  # #define MACRO value
            "preproc_function_def": "function",  # #define MACRO(x) ...
            "declaration": "function",  # forward declarations
        },
        import_node_types=["preproc_include"],
        export_node_types=[],
        visibility_fn=public_by_default,
        parent_extraction="none",
        parent_class_types=frozenset(),
        declaration_node_types=frozenset({"declaration"}),
    ),
    "kotlin": LanguageConfig(
        symbol_node_types={
            "function_declaration": "function",
            "class_declaration": "class",
            "object_declaration": "class",
            "type_alias": "type_alias",
            "property_declaration": "variable",
        },
        import_node_types=["import"],
        export_node_types=[],
        visibility_fn=kotlin_visibility,
        parent_extraction="nesting",
        parent_class_types=frozenset({"class_declaration", "object_declaration"}),
    ),
    "ruby": LanguageConfig(
        symbol_node_types={
            "method": "function",
            "singleton_method": "function",
            "class": "class",
            "module": "module",
            "assignment": "constant",
        },
        import_node_types=["call"],
        export_node_types=[],
        visibility_fn=public_by_default,
        parent_extraction="nesting",
        parent_class_types=frozenset({"class", "module"}),
    ),
    "csharp": LanguageConfig(
        symbol_node_types={
            "class_declaration": "class",
            "interface_declaration": "interface",
            "struct_declaration": "struct",
            "enum_declaration": "enum",
            "enum_member_declaration": "variable",
            "method_declaration": "method",
            "constructor_declaration": "function",
            "property_declaration": "variable",
            "field_declaration": "variable",
            "record_declaration": "class",
            "delegate_declaration": "function",
            "event_declaration": "variable",
            "event_field_declaration": "variable",
            "namespace_declaration": "module",
            "file_scoped_namespace_declaration": "module",
        },
        import_node_types=["using_directive", "global_using_directive"],
        export_node_types=[],
        visibility_fn=csharp_visibility,
        parent_extraction="nesting",
        parent_class_types=frozenset(
            {
                "class_declaration",
                "interface_declaration",
                "struct_declaration",
                "enum_declaration",
                "record_declaration",
                "namespace_declaration",
                "file_scoped_namespace_declaration",
            }
        ),
    ),
    "swift": LanguageConfig(
        symbol_node_types={
            "class_declaration": "class",
            "protocol_declaration": "interface",
            "function_declaration": "function",
            "protocol_function_declaration": "function",
            "property_declaration": "variable",
            "subscript_declaration": "method",
        },
        import_node_types=["import_declaration"],
        export_node_types=[],
        visibility_fn=swift_visibility,
        parent_extraction="nesting",
        parent_class_types=frozenset({"class_declaration", "protocol_declaration"}),
    ),
    "dart": LanguageConfig(
        symbol_node_types={
            "class_definition": "class",
            "mixin_declaration": "class",
            "enum_declaration": "enum",
            "extension_declaration": "class",
            "function_signature": "function",
            "getter_signature": "function",
            "setter_signature": "function",
            "type_alias": "type_alias",
        },
        import_node_types=[
            "import_specification",
            "library_export",
            "part_directive",
            "part_of_directive",
        ],
        export_node_types=[],
        visibility_fn=dart_visibility,
        parent_extraction="nesting",
        parent_class_types=frozenset(
            {
                "class_definition",
                "mixin_declaration",
                "extension_declaration",
                "enum_declaration",
            }
        ),
    ),
    "scala": LanguageConfig(
        symbol_node_types={
            "class_definition": "class",
            "trait_definition": "trait",
            "object_definition": "class",
            "function_definition": "function",
            "function_declaration": "function",
            "val_definition": "variable",
            "var_definition": "variable",
            "enum_definition": "enum",
            "given_definition": "variable",
        },
        import_node_types=["import_declaration"],
        export_node_types=[],
        visibility_fn=scala_visibility,
        parent_extraction="nesting",
        parent_class_types=frozenset({"class_definition", "trait_definition", "object_definition"}),
    ),
    "php": LanguageConfig(
        symbol_node_types={
            "class_declaration": "class",
            "interface_declaration": "interface",
            "trait_declaration": "trait",
            "enum_declaration": "enum",
            "method_declaration": "method",
            "function_definition": "function",
            "const_declaration": "constant",
            "property_declaration": "variable",
        },
        import_node_types=["namespace_use_declaration"],
        export_node_types=[],
        visibility_fn=php_visibility,
        parent_extraction="nesting",
        parent_class_types=frozenset(
            {"class_declaration", "interface_declaration", "trait_declaration", "enum_declaration"}
        ),
    ),
    "pascal": LanguageConfig(
        symbol_node_types={
            # declType wraps class/record/interface/helper/enum/set/array/alias
            # in one node shape (see spec docstring) -- "class" is the closest
            # single bucket, same tradeoff Go makes for `type_spec`: "struct".
            # A real kind refinement (peek at the `type` field to distinguish
            # class/struct/interface/enum) would need a hook parser.py doesn't
            # currently expose per-language the way it does for Go/Kotlin --
            # flagged as a follow-up, not attempted here.
            "declType": "class",
            "declProc": "function",  # signature only (interface decl / forward decl)
            "defProc": "function",   # full definition with body
            # Matches C#'s choice for the same concept (property_declaration ->
            # "variable"). Rust is the one language that keeps fields under a
            # distinct "property" kind; everywhere else a field and a callable
            # value share "variable", which is what stops the call resolver's
            # non-callable refusal from extending beyond Rust.
            "declProp": "variable",
        },
        import_node_types=["declUses"],
        export_node_types=[],       # Pascal has no explicit re-export syntax
        # No pascal_visibility exists yet. Pascal visibility is per-*section*
        # (`strict private`/`protected`/`public`/`published` governs every
        # declaration until the next section keyword, i.e. extractors/
        # visibility.py's per-declaration-modifier shape doesn't fit Pascal
        # without a declSection sibling-walk). public_by_default is the same
        # placeholder C/C++/JS/Ruby/Luau/Shell already use for real, not a
        # Pascal-specific hack -- but it is a placeholder, not a real answer.
        visibility_fn=public_by_default,
        parent_extraction="nesting",
        # NOT {"declClass", "declIntf", "declHelper"} -- see the spec docstring.
        # ASTParser._find_parent walks ancestors checking `ancestor.type in
        # parent_class_types` then reads `ancestor.child_by_field_name("name")`;
        # only declType carries a name field, so pointing this at the inner
        # class/interface/helper node would silently return no parent for every
        # method (found by tracing _find_parent's actual implementation, not
        # guessed).
        parent_class_types=frozenset({"declType"}),
    ),
    "luau": LanguageConfig(
        symbol_node_types={
            "function_declaration": "function",
            "type_definition": "type_alias",
        },
        import_node_types=["function_call"],
        export_node_types=[],
        visibility_fn=public_by_default,
        parent_extraction="none",
        parent_class_types=frozenset(),
    ),
    "shell": LanguageConfig(
        # Both `foo() {}` and `function foo {}` parse as function_definition.
        # Shell has no classes, so no parent tracking.
        symbol_node_types={
            "function_definition": "function",
        },
        import_node_types=["command"],  # `source` / `.` — see queries/shell.scm
        export_node_types=[],
        visibility_fn=public_by_default,
        parent_extraction="none",
        parent_class_types=frozenset(),
    ),
}

# An SFC's <script> block IS TypeScript, and sfc_source hands the parser a TS
# buffer, so the TypeScript config applies verbatim. Aliasing rather than
# copying keeps them from drifting apart.
LANGUAGE_CONFIGS["svelte"] = LANGUAGE_CONFIGS["typescript"]
LANGUAGE_CONFIGS["vue"] = LANGUAGE_CONFIGS["typescript"]
