"""LanguageSpec for elixir (extracted from the registry data table)."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="elixir",
    display_name="Elixir",
    # ExUnit conventions: test/foo_test.exs + test/test_helper.exs.
    test_stem_suffixes=("_test",),
    test_fixture_stems=("test_helper",),
    suite_anchor_stems=("test_helper",),
    # OTP Application callback (lib/<app>/application.ex); mix.exs is a
    # manifest, not an entry.
    entry_point_patterns=("application.ex",),
    manifest_files=("mix.exs",),
    extensions=frozenset({".ex", ".exs"}),
    is_passthrough=True,
    # Lightweight regex resolver: alias/import/use/require → defmodule index.
    import_support="partial",
)
