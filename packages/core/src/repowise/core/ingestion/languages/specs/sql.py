"""LanguageSpec for sql (extracted from the registry data table)."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="sql",
    display_name="SQL",
    extensions=frozenset({".sql"}),
    is_code=False,
    is_passthrough=True,
    # dbt ref()/source() resolution via the lightweight tier: real edges
    # inside dbt projects, nothing claimed for plain SQL files.
    import_support="partial",
)
