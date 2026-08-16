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
    # Primitives + ubiquitous JDK types that never resolve to a user-defined
    # Java/Kotlin class in the workspace. Stripping them at extraction time
    # avoids polluting the resolver with hopeless lookups. The list errs on
    # the side of inclusion: a user class colliding with one of these names
    # still surfaces through the value-import path, just not through the
    # type-ref path.
    builtin_types=frozenset(
        {
            # Java primitives + builtin type nodes
            "boolean", "byte", "short", "int", "long", "float", "double",
            "char", "void", "var",
            # java.lang (auto-imported)
            "Object", "String", "Class", "Enum", "Record",
            "Integer", "Long", "Double", "Float", "Boolean", "Character",
            "Byte", "Short", "Number", "Void",
            "Thread", "Runnable", "Runtime", "Process", "ProcessBuilder",
            "Throwable", "Exception", "RuntimeException", "Error",
            "IllegalArgumentException", "IllegalStateException",
            "NullPointerException", "UnsupportedOperationException",
            "IndexOutOfBoundsException", "ClassCastException",
            "ArithmeticException", "SecurityException", "ClassNotFoundException",
            "InterruptedException", "CloneNotSupportedException",
            "StringBuilder", "StringBuffer",
            "Comparable", "Iterable", "AutoCloseable", "Cloneable",
            "Override", "Deprecated", "SuppressWarnings",
            "FunctionalInterface", "SafeVarargs",
            "Math", "System",
            # java.util ubiquitous containers (almost always external when used
            # as a type position; the actual element type is captured separately
            # by the same query via the type_arguments inner capture).
            "List", "ArrayList", "LinkedList",
            "Map", "HashMap", "LinkedHashMap", "TreeMap", "ConcurrentHashMap",
            "Set", "HashSet", "LinkedHashSet", "TreeSet",
            "Collection", "Collections", "Iterator", "Optional",
            "Queue", "Deque", "ArrayDeque", "Stack",
            # java.util.function
            "Function", "BiFunction", "Consumer", "BiConsumer", "Supplier",
            "Predicate", "BiPredicate", "UnaryOperator", "BinaryOperator",
            # java.util.concurrent ubiquitous
            "Future", "CompletableFuture", "Executor", "ExecutorService",
            "CountDownLatch", "Semaphore", "AtomicBoolean", "AtomicInteger",
            "AtomicLong", "AtomicReference",
            # java.time
            "Instant", "Duration", "LocalDate", "LocalTime", "LocalDateTime",
            "ZonedDateTime", "OffsetDateTime", "Period", "ZoneId",
            # java.io
            "File", "InputStream", "OutputStream", "Reader", "Writer",
            "IOException", "Serializable",
        }
    ),
    color_hex="#B07219",
)
