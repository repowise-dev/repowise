"""LanguageSpec for ruby (extracted from the registry data table)."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="ruby",
    display_name="Ruby",
    import_support="full",
    test_stem_suffixes=("_spec",),
    test_fixture_stems=("spec_helper", "test_helper"),
    suite_anchor_stems=("spec_helper", "test_helper"),
    # RSpec: a Ruby file under spec/ is test material whatever its name
    # (support helpers, vendored fixtures) — no filename corroboration.
    test_dir_tokens=("spec", "specs"),
    # Rails app/jobs/ (models/controllers/services are generic tokens already).
    layer_dir_hints=(("jobs", "Service"),),
    entry_point_patterns=("main.rb", "app.rb", "config.ru"),
    extensions=frozenset({".rb"}),
    # Rack's config.ru is Ruby with a .ru extension — without this mapping
    # the traverser would skip it as unknown and the entry pattern is dead.
    special_filenames=frozenset({"config.ru"}),
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
