"""LanguageSpec for elixir (extracted from the registry data table)."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="elixir",
    display_name="Elixir",
    extensions=frozenset({".ex", ".exs"}),
    is_passthrough=True,
)
