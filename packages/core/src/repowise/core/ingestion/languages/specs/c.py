"""LanguageSpec for c (extracted from the registry data table)."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="c",
    display_name="C",
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
