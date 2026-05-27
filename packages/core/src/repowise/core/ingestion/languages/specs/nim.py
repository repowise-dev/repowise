"""LanguageSpec for nim (extracted from the registry data table)."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="nim",
    display_name="Nim",
    extensions=frozenset({".nim"}),
    is_passthrough=True,
)
