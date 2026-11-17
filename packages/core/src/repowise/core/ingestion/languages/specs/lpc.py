"""LDMud LPC, explicitly routed by mudlib root because it shares C's suffixes."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="lpc",
    display_name="LPC (LDMud)",
    extensions=frozenset(),
    grammar_package="tree_sitter_lpc",
    scm_file="lpc.scm",
    import_support="partial",
    # The driver invokes applies, named callbacks and runtime-loaded objects.
    # A partial static graph cannot establish that a program or lfun is dead.
    dead_code_exempt=True,
    color_hex="#7a4fa3",
)
