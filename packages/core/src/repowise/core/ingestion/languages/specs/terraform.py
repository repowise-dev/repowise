"""LanguageSpec for terraform (extracted from the registry data table)."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="terraform",
    display_name="Terraform",
    extensions=frozenset({".tf", ".hcl"}),
    is_infra=True,
    is_passthrough=True,
    color_hex="#5C4EE5",
)
