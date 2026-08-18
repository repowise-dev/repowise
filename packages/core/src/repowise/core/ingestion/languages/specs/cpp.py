"""LanguageSpec for cpp (extracted from the registry data table)."""

from ..spec import LanguageSpec
from .c import BUILTIN_TYPES as _C_BUILTIN_TYPES

#: Include fragments: C++ source that is ``#include``d into a translation unit
#: rather than compiled as one of its own. The convention carries inline and
#: template implementations, and generated binding tables pasted mid-file.
#:
#: Exported because three questions elsewhere need the same answer and used to
#: each keep their own list: which extensions are C++ at all, which behave like
#: a header for include resolution and reachability, and which are a fragment
#: rather than a module. See ``traverser`` for the last of those.
#:
#: ``.inc`` was claimed exclusively by the Pascal spec, which routed abseil's
#: 24 template-implementation fragments to a grammar that cannot parse them —
#: no symbols, no edges, no error. Reassigned here on the corpus count: 24 of
#: the 25 code-bearing ``.inc`` files are C++ and none are Pascal, on a corpus
#: that does contain Pascal. The one PHP ``.inc`` (a Drush plugin) is the known
#: cost; ``.inc`` is shared by C++, PHP and Pascal in the wild, and a
#: content- or sibling-aware router is the real answer if a second language's
#: usage ever shows up in the corpus.
INCLUDE_FRAGMENT_EXTENSIONS: frozenset[str] = frozenset({".inl", ".ipp", ".tpp", ".inc"})

SPEC = LanguageSpec(
    tag="cpp",
    display_name="C++",
    import_support="full",
    # GoogleTest conventions: foo_test.cc / foo_unittest.cc / test_foo.cpp.
    test_stem_prefixes=("test_",),
    test_stem_suffixes=("_test", "_unittest"),
    # A top-level include/ holds a C++ library's installed public headers —
    # its API surface (fmt, leveldb, boost layouts). Root-anchored: a
    # vendored include/ deep in another tree must not mint the layer.
    layer_dir_hints=(("/include", "API"),),
    extensions=frozenset({".cpp", ".cc", ".cxx", ".h", ".hh", ".hpp", ".hxx"})
    | INCLUDE_FRAGMENT_EXTENSIONS,
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
    # Same C extractor and grammar as c.py, so the same builtin set applies.
    builtin_types=_C_BUILTIN_TYPES,
    color_hex="#F34B7D",
)
