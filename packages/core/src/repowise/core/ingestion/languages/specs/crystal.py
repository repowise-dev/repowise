"""LanguageSpec for crystal (extracted from the registry data table)."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="crystal",
    display_name="Crystal",
    extensions=frozenset({".cr"}),
    # shards builds src/main.cr by default.
    entry_point_patterns=("main.cr",),
    is_passthrough=True,
)
