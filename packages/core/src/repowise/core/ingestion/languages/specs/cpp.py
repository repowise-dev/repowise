"""LanguageSpec for cpp (extracted from the registry data table)."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="cpp",
    display_name="C++",
    extensions=frozenset({".cpp", ".cc", ".cxx", ".h", ".hpp", ".hxx"}),
    grammar_package="tree_sitter_cpp",
    scm_file="cpp.scm",
    heritage_node_types=frozenset({"class_specifier", "struct_specifier"}),
    entry_point_patterns=("main.cpp", "main.cc"),
    builtin_calls=frozenset(
        {
            "printf",
            "scanf",
            "fprintf",
            "sprintf",
            "snprintf",
            "malloc",
            "calloc",
            "realloc",
            "free",
            "sizeof",
            "alignof",
            "typeid",
            "decltype",
            "static_cast",
            "dynamic_cast",
            "const_cast",
            "reinterpret_cast",
            "move",
            "forward",
            "make_shared",
            "make_unique",
            "make_pair",
            "cout",
            "cerr",
            "endl",
        }
    ),
    builtin_parents=frozenset(
        {
            "exception",
            "runtime_error",
            "logic_error",
            "invalid_argument",
            "out_of_range",
            "overflow_error",
            "string",
            "vector",
            "map",
            "set",
            "list",
            "deque",
            "unordered_map",
            "unordered_set",
            "shared_ptr",
            "unique_ptr",
            "weak_ptr",
        }
    ),
    color_hex="#F34B7D",
)
