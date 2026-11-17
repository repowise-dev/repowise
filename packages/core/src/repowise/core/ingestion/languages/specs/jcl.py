"""LanguageSpec for JCL jobs and procedures, a symbol-and-edge-only tag.

Steps become symbols in ``cobol/jcl.py``; ``framework_edges/mainframe.py`` links
each step to the program or PROC it runs. ``is_code=False``: health and dead
code do not grade job control.
"""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="jcl",
    display_name="JCL",
    extensions=frozenset({".jcl", ".prc", ".proc"}),
    is_code=False,
    is_passthrough=True,
    color_hex="#5C6E8A",
)
