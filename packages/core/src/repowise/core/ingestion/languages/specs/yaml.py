"""LanguageSpec for yaml (extracted from the registry data table)."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="yaml",
    display_name="YAML",
    extensions=frozenset({".yaml", ".yml"}),
    is_code=False,
    is_passthrough=True,
    color_hex="#CB171E",
)
