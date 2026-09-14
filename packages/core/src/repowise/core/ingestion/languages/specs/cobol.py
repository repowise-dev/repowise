"""LanguageSpec for COBOL 85 source and copybook files."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="cobol",
    display_name="COBOL",
    extensions=frozenset({".cbl", ".cob", ".cobol", ".cpy"}),
    # COBOL has no standalone Python grammar wheel. The language pack ships
    # the maintained ABI-14 nolanlwin/yutaro-sakamoto grammar and returns a
    # ready-to-use tree_sitter.Language from get_language("cobol").
    grammar_package="tree_sitter_language_pack",
    grammar_loader="get_language",
    grammar_loader_args=("cobol",),
    scm_file="cobol.scm",
    # JCL, schedulers and dynamic CALL data-items invoke programs outside the
    # source graph. Without those inputs, unused/unreachable claims are unsound.
    dead_code_exempt=True,
    import_support="none",
    entry_point_patterns=(),
    manifest_files=(),
    shebang_tokens=(),
    builtin_calls=frozenset({"ACCEPT", "DISPLAY", "GOBACK", "STOP"}),
    builtin_parents=frozenset(),
    color_hex="#005CA5",
)
