"""LanguageSpec for openapi (extracted from the registry data table)."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="openapi",
    display_name="OpenAPI",
    is_code=False,
    is_passthrough=True,
    is_api_contract=True,
)
