"""LanguageSpec for shell (extracted from the registry data table)."""

from ..spec import LanguageSpec

# Shell builtins and no-op keywords that show up as `command` invocations but
# are never user-defined functions — suppressed as call targets so the graph
# only records edges to real functions (defined here or in a sourced file).
_SHELL_BUILTINS = frozenset(
    {
        ".",
        ":",
        "[",
        "[[",
        "alias",
        "bg",
        "break",
        "builtin",
        "cd",
        "command",
        "continue",
        "declare",
        "echo",
        "eval",
        "exec",
        "exit",
        "export",
        "false",
        "fg",
        "getopts",
        "hash",
        "let",
        "local",
        "logout",
        "printf",
        "pushd",
        "popd",
        "pwd",
        "read",
        "readonly",
        "return",
        "set",
        "shift",
        "shopt",
        "source",
        "test",
        "trap",
        "true",
        "type",
        "typeset",
        "ulimit",
        "umask",
        "unalias",
        "unset",
        "wait",
    }
)

SPEC = LanguageSpec(
    tag="shell",
    display_name="Shell",
    extensions=frozenset({".sh", ".bash", ".zsh"}),
    # Shell is code *and* infra: files still promote to CI/infra presentation
    # by name/path (see registry.non_infra_code_extensions), but they now get a
    # real AST (functions, source edges, function-level complexity).
    is_infra=True,
    import_support="partial",
    grammar_package="tree_sitter_bash",
    scm_file="shell.scm",
    shebang_tokens=("bash", " sh", "zsh"),
    builtin_calls=_SHELL_BUILTINS,
    color_hex="#89E051",
)
