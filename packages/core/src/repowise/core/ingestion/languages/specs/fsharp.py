"""LanguageSpec for fsharp (extracted from the registry data table)."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="fsharp",
    display_name="F#",
    extensions=frozenset({".fs", ".fsi", ".fsx"}),
    is_passthrough=True,
)
