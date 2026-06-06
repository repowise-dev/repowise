"""LanguageSpec for go (extracted from the registry data table)."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="go",
    display_name="Go",
    import_support="full",
    test_stem_suffixes=("_test",),
    # golang-standards layout: internal/ and pkg/ hold application-internal
    # and exported library code (cmd/ → CLI is already a generic token).
    layer_dir_hints=(("internal", "Service"), ("pkg", "Service")),
    extensions=frozenset({".go"}),
    grammar_package="tree_sitter_go",
    scm_file="go.scm",
    heritage_node_types=frozenset({"type_spec"}),
    entry_point_patterns=("main.go", "cmd/main.go"),
    manifest_files=("go.mod",),
    lock_files=("go.sum",),
    generated_suffixes=("_grpc.pb.go",),
    blocked_dirs=("vendor",),
    builtin_calls=frozenset(
        {
            "make",
            "len",
            "cap",
            "new",
            "append",
            "copy",
            "close",
            "delete",
            "complex",
            "real",
            "imag",
            "panic",
            "recover",
            "print",
            "println",
        }
    ),
    builtin_parents=frozenset({"error"}),
    color_hex="#00ADD8",
)
