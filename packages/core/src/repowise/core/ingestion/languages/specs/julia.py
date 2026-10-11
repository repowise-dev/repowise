"""LanguageSpec for julia (extracted from the registry data table)."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="julia",
    display_name="Julia",
    extensions=frozenset({".jl"}),
    is_passthrough=True,
)
