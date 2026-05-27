"""LanguageSpec for php (extracted from the registry data table)."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="php",
    display_name="PHP",
    extensions=frozenset({".php"}),
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
