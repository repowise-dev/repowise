"""LanguageSpec for haskell (extracted from the registry data table)."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="haskell",
    display_name="Haskell",
    # hspec convention: FooSpec.hs under test/.
    test_camel_suffixes=("Spec",),
    entry_point_patterns=("Main.hs",),
    # Setup.hs/Setup.lhs are the Cabal build-script convention — package
    # plumbing, not domain code; manifests never anchor or face layers.
    manifest_files=("package.yaml", "stack.yaml", "Setup.hs", "Setup.lhs"),
    extensions=frozenset({".hs", ".lhs"}),
    is_passthrough=True,
    # Lightweight regex resolver: import statements → module declaration index.
    import_support="partial",
)
