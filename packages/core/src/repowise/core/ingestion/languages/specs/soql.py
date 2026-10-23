"""LanguageSpec for ``.soql`` files: saved Salesforce SOQL queries.

The sfdx project template ships ``scripts/soql/*.soql`` for running queries
from the editor. ``special_handlers`` reads the objects and fields a query
names (``salesforce_soql.parse_soql_file``); there are no symbols.
"""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="soql",
    display_name="SOQL",
    extensions=frozenset({".soql"}),
    is_code=False,
    is_passthrough=True,
    color_hex="#00A1E0",
)
