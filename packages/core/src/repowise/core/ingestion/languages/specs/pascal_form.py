"""LanguageSpec for Delphi, FireMonkey and Lazarus form files.

A form file is a text description of a unit's component tree and the event
handlers bound to it. It has no grammar here: ``framework_edges/pascal_forms.py``
reads it and links it to the unit that shares its stem.
"""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="pascal_form",
    display_name="Object Pascal form",
    extensions=frozenset({".dfm", ".fmx", ".lfm"}),
    is_code=False,
    is_passthrough=True,
    color_hex="#E3352E",
)
