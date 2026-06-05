"""LanguageSpec for haskell (extracted from the registry data table)."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="haskell",
    display_name="Haskell",
    # hspec convention: FooSpec.hs under test/.
    test_camel_suffixes=("Spec",),
    entry_point_patterns=("Main.hs",),
    extensions=frozenset({".hs", ".lhs"}),
    is_passthrough=True,
)
