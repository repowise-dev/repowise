"""LanguageSpec for elixir (extracted from the registry data table)."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="elixir",
    display_name="Elixir",
    # ExUnit conventions: test/foo_test.exs + test/test_helper.exs.
    test_stem_suffixes=("_test",),
    test_fixture_stems=("test_helper",),
    extensions=frozenset({".ex", ".exs"}),
    is_passthrough=True,
)
