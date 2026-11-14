"""LanguageSpec for Perl 5."""

from ..spec import LanguageSpec

# Perl builtins that parse as function calls. Suppressed as call targets so
# the graph only records edges to subs defined in the repo.
_PERL_BUILTINS = frozenset(
    {
        "bless",
        "chomp",
        "chop",
        "close",
        "defined",
        "delete",
        "die",
        "do",
        "each",
        "eval",
        "exists",
        "exit",
        "grep",
        "join",
        "keys",
        "lc",
        "length",
        "local",
        "map",
        "my",
        "open",
        "pop",
        "print",
        "printf",
        "push",
        "ref",
        "return",
        "reverse",
        "scalar",
        "shift",
        "sort",
        "splice",
        "split",
        "sprintf",
        "substr",
        "uc",
        "undef",
        "unshift",
        "values",
        "wantarray",
        "warn",
    }
)

SPEC = LanguageSpec(
    tag="perl",
    display_name="Perl",
    # ``.t`` is the Test::More / prove convention.
    extensions=frozenset({".pl", ".pm", ".t"}),
    import_support="partial",
    # tree-sitter-perl from the language pack, loaded as COBOL's is.
    grammar_package="tree_sitter_language_pack",
    grammar_loader="get_language",
    grammar_loader_args=("perl",),
    scm_file="perl.scm",
    # prove's t/: a Perl file there is test material whatever its name.
    test_dir_tokens=("t",),
    manifest_files=("Makefile.PL", "Build.PL", "cpanfile", "dist.ini"),
    lock_files=("cpanfile.snapshot",),
    shebang_tokens=("perl",),
    builtin_calls=_PERL_BUILTINS,
    color_hex="#0298C3",
)
