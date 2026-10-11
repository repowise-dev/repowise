"""LanguageSpec for dart (extracted from the registry data table)."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="dart",
    display_name="Dart",
    import_support="full",
    # package:test convention: test/foo_test.dart.
    test_stem_suffixes=("_test",),
    # Flutter flavor entrypoints (main_development.dart / main_staging.dart,
    # run via ``flutter run -t``) are prefix-shaped, which the traverser's
    # pattern forms can't express — they are stamped in ``_warmup_dart``.
    entry_point_patterns=("main.dart",),
    manifest_files=("pubspec.yaml",),
    extensions=frozenset({".dart"}),
    grammar_package="tree_sitter_dart",
    scm_file="dart.scm",
    heritage_node_types=frozenset({"class_definition", "mixin_declaration"}),
    builtin_calls=frozenset(
        {
            "print",
            "identical",
            "assert",
            "toString",
            "map",
            "where",
            "forEach",
            "add",
            "addAll",
            "contains",
            "setState",
        }
    ),
    builtin_parents=frozenset(
        {
            "Object",
            "Enum",
            "Comparable",
            "Iterable",
            "Exception",
            "Error",
        }
    ),
    color_hex="#0175C2",
)
