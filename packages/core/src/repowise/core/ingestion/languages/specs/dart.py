"""LanguageSpec for dart (extracted from the registry data table)."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="dart",
    display_name="Dart",
    extensions=frozenset({".dart"}),
    is_passthrough=True,
)
