"""LanguageSpec for dockerfile (extracted from the registry data table)."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="dockerfile",
    display_name="Dockerfile",
    special_filenames=frozenset({"Dockerfile", "dockerfile"}),
    is_infra=True,
    is_passthrough=True,
    color_hex="#384D54",
)
