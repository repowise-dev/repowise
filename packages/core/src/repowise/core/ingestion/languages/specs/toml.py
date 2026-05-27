"""LanguageSpec for toml (extracted from the registry data table)."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="toml",
    display_name="TOML",
    extensions=frozenset({".toml"}),
    is_code=False,
    is_passthrough=True,
    color_hex="#9C4221",
)
