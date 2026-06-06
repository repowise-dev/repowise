"""LanguageSpec for java (extracted from the registry data table)."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="java",
    display_name="Java",
    import_support="full",
    # JUnit/Maven conventions: FooTest/FooTests/FooIT; Surefire/Failsafe roots.
    test_camel_suffixes=("Test", "Tests", "IT"),
    # Test-data files (gson's ParameterizedTypeFixtures.java) — support
    # data in the test tree, never the suite's face in the tour.
    fixture_camel_suffixes=("Fixture", "Fixtures"),
    test_dir_paths=("src/test/java", "src/it/java", "src/integrationtest/java"),
    # JPMS/javadoc descriptors — source files that declare, not implement.
    descriptor_filenames=("module-info.java", "package-info.java"),
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
