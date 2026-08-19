"""LanguageSpec for kotlin (extracted from the registry data table)."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="kotlin",
    display_name="Kotlin",
    import_support="full",
    # JUnit/Kotest conventions: FooTest/FooTests/FooSpec; Gradle sourceset
    # roots — the "src/*Test" wildcard covers multiplatform/custom test
    # sourcesets (src/commonTest, src/jvmTest, src/integrationTest, …).
    test_camel_suffixes=("Test", "Tests", "Spec"),
    test_dir_paths=("src/test/kotlin", "src/*Test"),
    entry_point_patterns=("Main.kt", "Application.kt"),
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
    builtin_types=frozenset(
        {
            # Kotlin primitives (kotlin package, auto-imported)
            "Boolean", "Byte", "Short", "Int", "Long", "Float", "Double", "Char",
            "String", "Unit", "Nothing", "Any", "Number",
            "Array", "IntArray", "LongArray", "ByteArray", "ShortArray",
            "FloatArray", "DoubleArray", "CharArray", "BooleanArray",
            "List", "MutableList", "ArrayList",
            "Map", "MutableMap", "HashMap", "LinkedHashMap",
            "Set", "MutableSet", "HashSet", "LinkedHashSet",
            "Collection", "MutableCollection",
            "Iterable", "MutableIterable", "Iterator", "MutableIterator",
            "Sequence", "Pair", "Triple", "Result",
            "Comparable", "Comparator",
            "Throwable", "Exception", "RuntimeException", "Error",
            "IllegalArgumentException", "IllegalStateException",
            "NullPointerException", "UnsupportedOperationException",
            "IndexOutOfBoundsException", "ClassCastException",
            "Lazy", "Regex", "Range", "IntRange", "LongRange", "CharRange",
            "Enum", "Annotation",
            # kotlin.io / kotlin.text / kotlin.collections ubiquitous
            "Reader", "Writer", "BufferedReader", "BufferedWriter",
            # Coroutines + JVM common
            "Object", "Function", "Runnable", "Class", "Void",
        }
    ),
    color_hex="#A97BFF",
)
