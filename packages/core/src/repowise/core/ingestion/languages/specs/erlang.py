"""LanguageSpec for erlang (extracted from the registry data table)."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="erlang",
    display_name="Erlang",
    extensions=frozenset({".erl", ".hrl"}),
    is_passthrough=True,
)
