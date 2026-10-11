"""LanguageSpec for elm (extracted from the registry data table)."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="elm",
    display_name="Elm",
    extensions=frozenset({".elm"}),
    # elm make targets src/Main.elm.
    entry_point_patterns=("Main.elm",),
    is_passthrough=True,
)
