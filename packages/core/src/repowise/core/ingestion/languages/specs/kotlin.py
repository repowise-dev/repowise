"""LanguageSpec for kotlin (extracted from the registry data table)."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="kotlin",
    display_name="Kotlin",
    extensions=frozenset({".kt", ".kts"}),
    grammar_package="tree_sitter_kotlin",
    scm_file="kotlin.scm",
    heritage_node_types=frozenset({"class_declaration", "object_declaration"}),
    manifest_files=("build.gradle.kts", "build.gradle"),
    blocked_dirs=(".gradle",),
    builtin_calls=frozenset(
        {
            "println",
            "print",
            "readLine",
            "arrayOf",
            "listOf",
            "mutableListOf",
            "setOf",
            "mutableSetOf",
            "mapOf",
            "mutableMapOf",
            "hashMapOf",
            "lazy",
            "require",
            "check",
            "error",
            "TODO",
            "run",
            "let",
            "also",
            "apply",
            "with",
        }
    ),
    builtin_parents=frozenset(
        {
            "Any",
            "Throwable",
            "Exception",
            "RuntimeException",
            "Error",
            "Enum",
            "Comparable",
            "Iterable",
            "Serializable",
        }
    ),
    color_hex="#A97BFF",
)
