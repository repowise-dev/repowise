"""LanguageSpec for graphql (extracted from the registry data table)."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="graphql",
    display_name="GraphQL",
    extensions=frozenset({".graphql", ".gql"}),
    is_code=False,
    is_passthrough=True,
    is_api_contract=True,
)
