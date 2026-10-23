"""LanguageSpec for Salesforce object and field metadata.

Source format decomposes an SObject into ``objects/<Obj>/<Obj>.object-meta.xml``
plus one ``objects/<Obj>/fields/<Field>.field-meta.xml`` per field; the legacy
metadata API format keeps the fields inline in ``objects/<Obj>.object``.
``special_handlers`` reads them into a class-kind symbol per object and a
property-kind symbol per field (the SQL DDL precedent: a table is a class, its
columns its members), which SOQL, DML and trigger references in Apex bind to.

Only these two ``-meta.xml`` kinds are claimed. Every other sidecar
(``Foo.cls-meta.xml``, layouts, flows) keeps the unclaimed ``.xml`` suffix.

``is_code=False`` for the same reason as SQL: the files are data, and dead code
never reports a schema symbol, because layouts, permission sets, flows and the
org itself use objects and fields where no parser looks.
"""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="salesforce_object",
    display_name="Salesforce Object",
    extensions=frozenset({".object"}),
    name_suffixes=frozenset({".object-meta.xml", ".field-meta.xml"}),
    is_code=False,
    is_passthrough=True,
    # Salesforce cloud blue.
    color_hex="#00A1E0",
)
