"""LanguageSpec for json (extracted from the registry data table)."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="json",
    display_name="JSON",
    extensions=frozenset({".json"}),
    is_code=False,
    is_passthrough=True,
    color_hex="#292929",
)
