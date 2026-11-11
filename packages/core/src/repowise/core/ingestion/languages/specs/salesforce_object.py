"""LanguageSpec for Salesforce object and field metadata.

Each object becomes a class symbol and each field a property symbol (the SQL
DDL precedent). Data, so dead code never reports them: layouts, permission sets
and the org use objects and fields where no parser looks.
"""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="salesforce_object",
    display_name="Salesforce Object",
    extensions=frozenset({".object"}),
    name_suffixes=frozenset({".object-meta.xml", ".field-meta.xml"}),
    is_code=False,
    is_passthrough=True,
    color_hex="#00A1E0",
)
