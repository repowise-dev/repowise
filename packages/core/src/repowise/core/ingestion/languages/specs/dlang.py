"""LanguageSpec for dlang (extracted from the registry data table)."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="dlang",
    display_name="D",
    extensions=frozenset({".d"}),
    # dub convention: source/app.d.
    entry_point_patterns=("app.d",),
    is_passthrough=True,
)
