"""LanguageSpec for ``.soql`` files: saved SOQL queries, read for the objects and fields they name."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="soql",
    display_name="SOQL",
    extensions=frozenset({".soql"}),
    is_code=False,
    is_passthrough=True,
    color_hex="#00A1E0",
)
