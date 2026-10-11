"""LanguageSpec for proto (extracted from the registry data table)."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="proto",
    display_name="Protocol Buffers",
    extensions=frozenset({".proto"}),
    is_code=False,
    is_passthrough=True,
    is_api_contract=True,
)
