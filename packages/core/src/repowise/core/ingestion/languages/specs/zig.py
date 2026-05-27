"""LanguageSpec for zig (extracted from the registry data table)."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="zig",
    display_name="Zig",
    extensions=frozenset({".zig"}),
    is_passthrough=True,
)
