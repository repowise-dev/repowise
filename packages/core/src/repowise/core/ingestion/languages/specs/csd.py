"""LanguageSpec for CICS CSD definitions (DFHCSDUP input): programs and transactions.

``framework_edges/mainframe.py`` links each defined program, and each
transaction's program, from the CSD. ``is_code=False``, as for job control.
"""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="csd",
    display_name="CICS CSD",
    extensions=frozenset({".csd"}),
    is_code=False,
    is_passthrough=True,
    color_hex="#3E6A8C",
)
