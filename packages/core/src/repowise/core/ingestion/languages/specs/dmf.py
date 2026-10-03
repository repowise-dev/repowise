"""LanguageSpec for BYOND Dream Maker interface files."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="dmf",
    display_name="BYOND Dream Maker Interface",
    extensions=frozenset({".dmf"}),
    is_code=False,
    is_passthrough=True,
    color_hex="#447FC0",
)
