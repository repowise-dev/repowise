"""LanguageSpec for r (extracted from the registry data table)."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="r",
    display_name="R",
    extensions=frozenset({".r"}),
    is_passthrough=True,
)
