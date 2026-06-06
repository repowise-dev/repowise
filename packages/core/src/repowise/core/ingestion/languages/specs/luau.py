"""LanguageSpec for luau (extracted from the registry data table)."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="luau",
    display_name="Luau",
    import_support="partial",
    # Rojo treats both .lua and .luau as Luau modules.  Luau's grammar is
    # a superset of Lua 5.1, so vanilla Lua files parse cleanly too.
    extensions=frozenset({".lua", ".luau"}),
    grammar_package="tree_sitter_luau",
    scm_file="luau.scm",
    heritage_node_types=frozenset(),
    entry_point_patterns=("init.luau", "init.lua"),
    manifest_files=("default.project.json", "wally.toml", ".rojo.json"),
    blocked_dirs=("Packages", "ServerPackages", "DevPackages"),
    builtin_calls=frozenset(
        {
            "print",
            "warn",
            "error",
            "assert",
            "pcall",
            "xpcall",
            "select",
            "type",
            "typeof",
            "tonumber",
            "tostring",
            "ipairs",
            "pairs",
            "next",
            "rawget",
            "rawset",
            "rawequal",
            "rawlen",
            "setmetatable",
            "getmetatable",
            "unpack",
            "require",
        }
    ),
    color_hex="#00A2FF",
)
