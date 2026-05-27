"""LanguageSpec for ruby (extracted from the registry data table)."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="ruby",
    display_name="Ruby",
    extensions=frozenset({".rb"}),
    grammar_package="tree_sitter_ruby",
    scm_file="ruby.scm",
    heritage_node_types=frozenset({"class"}),
    manifest_files=("Gemfile",),
    lock_files=("Gemfile.lock",),
    shebang_tokens=("ruby",),
    builtin_calls=frozenset(
        {
            "puts",
            "print",
            "p",
            "pp",
            "raise",
            "fail",
            "require",
            "require_relative",
            "include",
            "extend",
            "prepend",
            "attr_reader",
            "attr_writer",
            "attr_accessor",
            "lambda",
            "proc",
        }
    ),
    builtin_parents=frozenset(
        {
            "Object",
            "BasicObject",
            "Exception",
            "StandardError",
            "RuntimeError",
            "ScriptError",
            "LoadError",
            "SyntaxError",
            "Comparable",
            "Enumerable",
            "Kernel",
        }
    ),
    color_hex="#CC342D",
)
