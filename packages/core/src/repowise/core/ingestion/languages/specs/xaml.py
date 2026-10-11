"""LanguageSpec for xaml (extracted from the registry data table)."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="xaml",
    display_name="XAML",
    extensions=frozenset({".xaml", ".axaml"}),
    is_code=False,
    is_passthrough=True,
    color_hex="#0C479C",
)
