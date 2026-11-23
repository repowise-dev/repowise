"""LanguageSpec for Salesforce Visualforce pages and components, an edge-only tag.

Markup declares no symbols (``is_code=False``, never reported as dead); the
edges come from ``framework_edges/visualforce.py``. Both suffixes are claimed
anywhere, as Salesforce maps them to ApexPage / ApexComponent in any folder.
"""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="visualforce",
    display_name="Visualforce",
    extensions=frozenset({".page", ".component"}),
    is_code=False,
    is_passthrough=True,
    # Salesforce blue, the same as the Apex spec.
    color_hex="#1797C0",
)
