"""LanguageSpec for java (extracted from the registry data table)."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="java",
    display_name="Java",
    extensions=frozenset({".java"}),
    grammar_package="tree_sitter_java",
    scm_file="java.scm",
    heritage_node_types=frozenset(
        {"class_declaration", "interface_declaration", "enum_declaration"}
    ),
    entry_point_patterns=("Main.java", "Application.java"),
    manifest_files=("pom.xml", "build.gradle", "build.gradle.kts"),
    blocked_dirs=(".gradle",),
    builtin_calls=frozenset(
        {
            "System",
            "Objects",
            "Arrays",
            "Collections",
            "Math",
            "Integer",
            "Long",
            "Double",
            "Float",
            "Boolean",
            "Character",
            "Byte",
            "Short",
            "String",
            "Object",
            "Class",
            "Thread",
            "Throwable",
            "Exception",
            "RuntimeException",
            "Error",
            "StringBuilder",
            "StringBuffer",
        }
    ),
    builtin_parents=frozenset(
        {
            "Object",
            "Throwable",
            "Exception",
            "RuntimeException",
            "Error",
            "Enum",
            "Serializable",
            "Cloneable",
            "Comparable",
            "Iterable",
            "AutoCloseable",
            "Closeable",
        }
    ),
    color_hex="#B07219",
)
