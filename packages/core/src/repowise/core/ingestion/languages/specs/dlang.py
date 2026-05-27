"""LanguageSpec for dlang (extracted from the registry data table)."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="dlang",
    display_name="D",
    extensions=frozenset({".d"}),
    is_passthrough=True,
)
