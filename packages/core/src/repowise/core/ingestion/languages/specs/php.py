"""LanguageSpec for php (extracted from the registry data table)."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="php",
    display_name="PHP",
    # Live-validated on Slim @ e12cb05: PSR-4 + file-based requires —
    # 5% orphans; externals are real composer deps (no vendor/ checked in).
    import_support="full",
    # PHPUnit convention: FooTest.php under tests/.
    test_camel_suffixes=("Test",),
    # Front-controller + Laravel CLI ("artisan" carries no extension —
    # the special-filename mapping keeps the pattern alive in the traverser).
    entry_point_patterns=("index.php", "artisan"),
    extensions=frozenset({".php"}),
    special_filenames=frozenset({"artisan"}),
    shebang_tokens=("php",),
    grammar_package="tree_sitter_php",
    grammar_loader="language_php",
    scm_file="php.scm",
    heritage_node_types=frozenset(
        {"class_declaration", "interface_declaration", "enum_declaration"}
    ),
    manifest_files=("composer.json",),
    lock_files=("composer.lock",),
    blocked_dirs=("vendor",),
    builtin_calls=frozenset(
        {
            "echo",
            "print",
            "var_dump",
            "print_r",
            "isset",
            "empty",
            "unset",
            "array",
            "count",
            "strlen",
            "strpos",
            "substr",
            "implode",
            "explode",
            "json_encode",
            "json_decode",
        }
    ),
    builtin_parents=frozenset(
        {
            "stdClass",
            "Exception",
            "RuntimeException",
            "InvalidArgumentException",
            "LogicException",
            "Iterator",
            "IteratorAggregate",
            "Countable",
            "Serializable",
            "JsonSerializable",
            "Stringable",
            "Throwable",
        }
    ),
    color_hex="#4F5D95",
)
