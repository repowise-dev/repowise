"""LanguageSpec for objectivec (extracted from the registry data table)."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="objectivec",
    display_name="Objective-C",
    extensions=frozenset({".m", ".mm"}),
    is_passthrough=True,
)
