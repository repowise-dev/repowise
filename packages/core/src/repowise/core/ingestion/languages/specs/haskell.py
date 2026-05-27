"""LanguageSpec for haskell (extracted from the registry data table)."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="haskell",
    display_name="Haskell",
    extensions=frozenset({".hs", ".lhs"}),
    is_passthrough=True,
)
