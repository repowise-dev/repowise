"""LanguageSpec for elm (extracted from the registry data table)."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="elm",
    display_name="Elm",
    extensions=frozenset({".elm"}),
    is_passthrough=True,
)
