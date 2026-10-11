"""LanguageSpec for nim (extracted from the registry data table)."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="nim",
    display_name="Nim",
    extensions=frozenset({".nim"}),
    # nimble binary projects: src/main.nim.
    entry_point_patterns=("main.nim",),
    is_passthrough=True,
)
