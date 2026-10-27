"""LanguageSpec for CICS BMS map source: mapsets and maps as symbols.

``framework_edges/mainframe.py`` links the COBOL programs that send or receive
a map to its mapset. ``is_code=False``, as for other screen definitions.
"""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="bms",
    display_name="BMS",
    extensions=frozenset({".bms"}),
    is_code=False,
    is_passthrough=True,
    color_hex="#4A7AA8",
)
