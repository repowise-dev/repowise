"""LanguageSpec for c (extracted from the registry data table)."""

from ..spec import LanguageSpec

#: Predeclared / standard-library scalar types that never resolve to a
#: user-defined struct, so they're dropped before the resolver lookup.
#: ``primitive_type`` / ``sized_type_specifier`` nodes are filtered
#: structurally in the extractor; this set catches the ``<stdint.h>`` /
#: ``<stddef.h>`` typedefs that the grammar surfaces as plain
#: ``type_identifier`` nodes.
#:
#: Exported because cpp shares the C extractor and grammar, so it needs the
#: same set.
BUILTIN_TYPES: frozenset[str] = frozenset(
    {
        "void", "char", "short", "int", "long", "float", "double",
        "signed", "unsigned", "bool", "_Bool", "_Complex",
        "size_t", "ssize_t", "rsize_t", "ptrdiff_t", "intptr_t", "uintptr_t",
        "int8_t", "int16_t", "int32_t", "int64_t",
        "uint8_t", "uint16_t", "uint32_t", "uint64_t",
        "intmax_t", "uintmax_t", "wchar_t", "wint_t", "char16_t", "char32_t",
        "va_list", "FILE",
    }
)

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
    builtin_types=BUILTIN_TYPES,
    color_hex="#555555",
)
