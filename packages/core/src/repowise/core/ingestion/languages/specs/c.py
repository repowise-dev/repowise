"""LanguageSpec for c (extracted from the registry data table)."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="c",
    display_name="C",
    import_support="full",
    # Same test conventions as C++ (GoogleTest/Unity): foo_test.c / test_foo.c.
    test_stem_prefixes=("test_",),
    test_stem_suffixes=("_test", "_unittest"),
    # A top-level include/ holds a C library's installed public headers —
    # its API surface (libuv, curl, openssl layouts). Root-anchored: a
    # vendored include/ deep in another tree must not mint the layer.
    layer_dir_hints=(("/include", "API"),),
    extensions=frozenset({".c"}),
    shares_grammar_with="cpp",
    scm_file="c.scm",
    heritage_node_types=frozenset(),
    entry_point_patterns=("main.c",),
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
            "memcpy",
            "memset",
            "memmove",
            "memcmp",
            "strlen",
            "strcpy",
            "strncpy",
            "strcat",
            "strcmp",
            "strncmp",
            "sizeof",
            "offsetof",
            "assert",
            "abort",
            "exit",
        }
    ),
    color_hex="#555555",
)
