"""LanguageSpec for fsharp (extracted from the registry data table)."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="fsharp",
    display_name="F#",
    extensions=frozenset({".fs", ".fsi", ".fsx"}),
    # .NET convention shared with C#: Program.fs.
    entry_point_patterns=("Program.fs",),
    is_passthrough=True,
    # Lightweight regex resolver: open → namespace/module declaration index,
    # plus the fsproj compile-order dependency spine.
    import_support="partial",
)
